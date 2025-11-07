"""
Application principale FastAPI pour WebExtract Service.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.api.v1.endpoints import extract, research, vision
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
    logger.info("Démarrage de WebExtract Service...")

    # Initialiser Playwright
    playwright_ok = await ensure_playwright_installed()
    if playwright_ok:
        logger.info("Playwright initialisé avec succès")
    else:
        logger.warning("Playwright non disponible - extraction limitée")

    yield

    # Shutdown
    logger.info("Arrêt de WebExtract Service...")


# Créer l'application FastAPI
app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description="""
# 🚀 WebTools - API d'Extraction et d'Analyse Web avec IA

Service autonome pour l'extraction de contenu web, la recherche intelligente multi-pages et l'analyse d'images, propulsé par **Albert API** (LLM gouvernemental français).

## ✨ Fonctionnalités Principales

### 🌐 Extraction Web Multi-Stratégies
- **DirectExtractor**: Playwright pour sites statiques/SPA (2-5s)
- **AgentExtractor**: Navigation intelligente avec LLM (10-30s)
- **HTTP Fallback**: Extraction basique de secours
- Support sites avec JavaScript lourd (GitHub, YouTube, React, Vue)

### 🔍 Deep Research (Recherche Profonde)
- Recherche multi-pages avec navigation intelligente guidée par LLM
- Intégration **SearXNG** pour découverte automatique de sources
- Scoring de pertinence et analyse de chaque page
- **Synthèse avec citations vérifiables** (0% hallucination)
- Configuration profondeur (1-3) et nombre de sources (1-20)

### 🎨 Vision AI (Analyse d'Images)
- **OCR**: Extraction de texte depuis images (1-2s)
- **Analyse**: Graphiques, cartes, diagrammes (4-10s)
- **Description**: Logos, UI, photos détaillées
- Support: PNG, JPG, WebP, GIF
- Modèle: **albert-large** (128K contexte)

### 🤖 Support Multi-LLM
- **Albert API** (albert-code, albert-large) - Par défaut
- **OpenAI** (GPT-4, GPT-3.5)
- **Anthropic** (Claude 3)

## 📌 Endpoints API

| Endpoint | Méthode | Description | Temps Moyen |
|----------|---------|-------------|-------------|
| `/api/v1/extract` | POST | Extraire contenu web | 2-30s |
| `/api/v1/research` | POST | Recherche profonde multi-pages | 15-45s |
| `/api/v1/vision` | POST | Analyser une image | 1-12s |
| `/health` | GET | Health check global | <1s |

## 🎯 Cas d'Usage

- **Veille Technologique**: Recherche automatisée avec synthèse
- **Extraction Documentation**: Récupération contenu technique
- **Analyse Visuelle**: OCR factures, graphiques, cartes
- **Research Assistant**: Questions → Réponses avec sources
- **Data Scraping**: Extraction intelligente avec LLM

## 📊 Garanties Qualité

- ✅ **100% Extraction Réelle** - Pas d'hallucination
- ✅ **Citations Vérifiables** - Toutes sources avec URLs
- ✅ **Traçabilité Complète** - Logs détaillés
- ✅ **Sécurité** - Pas de secrets exposés

## 📚 Documentation

- **Interactive**: Testez directement dans `/docs`
- **OpenAPI**: Schéma complet dans `/openapi.json`
- **GitHub**: [github.com/nic01asFr/webtools](https://github.com/nic01asFr/webtools)

## 🚀 Quick Start

```bash
curl -X POST "http://localhost:8000/api/v1/research" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "Comment utiliser FastAPI ?", "max_depth": 2}'
```

---

**Propulsé par Albert API 🇫🇷 | Fait avec ❤️ en France**
    """,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# Configuration CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # À restreindre en production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inclure les routers
app.include_router(
    extract.router,
    prefix="/api/v1",
    tags=["extraction"]
)

app.include_router(
    research.router,
    prefix="/api/v1",
    tags=["research"]
)

app.include_router(
    vision.router,
    prefix="/api/v1",
    tags=["vision"]
)


@app.get("/", response_class=JSONResponse)
async def root():
    """Endpoint racine."""
    return {
        "service": "WebExtract Service",
        "version": settings.api_version,
        "docs": "/docs",
        "health": "/health"
    }


@app.get("/health", response_model=HealthResponse, tags=["monitoring"])
async def health_check():
    """Endpoint de santé du service."""
    return HealthResponse(
        status="healthy",
        version=settings.api_version,
        playwright_available=is_playwright_available()
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
        log_level=settings.log_level.lower()
    )
