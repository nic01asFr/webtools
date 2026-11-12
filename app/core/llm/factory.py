"""
Factory pour créer des clients LLM selon le provider.
"""

from collections.abc import Callable
from typing import Any, ClassVar

from .albert import AlbertLLMClient
from .anthropic import AnthropicLLMClient
from .base import BaseLLMClient, LLMClientError
from .openai import OpenAILLMClient

# Constructeur de fournisseur : (api_key, model, base_url, **options) -> client
ProviderBuilder = Callable[..., BaseLLMClient]


def _build_openai(api_key: str, model: str, base_url: str | None, **options: Any) -> BaseLLMClient:
    return OpenAILLMClient(api_key=api_key, model=model, base_url=base_url, **options)


def _build_anthropic(api_key: str, model: str, base_url: str | None, **options: Any) -> BaseLLMClient:
    return AnthropicLLMClient(api_key=api_key, model=model, base_url=base_url, **options)


def _build_albert(api_key: str, model: str, base_url: str | None, **options: Any) -> BaseLLMClient:
    # Albert necessite une base_url
    return AlbertLLMClient(
        api_key=api_key,
        model=model,
        base_url=base_url or "https://albert.api.etalab.gouv.fr",
        **options,
    )


class LLMFactory:
    """Factory pour créer des instances de clients LLM."""

    _providers: ClassVar[dict[str, ProviderBuilder]] = {
        "openai": _build_openai,
        "anthropic": _build_anthropic,
        "albert": _build_albert,
    }

    @classmethod
    def register(cls, name: str, builder: ProviderBuilder) -> None:
        """Enregistre (ou remplace) un fournisseur par nom, par exemple un faux dans un test."""
        cls._providers[name.lower()] = builder

    @classmethod
    def unregister(cls, name: str) -> None:
        """Retire un fournisseur enregistre."""
        cls._providers.pop(name.lower(), None)

    @classmethod
    def providers(cls) -> list[str]:
        return sorted(cls._providers)

    @classmethod
    def create(
        cls,
        provider: str,
        api_key: str,
        model: str,
        base_url: str | None = None,
        **options: Any
    ) -> BaseLLMClient:
        """
        Crée un client LLM selon le provider spécifié.

        Args:
            provider: Nom du provider ("openai", "anthropic", "albert" ou enregistre)
            api_key: Clé API pour le provider
            model: Nom du modèle à utiliser
            base_url: URL de base optionnelle
            **options: options transmises au constructeur (ex. transport)

        Returns:
            Nouvelle instance de BaseLLMClient (non partagee)

        Raises:
            LLMClientError: Si le provider n'est pas supporté
        """
        builder = cls._providers.get(provider.lower())
        if builder is None:
            raise LLMClientError(
                f"Provider '{provider}' non supporté. "
                f"Providers supportés: {', '.join(cls.providers())}"
            )
        return builder(api_key=api_key, model=model, base_url=base_url, **options)
