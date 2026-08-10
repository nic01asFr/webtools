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

# Optionnel a dessein : si WEBTOOLS_API_KEY n'est pas defini, l'API reste
# ouverte (utile en developpement local et pour ne pas casser un deploiement
# existant du jour au lendemain). Un avertissement est emis au demarrage.
WEBTOOLS_API_KEY = os.environ.get("WEBTOOLS_API_KEY", "")

if not WEBTOOLS_API_KEY:
    logger.warning(
        "WEBTOOLS_API_KEY non defini : l'API REST est ACCESSIBLE SANS "
        "AUTHENTIFICATION. A definir imperativement pour tout deploiement "
        "expose publiquement."
    )


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """
    Verifie l'en-tete X-API-Key. A brancher en dependance de router pour
    couvrir tous ses endpoints d'un coup.
    """
    if not WEBTOOLS_API_KEY:
        return  # non configure : pas de verification (cf. avertissement au demarrage)

    if not x_api_key or not hmac.compare_digest(x_api_key, WEBTOOLS_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cle d'API invalide ou absente (en-tete X-API-Key requis)",
            headers={"WWW-Authenticate": "ApiKey"},
        )
