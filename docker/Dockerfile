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

# Copier les fichiers de dépendances et le code
COPY --chown=webextract:webextract pyproject.toml ./
COPY --chown=webextract:webextract app ./app
# mcp_server.py etait absent de l'image : elle ne pouvait donc pas servir le
# MCP, d'ou deux modes de deploiement divergents (Docker incomplet vs
# start_all.sh complet). Les deux voies convergent desormais.
COPY --chown=webextract:webextract mcp_server.py ./
COPY --chown=webextract:webextract start_all.sh ./

# Installer les dépendances Python
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Installer les dépendances système de Playwright (doit être root)
RUN python -m playwright install-deps chromium

# Emplacement unique et explicite des navigateurs, partage par tous les
# process (uvicorn ET mcp_server.py, qui peuvent tourner sous des
# interpreteurs differents).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN mkdir -p /ms-playwright && chown webextract:webextract /ms-playwright

# Passer à l'utilisateur non-root
USER webextract

# Installer Playwright en tant qu'utilisateur webextract
RUN python -m playwright install chromium

# PLAYWRIGHT_BROWSERS_PATH plutot que des symlinks au numero de build code
# en dur (chromium-1187) : ce numero casse a la prochaine montee de
# Playwright, avec une erreur qui ne ressemble pas a sa cause. start_all.sh
# fait deja ce choix - les deux voies convergent.

# Variables d'environnement par défaut
ENV PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO

# Exposer le port de l'API
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Commande de démarrage
# On démarre directement uvicorn, Playwright gère le headless tout seul
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
