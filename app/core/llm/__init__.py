"""LLM clients module"""

import os

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


async def get_llm_client() -> BaseLLMClient:
    """
    Rend le client LLM partage du processus, configure par les variables
    d'environnement. Deux appels avec la meme configuration rendent le meme
    objet ; close() est sans effet dessus (voir close_llm_client).

    Returns:
        Instance de BaseLLMClient configurée
    """
    provider = os.getenv("DEFAULT_LLM_PROVIDER", "openai")
    model = os.getenv("DEFAULT_LLM_MODEL", "qwen3-6-35b-moe")
    api_key = os.getenv("DEFAULT_LLM_API_KEY", "")
    base_url = os.getenv("DEFAULT_LLM_BASE_URL", "https://llm.lab.sspcloud.fr/api")

    key = (provider.lower(), model, base_url, api_key)
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
]
