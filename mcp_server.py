"""
Serveur MCP pour webtools : client HTTP de l'API REST.

Les outils appellent l'API (WEBTOOLS_API, cle WEBTOOLS_API_KEY) au lieu
d'importer ses fonctions : ce module n'importe rien de `app/` et tourne dans
son propre environnement Python (les dependances de l'API et du serveur MCP
sont incompatibles entre elles).
"""

import asyncio
import hmac
import json
import logging
import os
import re
import tempfile
import time
import uuid
from typing import Annotated, Any

import httpx
from fastmcp import Context, FastMCP
from fastmcp.server.auth.auth import ClientRegistrationOptions
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.middleware import Middleware as StarletteMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webtools-mcp")

# Authentification : flux OAuth 2.1 conforme a la spec MCP (decouverte,
# enregistrement dynamique de client, PKCE, echange de code) pour que
# Claude Desktop/Code puissent se connecter via leur mecanisme natif
# "Add custom connector". InMemoryOAuthProvider gere la mecanique OAuth,
# mais delivre normalement un code d'autorisation sans jamais verifier
# qui fait la demande (documente "for testing purposes" dans son propre
# code source) - le middleware ci-dessous comble ce trou en exigeant la
# cle partagee avant de laisser passer vers /authorize, une seule fois
# par session navigateur (cookie signe de courte duree).
MCP_ACCESS_KEY = os.environ.get("MCP_ACCESS_TOKEN", "")
if not MCP_ACCESS_KEY:
    raise RuntimeError("MCP_ACCESS_TOKEN doit etre defini avant de lancer le serveur MCP expose publiquement")

SESSION_COOKIE = "webtools_mcp_gate"
SESSION_TTL_SECONDS = 600  # 10 min, le temps de finir le flux OAuth


def _make_session_value() -> str:
    ts = str(int(time.time()))
    sig = hmac.new(MCP_ACCESS_KEY.encode(), ts.encode(), "sha256").hexdigest()
    return f"{ts}.{sig}"


def _is_valid_session(value: str | None) -> bool:
    if not value or "." not in value:
        return False
    ts, sig = value.split(".", 1)
    expected = hmac.new(MCP_ACCESS_KEY.encode(), ts.encode(), "sha256").hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    # int(ts) sans garde levait ValueError sur un cookie malforme ("abc.<sig>")
    # -> 500 au lieu d'un refus propre. compare_digest protege deja du
    # contournement, ceci est de la robustesse.
    try:
        issued_at = int(ts)
    except ValueError:
        return False
    return (time.time() - issued_at) < SESSION_TTL_SECONDS


LOGIN_FORM = """<!DOCTYPE html><html><body style="font-family:sans-serif;max-width:400px;margin:80px auto">
<h2>webtools - Autorisation requise</h2>
<p>Entrez la cle d'acces partagee pour continuer la connexion.</p>
<form method="post">
<input type="password" name="key" placeholder="Cle d'acces" style="width:100%;padding:8px" autofocus>
<button type="submit" style="margin-top:10px;padding:8px 16px">Continuer</button>
{error}
</form></body></html>"""


class AuthorizeGateMiddleware(BaseHTTPMiddleware):
    """Exige la cle d'acces partagee avant de laisser passer vers /authorize."""

    async def dispatch(self, request: Request, call_next):
        logger.info(f"[GATE] dispatch appele pour path={request.url.path} method={request.method}")
        if not request.url.path.endswith("/authorize"):
            return await call_next(request)
        logger.info("[GATE] /authorize intercepte")

        session = request.cookies.get(SESSION_COOKIE)
        if _is_valid_session(session):
            return await call_next(request)

        if request.method == "POST":
            form = await request.form()
            submitted = form.get("key", "")
            if hmac.compare_digest(str(submitted), MCP_ACCESS_KEY):
                # Cle correcte : poser le cookie, re-soumettre en GET vers
                # la meme URL (avec les parametres OAuth d'origine intacts,
                # deja dans la query string de la requete initiale)
                resp = RedirectResponse(url=str(request.url), status_code=303)
                resp.set_cookie(
                    SESSION_COOKIE, _make_session_value(),
                    max_age=SESSION_TTL_SECONDS,
                    httponly=True,
                    secure=True,      # service publie en HTTPS
                    samesite="lax",
                )
                return resp
            # Ralentissement sur echec : sans lui, la cle partagee est
            # brute-forcable a la vitesse du reseau.
            await asyncio.sleep(1)
            return HTMLResponse(LOGIN_FORM.format(error="<p style='color:red'>Cle incorrecte</p>"), status_code=401)

        return HTMLResponse(LOGIN_FORM.format(error=""))


mcp = FastMCP(
    "webtools",
    auth=InMemoryOAuthProvider(
        base_url=os.environ.get("MCP_PUBLIC_URL", "http://localhost:8090"),
        client_registration_options=ClientRegistrationOptions(enabled=True),
    ),
)

# --- Client HTTP de l'API REST --------------------------------------------

# Transport injectable : les tests y placent un httpx.MockTransport.
_TRANSPORT: httpx.AsyncBaseTransport | None = None

_QUICK_TIMEOUT = 120.0
_DEEP_TIMEOUT = 900.0
_SHORT_TIMEOUT = 60.0


def _api_base() -> str:
    return os.environ.get("WEBTOOLS_API", "http://localhost:8000").rstrip("/")


def _api_headers() -> dict[str, str]:
    key = os.environ.get("WEBTOOLS_API_KEY", "")
    return {"X-API-Key": key} if key else {}


def _error_detail(response: httpx.Response) -> str:
    """Extrait un message lisible d'une reponse HTTP en erreur."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:300].strip()
    if isinstance(body, dict) and body.get("detail"):
        return str(body["detail"])[:300]
    return str(body)[:300]


async def _api_post(path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """
    POST JSON vers l'API. Rend toujours un dictionnaire : le corps decode en
    cas de succes HTTP, sinon {"success": False, "error": ...}.
    """
    try:
        async with httpx.AsyncClient(
            base_url=_api_base(),
            headers=_api_headers(),
            timeout=timeout,
            transport=_TRANSPORT,
        ) as client:
            response = await client.post(path, json=payload)
    except httpx.HTTPError as exc:
        return {"success": False, "error": f"API injoignable ({type(exc).__name__}): {exc}"}

    if response.status_code != 200:
        return {
            "success": False,
            "error": f"API HTTP {response.status_code}: {_error_detail(response)}",
        }
    try:
        body = response.json()
    except ValueError:
        return {"success": False, "error": "Reponse de l'API non JSON"}
    if not isinstance(body, dict):
        return {"success": False, "error": "Reponse de l'API inattendue (objet JSON attendu)"}
    return body


# --- Conversion du rapport en markdown -------------------------------------

def _scalar_items(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(v, (str, int, float, bool))}


def format_deep_report(payload: dict[str, Any]) -> str:
    """
    Convertit la reponse de /research/deep en markdown lisible par un agent.
    Fonction pure, tolerante aux champs absents ou ajoutes. Le champ `data`
    des sections (passages extraits integraux) n'est jamais emis.
    """
    result = payload.get("result")
    if not isinstance(result, dict):
        result = {}
    parts: list[str] = []

    title = result.get("title") or payload.get("title") or payload.get("topic")
    if title:
        parts.append(f"# {title}")
    summary = result.get("summary")
    if summary:
        parts.append(f"## Synthese\n\n{summary}")

    sections = result.get("sections")
    for section in sections if isinstance(sections, list) else []:
        if not isinstance(section, dict):
            continue
        heading = section.get("title") or "Section"
        content = section.get("content") or ""
        parts.append(f"## {heading}\n\n{content}".rstrip())

    biblio = result.get("bibliography")
    lines: list[str] = []
    for index, entry in enumerate(biblio if isinstance(biblio, list) else [], start=1):
        if not isinstance(entry, dict):
            continue
        # Les [n] du texte sont les identifiants de l'API, pas la position.
        if isinstance(entry.get("id"), int):
            index = entry["id"]
        label = entry.get("title") or entry.get("url") or "Source"
        line = f"{index}. {label}"
        if entry.get("url") and entry.get("url") != label:
            line += f" - {entry['url']}"
        extras = [str(entry[k]) for k in ("type", "date") if entry.get(k)]
        if extras:
            line += f" ({', '.join(extras)})"
        lines.append(line)
    if lines:
        parts.append("## Bibliographie\n\n" + "\n".join(lines))

    return "\n\n".join(parts)


def _count_sources(sources: Any) -> int:
    """Nombre de sources : l'API les groupe par categorie (websites, apis, ...)."""
    if isinstance(sources, list):
        return len(sources)
    if not isinstance(sources, dict):
        return 0
    return sum(len(group) for group in sources.values() if isinstance(group, list))


def build_deep_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    """Bloc diagnostics : ce que l'API dit de l'execution, sans le contenu."""
    diagnostics: dict[str, Any] = {}
    api_diag = payload.get("diagnostics")
    if isinstance(api_diag, dict):
        diagnostics.update(api_diag)
    for key in (
        "degraded", "timed_out", "failed_sections", "unsourced_sections",
        "missing_sections", "processing_time", "error",
    ):
        if payload.get(key) is not None and key not in diagnostics:
            diagnostics[key] = payload[key]
    execution = _scalar_items(payload.get("execution"))
    if execution and "execution" not in diagnostics:
        diagnostics["execution"] = execution
    sources = payload.get("sources")
    if isinstance(sources, (list, dict)) and "sources_count" not in diagnostics:
        diagnostics["sources_count"] = _count_sources(sources)
    return diagnostics


def deep_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Resultat d'outil pour une reponse de recherche approfondie."""
    if payload.get("success") is False:
        failure: dict[str, Any] = {
            "success": False,
            "error": payload.get("error") or "La recherche approfondie a echoue",
            "diagnostics": build_deep_diagnostics(payload),
        }
        # Un rapport degrade ou partiel (delai global depasse) est rendu avec
        # success=false : le rapport existe, il ne faut pas le jeter.
        result = payload.get("result")
        if isinstance(result, dict) and (result.get("sections") or result.get("summary")):
            failure["report_markdown"] = format_deep_report(payload)
            failure["degraded"] = bool(payload.get("degraded"))
            failure["timed_out"] = bool(payload.get("timed_out"))
        return failure
    return {
        "success": True,
        "topic": payload.get("topic"),
        "report_markdown": format_deep_report(payload),
        "diagnostics": build_deep_diagnostics(payload),
    }


# --- Recherche approfondie en tache de fond --------------------------------
# research_deep peut prendre de 30 s a plusieurs minutes : un appel bloquant
# fait expirer le client MCP. La tache tourne en arriere-plan et son etat est
# ecrit sur disque (un fichier JSON par tache) pour survivre a une relance du
# superviseur.

_TASKS: dict[str, dict[str, Any]] = {}
_BACKGROUND: set[asyncio.Task] = set()
_TASK_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_TASK_RETENTION_SECONDS = 3600


def _max_concurrent_deep() -> int:
    try:
        return max(1, int(os.environ.get("MCP_MAX_CONCURRENT_DEEP", "2")))
    except ValueError:
        return 2


def _state_dir() -> str:
    return os.environ.get("WEBTOOLS_STATE_DIR") or os.path.join(
        tempfile.gettempdir(), "webtools_mcp_state"
    )


def _persist_task(task_id: str) -> None:
    """Ecriture atomique : fichier temporaire puis os.replace."""
    directory = _state_dir()
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=f".{task_id}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(_TASKS[task_id], handle, ensure_ascii=False)
            os.replace(tmp_path, os.path.join(directory, f"{task_id}.json"))
        except BaseException:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
    except OSError as exc:
        logger.error("Persistance de la tache %s impossible: %s", task_id, exc)


def _forget_task(task_id: str) -> None:
    _TASKS.pop(task_id, None)
    try:
        os.unlink(os.path.join(_state_dir(), f"{task_id}.json"))
    except OSError:
        pass


def load_tasks() -> None:
    """
    Recharge les taches ecrites sur disque. Une tache `running` n'a plus de
    coroutine apres une relance : elle devient `interrupted`.
    """
    _TASKS.clear()
    directory = _state_dir()
    if not os.path.isdir(directory):
        return
    for name in os.listdir(directory):
        task_id, ext = os.path.splitext(name)
        if ext != ".json" or not _TASK_ID_RE.match(task_id):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as handle:
                task = json.load(handle)
        except (OSError, ValueError):
            logger.warning("Fichier de tache illisible ignore: %s", name)
            continue
        if not isinstance(task, dict) or "status" not in task:
            continue
        _TASKS[task_id] = task
        if task["status"] == "running":
            task["status"] = "interrupted"
            task["error"] = "Serveur MCP relance pendant l'execution"
            _persist_task(task_id)


def _running_count() -> int:
    return sum(1 for t in _TASKS.values() if t.get("status") == "running")


def _deep_request_body(topic: str, max_sources_per_section: int | None) -> dict[str, Any]:
    """Corps de la requete /research/deep : seuls les parametres donnes sont envoyes."""
    body: dict[str, Any] = {"topic": topic}
    if max_sources_per_section is not None:
        body["max_sources_per_section"] = max_sources_per_section
    return body


async def _run_research_deep_background(
    task_id: str, topic: str, max_sources_per_section: int | None = None
) -> None:
    task = _TASKS[task_id]
    try:
        payload = await _api_post(
            "/api/v1/research/deep",
            _deep_request_body(topic, max_sources_per_section),
            _DEEP_TIMEOUT,
        )
        outcome = deep_result(payload)
        if outcome["success"]:
            task["status"] = "completed"
            task["result"] = outcome
        else:
            task["status"] = "failed"
            task["error"] = outcome["error"]
            task["result"] = None
    except asyncio.CancelledError:
        task["status"] = "interrupted"
        task["error"] = "Tache annulee"
        _persist_task(task_id)
        raise
    except Exception as exc:  # noqa: BLE001 - garde-fou : la tache ne doit jamais rester `running`
        task["status"] = "failed"
        task["error"] = f"{type(exc).__name__}: {exc}"
    task["phase"] = "Termine"
    _persist_task(task_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Extraire une page web",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def webtools_extract(
    url: Annotated[str, Field(description="URL de la page a extraire (doit commencer par http:// ou https://)")],
    prompt: Annotated[
        str | None,
        Field(description="Consigne d'extraction personnalisee (optionnel)"),
    ] = None,
) -> dict:
    """
    Extrait le contenu propre d'une page web (texte principal, sans navigation/pub).
    Chaine d'escalade automatique cote API. Protege contre le SSRF.
    """
    payload: dict[str, Any] = {"url": url}
    if prompt:
        payload["prompt"] = prompt
    body = await _api_post("/api/v1/extract", payload, _QUICK_TIMEOUT)
    return {
        "success": bool(body.get("success", False)),
        "title": body.get("title"),
        "content": body.get("content"),
        "error": body.get("error"),
        "metadata": body.get("metadata") or {},
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Rechercher sur le web",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def webtools_search(
    query: Annotated[str, Field(max_length=500, description="Termes de recherche")],
    max_results: Annotated[int, Field(ge=1, le=50, description="Nombre maximum de resultats")] = 10,
) -> dict:
    """
    Recherche web simple (titres, URLs, extraits) via une instance SearXNG
    auto-hebergee. Rapide, sans appel LLM.
    """
    body = await _api_post(
        "/api/v1/search", {"query": query, "max_results": max_results}, _SHORT_TIMEOUT
    )
    results = body.get("results")
    results = results if isinstance(results, list) else []
    out: dict[str, Any] = {
        "query": query,
        "results": results,
        "total": body.get("total", len(results)),
    }
    if body.get("success") is False or body.get("error"):
        out["success"] = False
        out["error"] = body.get("error")
    return out


@mcp.tool(
    annotations=ToolAnnotations(
        title="Analyser une image",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def webtools_vision(
    image_url: Annotated[str, Field(description="URL de l'image a analyser")],
    prompt: Annotated[str, Field(description="Question ou instruction concernant l'image")],
) -> dict:
    """
    Analyse ou decrit une image a partir de son URL (OCR, description, lecture
    de graphique...).
    """
    body = await _api_post(
        "/api/v1/vision", {"image_url": image_url, "prompt": prompt}, _QUICK_TIMEOUT
    )
    return {
        "success": bool(body.get("success", False)),
        "analysis": body.get("analysis"),
        "error": body.get("error"),
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Recherche rapide sourcée",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
async def webtools_research_quick(
    query: Annotated[str, Field(min_length=5, max_length=500, description="Question factuelle")],
) -> dict:
    """
    Repond a une question factuelle en quelques secondes : trouve des sources
    via SearXNG, les extrait, synthetise une reponse courte avec citations
    [1][2][3] et le detail des sources tentees, extraites et ecartees.
    """
    body = await _api_post("/api/v1/research/quick", {"query": query}, _QUICK_TIMEOUT)
    sources = body.get("sources")
    return {
        "success": bool(body.get("success", False)),
        "answer": body.get("answer"),
        "confidence": body.get("confidence"),
        "sources": [
            {"title": s.get("title"), "url": s.get("url")}
            for s in (sources if isinstance(sources, list) else [])
            if isinstance(s, dict)
        ],
        "error": body.get("error"),
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Recherche approfondie avec rapport",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
async def webtools_research_deep(
    topic: Annotated[str, Field(min_length=5, max_length=500, description="Sujet de la recherche approfondie")],
    max_sources_per_section: Annotated[
        int | None,
        Field(ge=1, le=20, description="Plafond de sources par section ; absent : selon la profondeur de la section"),
    ] = None,
    ctx: Context = None,
) -> dict:
    """
    Recherche approfondie complete sur un sujet large : rapport markdown avec
    bibliographie et bloc diagnostics. Prend de 30 secondes a plusieurs
    minutes (jusqu'a ~10 minutes sous forte charge) et ATTEND la fin, ce qui
    peut declencher un timeout cote client. Pour un usage interactif,
    preferer webtools_research_deep_start + webtools_research_deep_status.
    """
    if ctx:
        await ctx.report_progress(progress=0, total=100, message=f"Demarrage de la recherche sur '{topic}'...")
    payload = await _api_post(
        "/api/v1/research/deep", _deep_request_body(topic, max_sources_per_section), _DEEP_TIMEOUT
    )
    if ctx:
        await ctx.report_progress(progress=100, total=100, message="Recherche terminee")
    return deep_result(payload)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Démarrer une recherche approfondie (arrière-plan)",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )
)
async def webtools_research_deep_start(
    topic: Annotated[str, Field(min_length=5, max_length=500, description="Sujet de la recherche approfondie")],
    max_sources_per_section: Annotated[
        int | None,
        Field(ge=1, le=20, description="Plafond de sources par section ; absent : selon la profondeur de la section"),
    ] = None,
) -> dict:
    """
    Démarre une recherche approfondie en tâche de fond et retourne
    immédiatement un identifiant de tâche. Utiliser
    webtools_research_deep_status(task_id) pour suivre l'avancement et
    récupérer le résultat. Répond `busy` si le plafond de recherches
    simultanées (MCP_MAX_CONCURRENT_DEEP) est atteint.
    """
    running = _running_count()
    limit = _max_concurrent_deep()
    if running >= limit:
        return {
            "status": "busy",
            "running": running,
            "max_concurrent": limit,
            "hint": "Attendre la fin d'une recherche en cours (webtools_research_deep_status).",
        }
    task_id = uuid.uuid4().hex[:12]
    _TASKS[task_id] = {
        "status": "running",
        "phase": "Requete en cours aupres de l'API",
        "topic": topic,
        "result": None,
        "error": None,
        "started_at": time.time(),
    }
    _persist_task(task_id)
    handle = asyncio.create_task(
        _run_research_deep_background(task_id, topic, max_sources_per_section)
    )
    _BACKGROUND.add(handle)
    handle.add_done_callback(_BACKGROUND.discard)
    return {
        "task_id": task_id,
        "status": "started",
        "hint": "Interrogez webtools_research_deep_status avec ce task_id (généralement 30s à quelques minutes).",
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Vérifier une recherche approfondie",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
async def webtools_research_deep_status(
    task_id: Annotated[str, Field(description="Identifiant de tâche retourné par webtools_research_deep_start")],
) -> dict:
    """
    Vérifie l'état d'une recherche approfondie lancée en arrière-plan.
    Statuts : "running", "completed", "failed", "interrupted" (serveur relancé
    pendant l'exécution). Le résultat (même structure que
    webtools_research_deep) est rendu une fois terminée.
    """
    now = time.time()
    for tid in [
        t for t, v in _TASKS.items()
        if v.get("status") != "running" and now - v.get("started_at", now) > _TASK_RETENTION_SECONDS
    ]:
        _forget_task(tid)

    task = _TASKS.get(task_id)
    if task is None:
        return {"status": "not_found", "error": f"Aucune tâche avec l'identifiant {task_id}"}

    out: dict[str, Any] = {
        "status": task["status"],
        "elapsed_seconds": round(now - task.get("started_at", now), 1),
    }
    if task["status"] == "running":
        out["phase_hint"] = task.get("phase")
    elif task["status"] == "completed":
        out["result"] = task.get("result")
    else:
        out["error"] = task.get("error")
    return out


load_tasks()


if __name__ == "__main__":
    import uvicorn
    # Le middleware du constructeur FastMCP() est "niveau protocole MCP"
    # (intercepte les appels d'outils/messages), pas HTTP/Starlette brut -
    # inadapte pour filtrer une route OAuth avant qu'elle ne soit traitee.
    # Le vrai point d'insertion HTTP est le parametre middleware de
    # http_app(), qui construit l'app Starlette complete nous-memes plutot
    # que de laisser mcp.run() le faire implicitement.
    app = mcp.http_app(middleware=[StarletteMiddleware(AuthorizeGateMiddleware)])
    uvicorn.run(app, host="0.0.0.0", port=8090)
