# 🚀 WebTools - API d'Extraction et d'Analyse Web avec IA

WebTools est un service FastAPI autonome pour l'extraction de contenu web, la recherche intelligente multi-pages et l'analyse d'images, propulsé par Albert API (LLM gouvernemental français).

[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](https://www.docker.com)

## ✨ Fonctionnalités

### 🌐 Extraction Web Multi-Stratégies
- **DirectExtractor**: Extraction rapide avec Playwright (sites statiques/SPA)
- **AgentExtractor**: Navigation intelligente avec browser-use + LLM
- **Fallback HTTP**: Extraction basique si Playwright indisponible
- Support des sites avec JavaScript lourd (GitHub, YouTube, etc.)

### 🔍 Deep Research (Recherche Profonde)
- Recherche multi-pages avec navigation intelligente guidée par LLM
- Intégration SearXNG pour découverte automatique de sources
- Analyse de pertinence et scoring des pages
- Synthèse finale avec citations de sources
- Configuration de profondeur et nombre de sources

### 🎨 Vision AI (Analyse d'Images)
- OCR et extraction de texte depuis images
- Analyse de cartes géographiques, graphiques, diagrammes
- Description détaillée d'images (logos, UI, photos)
- Support PNG, JPG, WebP, GIF
- Modèle: **albert-large** (128K contexte)

### 🤖 Support Multi-LLM
- **Albert API** (albert-code, albert-large) - Par défaut
- **OpenAI** (GPT-4, GPT-3.5)
- **Anthropic** (Claude 3)

## 📦 Installation

### Prérequis
- Docker & Docker Compose
- Clé API Albert: [https://albert.api.etalab.gouv.fr](https://albert.api.etalab.gouv.fr)

### Déploiement Rapide

1. **Cloner le repository**
\`\`\`bash
git clone https://github.com/nic01asFr/webtools.git
cd webtools
\`\`\`

2. **Configurer les variables d'environnement**
\`\`\`bash
cp .env.example .env
# Éditer .env avec votre clé API Albert
\`\`\`

3. **Lancer avec Docker Compose**
\`\`\`bash
docker-compose up -d
\`\`\`

4. **Vérifier le déploiement**
\`\`\`bash
curl http://localhost:8000/health
\`\`\`

## 🔧 Configuration

### Variables d'Environnement

Créez un fichier \`.env\` à la racine du projet:

\`\`\`bash
# API Configuration
API_TITLE=WebTools API
API_VERSION=1.0.0
API_HOST=0.0.0.0
API_PORT=8000

# LLM Configuration (Albert par défaut)
DEFAULT_LLM_PROVIDER=albert
DEFAULT_LLM_MODEL=albert-code
DEFAULT_LLM_API_KEY=votre_cle_api_albert_ici
DEFAULT_LLM_BASE_URL=https://albert.api.etalab.gouv.fr

# Logging
LOG_LEVEL=INFO
\`\`\`

## 📚 Utilisation

### Documentation Interactive
Accédez à \`http://localhost:8000/docs\` pour la documentation Swagger UI complète.

### 1. Extraction de Contenu Web

\`\`\`bash
curl -X POST "http://localhost:8000/api/v1/extract" \\
  -H "Content-Type: application/json" \\
  -d '{
    "url": "https://fastapi.tiangolo.com/",
    "extraction_type": "article"
  }'
\`\`\`

### 2. Deep Research (Recherche Profonde)

\`\`\`bash
curl -X POST "http://localhost:8000/api/v1/research" \\
  -H "Content-Type: application/json" \\
  -d '{
    "query": "Comment déployer FastAPI avec Docker ?",
    "max_depth": 2,
    "max_sources": 5
  }'
\`\`\`

### 3. Analyse d'Images (Vision AI)

\`\`\`bash
curl -X POST "http://localhost:8000/api/v1/vision" \\
  -H "Content-Type: application/json" \\
  -d '{
    "image_url": "https://example.com/image.png",
    "prompt": "Décris cette image en détail"
  }'
\`\`\`

### 4. Recherche Interactive sur Site (Site Search) 🆕

\`\`\`bash
curl -X POST "http://localhost:8000/api/v1/search-site" \\
  -H "Content-Type: application/json" \\
  -d '{
    "site_url": "https://www.legifrance.gouv.fr/",
    "search_query": "droit du travail congés payés",
    "max_results": 10
  }'
\`\`\`

**Fonctionnalités:**
- Détecte automatiquement les formulaires de recherche (input, textarea)
- Remplit et soumet le formulaire avec votre requête
- Extrait les résultats affichés (titres, URLs, extraits)
- Supporte les sites avec JavaScript (SPA, sites dynamiques)
- Double stratégie: browser-use + LLM pour sites complexes, Playwright direct pour sites simples

**Cas d'usage:**
- 🏛️ Recherche juridique (Legifrance, EUR-Lex)
- 📚 Documentation technique (sans API)
- 🏥 Bases de données spécialisées
- 📰 Archives de presse

## 🏗️ Architecture

\`\`\`
webtools/
├── app/
│   ├── api/              # Endpoints FastAPI
│   ├── agents/           # Agents intelligents
│   ├── core/             # Core functionality
│   │   └── llm/          # Clients LLM
│   ├── extractors/       # Stratégies d'extraction
│   ├── services/         # Services externes
│   └── main.py           # Application FastAPI
├── docker/
│   └── Dockerfile
├── docker-compose.yml
└── pyproject.toml
\`\`\`

## 🔌 API Endpoints

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| \`/api/v1/extract\` | POST | Extraire le contenu d'une URL |
| \`/api/v1/research\` | POST | Recherche profonde multi-pages |
| \`/api/v1/vision\` | POST | Analyser une image |
| \`/api/v1/search-site\` | POST | Recherche interactive sur site |
| \`/health\` | GET | Health check global |
| \`/docs\` | GET | Documentation Swagger UI |

## 📊 Performances

| Opération | Temps Moyen | Taux de Succès |
|-----------|-------------|----------------|
| Extraction simple | 2-5s | 98% |
| Extraction avec agent | 10-30s | 95% |
| Research (depth=2) | 15-45s | 90% |
| Vision OCR | 3-5s | 95% |
| Vision description | 6-10s | 92% |
| Site search (simple) | 10-25s | 85% |
| Site search (complexe) | 30-60s | 70% |

## 🔐 Sécurité

⚠️ **IMPORTANT**: Ne jamais commiter le fichier \`.env\` avec vos clés API.

Le fichier \`.gitignore\` est configuré pour exclure:
- \`.env\`
- Fichiers de secrets
- Cache Python
- Logs

## 🤝 Contribution

Les contributions sont les bienvenues! Pour contribuer:

1. Fork le projet
2. Créer une branche (\`git checkout -b feature/amazing\`)
3. Commit les changements (\`git commit -m 'Add amazing feature'\`)
4. Push la branche (\`git push origin feature/amazing\`)
5. Ouvrir une Pull Request

## 📝 Licence

MIT License

## 🙏 Remerciements

- [FastAPI](https://fastapi.tiangolo.com/) - Framework web moderne
- [Playwright](https://playwright.dev/) - Automation navigateur
- [Albert API](https://albert.api.etalab.gouv.fr/) - LLM gouvernemental français
- [SearXNG](https://github.com/searxng/searxng) - Meta-moteur de recherche

## 📧 Contact

- GitHub: [@nic01asFr](https://github.com/nic01asFr)
- Repository: [webtools](https://github.com/nic01asFr/webtools)

---

**Fait avec ❤️ en France** 🇫🇷
