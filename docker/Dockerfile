# Dockerfile pour WebExtract Service
FROM python:3.11-slim

LABEL maintainer="WebExtract Team"
LABEL description="Service autonome d'extraction de contenu web"

# Installer les dépendances système nécessaires pour Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Dépendances Playwright/Chromium
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    # X virtual framebuffer pour mode headless
    xvfb \
    xauth \
    # Outils de debug (optionnel)
    curl \
    && rm -rf /var/lib/apt/lists/*

# Créer un utilisateur non-root
RUN useradd -m -u 1000 webextract && \
    mkdir -p /app && \
    chown -R webextract:webextract /app

# Définir le répertoire de travail
WORKDIR /app

# Deux environnements Python dans une seule image : les dependances de l'API
# (browser-use impose pydantic<2.11) et celles du serveur MCP (fastmcp exige
# pydantic>=2.12) ne se resolvent pas ensemble (C81). Une image unique garde
# un seul build et un seul tag ; le service choisit son interpreteur :
#   API : /opt/venv-api/bin/python    (requirements.lock)
#   MCP : /opt/venv-mcp/bin/python    (requirements-mcp.lock)
# Les noms correspondent a WEBTOOLS_API_PYTHON et WEBTOOLS_MCP_PYTHON de
# start_all.sh. Pour regenerer les verrous : voir GETTING_STARTED.md.
COPY requirements.lock requirements-mcp.lock ./
RUN python -m venv /opt/venv-api && \
    /opt/venv-api/bin/pip install --no-cache-dir -r requirements.lock && \
    python -m venv /opt/venv-mcp && \
    /opt/venv-mcp/bin/pip install --no-cache-dir -r requirements-mcp.lock

# Le code est lu depuis /app (WORKDIR) : aucune installation du paquet.
COPY --chown=webextract:webextract pyproject.toml ./
COPY --chown=webextract:webextract app ./app
COPY --chown=webextract:webextract mcp_server.py start_all.sh ./

# Installer les dependances systeme de Playwright (doit etre root)
RUN /opt/venv-api/bin/python -m playwright install-deps chromium

# Emplacement unique et explicite des navigateurs (seule l'API en a besoin).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN mkdir -p /ms-playwright && chown webextract:webextract /ms-playwright

# Etat des taches de fond du serveur MCP (WEBTOOLS_STATE_DIR)
RUN mkdir -p /state && chown webextract:webextract /state

# Passer a l'utilisateur non-root
USER webextract

# Installer Chromium en tant qu'utilisateur webextract
RUN /opt/venv-api/bin/python -m playwright install chromium

# Variables d'environnement par défaut
ENV PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO \
    WEBTOOLS_API_PYTHON=/opt/venv-api/bin/python \
    WEBTOOLS_MCP_PYTHON=/opt/venv-mcp/bin/python

# Ports : API (8000) et serveur MCP (8090)
EXPOSE 8000 8090

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Commande par defaut : l'API. Le service webtools-mcp de docker-compose.yml
# remplace la commande par "/opt/venv-mcp/bin/python mcp_server.py".
# SearXNG est un conteneur a part (service searxng).
CMD ["/opt/venv-api/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
