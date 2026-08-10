#!/bin/bash
# Supervision legere (sans systemd/root) des 3 services webtools :
# SearXNG (recherche), API FastAPI (extraction/recherche approfondie),
# serveur MCP (exposition OAuth pour Claude Desktop/Code). Chaque service
# tourne dans une boucle qui le relance automatiquement s'il crashe ou si
# le pod redemarre.

set -u
cd "$(dirname "$0")"

# Secrets : lus depuis l'environnement, JAMAIS ecrits en dur ici (ce fichier
# est versionne dans un depot public). Definir DEFAULT_LLM_API_KEY avant de
# lancer ce script, ou le sourcer depuis un .env non versionne.
export DEFAULT_LLM_API_KEY="${DEFAULT_LLM_API_KEY:?DEFAULT_LLM_API_KEY doit etre defini avant de lancer ce script}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-$DEFAULT_LLM_API_KEY}"

# Cle d'API protegeant l'API REST publique (en-tete X-API-Key). Sans elle,
# l'API est ouverte a quiconque connait l'URL : consommation du quota LLM et
# usage du service comme proxy de scraping anonymisant.
export WEBTOOLS_API_KEY="${WEBTOOLS_API_KEY:?WEBTOOLS_API_KEY doit etre defini avant de lancer ce script}"

# Configuration non secrete
export OPENAI_BASE_URL="https://llm.lab.sspcloud.fr/api"
export DEFAULT_LLM_PROVIDER="openai"
export DEFAULT_LLM_MODEL="qwen3-6-35b-moe"
export DEFAULT_LLM_BASE_URL="https://llm.lab.sspcloud.fr/api"
export SEARXNG_BASE_URL="http://localhost:8081"
export MCP_ACCESS_TOKEN="${MCP_ACCESS_TOKEN:?MCP_ACCESS_TOKEN doit etre defini avant de lancer ce script}"
export MCP_PUBLIC_URL="${MCP_PUBLIC_URL:-http://localhost:8090}"

# Chromium est installe a /home/onyxia/.cache/ms-playwright (emplacement
# standard), mais mcp_server.py (lance via un python3 different de celui
# d'uvicorn) le cherchait ailleurs par defaut - fixe explicitement pour
# que tous les process s'accordent sur le meme emplacement reel.
export PLAYWRIGHT_BROWSERS_PATH="/home/onyxia/.cache/ms-playwright"

mkdir -p /tmp/webtools_logs

supervise() {
  local name="$1"
  shift
  while true; do
    echo "[$(date -Iseconds)] demarrage $name" >> "/tmp/webtools_logs/${name}.log"
    "$@" >> "/tmp/webtools_logs/${name}.log" 2>&1
    echo "[$(date -Iseconds)] $name arrete (code $?), relance dans 3s" >> "/tmp/webtools_logs/${name}.log"
    sleep 3
  done
}

export SEARXNG_PORT=8081
export SEARXNG_BIND_ADDRESS=127.0.0.1
supervise searxng /home/onyxia/work/projects/searxng-venv/bin/python -m searx.webapp &

sleep 8

supervise webtools xvfb-run -a uvicorn app.main:app --host 0.0.0.0 --port 8000 &

sleep 5

supervise mcp xvfb-run -a python3 mcp_server.py &

wait
