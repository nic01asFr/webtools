# WebTools API v2.0

API FastAPI pour extraction et recherche web intelligente, propulsée par Albert API (LLM gouvernemental français).

[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](https://www.docker.com)

## Fonctionnalités

### 5 Endpoints Essentiels

**`/api/v1/extract`** - Extraction de contenu web
- Contenu nettoyé (texte, liens, images, métadonnées)
- Support JavaScript avec Playwright
- Réduction ~87% du HTML brut

**`/api/v1/vision`** - Analyse d'images
- OCR et extraction de texte
- Analyse de graphiques, diagrammes, cartes
- Description détaillée (logos, UI, photos)

**`/api/v1/search`** - Recherche web simple
- Résultats avec titre, URL, snippet
- Filtres par langue, période, domaine
- Intégration SearXNG

**`/api/v1/research/quick`** - Réponse rapide documentée
- Question factuelle → Réponse synthétique + sources citées
- 3-15 sources selon complexité
- Niveau de confiance (high/medium/low)

**`/api/v1/research/deep`** - Rapport de recherche complet
- Rapport structuré en sections avec bibliographie
- 10-50 sources analysées et croisées
- Mode streaming (SSE) ou JSON final
- Architecture 4 phases : Exploration → Planning → Construction → Cohérence

### Support Multi-LLM
- **Albert API** (albert-code, albert-large) - Par défaut
- **OpenAI** (GPT-4, GPT-3.5)
- **Anthropic** (Claude 3)

## Installation

### Prérequis
- Docker & Docker Compose
- Clé API Albert: [https://albert.api.etalab.gouv.fr](https://albert.api.etalab.gouv.fr)

### Déploiement

```bash
# 1. Cloner le repository
git clone https://github.com/nic01asFr/webtools.git
cd webtools

# 2. Configurer les variables d'environnement
cp .env.example .env
# Éditer .env avec votre clé API Albert

# 3. Lancer avec Docker Compose
docker-compose up -d

# 4. Vérifier le déploiement
curl http://localhost:8000/health
```

## Configuration

### Variables d'Environnement

Créez un fichier `.env` à la racine :

```bash
# API Configuration
API_TITLE=WebTools API
API_VERSION=2.0.0
API_HOST=0.0.0.0
API_PORT=8000

# LLM Configuration (Albert par défaut)
DEFAULT_LLM_PROVIDER=albert
DEFAULT_LLM_MODEL=albert-code
DEFAULT_LLM_API_KEY=votre_cle_api_albert_ici
DEFAULT_LLM_BASE_URL=https://albert.api.etalab.gouv.fr

# Logging
LOG_LEVEL=INFO
```

## Utilisation

### Documentation Interactive
Accédez à `http://localhost:8000/docs` pour la documentation Swagger UI complète.

### 1. Extraction de Contenu

```bash
curl -X POST "http://localhost:8000/api/v1/extract" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/article",
    "clean_html": true,
    "extract_images": true
  }'
```

**Résultat :**
```json
{
  "url": "https://example.com/article",
  "title": "Titre de l'article",
  "content": "Texte principal sans navigation ni publicités...",
  "images": ["https://example.com/img1.jpg"],
  "word_count": 1500
}
```

### 2. Analyse d'Image

```bash
curl -X POST "http://localhost:8000/api/v1/vision" \
  -H "Content-Type: application/json" \
  -d '{
    "image_url": "https://example.com/chart.png",
    "prompt": "Extraire les données de ce graphique"
  }'
```

**Résultat :**
```json
{
  "image_url": "https://example.com/chart.png",
  "analysis": "Graphique en barres montrant l'évolution...",
  "confidence": 0.95
}
```

### 3. Recherche Simple

```bash
curl -X POST "http://localhost:8000/api/v1/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "FastAPI tutorial",
    "max_results": 10,
    "language": "fr"
  }'
```

**Résultat :**
```json
{
  "query": "FastAPI tutorial",
  "results": [
    {
      "title": "FastAPI Documentation",
      "url": "https://fastapi.tiangolo.com",
      "snippet": "FastAPI is a modern web framework..."
    }
  ],
  "total": 10
}
```

### 4. Réponse Rapide Documentée

```bash
curl -X POST "http://localhost:8000/api/v1/research/quick" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Quelle est la capitale du Japon?",
    "max_sources": 5
  }'
```

**Résultat :**
```json
{
  "query": "Quelle est la capitale du Japon?",
  "answer": "Tokyo est la capitale du Japon depuis 1868. [1][2]",
  "sources": [
    {
      "title": "Tokyo - Wikipedia",
      "url": "https://fr.wikipedia.org/wiki/Tokyo",
      "relevance": 0.95
    }
  ],
  "confidence": "high"
}
```

### 5. Rapport de Recherche Complet

```bash
# Mode non-streaming (JSON final)
curl -X POST "http://localhost:8000/api/v1/research/deep" \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "Impact de l'\''IA sur l'\''emploi",
    "max_sources": 20,
    "stream": false
  }'

# Mode streaming (progression temps réel)
curl -X POST "http://localhost:8000/api/v1/research/deep" \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "Impact de l'\''IA sur l'\''emploi",
    "max_sources": 20,
    "stream": true
  }'
```

**Résultat :**
```json
{
  "query": "Impact de l'IA sur l'emploi",
  "report": {
    "title": "Analyse de l'impact de l'IA sur le marché de l'emploi",
    "sections": [
      {
        "title": "1. État actuel du marché",
        "content": "L'intelligence artificielle transforme... [1][3]",
        "subsections": [...]
      },
      {
        "title": "2. Secteurs affectés",
        "content": "Les secteurs les plus impactés incluent... [2][5]"
      }
    ]
  },
  "bibliography": [
    {
      "id": 1,
      "title": "AI Impact Study 2024",
      "url": "https://..."
    }
  ],
  "metadata": {
    "sources_analyzed": 25,
    "research_time": "45s",
    "confidence": "high"
  }
}
```

## Architecture

```
Webtools/
├── app/
│   ├── api/                    # Endpoints FastAPI
│   │   ├── extract.py          # /extract endpoint
│   │   ├── vision.py           # /vision endpoint
│   │   ├── search.py           # /search endpoint
│   │   └── research.py         # /research/quick & /research/deep
│   ├── agents/                 # Orchestration intelligente
│   │   └── intelligent_orchestrator.py  # Deep research 4-phase
│   ├── core/                   # Configuration et LLM
│   │   ├── config.py           # Settings
│   │   ├── llm/                # Clients LLM multi-provider
│   │   └── content_cleaner.py  # Nettoyage HTML avancé
│   ├── extractors/             # Stratégies d'extraction
│   │   ├── direct_extractor.py # Playwright
│   │   └── agent_extractor.py  # Browser-use + LLM
│   ├── services/               # Services externes
│   │   └── searxng_service.py  # Meta-search
│   └── main.py                 # Application FastAPI
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

## Architecture Deep Research (v2.0)

Le endpoint `/research/deep` utilise une architecture 4-phase pour produire des rapports structurés :

**Phase 1 : Exploration & Planning**
- Exploration initiale restreinte (8 sources max)
- Évaluation du champ de recherche
- Création d'un plan détaillé (canvas) avec analyse de complexité 5D

**Phase 2 : Construction Section-par-Section**
- Recherches ciblées par section
- Extraction et nettoyage des sources
- Croisement des données inter-sections
- Synthèse qualitative avec instructions LLM précises

**Phase 3 : Cohérence Globale**
- Analyse des intersections entre sections
- Stratégie adaptative (≤10 sections : directe, >10 : distribuée)
- Application d'améliorations prioritaires

**Phase 4 : Finalisation**
- Assemblage du rapport complet
- Génération de la bibliographie
- Traces complètes du processus

Documentation complète : `DEEP_RESEARCH_ARCHITECTURE.md`

## API Endpoints

| Endpoint | Méthode | Description | Temps Moyen |
|----------|---------|-------------|-------------|
| `/api/v1/extract` | POST | Extraire le contenu d'une URL | 2-30s |
| `/api/v1/vision` | POST | Analyser une image | 1-10s |
| `/api/v1/search` | POST | Recherche web simple | 1-5s |
| `/api/v1/research/quick` | POST | Réponse rapide documentée | 15-60s |
| `/api/v1/research/deep` | POST | Rapport complet structuré | 30-300s |
| `/health` | GET | Health check global | <1s |
| `/docs` | GET | Documentation Swagger UI | - |

## Performances

| Opération | Temps Moyen | Sources |
|-----------|-------------|---------|
| Extraction simple | 2-5s | 1 |
| Extraction avec agent | 10-30s | 1 |
| Research quick | 15-60s | 3-15 |
| Research deep (simple) | 30-90s | 10-20 |
| Research deep (complexe) | 90-300s | 20-50 |
| Vision OCR | 3-5s | - |
| Vision description | 6-10s | - |

## Sécurité

⚠️ **IMPORTANT** : Ne jamais commiter le fichier `.env` avec vos clés API.

Le fichier `.gitignore` est configuré pour exclure :
- `.env`
- Fichiers de secrets
- Cache Python
- Logs

## Guide de Sélection d'Endpoint

- URL spécifique à extraire ? → `/extract`
- Image à analyser ? → `/vision`
- Chercher des pages sur un sujet ? → `/search`
- Question factuelle rapide ? → `/research/quick`
- Rapport approfondi structuré ? → `/research/deep`

## Documentation Complète

- **Swagger UI Interactive** : `/docs`
- **ReDoc** : `/redoc`
- **Guide Architecture Deep Research** : `DEEP_RESEARCH_ARCHITECTURE.md`
- **Changelog** : `CHANGELOG.md`
- **Guide Agent IA** : `AI_AGENT_GUIDE.md`

## Contribution

Les contributions sont les bienvenues ! Pour contribuer :

1. Fork le projet
2. Créer une branche (`git checkout -b feature/amazing`)
3. Commit les changements (`git commit -m 'Add amazing feature'`)
4. Push la branche (`git push origin feature/amazing`)
5. Ouvrir une Pull Request

## Licence

MIT License

## Remerciements

- [FastAPI](https://fastapi.tiangolo.com/) - Framework web moderne
- [Playwright](https://playwright.dev/) - Automation navigateur
- [Albert API](https://albert.api.etalab.gouv.fr/) - LLM gouvernemental français
- [SearXNG](https://github.com/searxng/searxng) - Meta-moteur de recherche

## Contact

- GitHub: [@nic01asFr](https://github.com/nic01asFr)
- Repository: [webtools](https://github.com/nic01asFr/webtools)

---

**Fait avec ❤️ en France** 🇫🇷
