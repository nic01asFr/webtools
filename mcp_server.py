"""
Serveur MCP pour webtools - expose les capacites d'extraction/recherche web
directement (sans repasser par HTTP, meme process) pour des clients type
Claude Code et autres agents compatibles MCP.
"""

import os
import sys
import logging

# Chemin dynamique (relatif a ce fichier) plutot que code en dur - ce
# fichier a deja migre d'un pod de dev (webtools-test3) vers un pod de
# service dedie, un chemin absolu casserait a chaque nouvelle migration.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configuration non secrete : valeurs par defaut acceptables en clair.
os.environ.setdefault("OPENAI_BASE_URL", "https://llm.lab.sspcloud.fr/api")
os.environ.setdefault("DEFAULT_LLM_PROVIDER", "openai")
os.environ.setdefault("DEFAULT_LLM_MODEL", "qwen3-6-35b-moe")
os.environ.setdefault("DEFAULT_LLM_BASE_URL", "https://llm.lab.sspcloud.fr/api")
os.environ.setdefault("SEARXNG_BASE_URL", "http://localhost:8081")

# Secrets : JAMAIS de valeur par defaut en dur. Le pattern
# os.environ.setdefault("...", "<vraie_cle>") est traitre - il fonctionne
# silencieusement, donc rien ne signale que la cle est committee en clair
# (c'est exactement ce qui s'est produit ici avant ce correctif). Lecture
# stricte avec echec explicite au demarrage, comme pour MCP_ACCESS_TOKEN.
_llm_key = os.environ.get("DEFAULT_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
if not _llm_key:
    raise RuntimeError(
        "DEFAULT_LLM_API_KEY (ou OPENAI_API_KEY) doit etre defini dans "
        "l'environnement avant de lancer le serveur MCP."
    )
os.environ.setdefault("OPENAI_API_KEY", _llm_key)
os.environ.setdefault("DEFAULT_LLM_API_KEY", _llm_key)

from fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations
from typing import Annotated, Literal
from pydantic import Field
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.requests import Request
import hmac
import time

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
    return (time.time() - int(ts)) < SESSION_TTL_SECONDS


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
        logger.info(f"[GATE] /authorize intercepte")

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
                resp.set_cookie(SESSION_COOKIE, _make_session_value(), max_age=SESSION_TTL_SECONDS, httponly=True)
                return resp
            return HTMLResponse(LOGIN_FORM.format(error="<p style='color:red'>Cle incorrecte</p>"), status_code=401)

        return HTMLResponse(LOGIN_FORM.format(error=""))


from fastmcp.server.auth.auth import ClientRegistrationOptions
from starlette.middleware import Middleware as StarletteMiddleware

mcp = FastMCP(
    "webtools",
    auth=InMemoryOAuthProvider(
        base_url=os.environ.get("MCP_PUBLIC_URL", "http://localhost:8090"),
        client_registration_options=ClientRegistrationOptions(enabled=True),
    ),
)

# --- Recherche approfondie en tache de fond -------------------------------
# research_deep peut prendre 30s a plusieurs minutes (jusqu'a 578s observe
# sous degradation reseau). Un appel bloquant unique fait timeout cote
# client MCP (Claude Desktop) bien avant la fin. Solution : lancer en
# arriere-plan (asyncio.create_task, pas de nouvelle dependance infra type
# Redis/pydocket - disproportionne pour un usage personnel), avec un outil
# compagnon pour interroger l'etat a la demande. Le "hint" de progression
# est derive des logs deja emis par l'orchestrateur (aucune modification
# de sa logique metier deja testee), via un handler de logging scope a
# chaque tache.
import asyncio
import logging as _logging
import time as _time
import uuid as _uuid

_TASKS: dict = {}  # task_id -> {status, phase, result, error, started_at}


class _TaskPhaseCapture(_logging.Handler):
    """Capture la derniere ligne de log de l'orchestrateur comme indice de phase en cours."""

    def __init__(self, task_id: str):
        super().__init__()
        self.task_id = task_id

    def emit(self, record):
        try:
            msg = record.getMessage().strip()
            if msg and self.task_id in _TASKS:
                _TASKS[self.task_id]["phase"] = msg
        except Exception:
            pass


async def _run_research_deep_background(task_id: str, topic: str, max_sources: int):
    handler = _TaskPhaseCapture(task_id)
    orchestrator_logger = _logging.getLogger("app.agents.intelligent_orchestrator")
    orchestrator_logger.addHandler(handler)
    try:
        from app.api.v1.endpoints.research_deep import research_deep as _research_deep, DeepResearchRequest
        import json as jsonlib

        req = DeepResearchRequest(topic=topic, max_results=max_sources)
        response = await _research_deep(req)
        body = b""
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
        _TASKS[task_id]["status"] = "completed"
        _TASKS[task_id]["result"] = jsonlib.loads(body)
    except Exception as e:
        _TASKS[task_id]["status"] = "failed"
        _TASKS[task_id]["error"] = str(e)
    finally:
        orchestrator_logger.removeHandler(handler)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Extraire une page web",
        readOnlyHint=True,      # ne modifie aucun etat, lecture seule
        destructiveHint=False,
        idempotentHint=True,    # meme URL -> meme contenu (cache TTL 5 min)
        openWorldHint=True,     # accede au web ouvert, pas un systeme ferme
    )
)
async def webtools_extract(
    url: Annotated[str, Field(description="URL de la page a extraire (doit commencer par http:// ou https://)")],
    extraction_type: Annotated[
        Literal["general", "article", "product", "repository", "documentation"],
        Field(description="Type de contenu attendu, oriente le comportement d'extraction")
    ] = "general",
) -> dict:
    """
    Extrait le contenu propre d'une page web (texte principal, sans navigation/pub).
    Chaine d'escalade automatique : flux RSS -> extraction directe -> LLM leger ->
    agent IA en dernier recours. Protege contre le SSRF (rejette les URLs internes).
    """
    from app.manager import ExtractorManager
    from app.api.models import ExtractionOptions
    from app.core.llm import get_llm_client

    manager = ExtractorManager()
    llm_client = await get_llm_client()
    result = await manager.extract(
        url=url, extraction_type=extraction_type,
        llm_client=llm_client, options=ExtractionOptions()
    )
    return {
        "success": result.success,
        "title": result.title,
        "content": result.content,
        "error": result.error,
        "metadata": result.metadata or {}
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
    query: Annotated[str, Field(description="Termes de recherche")],
    max_results: Annotated[int, Field(ge=1, le=50, description="Nombre maximum de resultats")] = 10,
) -> dict:
    """
    Recherche web simple (titres, URLs, extraits) via une instance SearXNG
    auto-hebergee. Rapide, sans appel LLM.
    """
    from app.services.search_service import searxng_client

    results = await searxng_client.search(query=query, max_results=max_results)
    return {
        "query": query,
        "results": [
            {"title": r.title, "url": r.url, "snippet": r.content[:300]}
            for r in results
        ],
        "total": len(results)
    }


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
    from app.core.llm import get_llm_client

    llm_client = await get_llm_client()
    try:
        analysis = await llm_client.generate_with_vision(
            text=prompt, image_url=image_url, timeout=25.0, max_retries=0
        )
        return {"success": True, "analysis": analysis}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(
    annotations=ToolAnnotations(
        title="Recherche rapide sourcée",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,  # generation LLM + resultats de recherche varient dans le temps
        openWorldHint=True,
    )
)
async def webtools_research_quick(
    query: Annotated[str, Field(min_length=5, max_length=500, description="Question factuelle")],
    max_sources: Annotated[int, Field(ge=1, le=15, description="Nombre de sources a consulter")] = 5,
) -> dict:
    """
    Repond a une question factuelle en quelques secondes : trouve des sources
    via SearXNG, les extrait, synthetise une reponse courte avec citations
    [1][2][3] et un niveau de confiance (high/medium/low).
    """
    from app.api.models import QuickResearchRequest
    from app.api.v1.endpoints.research_quick import research_quick as _research_quick

    req = QuickResearchRequest(query=query, max_sources=max_sources)
    resp = await _research_quick(req)
    return {
        "success": resp.success,
        "answer": resp.answer,
        "confidence": resp.confidence,
        "sources": [{"title": s.title, "url": s.url} for s in resp.sources],
        "error": resp.error
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Recherche approfondie avec rapport",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,  # plan + synthese LLM varient d'un appel a l'autre
        openWorldHint=True,
    )
)
async def webtools_research_deep(
    topic: Annotated[str, Field(min_length=5, max_length=500, description="Sujet de la recherche approfondie")],
    max_sources: Annotated[int, Field(ge=1, le=15, description="Sources par section (plus = plus long, quelques minutes possibles)")] = 2,
    ctx: Context = None,
) -> dict:
    """
    Recherche approfondie complete sur un sujet large : plan structure genere
    dynamiquement, recherche+extraction paralellisees section par section,
    corroboration entre sources independantes, cohesion narrative, rapport
    final avec bibliographie. Prend de 30 secondes a plusieurs minutes selon
    l'ampleur du sujet (jusqu'a ~10 minutes observees sous forte charge
    reseau) - ATTEND la fin complete avant de repondre, ce qui peut declencher
    un timeout cote client sur les sujets longs. Pour un usage interactif,
    preferer webtools_research_deep_start + webtools_research_deep_status
    (lancement en tache de fond, sans blocage). Cet outil bloquant reste
    adapte a un usage script/batch qui peut attendre patiemment.
    """
    from app.api.v1.endpoints.research_deep import research_deep as _research_deep, DeepResearchRequest
    import json as jsonlib

    if ctx:
        await ctx.report_progress(progress=0, total=100, message=f"Démarrage de la recherche sur '{topic}'...")

    req = DeepResearchRequest(topic=topic, max_results=max_sources)
    response = await _research_deep(req)

    # research_deep retourne un StreamingResponse (chunking HTTP) - on
    # reassemble le JSON complet ici puisqu'on appelle la fonction en
    # direct, hors contexte HTTP.
    body = b""
    async for chunk in response.body_iterator:
        body += chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")

    if ctx:
        await ctx.report_progress(progress=100, total=100, message="Recherche terminée")

    return jsonlib.loads(body)


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
    max_sources: Annotated[int, Field(ge=1, le=15, description="Sources par section (plus = plus long)")] = 2,
) -> dict:
    """
    Démarre une recherche approfondie en tâche de fond et retourne
    immédiatement un identifiant de tâche, sans attendre la fin (qui peut
    prendre de 30 secondes à plusieurs minutes). Utiliser
    webtools_research_deep_status(task_id) pour suivre l'avancement et
    récupérer le résultat une fois prêt - c'est l'approche recommandée
    pour un usage interactif, contrairement à webtools_research_deep qui
    bloque jusqu'à la fin complète et peut déclencher un timeout côté client.
    """
    task_id = _uuid.uuid4().hex[:12]
    _TASKS[task_id] = {
        "status": "running",
        "phase": "Démarrage...",
        "result": None,
        "error": None,
        "started_at": _time.time(),
    }
    asyncio.create_task(_run_research_deep_background(task_id, topic, max_sources))
    return {
        "task_id": task_id,
        "status": "started",
        "hint": "Interrogez webtools_research_deep_status avec ce task_id pour suivre l'avancement (généralement 30s à quelques minutes)."
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Vérifier une recherche approfondie",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,  # interroger le statut plusieurs fois est sans effet de bord
        openWorldHint=False,  # interroge un etat interne du serveur, pas le web ouvert
    )
)
async def webtools_research_deep_status(
    task_id: Annotated[str, Field(description="Identifiant de tâche retourné par webtools_research_deep_start")],
) -> dict:
    """
    Vérifie l'état d'une recherche approfondie lancée en arrière-plan.
    Retourne le statut ("running", "completed", "failed"), un indice de
    phase en cours si toujours en exécution, et le résultat complet une
    fois terminée (même structure que webtools_research_deep).
    """
    # Purge opportuniste des taches terminees depuis plus d'1h - evite une
    # fuite memoire lente sur ce dict jamais autrement nettoye, sans besoin
    # d'un scheduler separe pour un usage a ce volume.
    now = _time.time()
    for tid in [t for t, v in _TASKS.items() if v["status"] != "running" and now - v["started_at"] > 3600]:
        del _TASKS[tid]

    task = _TASKS.get(task_id)
    if task is None:
        return {"status": "not_found", "error": f"Aucune tâche avec l'identifiant {task_id}"}

    elapsed = round(_time.time() - task["started_at"], 1)
    out = {"status": task["status"], "elapsed_seconds": elapsed}
    if task["status"] == "running":
        out["phase_hint"] = task["phase"]
    elif task["status"] == "completed":
        out["result"] = task["result"]
    elif task["status"] == "failed":
        out["error"] = task["error"]
    return out


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
