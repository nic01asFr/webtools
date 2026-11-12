"""
Authentification par cle d'API pour les endpoints publics.

Le service est expose publiquement (Traefik/ingress) et consomme des appels
LLM reels : sans authentification, quiconque connait l'URL peut epuiser le
quota, et /extract fait tourner Chromium sur une URL arbitraire (le service
devient un proxy de scraping anonymisant, imputable a l'IP de l'hote).

Le serveur MCP a deja sa propre protection (OAuth + cle partagee) ; ceci
couvre l'API REST, qui ne l'avait pas.
"""

import hmac
import logging
import os

from fastapi import Header, HTTPException, status

logger = logging.getLogger(__name__)

ANONYMOUS_ENV = "WEBTOOLS_ALLOW_ANONYMOUS"


class AuthConfigurationError(RuntimeError):
    """Le service n'a pas de cle d'API et le mode anonyme n'est pas demande."""


def _api_key() -> str:
    # Lu a chaque appel : la valeur ne doit pas etre figee a l'import.
    return os.environ.get("WEBTOOLS_API_KEY", "")


def _anonymous_allowed() -> bool:
    return os.environ.get(ANONYMOUS_ENV, "").strip() == "1"


def verify_auth_configuration() -> None:
    """
    A appeler au demarrage du service (pas a l'import du module, pour que
    les tests puissent importer l'application sans cle).

    Sans WEBTOOLS_API_KEY, refuse de demarrer sauf si WEBTOOLS_ALLOW_ANONYMOUS=1
    est pose explicitement.
    """
    if _api_key():
        return
    if _anonymous_allowed():
        logger.warning(
            "WEBTOOLS_API_KEY non defini et %s=1 : l'API REST est ACCESSIBLE "
            "SANS AUTHENTIFICATION.",
            ANONYMOUS_ENV,
        )
        return
    raise AuthConfigurationError(
        "WEBTOOLS_API_KEY non defini : demarrage refuse. Definir la cle, ou "
        f"poser {ANONYMOUS_ENV}=1 pour accepter un service sans authentification."
    )


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """
    Verifie l'en-tete X-API-Key. A brancher en dependance de router pour
    couvrir tous ses endpoints d'un coup.
    """
    expected = _api_key()
    if not expected and _anonymous_allowed():
        return

    # Comparaison sur des octets : compare_digest leve TypeError sur une
    # chaine non ASCII, ce qui donnait une erreur 500 au lieu d'un 401.
    # Sans cle configuree et sans mode anonyme, tout est refuse.
    valid = (
        bool(expected)
        and bool(x_api_key)
        and hmac.compare_digest(x_api_key.encode("utf-8"), expected.encode("utf-8"))
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cle d'API invalide ou absente (en-tete X-API-Key requis)",
            headers={"WWW-Authenticate": "ApiKey"},
        )
