"""LLM clients module"""

import os
from .factory import LLMFactory
from .base import BaseLLMClient


async def get_llm_client() -> BaseLLMClient:
    """
    Crée un client LLM à partir des variables d'environnement.

    Returns:
        Instance de BaseLLMClient configurée
    """
    provider = os.getenv("DEFAULT_LLM_PROVIDER", "openai")
    model = os.getenv("DEFAULT_LLM_MODEL", "qwen3-6-35b-moe")
    api_key = os.getenv("DEFAULT_LLM_API_KEY", "")
    base_url = os.getenv("DEFAULT_LLM_BASE_URL", "https://llm.lab.sspcloud.fr/api")

    return LLMFactory.create(
        provider=provider,
        api_key=api_key,
        model=model,
        base_url=base_url
    )


__all__ = ["get_llm_client", "LLMFactory", "BaseLLMClient"]
