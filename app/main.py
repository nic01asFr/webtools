"""
Application principale FastAPI pour Webtools Service.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.api.v1.endpoints import extract, vision, search_site, research_deep, search, research_quick
from app.api.v1.endpoints import research as research_old

from app.api.models import HealthResponse
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

    yield

    # Shutdown
    logger.info("Arrêt de Webtools Service...")


# Créer l'application FastAPI
app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description="""
# WebTools API

5 endpoints pour extraction et recherche web.

## Ce Que Vous Obtenez

### `/api/v1/extract`
**Input** : URL
**Output** : Contenu nettoyé (texte, liens, images, métadonnées)

Options :
- `clean_html: true` → Article sans navigation/pub (~87% réduction)
- `extract_images: true` → Liste URLs images
- `extract_links: true` → Liens du contenu

Exemple :
```json
{
  "url": "https://example.com/article",
  "title": "Titre de l'article",
  "content": "Texte principal...",
  "images": ["https://example.com/img1.jpg"],
  "word_count": 1500
}
```

---

### `/api/v1/vision`
**Input** : URL image + question
**Output** : Analyse textuelle selon la question

Selon votre question :
- "extraire le texte" → OCR complet
- "décrire l'image" → Description objets/couleurs/contexte
- "analyser le graphique" → Données extraites
- "identifier les logos" → Liste marques détectées

Exemple :
```json
{
  "image_url": "https://example.com/chart.png",
  "analysis": "Graphique en barres montrant...",
  "confidence": 0.95
}
```

---

### `/api/v1/search`
**Input** : Requête de recherche
**Output** : Liste résultats (titre, URL, snippet)

Options :
- `max_results: 20` → Jusqu'à 20 résultats (défaut: 10)
- `language: "fr"` → Résultats français uniquement
- `time_range: "month"` → Résultats du dernier mois
- `target_url` + `scope: "site"` → Rechercher dans un site spécifique

Exemple :
```json
{
  "query": "Claude AI",
  "results": [
    {
      "title": "What is Claude?",
      "url": "https://anthropic.com/claude",
      "snippet": "Claude is an AI assistant..."
    }
  ],
  "total": 10
}
```

---

### `/api/v1/research/quick`
**Input** : Question factuelle
**Output** : Réponse synthétique + sources citées

Selon la complexité :
- Question simple → Réponse directe + 3-5 sources
- Question multi-aspects → Réponse structurée + 5-10 sources
- `max_sources: 15` → Plus de sources

Options de contrainte :
- `sources.strategy: "priority"` → Essayer ces URLs d'abord
- `sources.strategy: "exclusive"` → UNIQUEMENT ces URLs
- `sources.strategy: "complement"` → Ajouter aux résultats web

Exemple :
```json
{
  "query": "Capitale du Japon?",
  "answer": "Tokyo est la capitale depuis 1868. [1][2]",
  "sources": [
    {"title": "Tokyo - Wikipedia", "url": "https://...", "relevance": 0.95}
  ],
  "confidence": "high"
}
```

---

### `/api/v1/research/deep`
**Input** : Sujet de recherche
**Output** : Rapport structuré en sections + bibliographie

Selon le mode :
- `stream: false` → JSON complet à la fin (30-90s)
- `stream: true` → Progression temps réel (SSE)

Selon la profondeur :
- Sujet simple → 3-5 sections, 10-15 sources
- Sujet complexe → 7-12 sections, 20-30 sources
- `max_sources: 50` → Recherche exhaustive

Options de contrainte :
- `sources.required` → URLs OBLIGATOIRES
- `sources.suggested` → URLs prioritaires
- `domains_whitelist` → Limiter à ces domaines
- `exclusions` → Exclure ces domaines

Exemple :
```json
{
  "query": "Impact IA sur emploi",
  "report": {
    "title": "Analyse de l'impact de l'IA...",
    "sections": [
      {
        "title": "1. État actuel",
        "content": "L'IA transforme... [1][3]",
        "subsections": [...]
      }
    ]
  },
  "bibliography": [
    {"id": 1, "title": "AI Study 2024", "url": "https://..."}
  ],
  "metadata": {
    "sources_analyzed": 25,
    "research_time": "45s"
  }
}
```

## 🎯 URLs Optionnelles

Tous les endpoints de **recherche** acceptent des URLs pour guider/contraindre:

**`/search`**: `target_url` + `scope` (site/domain/page)
- Rechercher dans un site spécifique
- Recherche interactive sur page

**`/research/quick`**: `sources.urls` + `strategy`
- `priority`: Essayer d'abord ces URLs
- `exclusive`: UNIQUEMENT ces URLs
- `complement`: Ajouter aux résultats web

**`/research/deep`**: Contraintes avancées
- `required`: URLs OBLIGATOIRES
- `suggested`: URLs prioritaires
- `domains_whitelist`: Limiter à domaines
- `exclusions`: Exclure domaines

## 📌 Endpoints

| Endpoint | Usage | Temps | Autonomie |
|----------|-------|-------|-----------|
| `/extract` | 1 page web | 2-30s | ⭐ |
| `/vision` | 1 image | 1-10s | ⭐ |
| `/search` | Découverte sources | 1-5s | ⭐⭐ |
| `/research/quick` | Question précise | 15-60s | ⭐⭐⭐ |
| `/research/deep` | Rapport complet | 60-300s | ⭐⭐⭐⭐⭐ |

## 🚀 Quick Start

```bash
# Recherche simple avec enrichissement IA
curl -X POST "http://localhost:8000/api/v1/search" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "FastAPI tutorial", "llm_enrichment": true, "dorking": true}'

# Question avec source prioritaire (API)
curl -X POST "http://localhost:8000/api/v1/research/quick" \\
  -H "Content-Type: application/json" \\
  -d '{
    "query": "3 plus grandes villes de France",
    "sources": {
      "urls": ["https://geo.api.gouv.fr"],
      "strategy": "priority"
    }
  }'

# Rapport approfondi avec contraintes
curl -X POST "http://localhost:8000/api/v1/research/deep" \\
  -H "Content-Type: application/json" \\
  -d '{
    "topic": "Démographie Île-de-France",
    "sources": {
      "required": ["https://api.insee.fr", "https://geo.api.gouv.fr"]
    },
    "output_format": {"structure": "data_analysis", "include_charts": true}
  }'
```

## 📚 Documentation Complète

- **Interactive Swagger**: `/docs`
- **ReDoc**: `/redoc`
- **Guide complet**: `API_DOCUMENTATION.md`

---

**V2.0.0 | Propulsé par Albert API 🇫🇷**
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
    tags=["basic"]
)

app.include_router(
    vision.router,
    prefix="/api/v1",
    tags=["basic"]
)


# RECHERCHE
app.include_router(
    search.router,
    prefix="/api/v1",
    tags=["basic"]
)

app.include_router(
    research_quick.router,
    prefix="/api/v1",
    tags=["research"]
)

app.include_router(
    research_deep.router,
    prefix="/api/v1",
    tags=["research"]
)


# ====================================================================
# ANCIENS ENDPOINTS - DEPRECATED (retirés de l'OpenAPI, code conservé)
# ====================================================================

# Les anciens routers sont conservés dans le code mais exclus de la documentation
# pour simplifier l'API publique. Ils restent fonctionnels en interne si besoin.

app.include_router(
    research_old.router,
    prefix="/api/v1",
    tags=["deprecated"],
    include_in_schema=False  # Masquer de l'OpenAPI
)

app.include_router(
    search_site.router,
    prefix="/api/v1",
    tags=["deprecated"],
    include_in_schema=False
)







@app.get("/", response_class=JSONResponse)
async def root():
    """Endpoint racine."""
    return {
        "service": "Webtools Service",
        "version": settings.api_version,
        "docs": "/docs",
        "health": "/health"
    }


@app.get("/health", response_model=HealthResponse, tags=["monitoring"])
async def health_check():
    """
    Endpoint de sante du service. Verifie aussi les dependances externes
    (SearXNG, config LLM) - un /health qui ne teste que le process FastAPI
    lui-meme donne une fausse impression de sante si une dependance est
    indisponible.
    """
    import os
    from app.services.search_service import searxng_client

    playwright_ok = is_playwright_available()

    try:
        searxng_ok = await searxng_client.health_check()
    except Exception:
        searxng_ok = False

    llm_configured = bool(os.getenv("DEFAULT_LLM_API_KEY", ""))

    overall_status = "healthy" if (playwright_ok and llm_configured) else "degraded"

    return HealthResponse(
        status=overall_status,
        version=settings.api_version,
        playwright_available=playwright_ok,
        searxng_available=searxng_ok,
        llm_configured=llm_configured
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
