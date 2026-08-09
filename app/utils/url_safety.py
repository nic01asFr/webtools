"""
Protection SSRF : verifie qu'une URL cible ne pointe pas vers une ressource
reseau interne (loopback, plages privees RFC 1918, link-local, etc.) avant
toute tentative d'extraction.

Resout reellement le nom d'hote en IP avant de juger - un nom de domaine
public peut tres bien resoudre vers une IP interne (DNS rebinding), donc
verifier uniquement la chaine de caracteres de l'URL ne suffit pas.
"""

import ipaddress
import socket
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class UnsafeURLError(Exception):
    """Levee quand une URL cible une ressource reseau interne."""
    pass


def _is_private_or_reserved(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # IP illisible -> prudence, on refuse

    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_safe_url(url: str) -> None:
    """
    Leve UnsafeURLError si l'URL cible une ressource reseau interne.
    A appeler juste avant toute tentative reelle de connexion (Playwright,
    httpx, trafilatura...), pas seulement a la validation du schema Pydantic.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError(f"Schema non autorise: {parsed.scheme}")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeURLError("URL sans nom d'hote exploitable")

    # Resolution DNS reelle - le nom d'hote peut resoudre vers une IP interne
    # meme s'il a l'air d'un domaine public (DNS rebinding).
    try:
        resolved_ips = {info[4][0] for info in socket.getaddrinfo(hostname, None)}
    except socket.gaierror as e:
        raise UnsafeURLError(f"Resolution DNS impossible pour {hostname}: {e}")

    for ip in resolved_ips:
        if _is_private_or_reserved(ip):
            logger.warning(f"URL bloquee (SSRF) : {url} -> {hostname} resout vers {ip} (plage privee/reservee)")
            raise UnsafeURLError(
                f"L'URL cible une ressource reseau interne ({hostname} -> {ip})"
            )
