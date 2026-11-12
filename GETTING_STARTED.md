# Guide de Démarrage - WebExtract Service

## 🎉 Félicitations !

Votre service **WebExtract** a été créé avec succès ! Ce service autonome d'extraction de contenu web est basé sur le système d'extraction de Colaig, mais entièrement indépendant et multi-LLM.

## 📊 Statistiques du Projet

- **36 fichiers** créés
- **27 fichiers Python** (~2076 lignes de code)
- **Architecture modulaire** et extensible
- **Tests unitaires** inclus
- **Docker ready** avec Dockerfile et docker-compose

## 🚀 Démarrage Rapide

### Option 1 : Démarrage Local

```bash
# 1. Aller dans le répertoire du projet
cd /home/user/webextract-service

# 2. Installer les dépendances
make install
# ou
pip install -e .
playwright install chromium

# 3. Configurer les variables d'environnement
cp .env.example .env
# Éditer .env avec vos clés API

# 4. Lancer le service
make run
# ou
uvicorn app.main:app --reload
```

Le service sera accessible à : **http://localhost:8000**

### Option 2 : Démarrage avec Docker (Recommandé)

```bash
# 1. Configurer les variables d'environnement
cp .env.example .env
# Éditer .env avec vos clés API

# 2. Construire et lancer
make docker-build
make docker-up

# 3. Vérifier les logs
make docker-logs

# 4. Tester le service
curl http://localhost:8000/health
```

## Deploiement : trois services, deux environnements Python

webtools se compose de trois services, identiques avec Docker
(`docker-compose.yml`) et sans (`start_all.sh`) :

| Service | Role | Environnement |
|---|---|---|
| `searxng` | moteur de recherche, configure par `docker/searxng/settings.yml` | image epinglee par condensat |
| `webtools` | API FastAPI | dependances de `requirements.lock` |
| `webtools-mcp` | serveur MCP, client HTTP de l'API | dependances de `requirements-mcp.lock` |

L'API (`browser-use` impose `pydantic<2.11`) et le serveur MCP (`fastmcp`
exige `pydantic>=2.12`) ne se resolvent pas dans un meme environnement : ne
les installez jamais ensemble. L'image Docker contient les deux venvs
(`/opt/venv-api`, `/opt/venv-mcp`).

Sans Docker, designez les interpreteurs par variables :

```bash
python -m venv venv-api && venv-api/bin/pip install -r requirements.lock
python -m venv venv-mcp && venv-mcp/bin/pip install -r requirements-mcp.lock
export WEBTOOLS_API_PYTHON=$PWD/venv-api/bin/python
export WEBTOOLS_MCP_PYTHON=$PWD/venv-mcp/bin/python
# Variables obligatoires : voir .env.example
./start_all.sh
```

La configuration SearXNG est `docker/searxng/settings.yml`
(`SEARXNG_SETTINGS_PATH` dans `start_all.sh`) ; le test
`tests/unit/config/test_searxng_settings.py` verifie qu'elle active chaque
moteur nomme dans `AXIS_PREFERENCE`, `ENGINE_INTERVALS` et
`FALLBACK_PROFILES`. Ajouter un moteur au code oblige a l'ajouter au YAML.

### Regenerer les fichiers de verrou

Toujours dans un venv jetable hors du depot, jamais dans un venv partage :

```bash
T="$(mktemp -d)" && python3.11 -m venv "$T/venv" && "$T/venv/bin/pip" install pip-tools
# API : dependances principales de pyproject.toml
"$T/venv/bin/python" -m piptools compile --no-header --strip-extras -o requirements.lock pyproject.toml
# MCP : liste de l'extra mcp de pyproject.toml, plus httpx
printf 'fastmcp>=3.0.0
mcp>=1.0.0
starlette>=0.37.0
httpx>=0.27.2
' > "$T/mcp.in"
"$T/venv/bin/python" -m piptools compile --no-header --strip-extras -o requirements-mcp.lock "$T/mcp.in"
```

Puis remettre les deux lignes de commentaire d'en-tete des verrous (voir
`git diff`). Utiliser Python 3.11, comme l'image. Variante sans pip-tools :
`pip install --dry-run --ignore-installed --report rapport.json -r <liste>`.

### Mettre a jour l'image SearXNG

`docker buildx imagetools inspect searxng/searxng:latest` donne le condensat ;
le reporter dans `docker-compose.yml`, puis lancer `pytest tests/unit/config`
et verifier `/config` de l'instance (moteurs presents et actifs).

## 🔧 Configuration Minimale

Éditez le fichier `.env` avec au moins une configuration LLM :

```bash
# Pour OpenAI
DEFAULT_LLM_PROVIDER=openai
DEFAULT_LLM_MODEL=gpt-4o
OPENAI_API_KEY=sk-votre-clé-ici

# OU pour Anthropic
DEFAULT_LLM_PROVIDER=anthropic
DEFAULT_LLM_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=sk-ant-votre-clé-ici

# OU pour Albert
DEFAULT_LLM_PROVIDER=albert
DEFAULT_LLM_MODEL=AgentPublic/llama3-instruct-8b
ALBERT_API_KEY=votre-clé-albert
ALBERT_API_URL=https://albert.api.etalab.gouv.fr
```

## 📖 Documentation de l'API

Une fois le service lancé, accédez à :

- **Documentation interactive Swagger** : http://localhost:8000/docs
- **Documentation ReDoc** : http://localhost:8000/redoc
- **Schéma OpenAPI** : http://localhost:8000/openapi.json

## 🧪 Tester le Service

### Test de Santé

```bash
curl http://localhost:8000/health
```

### Extraction Simple

```bash
curl -X POST "http://localhost:8000/api/v1/extract" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.example.com",
    "extraction_type": "general",
    "llm_config": {
      "provider": "openai",
      "api_key": "sk-votre-clé",
      "model": "gpt-4o"
    },
    "options": {
      "use_agent": true,
      "timeout": 45
    }
  }'
```

### Extraction d'Article

```bash
curl -X POST "http://localhost:8000/api/v1/extract" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://blog.example.com/mon-article",
    "extraction_type": "article"
  }'
```

## 🏗️ Architecture

```
webextract-service/
├── app/
│   ├── api/                    # API REST
│   │   ├── models.py           # Modèles Pydantic
│   │   └── v1/endpoints/       # Endpoints
│   ├── core/                   # Core components
│   │   ├── config.py           # Configuration
│   │   ├── browser/            # Gestion Playwright
│   │   └── llm/                # Clients LLM
│   │       ├── base.py         # Interface
│   │       ├── albert.py       # Albert
│   │       ├── openai.py       # OpenAI
│   │       ├── anthropic.py    # Anthropic
│   │       └── factory.py      # Factory
│   ├── extractors/             # Extractors
│   │   ├── base.py             # Interface
│   │   ├── direct_extractor.py # Playwright direct
│   │   └── agent_extractor.py  # Agent IA
│   ├── utils/                  # Utilitaires
│   │   ├── content_detector.py # Détection type
│   │   └── prompts.py          # Templates prompts
│   ├── manager.py              # Orchestrateur
│   └── main.py                 # FastAPI app
├── tests/                      # Tests
├── docker/                     # Docker
└── pyproject.toml              # Dépendances
```

## 🔑 Points Clés

### Support Multi-LLM

Le service supporte plusieurs providers LLM :
- **OpenAI** : GPT-4, GPT-4o, etc.
- **Anthropic** : Claude 3, Claude 3.5
- **Albert** : LLM gouvernemental français
- Tout provider compatible OpenAI API

### Stratégies d'Extraction

1. **Direct Playwright** : Rapide, pour sites statiques
2. **Agent IA** : Intelligent, gère contenu dynamique
3. **Fallback automatique** : Si agent échoue → direct

### Détection Automatique

Le service détecte automatiquement le type de contenu :
- GitHub/GitLab → `repository`
- Sites docs → `documentation`
- E-commerce → `product`
- Blogs/news → `article`
- Autre → `webpage`

## 📝 Commandes Utiles (Makefile)

```bash
make help           # Affiche l'aide
make install        # Installe les dépendances
make run            # Lance le service localement
make test           # Lance les tests
make test-cov       # Tests avec couverture
make clean          # Nettoie les fichiers temp

# Docker
make docker-build   # Construit l'image
make docker-up      # Lance le service
make docker-down    # Arrête le service
make docker-logs    # Affiche les logs
make docker-shell   # Ouvre un shell dans le conteneur
```

## 🐛 Debugging

### Logs Détaillés

```bash
# Local
LOG_LEVEL=DEBUG uvicorn app.main:app --reload

# Docker
docker-compose logs -f webextract
```

### Tester Playwright

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
    browser.close()
```

## 🔒 Sécurité

**Important** :
- Ne committez jamais vos clés API
- Utilisez des variables d'environnement
- En production : HTTPS + authentification

## 🤝 Prochaines Étapes

1. **Tester le service** avec vos URLs
2. **Ajuster les prompts** dans `app/utils/prompts.py`
3. **Ajouter des tests** dans `tests/`
4. **Configurer CI/CD** (GitHub Actions, GitLab CI)
5. **Déployer en production** (Kubernetes, Cloud Run, etc.)

## 📧 Support

Pour toute question :
- Ouvrez une issue sur GitHub
- Consultez la documentation API à `/docs`
- Vérifiez les logs pour les erreurs

## 🎯 Différences avec Colaig

| Aspect | Colaig | WebExtract Service |
|--------|--------|-------------------|
| **Dépendance LLM** | Albert uniquement | Multi-provider |
| **Interface** | Matrix bot | API REST |
| **Configuration** | Config Colaig | Standalone simple |
| **Déploiement** | Intégré | Service Docker indépendant |
| **Complexité** | ~26K lignes | ~2K lignes focused |

## 📜 License

MIT License - Voir [LICENSE](LICENSE)

Adapté depuis [Colaig](https://github.com/etalab-ia/albert-tchap) par Etalab.

---

**Bon développement ! 🚀**
