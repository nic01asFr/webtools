"""
Gestionnaire pour Playwright.
"""

import asyncio
import os
import sys
import subprocess
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.utils.url_safety import Resolver, UnsafeURLError, assert_safe_url_async

logger = logging.getLogger(__name__)

# Etat d'initialisation global. Un echec d'installation est memorise : sans
# cela, chaque requete relancerait un sous-processus de plusieurs minutes.
_playwright_initialized = False
_playwright_available = False
_playwright_error: str | None = None
_install_lock: asyncio.Lock | None = None


def browsers_path(env: dict[str, str] | None = None, platform: str | None = None) -> Path:
    """
    Dossier des navigateurs Playwright : la valeur de PLAYWRIGHT_BROWSERS_PATH
    si elle est definie, sinon l'emplacement par defaut du systeme.
    "0" designe, pour Playwright, le dossier de navigateurs du paquet.
    """
    env = os.environ if env is None else env
    platform = platform or sys.platform
    configured = env.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured == "0":
        import playwright
        return Path(playwright.__file__).parent / "driver" / "package" / ".local-browsers"
    if configured:
        return Path(configured)
    if platform.startswith("win"):
        base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ms-playwright"
    if platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def _has_chromium(path: Path) -> bool:
    try:
        return path.is_dir() and any(d.name.startswith("chromium") for d in path.iterdir())
    except OSError:
        return False


def _run_install() -> "subprocess.CompletedProcess[str]":
    """Installe Chromium (bloquant : a appeler depuis un thread)."""
    return subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
        timeout=300,
    )


async def ensure_playwright_installed() -> bool:
    """
    Vérifie et installe Playwright si nécessaire.

    Returns:
        True si Playwright est disponible et configuré
    """
    global _playwright_initialized, _playwright_available, _playwright_error, _install_lock

    if _playwright_initialized:
        return _playwright_available

    if _install_lock is None:
        _install_lock = asyncio.Lock()

    async with _install_lock:
        if _playwright_initialized:
            return _playwright_available

        try:
            logger.info("Vérification de l'installation de Playwright...")
            directory = browsers_path()

            if await asyncio.to_thread(_has_chromium, directory):
                logger.info("Navigateurs Playwright trouvés dans %s", directory)
                _playwright_available = True
            else:
                logger.info("Installation de Playwright (cible : %s)...", directory)
                result = await asyncio.to_thread(_run_install)
                if result.returncode == 0:
                    logger.info("Playwright installé avec succès")
                    _playwright_available = True
                else:
                    _playwright_error = (result.stderr or "")[-500:]
                    logger.error("Échec de l'installation de Playwright: %s", _playwright_error)
                    _playwright_available = False
        except Exception as e:
            _playwright_error = str(e)
            logger.error("Erreur lors de la configuration de Playwright: %s", e)
            _playwright_available = False

        # Succes comme echec sont memorises : pas de nouvelle tentative.
        _playwright_initialized = True

    return _playwright_available


def is_playwright_available() -> bool:
    """
    Vérifie si Playwright est disponible.

    Returns:
        True si Playwright est disponible
    """
    return _playwright_available


def reset_playwright_state() -> None:
    """Remet l'etat memorise a zero (tests)."""
    global _playwright_initialized, _playwright_available, _playwright_error, _install_lock
    _playwright_initialized = False
    _playwright_available = False
    _playwright_error = None
    _install_lock = None


# Schemas locaux au navigateur : ils ne produisent aucune requete reseau.
_LOCAL_SCHEMES = ("data", "blob", "about")


async def install_ssrf_route(
    page: Any, resolver: Resolver | None = None
) -> list[str]:
    """
    Filtre toutes les requetes de la page (navigation, redirections,
    sous-ressources, XHR) : celles qui visent une adresse interne sont
    abandonnees. Retourne la liste (alimentee au fil de l'eau) des URL
    refusees, pour que l'appelant puisse expliquer un echec de navigation.
    """
    blocked: list[str] = []

    async def _handler(route: Any, request: Any) -> None:
        url = request.url
        try:
            local = urlparse(url).scheme in _LOCAL_SCHEMES
        except ValueError:
            local = False
        if local:
            await route.continue_()
            return
        try:
            await assert_safe_url_async(url, resolver)
        except UnsafeURLError as e:
            logger.warning("Requete navigateur refusee (SSRF): %s (%s)", url, e)
            blocked.append(url)
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    await page.route("**/*", _handler)
    return blocked


