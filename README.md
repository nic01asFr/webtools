# WebTools API v3.0

Service d'extraction de contenu web et de recherche sourcée. Une API REST
(FastAPI) et un serveur MCP qui l'appelle. Le service produit aussi des
rapports de recherche approfondie : un plan par sections, une collecte de
sources par section, une rédaction avec citations et une bibliographie.

[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org)

## Ce que fait le service

Trois services tournent ensemble (`docker-compose.yml`, `start_all.sh`) :

| Service | Rôle |
|---|---|
| `webtools` | API REST FastAPI, port 8000 |
| `webtools-mcp` | Serveur MCP, client HTTP de l'API, port 8090 |
| `searxng` | Moteur de recherche (SearXNG), réseau interne uniquement |

Le LLM par défaut est un modèle compatible OpenAI hébergé sur SSPCloud :
fournisseur `openai`, modèle `qwen3-6-35b-moe`, URL
`https://llm.lab.sspcloud.fr/api` (source unique : `app/core/config.py`).
Les fournisseurs `anthropic` et `albert` existent, mais ils n'ont ni sorties
structurées ni embeddings : le pipeline approfondi y tourne en mode dégradé.
Le fournisseur `anthropic` ne gère pas l'analyse d'images.

## Endpoints

Tous les endpoints `/api/v1/...` exigent l'en-tête `X-API-Key`. Le service
refuse de démarrer sans `WEBTOOLS_API_KEY`, sauf si
`WEBTOOLS_ALLOW_ANONYMOUS=1` est posé explicitement.

| Endpoint | Méthode | Usage |
|---|---|---|
| `/api/v1/extract` | POST | Contenu d'une page web |
| `/api/v1/vision` | POST | Analyse d'une image par le LLM |
| `/api/v1/search` | POST | Recherche web (titre, URL, extrait) |
| `/api/v1/research/quick` | POST | Réponse courte avec sources citées |
| `/api/v1/research/deep` | POST | Rapport structuré avec bibliographie |
| `/api/v1/vision/health` | GET | Le LLM configuré permet-il la vision |
| `/health` | GET | État réel : Playwright, SearXNG, LLM, catalogue de moteurs, commit chargé |
| `/ready` | GET | Sonde de disponibilité (Playwright) |
| `/docs`, `/redoc`, `/openapi.json` | GET | Documentation générée |

Les modèles Pydantic de `app/api/models.py` et de
`app/api/v1/endpoints/research_deep.py` font foi. Les champs ci-dessous sont
ceux que le code lit.

### `/api/v1/extract`

| Champ | Type | Effet |
|---|---|---|
| `url` | texte, obligatoire | Commence par `http://` ou `https://`. Les adresses non publiques sont refusées (garde SSRF) |
| `prompt` | texte, optionnel | Consigne d'extraction |
| `extraction_type` | `general` (défaut), `article`, `product`, `repository`, `documentation` | Type de contenu attendu |
| `llm_config` | objet, optionnel | `provider`, `api_key`, `model`, `base_url` : LLM propre à la requête. Sinon, le LLM du service |
| `options` | objet, optionnel | `use_agent`, `timeout` (5 à 300 s, défaut 45), `try_rss_first`, `rss_tier_root_only`, `single_llm_pass_enabled`, `headless`, `enable_ocr`. Un champ inconnu est refusé (422) |

Réponse : `success`, `url`, `content_type`, `title`, `content`, `metadata`,
`error`. L'extraction essaie le flux RSS, le navigateur direct, un appel LLM
léger, puis un agent navigateur. Sans clé LLM, elle se limite à l'extraction
directe et `metadata.llm_disponible` vaut `false`.

### `/api/v1/vision`

`image_url` et `prompt` (5 à 1000 caractères) sont obligatoires.
Optionnels : `system_prompt`, `temperature` (0 à 2, défaut 0,15),
`max_tokens` (50 à 4096, défaut 500). Réponse : `success`, `image_url`,
`prompt`, `analysis`, `model_used`, `processing_time_seconds`, `error`.
Erreurs HTTP : 400 (URL non publique), 501 (modèle sans vision),
502 ou 504 (échec ou délai du modèle), 503 (aucune clé LLM).

### `/api/v1/search`

`query` (500 caractères au plus) est obligatoire. Optionnels :
`max_results` (1 à 50, défaut 10), `language` (défaut `fr`), `categories`
et `engines` (noms SearXNG). Réponse : `query`, `results` (`title`, `url`,
`snippet` de 300 caractères au plus), `total`, `success`, `error`. Aucun
appel LLM.

### `/api/v1/research/quick`

`query` (5 à 2000 caractères) et `max_sources` (1 à 15, défaut 5). Le
service cherche, extrait chaque source, puis demande au LLM une réponse
courte avec citations `[1]`, `[2]`. Réponse : `answer`, `sources`
(`title`, `url`, `relevance` si le moteur fournit un score),
`sources_attempted`, `sources_extracted`, `sources_discarded`, `discarded`
(`url`, `reason`), `success`, `error`. Le champ `confidence` n'est pas
calculé : il vaut toujours `null`. Un moteur de recherche en panne ou un LLM
muet donnent une erreur HTTP (502, 504), pas une réponse vide en succès.

### `/api/v1/research/deep`

Rapport structuré. Champs de la requête :

| Champ | Effet |
|---|---|
| `topic` (5 à 4000 caractères, obligatoire) | Sujet |
| `objectives` | Liste d'objectifs transmise au modèle qui planifie |
| `context.language` | Langue préférée, transmise au modèle qui planifie (la langue de recherche et de rédaction est détectée sur `topic`) |
| `timeout` (60 à 1200 s, défaut 600) | Délai global. Passé ce délai, la réponse porte les sections déjà rédigées, avec `degraded: true` |
| `max_sources_per_section` (1 à 20) | Plafond de sources par section. Absent : 3, 5 ou 8 selon la profondeur de la section |
| `output_format.sections` (ou `output_sections`) | Sections imposées : le rapport contient exactement ces titres, dans cet ordre |
| `output_format.include_section_data` | Joint à chaque section les passages extraits (`data`) |
| `sources.required` | URL obligatoires : extraites avant le plan, proposées à toutes les sections |
| `sources.domains_whitelist`, `sources.exclusions` | Domaines autorisés, domaines exclus |

La réponse a toujours la même forme, en succès, en rapport dégradé et en
échec (code HTTP 200) : `success`, `degraded`, `timed_out`, `topic`,
`processing_time`, `error`, `result` (`title`, `summary`, `sections`,
`bibliography`, `metadata`), `failed_sections`, `unsourced_sections`,
`missing_sections`, `execution`, `sources` et `diagnostics`. `success` vaut
`false` quand le rapport est dégradé ; `result` est alors présent s'il
existe. `diagnostics` porte la configuration effective (`config`), les
dégradations rencontrées (`events`) et les invariants du rapport
(`invariants`). Le détail de l'enchaînement est dans
`DEEP_RESEARCH_ARCHITECTURE.md`.

Requêtes sans effet retirées en 3.0 : `max_steps`, `max_results`,
`context.min_sources`, `context.date_range`, `context.include_data`,
`context.include_reports`, `context.include_academic`, `sources.suggested`,
`output_format.structure`, `output_format.include_charts`,
`output_format.include_timeline`. Toute requête qui les envoie reçoit 422.
Il n'existe pas de mode flux (`stream`) : la réponse est un JSON unique.

Exemple :

```bash
curl -X POST "http://localhost:8000/api/v1/research/deep" \
  -H "X-API-Key: $WEBTOOLS_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "topic": "Démographie de l'\''Île-de-France depuis 2000",
    "output_format": {"sections": ["Contexte", "Évolution", "Perspectives"]},
    "sources": {"required": ["https://www.insee.fr/fr/accueil"]},
    "max_sources_per_section": 5,
    "timeout": 900
  }'
```

## Serveur MCP

`mcp_server.py` est un client HTTP de l'API REST : il appelle l'API
(`WEBTOOLS_API`, clé `WEBTOOLS_API_KEY`) et n'importe rien de `app/`. Ce que
le MCP renvoie est donc ce que l'API produit. Il protège son accès par un
flux OAuth 2.1 et une clé d'accès partagée (`MCP_ACCESS_TOKEN`).

Outils : `webtools_extract`, `webtools_search`, `webtools_vision`,
`webtools_research_quick`, `webtools_research_deep` (attend la fin du
rapport), `webtools_research_deep_start` et `webtools_research_deep_status`
(rapport en tâche de fond, état conservé sur disque, plafond de
`MCP_MAX_CONCURRENT_DEEP` recherches simultanées).

Le serveur MCP et l'API tournent dans deux environnements Python distincts :
leurs dépendances sont incompatibles (`browser-use` exige `pydantic<2.11`,
`fastmcp` exige `pydantic>=2.12`). Ne jamais les installer ensemble.

## Démarrage

Prérequis : Docker et Docker Compose, ou deux environnements Python 3.11.

```bash
cp .env.example .env     # puis renseigner les variables marquées OBLIGATOIRE
WEBTOOLS_COMMIT=$(git rev-parse HEAD) docker compose build
docker compose up -d
curl http://localhost:8000/health
```

Variables obligatoires : `WEBTOOLS_API_KEY`, `DEFAULT_LLM_API_KEY`,
`SEARXNG_SECRET`, `SEARXNG_CONTACT_URL`, `MCP_ACCESS_TOKEN`. Le réseau
Docker `proxy` est externe (Traefik) : hors production, le créer avec
`docker network create proxy`.

Sans Docker : deux environnements Python, puis `./start_all.sh`. La
procédure complète, la vérification d'un déploiement et la régénération des
fichiers de verrou sont dans `GETTING_STARTED.md`. Toutes les variables sont
dans `docs/CONFIGURATION.md`.

## Tests

`make check` vérifie l'API (interpréteur de l'environnement API) :

```bash
python -m ruff check --select F821,F811,E9 .
python -m pytest tests -q -m "not live"
```

`make check-mcp` lance les tests du serveur MCP avec l'interpréteur de
l'environnement MCP (`MCP_PYTHON`).

Les tests n'appellent ni le réseau ni un LLM réel.

## Documentation

| Document | Contenu |
|---|---|
| `GETTING_STARTED.md` | Installation, déploiement, vérification |
| `docs/CONFIGURATION.md` | Tous les réglages du pipeline et les variables d'environnement |
| `DEEP_RESEARCH_ARCHITECTURE.md` | Pipeline de recherche approfondie, étape par étape |
| `USE_CASES.md` | Usages et enchaînements d'endpoints |
| `benchmarks/PROTOCOLE.md` | Protocole de mesure sur DeepResearch Bench |
| `benchmarks/INSTALL.md`, `benchmarks/replay.md` | Installation du harnais, rejeu de la collecte |
| `CHANGELOG.md` | Historique, changements incompatibles de la 3.0 |
| `PLAN_FIABILISATION.md` | Plan de fiabilisation et état d'avancement |

## Sécurité

- Ne jamais versionner `.env`.
- L'API est protégée par `X-API-Key`. Le mode anonyme est un choix explicite.
- Les URL fournies par l'appelant (pages, images, `llm_config.base_url`)
  passent par une garde contre les adresses non publiques.

## Licence

MIT. Voir `LICENSE`.
