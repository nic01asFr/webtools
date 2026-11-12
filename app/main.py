"""
Application principale FastAPI pour Webtools Service.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.api.v1.endpoints import extract, vision, research_deep, search, research_quick

from app.api.models import HealthResponse
from app.api.auth import require_api_key, verify_auth_configuration
from app.core import build_info
from app.core.browser.playwright_manager import ensure_playwright_installed, is_playwright_available

# Configuration du logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestion du cycle de vie de l'application."""
    # Startup
    logger.info("Démarrage de Webtools Service...")

    # Initialiser Playwright
    playwright_ok = await ensure_playwright_installed()
    if playwright_ok:
        logger.info("Playwright initialisé avec succès")
    else:
        logger.warning("Playwright non disponible - extraction limitée")

    # Catalogue de moteurs : decouvert depuis /config de SearXNG plutot que
    # code en dur, pour rester juste si la configuration change. Best-effort -
    # un echec laisse le catalogue vide et les recherches se font sans
    # selection, comme avant.
    import os
    from app.core.config import get_searxng_base_url
    from app.services.engine_catalog import engine_catalog
    await engine_catalog.load(get_searxng_base_url())

    # browser-use 0.1.41 verifie OPENAI_API_KEY dans l'environnement avant de
    # lancer un agent ; le client LLM ne l'y ecrit plus (C54). On la derive
    # une fois ici de la cle du service, sans ecraser une valeur explicite.
    if os.environ.get("DEFAULT_LLM_API_KEY"):
        os.environ.setdefault("OPENAI_API_KEY", os.environ["DEFAULT_LLM_API_KEY"])

    yield

    # Shutdown : l'instance partagee du client LLM (C50) ferme son pool HTTP.
    from app.core.llm import close_llm_client
    await close_llm_client()
    logger.info("Arrêt de Webtools Service...")


# Créer l'application FastAPI
app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description="""
# WebTools API

Extraction de contenu web et recherche sourcée : 5 endpoints sous `/api/v1`.
Tous exigent l'en-tête `X-API-Key`. Le LLM par défaut est un modèle compatible
OpenAI hébergé sur SSPCloud (`qwen3-6-35b-moe`) ; la recherche passe par SearXNG.

### `/api/v1/extract`
**Entrée** : `url`, et en option `prompt`, `extraction_type`
(`general`, `article`, `product`, `repository`, `documentation`), `llm_config`
(LLM propre à la requête) et `options` (`use_agent`, `timeout` de 5 à 300 s,
`try_rss_first`, `rss_tier_root_only`, `single_llm_pass_enabled`, `headless`,
`enable_ocr`).
**Sortie** : `success`, `url`, `content_type`, `title`, `content`, `metadata`,
`error`. Les adresses non publiques sont refusées.

---

### `/api/v1/vision`
**Entrée** : `image_url`, `prompt` (5 à 1000 caractères) et en option
`system_prompt`, `temperature` (0 à 2, défaut 0,15), `max_tokens` (50 à 4096,
défaut 500).
**Sortie** : `analysis`, `model_used`, `processing_time_seconds`. Erreurs HTTP :
400 (URL non publique), 501 (modèle sans vision), 502 ou 504 (échec ou délai du
modèle), 503 (aucune clé LLM).

---

### `/api/v1/search`
**Entrée** : `query` et en option `max_results` (1 à 50, défaut 10), `language`
(défaut `fr`), `categories` et `engines` (noms SearXNG).
**Sortie** : `results` (`title`, `url`, `snippet`), `total`. Aucun appel LLM.

---

### `/api/v1/research/quick`
**Entrée** : `query` (5 à 2000 caractères) et en option `max_sources` (1 à 15,
défaut 5).
**Sortie** : `answer` avec citations `[1]`, `[2]`, `sources` (`title`, `url`,
`relevance` si le moteur fournit un score), `sources_attempted`,
`sources_extracted`, `sources_discarded`, `discarded`. `confidence` n'est pas
calculée (toujours `null`). Un moteur ou un LLM en échec donne une erreur HTTP
(502, 504).

---

### `/api/v1/research/deep`
**Entrée** : `topic`, avec les paramètres ci-dessous
**Sortie** : rapport structuré en sections avec bibliographie

Réponse JSON unique (pas de flux), de même forme en succès, en rapport dégradé
et en échec : `success`, `degraded`, `timed_out`, `error`, `result`
(`title`, `summary`, `sections`, `bibliography`, `metadata`), `execution`,
`sources` et `diagnostics` (`config`, `events`, `invariants`). La trace et les
sources sont conservées même en échec.

Paramètres :
- `timeout` (60-1200 s, défaut 600) → délai global ; passé ce délai, la réponse
  porte les sections déjà rédigées avec `degraded: true`
- `max_sources_per_section` (1-20) → plafond de sources par section ; absent :
  selon la profondeur de la section
- `output_format.include_section_data` → joint à chaque section les passages
  extraits (`data`, texte intégral) ; absent de la réponse sinon
- `objectives`, `context.language` → transmis au modèle qui planifie le rapport
- `output_format.sections` (ou `output_sections`) → sections IMPOSÉES : le rapport
  contient exactement ces sections, avec ces titres et dans cet ordre (titres
  comparés sans tenir compte de la casse ni des espaces) ; les sections proposées
  en plus par le modèle sont retirées, les manquantes ajoutées. Une section sans
  matière exploitable est retirée et signalée dans `unsourced_sections`. Les écarts
  du plan sont dans `diagnostics.events` (`plan.sections_imposees`)

Contraintes de sources :
- `sources.required` → URLs OBLIGATOIRES : chacune est extraite avant la rédaction
  (même hors de `domains_whitelist` ou dans `exclusions`), découpée en passages et
  proposée à toutes les sections ; la pertinence décide où elle sert. Une URL non
  extraite rend le rapport `degraded`, avec `required_sources_failed` (url, reason)
  et un message d'erreur ; une URL extraite mais non citée figure dans
  `required_sources_uncited` sans dégrader le rapport. Ces deux listes sont
  absentes quand elles sont vides. `execution.summary.required_sources_status`
  vaut `none_required`, `all_success` ou `partial_failure`
- `sources.domains_whitelist` → Limiter à ces domaines
- `sources.exclusions` → Exclure ces domaines

Les champs inconnus sont refusés (422). Ont été retirés car sans effet :
`max_steps`, `max_results`, `context.min_sources`, `context.date_range`,
`context.include_data`, `context.include_reports`, `context.include_academic`,
`sources.suggested`, `output_format.structure`, `output_format.include_charts`,
`output_format.include_timeline`.

Exemple (réponse abrégée) :
```json
{
  "success": true,
  "degraded": false,
  "topic": "Impact IA sur emploi",
  "result": {
    "title": "Analyse: Impact Ia Sur Emploi",
    "summary": "...",
    "sections": [
      {"title": "1. État actuel", "content": "L'IA transforme... [1][3]"}
    ],
    "bibliography": [
      {"id": 1, "title": "AI Study 2024", "url": "https://..."}
    ],
    "metadata": {"sections_count": 5}
  },
  "diagnostics": {"config": {}, "events": {"counts": {}, "events": []}, "invariants": {"ok": true, "violations": []}}
}
```


## Endpoints de service

- `/health` : état réel (Playwright, SearXNG, LLM, catalogue de moteurs), commit
  et heure de démarrage du code chargé.
- `/ready` : sonde de disponibilité.
- `/docs`, `/redoc`, `/openapi.json` : documentation générée.

## Exemples

```bash
curl -X POST "http://localhost:8000/api/v1/search" -H "X-API-Key: $WEBTOOLS_API_KEY" -H "Content-Type: application/json" -d '{"query": "FastAPI tutorial", "max_results": 5}'

curl -X POST "http://localhost:8000/api/v1/research/deep" -H "X-API-Key: $WEBTOOLS_API_KEY" -H "Content-Type: application/json" -d '{"topic": "Démographie Île-de-France", "sources": {"required": ["https://www.insee.fr/fr/accueil"]}, "max_sources_per_section": 5}'
```

Guides : `README.md`, `DEEP_RESEARCH_ARCHITECTURE.md`, `docs/CONFIGURATION.md`.
    """,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# Limite de taille de requete : sans ca, FastAPI/Starlette accepte des
# payloads de n'importe quelle taille avant meme la validation Pydantic -
# risque de deni de service par payload geant (verifie : 20 Mo acceptes
# sans broncher avant ce correctif). 5 Mo est largement suffisant pour
# tout usage legitime de cette API (extraction, recherche, vision).
MAX_REQUEST_SIZE = 5 * 1024 * 1024  # 5 Mo

@app.middleware("http")
async def limit_request_size(request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_REQUEST_SIZE:
        return JSONResponse(
            status_code=413,
            content={"detail": f"Requete trop volumineuse (max {MAX_REQUEST_SIZE // (1024*1024)} Mo)"}
        )
    return await call_next(request)

# Configuration CORS. allow_credentials=False car ce service n'utilise
# aucun cookie/session - allow_origins=["*"] avec credentials=True n'a pas
# de sens (invalide selon la spec CORS, la plupart des navigateurs le
# bloquent de toute facon).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ====================================================================
# NOUVEAUX ENDPOINTS V2 - Architecture rationalisée
# ====================================================================

# BASIQUES (3)
app.include_router(
    extract.router,
    prefix="/api/v1",
    tags=["basic"],
    dependencies=[Depends(require_api_key)]
)

app.include_router(
    vision.router,
    prefix="/api/v1",
    tags=["basic"],
    dependencies=[Depends(require_api_key)]
)


# RECHERCHE
app.include_router(
    search.router,
    prefix="/api/v1",
    tags=["basic"],
    dependencies=[Depends(require_api_key)]
)

app.include_router(
    research_quick.router,
    prefix="/api/v1",
    tags=["research"],
    dependencies=[Depends(require_api_key)]
)

app.include_router(
    research_deep.router,
    prefix="/api/v1",
    tags=["research"],
    dependencies=[Depends(require_api_key)]
)


# Refus de demarrer sans cle d'API (decision D6) : controle au demarrage du
# service et non a l'import, pour que les tests puissent importer l'application.
_lifespan_applicatif = app.router.lifespan_context


def load_environment() -> bool:
    """
    Charge le .env du repertoire courant (ou d'un parent) dans os.environ,
    sans ecraser une variable deja definie. pydantic-settings lit .env pour
    `settings` seulement : les lecteurs directs de l'environnement (cle de
    l'API REST, WEBTOOLS_COMMIT...) ne le voient pas sans ceci.

    Returns:
        True si un fichier .env a ete trouve
    """
    chemin = find_dotenv(usecwd=True)
    if not chemin:
        return False
    return load_dotenv(chemin, override=False)


@asynccontextmanager
async def _lifespan_avec_controle_auth(application: FastAPI):
    load_environment()
    verify_auth_configuration()
    async with _lifespan_applicatif(application) as etat:
        yield etat


app.router.lifespan_context = _lifespan_avec_controle_auth


@app.get("/", response_class=JSONResponse)
async def root():
    """Endpoint racine."""
    return {
        "service": "Webtools Service",
        "version": settings.api_version,
        "docs": "/docs",
        "health": "/health"
    }


# --- Sante -----------------------------------------------------------------

LLM_PROBE_TTL_SECONDS = 60.0
LLM_PROBE_TIMEOUT_SECONDS = 10.0

# (horodatage monotone, resultat) du dernier appel reel au LLM.
_llm_probe_cache: tuple[float, dict] | None = None

# Une seule sonde a la fois : a l'expiration du cache, des /health simultanes
# attendent la sonde en cours au lieu de partir tous vers le LLM.
_llm_probe_lock = asyncio.Lock()


async def _probe_llm() -> dict:
    """Appel minimal (1 token) au LLM ; ne leve jamais."""
    from app.core.llm import LLMEmptyContentError, get_llm_client, resolve_llm_config

    if not resolve_llm_config()[2]:
        return {"configured": False, "ok": False, "detail": "cle LLM absente"}
    try:
        client = await get_llm_client()
        await asyncio.wait_for(
            client.generate(
                [{"role": "user", "content": "ping"}], max_tokens=1
            ),
            timeout=LLM_PROBE_TIMEOUT_SECONDS,
        )
        return {"configured": True, "ok": True, "detail": "ok"}
    except LLMEmptyContentError:
        # max_tokens=1 peut ne rendre aucun contenu : le LLM a repondu, ce
        # que la sonde cherche a savoir.
        return {"configured": True, "ok": True, "detail": "reponse vide (le LLM repond)"}
    except Exception as exc:  # noqa: BLE001 - la sonde ne doit jamais lever
        return {"configured": True, "ok": False, "detail": f"{type(exc).__name__}: {exc}"[:200]}


async def _llm_status() -> dict:
    """Etat du LLM, mis en cache 60 s pour ne pas consommer de quota."""
    global _llm_probe_cache
    cache = _llm_probe_cache
    if cache and time.monotonic() - cache[0] < LLM_PROBE_TTL_SECONDS:
        return cache[1]
    async with _llm_probe_lock:
        # Une sonde terminee pendant l'attente du verrou vaut la notre.
        cache = _llm_probe_cache
        if cache and time.monotonic() - cache[0] < LLM_PROBE_TTL_SECONDS:
            return cache[1]
        result = await _probe_llm()
        _llm_probe_cache = (time.monotonic(), result)
        return result


def _engine_catalog_status() -> dict:
    from app.services.engine_catalog import engine_catalog

    par_axe: dict[str, int] = {}
    # Lecture seule du contenu du catalogue (aucun accesseur public).
    for name in engine_catalog._engines:
        axe = engine_catalog.axis_of(name)
        par_axe[axe] = par_axe.get(axe, 0) + 1
    return {
        "ok": engine_catalog.available,
        "engines_total": sum(par_axe.values()),
        "engines_per_axis": par_axe,
    }


@app.get("/health", response_model=HealthResponse, tags=["monitoring"])
async def health_check():
    """
    Endpoint de sante du service. `healthy` exige Playwright, SearXNG et un
    LLM qui repond (sonde minimale, mise en cache 60 s). Le commit charge et
    l'heure de demarrage permettent de savoir quel code tourne.
    """
    from app.services.search_service import searxng_client

    playwright_ok = is_playwright_available()

    try:
        searxng_ok = await searxng_client.health_check()
    except Exception:
        searxng_ok = False

    llm = await _llm_status()
    catalog = _engine_catalog_status()

    # Un catalogue vide : les recherches se font sans selection de moteurs.
    overall_status = (
        "healthy"
        if (playwright_ok and searxng_ok and llm["ok"] and catalog["ok"])
        else "degraded"
    )

    return HealthResponse(
        status=overall_status,
        version=settings.api_version,
        playwright_available=playwright_ok,
        searxng_available=searxng_ok,
        llm_configured=llm["configured"],
        llm_model=settings.default_llm_model,
        commit=build_info.COMMIT,
        commit_dirty=build_info.COMMIT_DIRTY,
        started_at=build_info.STARTED_AT,
        dependencies={
            "playwright": {"ok": playwright_ok},
            "searxng": {"ok": searxng_ok},
            "llm": llm,
            "engine_catalog": catalog,
        },
    )


@app.get("/ready", response_class=JSONResponse, tags=["monitoring"])
async def readiness_check():
    """Endpoint de readiness pour Kubernetes."""
    playwright_ready = is_playwright_available()

    if playwright_ready:
        return {"status": "ready", "playwright": "available"}
    else:
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "playwright": "unavailable"}
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
        log_level=settings.log_level.lower(),
        timeout_keep_alive=1200,  # 20 minutes pour recherches approfondies
        timeout_notify=1200
    )
