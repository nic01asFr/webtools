"""
Protection SSRF : verifie qu'une URL cible ne pointe pas vers une ressource
reseau interne (loopback, plages privees RFC 1918, CGNAT, link-local, 6to4,
etc.) avant une tentative d'extraction.

Le nom d'hote est resolu en IP avant de juger : un nom de domaine public peut
resoudre vers une IP interne, donc verifier la seule chaine de l'URL ne suffit
pas.

Limite connue : la verification et la connexion reelle font chacune leur
propre resolution DNS. Un serveur DNS hostile qui change sa reponse entre les
deux (DNS rebinding a fenetre courte) n'est pas couvert ; il faudrait
connecter l'IP deja validee.

Points d'application :
- `assert_safe_url` / `assert_safe_url_async` : une URL donnee ;
- `safe_request_hooks` : crochet httpx qui revalide chaque requete, y compris
  chaque saut de redirection ;
- le filtrage des sous-requetes du navigateur est dans
  `app/core/browser/playwright_manager.py` (`install_ssrf_route`).
"""

import asyncio
import ipaddress
import logging
import socket
from typing import Any
from collections.abc import Callable, Iterable
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Resolveur : nom d'hote -> adresses IP (chaines). Injectable pour les tests.
Resolver = Callable[[str], Iterable[str]]


class UnsafeURLError(Exception):
    """Levee quand une URL cible une ressource reseau interne ou est invalide."""
    pass


# Plages que `ipaddress` ne classe pas toutes comme privees selon la version.
_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "100.64.0.0/10",   # CGNAT (RFC 6598)
        "192.0.0.0/24",    # protocoles IETF
        "198.18.0.0/15",   # tests de performance
        "2002::/16",       # 6to4 : encapsule une IPv4 arbitraire
        "64:ff9b::/96",    # NAT64 : idem
        "64:ff9b:1::/48",  # NAT64 local
    )
)


def _is_private_or_reserved(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str.split("%", 1)[0])
    except ValueError:
        return True  # IP illisible -> prudence, on refuse

    # IPv4 encapsulee dans une IPv6 (::ffff:10.0.0.1) : on juge l'IPv4.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped

    if any(ip in net for net in _BLOCKED_NETWORKS if net.version == ip.version):
        return True

    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _default_resolver(hostname: str) -> set[str]:
    return {info[4][0] for info in socket.getaddrinfo(hostname, None)}


def _extract_hostname(url: str) -> str:
    """Valide schema et nom d'hote ; toute URL mal formee devient UnsafeURLError."""
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("URL vide ou invalide")
    try:
        parsed = urlparse(url.strip())
        hostname = parsed.hostname
        _ = parsed.port  # leve ValueError si le port est invalide
    except ValueError as e:
        raise UnsafeURLError(f"URL mal formee: {e}") from e

    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError(f"Schema non autorise: {parsed.scheme}")
    if not hostname:
        raise UnsafeURLError("URL sans nom d'hote exploitable")
    return hostname


def _check_resolved(url: str, hostname: str, resolved_ips: Iterable[str]) -> None:
    ips = list(resolved_ips)
    if not ips:
        raise UnsafeURLError(f"Resolution DNS vide pour {hostname}")
    for ip in ips:
        if _is_private_or_reserved(ip):
            logger.warning(
                "URL bloquee (SSRF) : %s -> %s resout vers %s (plage privee/reservee)",
                url, hostname, ip,
            )
            raise UnsafeURLError(
                f"L'URL cible une ressource reseau interne ({hostname} -> {ip})"
            )


def assert_safe_url(url: str, resolver: Resolver | None = None) -> None:
    """
    Leve UnsafeURLError si l'URL cible une ressource reseau interne ou est
    mal formee. Version synchrone : bloque pendant la resolution DNS, a ne pas
    appeler depuis la boucle d'evenements (utiliser `assert_safe_url_async`).
    """
    hostname = _extract_hostname(url)
    try:
        resolved = (resolver or _default_resolver)(hostname)
    except (socket.gaierror, UnicodeError, OSError) as e:
        raise UnsafeURLError(f"Resolution DNS impossible pour {hostname}: {e}") from e
    _check_resolved(url, hostname, resolved)


async def assert_safe_url_async(url: str, resolver: Resolver | None = None) -> None:
    """Equivalent asynchrone : la resolution DNS part dans un thread."""
    hostname = _extract_hostname(url)
    try:
        resolved = await asyncio.to_thread(lambda: list((resolver or _default_resolver)(hostname)))
    except (socket.gaierror, UnicodeError, OSError) as e:
        raise UnsafeURLError(f"Resolution DNS impossible pour {hostname}: {e}") from e
    _check_resolved(url, hostname, resolved)


def safe_request_hooks(resolver: Resolver | None = None) -> dict[str, list[Any]]:
    """
    `event_hooks` pour httpx.AsyncClient : chaque requete envoyee, y compris
    apres une redirection, est revalidee avant l'envoi. Une adresse interne
    leve UnsafeURLError depuis l'appel du client.
    """
    async def _check_request(request: httpx.Request) -> None:
        await assert_safe_url_async(str(request.url), resolver)

    return {"request": [_check_request]}
