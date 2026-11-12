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


async def _launch_probe() -> None:
    """Lance puis ferme Chromium en mode headless ; leve si le lancement echoue.

    La presence d'un dossier chromium-* ne suffit pas : chaque version de
    Playwright attend sa propre revision du navigateur (et, en headless, un
    chromium_headless_shell distinct). Verifie sur un poste ou seules les
    revisions 1217 et 1223 etaient presentes alors que Playwright 1.65
    attendait 1254 : la sante annoncait Playwright disponible et toutes les
    extractions echouaient ("Executable doesn't exist").
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        await browser.close()


async def _browser_launches() -> tuple[bool, str | None]:
    try:
        await _launch_probe()
        return True, None
    except Exception as e:  # noqa: BLE001 - la cause est rendue a l'appelant
        return False, f"{type(e).__name__}: {str(e)[:300]}"


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

            # Disponible = le navigateur se lance reellement. Sinon une seule
            # installation est tentee, puis un second essai de lancement.
            launched, cause = (
                await _browser_launches()
                if await asyncio.to_thread(_has_chromium, directory)
                else (False, "aucun navigateur Chromium dans " + str(directory))
            )
            if launched:
                logger.info("Chromium se lance (navigateurs dans %s)", directory)
                _playwright_available = True
            else:
                logger.info("Chromium indisponible (%s) ; installation, cible %s", cause, directory)
                result = await asyncio.to_thread(_run_install)
                if result.returncode != 0:
                    _playwright_error = (result.stderr or "")[-500:]
                    logger.error("Échec de l'installation de Playwright: %s", _playwright_error)
                    _playwright_available = False
                else:
                    launched, cause = await _browser_launches()
                    _playwright_available = launched
                    if not launched:
                        _playwright_error = cause
                        logger.error("Chromium installe mais ne se lance pas : %s", cause)
                    else:
                        logger.info("Playwright installé avec succès")
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


def playwright_error() -> str | None:
    """Cause memorisee de l'indisponibilite de Playwright (None si disponible)."""
    return _playwright_error


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


