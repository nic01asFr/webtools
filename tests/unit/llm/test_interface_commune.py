"""Meme suite d'interface pour les trois clients (decision D5)."""

import pytest
from app.core.llm.albert import AlbertLLMClient
from app.core.llm.anthropic import AnthropicLLMClient
from app.core.llm.base import BaseLLMClient, LLMClientError, LLMEmptyContentError
from app.core.llm.openai import OpenAILLMClient

from .helpers import anthropic_payload, chat_payload, json_transport

MESSAGES = [{"role": "user", "content": "bonjour"}]
PROVIDERS = ["openai", "albert", "anthropic"]


def build(provider: str, text, truncated: bool = False) -> BaseLLMClient:
    if provider == "openai":
        transport = json_transport(chat_payload(text, "length" if truncated else "stop"))
        return OpenAILLMClient(
            "k", "https://llm.test/api", "gpt-test", max_retries=0, transport=transport
        )
    if provider == "albert":
        transport = json_transport(chat_payload(text, "length" if truncated else "stop"))
        return AlbertLLMClient("k", "https://albert.test", "albert-large", transport=transport)
    transport = json_transport(
        anthropic_payload(text or "", "max_tokens" if truncated else "end_turn")
    )
    return AnthropicLLMClient("k", "https://claude.test", "claude-test", transport=transport)


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_generate_nominal(provider: str) -> None:
    client = build(provider, "reponse")
    assert await client.generate(MESSAGES) == "reponse"
    assert client.last_truncated is False
    await client.force_close()


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_contenu_vide_leve_llm_client_error(provider: str) -> None:
    client = build(provider, None)
    with pytest.raises(LLMClientError) as info:
        await client.generate(MESSAGES)
    assert isinstance(info.value, LLMEmptyContentError)
    await client.force_close()


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_sortie_coupee_signalee_sans_exception(provider: str) -> None:
    client = build(provider, "debut de texte", truncated=True)
    result = await client.generate_result(MESSAGES, max_tokens=5)
    assert result.text == "debut de texte"
    assert result.truncated is True
    assert client.last_finish_reason == "length"
    await client.force_close()


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_close_sans_effet_si_partagee(provider: str) -> None:
    client = build(provider, "x")
    client._shared = True
    await client.close()
    assert await client.generate(MESSAGES) == "x"
    await client.force_close()


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_close_ferme_une_instance_non_partagee(provider: str) -> None:
    client = build(provider, "x")
    await client.close()


@pytest.mark.parametrize(
    "provider,structured,embeddings,vision",
    [
        ("openai", True, True, True),
        ("albert", False, False, True),
        ("anthropic", False, False, False),
    ],
)
def test_capacites_coherentes_avec_les_methodes(provider, structured, embeddings, vision) -> None:
    client = build(provider, "x")
    assert client.supports_structured is structured
    assert client.supports_embeddings is embeddings
    assert client.supports_vision is vision
    assert hasattr(client, "generate_structured") is structured
    assert hasattr(client, "embed") is embeddings
    assert hasattr(client, "generate_with_vision") is vision
