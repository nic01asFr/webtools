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

# Installer les dépendances Python
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Installer les dépendances système de Playwright (doit être root)
RUN python -m playwright install-deps chromium

# Passer à l'utilisateur non-root
USER webextract

# Installer Playwright en tant qu'utilisateur webextract
RUN python -m playwright install chromium

# Créer les liens symboliques vers les binaires Playwright (workaround pour le path)
# Playwright cherche dans /usr/local/lib mais les binaires sont dans ~/.cache
USER root
RUN mkdir -p /usr/local/lib/python3.11/site-packages/playwright/driver/package/.local-browsers && \
    ln -sf /home/webextract/.cache/ms-playwright/chromium-1187 /usr/local/lib/python3.11/site-packages/playwright/driver/package/.local-browsers/ && \
    ln -sf /home/webextract/.cache/ms-playwright/chromium_headless_shell-1187 /usr/local/lib/python3.11/site-packages/playwright/driver/package/.local-browsers/
USER webextract

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
