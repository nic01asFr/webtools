"""
Client LLM pour Anthropic API (Claude).
"""

import httpx
from anthropic import AsyncAnthropic
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel

from .base import BaseLLMClient, LLMClientError, LLMEmptyContentError


class AnthropicLLMClient(BaseLLMClient):
    """
    Client pour l'API Anthropic (Claude 3, Claude 3.5, etc.)
    """

    supports_structured = False
    supports_embeddings = False
    supports_vision = False

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        model: str = "claude-3-5-sonnet-20241022",
        transport: httpx.AsyncBaseTransport | None = None
    ):
        """
        Initialise le client Anthropic.

        Args:
            api_key: Clé API Anthropic
            base_url: URL de base personnalisée (optionnel)
            model: Nom du modèle Claude à utiliser
            transport: transport httpx injecte (tests sans reseau)
        """
        super().__init__(api_key=api_key, base_url=base_url, model=model)

        # Client Anthropic asynchrone
        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url

        if transport is not None:
            kwargs["http_client"] = httpx.AsyncClient(transport=transport)

        self.client = AsyncAnthropic(**kwargs)

    async def generate(
        self,
        messages: list[dict[str, str]],
        **kwargs
    ) -> str:
        """
        Génère une réponse avec Claude.

        Args:
            messages: Liste de messages au format {"role": "...", "content": "..."}
            **kwargs: Paramètres additionnels (temperature, max_tokens, etc.)

        Returns:
            Contenu de la réponse générée
        """
        self._record_finish_reason(None)
        try:
            # Anthropic nécessite un message système séparé
            system_message = None
            filtered_messages = []

            for msg in messages:
                if msg["role"] == "system":
                    system_message = msg["content"]
                else:
                    filtered_messages.append(msg)

            # Paramètres pour l'API Anthropic
            api_params = {
                "model": self.model,
                "messages": filtered_messages,
                "max_tokens": kwargs.pop("max_tokens", 4096),
                **kwargs
            }

            if system_message:
                api_params["system"] = system_message

            response = await self.client.messages.create(**api_params)

            # stop_reason "max_tokens" est normalise en "length" (contrat commun)
            stop_reason = getattr(response, "stop_reason", None)
            self._record_finish_reason("length" if stop_reason == "max_tokens" else stop_reason)

            text = "".join(
                block.text for block in (response.content or [])
                if getattr(block, "type", None) == "text" and getattr(block, "text", None)
            )
            if not text.strip():
                raise LLMEmptyContentError(
                    f"Contenu vide rendu par Anthropic (stop_reason={stop_reason})"
                )
            return text

        except LLMClientError:
            raise
        except Exception as e:
            raise LLMClientError(f"Erreur lors de la génération Anthropic: {e!s}")

    def get_langchain_wrapper(self) -> BaseChatModel:
        """
        Retourne un wrapper LangChain pour browser-use.

        Anthropic est supporté par LangChain via ChatAnthropic.

        Returns:
            ChatAnthropic compatible avec browser-use
        """
        kwargs = {
            "api_key": self.api_key,
            "model": self.model
        }

        if self.base_url:
            kwargs["base_url"] = self.base_url

        return ChatAnthropic(**kwargs)

    async def _aclose(self) -> None:
        """Ferme le client Anthropic."""
        await self.client.close()
