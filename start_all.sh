#!/bin/bash
# Supervision legere (sans systemd/root) des 3 services webtools :
# SearXNG (recherche), API FastAPI (extraction/recherche approfondie),
# serveur MCP (client HTTP de l'API, expose en OAuth pour Claude Desktop/Code).
# Chaque service tourne dans une boucle qui le relance automatiquement s'il
# crashe ou si le pod redemarre. Memes services que docker-compose.yml.
#
# L'API et le serveur MCP ont des dependances incompatibles : ils tournent
# sous deux interpreteurs distincts, designes par WEBTOOLS_API_PYTHON et
# WEBTOOLS_MCP_PYTHON (voir GETTING_STARTED.md).

set -u
cd "$(dirname "$0")"

# Secrets : lus depuis l'environnement, JAMAIS ecrits en dur ici (ce fichier
# est versionne dans un depot public). Les definir avant de lancer ce script,
# ou le sourcer depuis un .env non versionne.
export DEFAULT_LLM_API_KEY="${DEFAULT_LLM_API_KEY:?DEFAULT_LLM_API_KEY doit etre defini avant de lancer ce script}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-$DEFAULT_LLM_API_KEY}"

# Cle d'API protegeant l'API REST publique (en-tete X-API-Key). Sans elle,
# l'API est ouverte a quiconque connait l'URL : consommation du quota LLM et
# usage du service comme proxy de scraping anonymisant.
export WEBTOOLS_API_KEY="${WEBTOOLS_API_KEY:?WEBTOOLS_API_KEY doit etre defini avant de lancer ce script}"
export MCP_ACCESS_TOKEN="${MCP_ACCESS_TOKEN:?MCP_ACCESS_TOKEN doit etre defini avant de lancer ce script}"
export SEARXNG_SECRET="${SEARXNG_SECRET:?SEARXNG_SECRET doit etre defini avant de lancer ce script}"
export SEARXNG_CONTACT_URL="${SEARXNG_CONTACT_URL:?SEARXNG_CONTACT_URL doit etre defini (exige par les moteurs wikipedia et wikidata)}"

# Interpreteurs. Aucun n'est suppose installe a un emplacement precis : le
# defaut est le python3 du PATH, a surcharger pour pointer vers les venvs.
WEBTOOLS_API_PYTHON="${WEBTOOLS_API_PYTHON:-python3}"
WEBTOOLS_MCP_PYTHON="${WEBTOOLS_MCP_PYTHON:-python3}"
SEARXNG_PYTHON="${SEARXNG_PYTHON:-$HOME/work/projects/searxng-venv/bin/python}"

# Configuration SearXNG : fichier versionne, source unique.
export SEARXNG_SETTINGS_PATH="${SEARXNG_SETTINGS_PATH:-$PWD/docker/searxng/settings.yml}"
export SEARXNG_PORT="${SEARXNG_PORT:-8081}"
export SEARXNG_BIND_ADDRESS="${SEARXNG_BIND_ADDRESS:-127.0.0.1}"
export SEARXNG_BASE_URL="${SEARXNG_BASE_URL:-http://localhost:${SEARXNG_PORT}}"

# URL de l'API pour le serveur MCP (client HTTP) ; la cle est WEBTOOLS_API_KEY.
export WEBTOOLS_API="${WEBTOOLS_API:-http://localhost:8000}"
export MCP_PUBLIC_URL="${MCP_PUBLIC_URL:-http://localhost:8090}"
export WEBTOOLS_STATE_DIR="${WEBTOOLS_STATE_DIR:-/tmp/webtools_state}"

# Les defauts du LLM (fournisseur, modele, URL) vivent dans app/core/config.py
# et ne sont pas repetes ici : seules les surcharges de l'environnement
# s'appliquent. Le SDK OpenAI lit OPENAI_BASE_URL ; sans surcharge, on la
# deduit de la configuration plutot que de la dupliquer.
if [ -z "${OPENAI_BASE_URL:-}" ]; then
  OPENAI_BASE_URL="$("$WEBTOOLS_API_PYTHON" -c 'from app.core.config import settings; print(settings.default_llm_base_url or "")' 2>/dev/null)"
  export OPENAI_BASE_URL
fi

# Un seul emplacement des navigateurs pour tous les process.
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"

WEBTOOLS_LOG_DIR="${WEBTOOLS_LOG_DIR:-/tmp/webtools_logs}"
mkdir -p "$WEBTOOLS_LOG_DIR" "$WEBTOOLS_STATE_DIR"

# Configuration de connexion lisible par l'exploitant (URL, cle d'API, jeton
# MCP), en droits 600 : l'identifiant affiche par Onyxia ouvre le pod, pas le
# serveur MCP. Ecrite seulement si WEBTOOLS_CONNEXION_README est defini.
if [ -n "${WEBTOOLS_CONNEXION_README:-}" ]; then
  "$WEBTOOLS_API_PYTHON" scripts/config_connexion.py "$WEBTOOLS_CONNEXION_README" \
    || echo "config_connexion.py a echoue (service lance quand meme)" >&2
fi

supervise() {
  local name="$1"
  shift
  while true; do
    echo "[$(date -Iseconds)] demarrage $name" >> "${WEBTOOLS_LOG_DIR}/${name}.log"
    "$@" >> "${WEBTOOLS_LOG_DIR}/${name}.log" 2>&1
    echo "[$(date -Iseconds)] $name arrete (code $?), relance dans 3s" >> "${WEBTOOLS_LOG_DIR}/${name}.log"
    sleep 3
  done
}

supervise searxng "$SEARXNG_PYTHON" -m searx.webapp &

sleep 8

supervise webtools xvfb-run -a "$WEBTOOLS_API_PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &

sleep 5

supervise mcp "$WEBTOOLS_MCP_PYTHON" mcp_server.py &

wait
