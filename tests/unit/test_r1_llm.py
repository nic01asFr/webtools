"""Client LLM par defaut : configuration par settings, echeance totale."""

import asyncio

import httpx
import pytest
from app.core import llm
from app.core.config import settings
from app.core.llm import LLMFactory, close_llm_client, get_llm_client
from app.core.llm.albert import AlbertLLMClient
from app.core.llm.anthropic import AnthropicLLMClient
from app.core.llm.base import LLMTimeoutError

from .llm.helpers import anthropic_payload, chat_payload, json_transport

MESSAGES = [{"role": "user", "content": "bonjour"}]


@pytest.fixture(autouse=True)
def _isoler(monkeypatch: pytest.MonkeyPatch):
    llm._shared_clients.clear()
    monkeypatch.delenv("DEFAULT_LLM_TOTAL_TIMEOUT", raising=False)
    for champ, valeur in {
        "default_llm_provider": "openai",
        "default_llm_model": "m-defaut",
        "default_llm_api_key": "cle-defaut",
        "default_llm_base_url": "https://llm.defaut/api",
        "openai_api_key": "",
        "anthropic_api_key": "",
        "albert_api_key": "",
        "albert_api_url": "https://albert.test",
    }.items():
        monkeypatch.setattr(settings, champ, valeur)
    yield
    llm._shared_clients.clear()


def _capturer(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    appels: list[dict] = []
    reel = LLMFactory.create

    def faux(provider: str, **options):
        appels.append({"provider": provider, **options})
        return reel("openai", api_key="k", model="m", base_url="https://llm.test/api")

    monkeypatch.setattr(LLMFactory, "create", faux)
    return appels


async def test_get_llm_client_suit_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    appels = _capturer(monkeypatch)
    monkeypatch.setattr(settings, "default_llm_model", "autre-modele")
    monkeypatch.setattr(settings, "default_llm_base_url", "https://autre.url/api")
    monkeypatch.setattr(settings, "default_llm_api_key", "autre-cle")
    await get_llm_client()
    assert appels == [
        {
            "provider": "openai",
            "api_key": "autre-cle",
            "model": "autre-modele",
            "base_url": "https://autre.url/api",
        }
    ]
    await close_llm_client()


async def test_la_cle_suit_le_fournisseur(monkeypatch: pytest.MonkeyPatch) -> None:
    appels = _capturer(monkeypatch)
    monkeypatch.setattr(settings, "default_llm_provider", "albert")
    monkeypatch.setattr(settings, "albert_api_key", "cle-albert")
    await get_llm_client()
    assert appels[0]["api_key"] == "cle-albert"
    assert appels[0]["base_url"] == "https://albert.test"
    await close_llm_client()


async def test_sans_cle_leve_valueerror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "default_llm_api_key", "")
    with pytest.raises(ValueError):
        await get_llm_client()


async def test_le_gestionnaire_utilise_le_client_partage() -> None:
    from app.manager import ExtractorManager

    client = await get_llm_client()
    assert await ExtractorManager()._create_default_llm_client() is client
    await close_llm_client()


# --- Echeance totale des clients Albert et Anthropic -----------------------


def _transport_lent(corps: dict, delai: float, vus: list[bytes]) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        vus.append(request.content)
        await asyncio.sleep(delai)
        return httpx.Response(200, json=corps)

    return httpx.MockTransport(handler)


async def test_albert_applique_l_echeance_totale() -> None:
    transport = _transport_lent(chat_payload("x"), 5.0, [])
    client = AlbertLLMClient("k", "https://albert.test", "albert-large", transport=transport)
    client.total_timeout = 0.05
    with pytest.raises(LLMTimeoutError):
        await client.generate(MESSAGES)
    await client.force_close()


async def test_anthropic_applique_l_echeance_totale() -> None:
    transport = _transport_lent(anthropic_payload("x"), 5.0, [])
    client = AnthropicLLMClient("k", "https://claude.test", "claude-test", transport=transport)
    client.total_timeout = 0.05
    with pytest.raises(LLMTimeoutError):
        await client.generate(MESSAGES)
    await client.force_close()


async def test_albert_echeance_en_argument_non_transmise_a_l_api() -> None:
    vus: list[bytes] = []
    transport = _transport_lent(chat_payload("ok"), 0.0, vus)
    client = AlbertLLMClient("k", "https://albert.test", "albert-large", transport=transport)
    assert await client.generate(MESSAGES, total_timeout=5.0) == "ok"
    assert b"total_timeout" not in vus[0]
    await client.force_close()


async def test_echeance_n_efface_pas_la_raison_d_arret() -> None:
    transport = json_transport(chat_payload("coupe", "length"))
    client = AlbertLLMClient("k", "https://albert.test", "albert-large", transport=transport)
    client.total_timeout = 5.0
    result = await client.generate_result(MESSAGES, max_tokens=5)
    assert result.truncated is True
    await client.force_close()
