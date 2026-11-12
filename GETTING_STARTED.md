# Guide de démarrage - webtools

Ce guide installe et lance webtools : une API REST de recherche et d'extraction
web, un serveur MCP qui l'appelle, et SearXNG. Pour ce que fait le service,
voir `README.md`. Pour tous les réglages, voir `docs/CONFIGURATION.md`.

## Démarrage rapide

### Avec Docker (recommandé)

```bash
# 1. Variables d'environnement
cp .env.example .env
# Renseigner les variables marquées OBLIGATOIRE : WEBTOOLS_API_KEY,
# DEFAULT_LLM_API_KEY, SEARXNG_SECRET, SEARXNG_CONTACT_URL, MCP_ACCESS_TOKEN

# 2. Construire (le commit est passé au build pour /health) et lancer
docker network create proxy       # une fois, hors production
WEBTOOLS_COMMIT=$(git rev-parse HEAD) docker compose build
docker compose up -d

# 3. Vérifier
docker compose ps
curl http://localhost:8000/health
```

L'API écoute sur le port 8000, le serveur MCP sur le port 8090. SearXNG n'est
pas exposé.

### Sans Docker

Les dépendances de l'API et du serveur MCP sont incompatibles : deux
environnements Python 3.11 (voir la section suivante).

```bash
# API, en développement
make dev-install PYTHON=<python du venv API>
cp .env.example .env              # puis renseigner les variables obligatoires
python -m uvicorn app.main:app --reload     # ou : make run
```

Sans `WEBTOOLS_API_KEY`, l'API refuse de démarrer. Pour un essai local sans
authentification, poser `WEBTOOLS_ALLOW_ANONYMOUS=1`.

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
python -m venv venv-api && venv-api/bin/pip install -r requirements.lock \
    --extra-index-url https://download.pytorch.org/whl/cpu
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

Les verrous visent l'image : Linux x86_64, Python 3.11, quel que soit le poste
qui les compile (un verrou compile sous Windows contient pywin32 et colorama,
qui ne s'installent pas sous Linux). Toujours dans un venv jetable hors du
depot, jamais dans un venv partage :

```bash
R="$PWD"; T="$(mktemp -d)" && python -m venv "$T/venv" && "$T/venv/bin/pip" install uv
# (sous Windows : "$T/venv/Scripts/pip" et "$T/venv/Scripts/uv")
UV="$T/venv/bin/uv"
CIBLE="--python-platform x86_64-unknown-linux-gnu --python-version 3.11 --no-header"
# API : dependances principales de pyproject.toml. Un verrou existant sert de
# preference : seules les lignes qui doivent changer changent.
# torch en roues CPU (le service ne fait aucun calcul GPU) : index PyTorch
# supplementaire, et unsafe-best-match pour que uv y prenne torch==X+cpu. Sans
# cela le verrou tire les roues CUDA (nvidia-*, triton : plus de 3 Go).
"$UV" pip compile $CIBLE --extra-index-url https://download.pytorch.org/whl/cpu   --index-strategy unsafe-best-match -o requirements.lock pyproject.toml
# MCP : liste de l'extra mcp de pyproject.toml, plus httpx
printf '%s\n' 'fastmcp>=3.0.0' 'mcp>=1.0.0' 'starlette>=0.37.0' 'httpx>=0.27.2' > "$T/mcp.in"
(cd "$T" && "$UV" pip compile $CIBLE -o "$R/requirements-mcp.lock" mcp.in)
```

Puis remettre les commentaires d'en-tete des verrous (voir `git diff`),
verifier qu'il ne reste ni `nvidia-*`, ni `cuda-*`, ni `triton` dans
`requirements.lock` (`torch` doit valoir `X+cpu`), et verifier que les annotations `# via -r` ne contiennent pas de chemin local
(elles doivent dire `-r mcp.in`). Controle sans installer, depuis un venv
quelconque (les roues CUDA de torch pesent plus de 3 Go) :

```bash
pip download -r requirements-mcp.lock --platform manylinux2014_x86_64   --python-version 3.11 --implementation cp --only-binary=:all: --no-deps -d "$T/dl"
```

Pour requirements.lock, ajouter une option `--platform manylinux_2_N_x86_64`
par version de glibc utile (de `manylinux_2_17` a `manylinux_2_39` ; les roues
nvidia-* utilisent `manylinux_2_18`, `manylinux_2_25`...).

### Construire l'image

L'image n'a pas de depot git : le commit charge est passe au build, sinon
`/health.commit` vaut `unknown` et le harnais de benchmark refuse de demarrer.

```bash
WEBTOOLS_COMMIT=$(git rev-parse HEAD) docker compose build
# ou : docker build -f docker/Dockerfile --build-arg WEBTOOLS_COMMIT=$(git rev-parse HEAD) .
```

### Verifier un deploiement

Apres un `docker compose up -d` (ou `start_all.sh`), controler les quatre
elements. Exemple avec les ports publies localement (8000 API, 8090 MCP) ;
`$CLE` est `WEBTOOLS_API_KEY`.

```bash
docker compose ps                          # trois services, "healthy"
curl -s http://localhost:8000/health       # commit attendu, searxng ok, catalogue de moteurs charge
# SearXNG depuis le conteneur de l'API : resultats reels de plusieurs moteurs
docker compose exec webtools curl -s "http://searxng:8080/search?q=test&format=json"   | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['results']), sorted({e for r in d['results'] for e in r.get('engines',[])}))"
# Serveur MCP : decouverte OAuth 200, endpoint protege 401
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8090/.well-known/oauth-authorization-server
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8090/mcp
# Recherche reelle et extraction (Playwright) par l'API
curl -s -X POST http://localhost:8000/api/v1/search -H "X-API-Key: $CLE"   -H "Content-Type: application/json" -d '{"query": "test"}'
curl -s -X POST http://localhost:8000/api/v1/extract -H "X-API-Key: $CLE"   -H "Content-Type: application/json" -d '{"url": "https://example.com"}'
```

`/health` indique `llm` en echec tant que `DEFAULT_LLM_API_KEY` n'est pas une
vraie cle : c'est normal avec une valeur factice. Le reseau `proxy` de
`docker-compose.yml` est externe (Traefik) : hors production, le creer avec
`docker network create proxy`.

### Mettre a jour l'image SearXNG

`docker buildx imagetools inspect searxng/searxng:latest` donne le condensat ;
le reporter dans `docker-compose.yml`, puis lancer `pytest tests/unit/config`
et verifier `/config` de l'instance (moteurs presents et actifs).

## Configuration minimale

Les valeurs par défaut du LLM sont celles de `app/core/config.py` : fournisseur
`openai` (tout service compatible OpenAI), modèle `qwen3-6-35b-moe`, URL
`https://llm.lab.sspcloud.fr/api`. Il suffit de fournir la clé :

```bash
WEBTOOLS_API_KEY=une_cle_pour_l_api
DEFAULT_LLM_API_KEY=la_cle_du_llm
```

Pour un autre fournisseur compatible OpenAI, ajouter `DEFAULT_LLM_MODEL` et
`DEFAULT_LLM_BASE_URL`. Les fournisseurs `anthropic` et `albert` existent
(`DEFAULT_LLM_PROVIDER`, `ANTHROPIC_API_KEY`, `ALBERT_API_KEY`), mais sans
sorties structurées ni embeddings : le pipeline de recherche approfondie y
tourne en mode dégradé. Détail des variables : `docs/CONFIGURATION.md`.

## Documentation de l'API

Une fois le service lancé :

- Swagger : http://localhost:8000/docs
- ReDoc : http://localhost:8000/redoc
- Schéma OpenAPI : http://localhost:8000/openapi.json

## Tester le service

`$CLE` est la valeur de `WEBTOOLS_API_KEY`.

```bash
curl http://localhost:8000/health

curl -X POST "http://localhost:8000/api/v1/extract" \
  -H "X-API-Key: $CLE" -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "extraction_type": "general"}'

curl -X POST "http://localhost:8000/api/v1/search" \
  -H "X-API-Key: $CLE" -H "Content-Type: application/json" \
  -d '{"query": "FastAPI tutorial", "max_results": 5}'
```

## Architecture du dépôt

```
app/
  main.py                    application FastAPI, /health, /ready
  api/                       modèles Pydantic, authentification, endpoints (v1/endpoints/)
  agents/                    intelligent_orchestrator.py : enchaînement de la recherche approfondie
  research/                  configuration, contexte, citations, invariants, rejeu
    stages/                  une étape du pipeline par module
  core/                      configuration, clients LLM (llm/), navigateur (browser/)
  extractors/                extraction directe, agent navigateur, PDF, RSS
  services/                  client SearXNG, catalogue de moteurs
  manager.py                 chaîne d'extraction et cache
  utils/                     découpage en passages, langue, garde SSRF
mcp_server.py                serveur MCP, client HTTP de l'API
benchmarks/                  harnais DeepResearch Bench
docker/                      Dockerfile, configuration SearXNG
docs/                        CONFIGURATION.md et son générateur
tests/                       unit/, contract/, fakes/
```

## Commandes utiles (Makefile)

```bash
make help           # liste les commandes
make dev-install    # installe l'API et les outils de développement (PYTHON=...)
make install-mcp    # installe l'environnement MCP (MCP_PYTHON=...)
make check          # ruff (F821, F811, E9) puis pytest hors tests live (API)
make check-mcp      # tests du serveur MCP (interpréteur MCP)
make run            # lance l'API en local
make docker-build   # docker-compose build
make docker-up      # docker-compose up -d
make docker-logs    # journaux des conteneurs
```

Les tests n'appellent ni le réseau ni un LLM réel. Un test marqué `live` est
exclu par défaut.

## Dépannage

```bash
# Journaux détaillés en local
LOG_LEVEL=DEBUG python -m uvicorn app.main:app --reload

# Docker
docker compose logs -f webtools
docker compose logs -f webtools-mcp
```

- `/health` renvoie `degraded` tant qu'un de ces éléments manque : Playwright,
  SearXNG, un LLM qui répond, le catalogue de moteurs. Le détail est dans
  `dependencies`.
- Le service ne démarre pas : vérifier `WEBTOOLS_API_KEY` (ou
  `WEBTOOLS_ALLOW_ANONYMOUS=1`).
- `/health.commit` vaut `unknown` : l'image a été construite sans
  `WEBTOOLS_COMMIT`.

## Sécurité

- Ne jamais versionner `.env`.
- Toute URL fournie par l'appelant passe par une garde contre les adresses non
  publiques.
- En production, placer l'API derrière HTTPS (Traefik dans `docker-compose.yml`).

## Licence

MIT. Voir `LICENSE`. Le projet est parti du système d'extraction de Colaig
(Etalab), dont il est désormais indépendant.
