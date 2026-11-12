"""LLM clients module"""

import os

from app.core.config import settings

from .base import (
    BaseLLMClient,
    GenerationResult,
    LLMClientError,
    LLMEmptyContentError,
    LLMSchemaError,
    LLMTimeoutError,
    LLMTruncatedError,
)
from .factory import LLMFactory

# Instances partagees, une par configuration (fournisseur, modele, URL, cle).
_shared_clients: dict[tuple[str, str, str, str], BaseLLMClient] = {}


def _parse_total_timeout(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def resolve_llm_config() -> tuple[str, str, str, str | None]:
    """
    Fournisseur, modele, cle d'API et URL du LLM par defaut, lus dans
    `settings` (app/core/config.py), source unique. La cle suit le
    fournisseur (OPENAI_API_KEY, ANTHROPIC_API_KEY, ALBERT_API_KEY), puis
    DEFAULT_LLM_API_KEY ; Albert a sa propre URL.

    Returns:
        (fournisseur, modele, cle, url)
    """
    provider = settings.default_llm_provider
    base_url = settings.default_llm_base_url
    specifique = {
        "openai": settings.openai_api_key,
        "anthropic": settings.anthropic_api_key,
        "albert": settings.albert_api_key,
    }.get(provider.lower(), "")
    if provider.lower() == "albert":
        base_url = settings.albert_api_url
    api_key = specifique or settings.default_llm_api_key
    return provider, settings.default_llm_model, api_key, base_url


async def get_llm_client() -> BaseLLMClient:
    """
    Rend le client LLM partage du processus, configure par `settings`. Deux
    appels avec la meme configuration rendent le meme objet ; close() est
    sans effet dessus (voir close_llm_client).

    Returns:
        Instance de BaseLLMClient configurée

    Raises:
        ValueError: si aucune cle d'API n'est configuree pour le fournisseur
    """
    provider, model, api_key, base_url = resolve_llm_config()
    if not api_key:
        raise ValueError(
            f"Clé API manquante pour le provider {provider}. "
            f"Configurez la variable d'environnement appropriée."
        )

    key = (provider.lower(), model, base_url or "", api_key)
    client = _shared_clients.get(key)
    if client is None:
        client = LLMFactory.create(
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=base_url
        )
        client.total_timeout = _parse_total_timeout(os.getenv("DEFAULT_LLM_TOTAL_TIMEOUT"))
        client._shared = True
        _shared_clients[key] = client
    return client


async def close_llm_client() -> None:
    """Ferme reellement les clients partages (a appeler a l'arret du service)."""
    clients = list(_shared_clients.values())
    _shared_clients.clear()
    for client in clients:
        await client.force_close()


__all__ = [
    "BaseLLMClient",
    "GenerationResult",
    "LLMClientError",
    "LLMEmptyContentError",
    "LLMFactory",
    "LLMSchemaError",
    "LLMTimeoutError",
    "LLMTruncatedError",
    "close_llm_client",
    "get_llm_client",
    "resolve_llm_config",
]
