"""
Gestionnaire pour Playwright.
"""

import os
import sys
import subprocess
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# État d'initialisation global
_playwright_initialized = False
_playwright_available = False


async def ensure_playwright_installed() -> bool:
    """
    Vérifie et installe Playwright si nécessaire.

    Returns:
        True si Playwright est disponible et configuré
    """
    global _playwright_initialized, _playwright_available

    if _playwright_initialized:
        return _playwright_available

    try:
        # Configurer les variables d'environnement pour le mode headless
        os.environ["PLAYWRIGHT_HEADLESS"] = "true"
        os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "0"
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"

        # Forcer le mode headless si DISPLAY n'est pas défini
        if "DISPLAY" not in os.environ or os.environ.get("DISPLAY") == "":
            os.environ["DISPLAY"] = ""

        logger.info("Vérification de l'installation de Playwright...")

        # Vérifier si le répertoire des binaires Playwright existe
        playwright_dir = os.path.expanduser("~/.cache/ms-playwright")
        chromium_dirs = [d for d in os.listdir(playwright_dir) if d.startswith('chromium-')] if os.path.exists(playwright_dir) else []

        if chromium_dirs:
            logger.info(f"Répertoire(s) Playwright trouvé(s): {chromium_dirs}")
            # Playwright est installé, on le marque comme disponible
            # Les erreurs éventuelles seront gérées au runtime
            _playwright_initialized = True
            _playwright_available = True
            logger.info("Playwright marqué comme disponible")

        else:
            # Installer Playwright
            logger.info("Installation de Playwright...")

            install_cmd = [sys.executable, "-m", "playwright", "install", "chromium"]

            result = subprocess.run(
                install_cmd,
                capture_output=True,
                text=True,
                check=False,
                env=os.environ.copy(),
                timeout=300
            )

            if result.returncode == 0:
                logger.info("Playwright installé avec succès")
                _playwright_initialized = True
                _playwright_available = True
            else:
                logger.error(f"Échec de l'installation de Playwright: {result.stderr}")

    except Exception as e:
        logger.error(f"Erreur lors de la configuration de Playwright: {e}")
        _playwright_initialized = True
        _playwright_available = False

    return _playwright_available


def is_playwright_available() -> bool:
    """
    Vérifie si Playwright est disponible.

    Returns:
        True si Playwright est disponible
    """
    return _playwright_available
