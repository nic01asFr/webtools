"""Client OpenAI : appels simules par httpx.MockTransport, sans reseau."""

import asyncio
import logging
import os

import httpx
import pytest
from app.core.llm.base import (
    LLMClientError,
    LLMEmptyContentError,
    LLMSchemaError,
    LLMTimeoutError,
    LLMTruncatedError,
)
from app.core.llm.openai import OpenAILLMClient

from .helpers import chat_payload, json_transport, request_body

MESSAGES = [{"role": "user", "content": "bonjour"}]
SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "integer"}},
    "required": ["a"],
    "additionalProperties": False,
}


def make_client(transport: httpx.MockTransport, **kwargs) -> OpenAILLMClient:
    kwargs.setdefault("max_retries", 0)
    return OpenAILLMClient(
        api_key="cle-test",
        base_url="https://llm.test/api",
        model=kwargs.pop("model", "gpt-test"),
        transport=transport,
        **kwargs,
    )


async def test_generate_nominal() -> None:
    client = make_client(json_transport(chat_payload("salut")))
    assert await client.generate(MESSAGES) == "salut"
    assert client.last_finish_reason == "stop"
    assert client.last_truncated is False
    await client.force_close()


@pytest.mark.parametrize("content", [None, "", "   "])
async def test_generate_contenu_vide_leve(content) -> None:
    client = make_client(json_transport(chat_payload(content)))
    with pytest.raises(LLMEmptyContentError) as info:
        await client.generate(MESSAGES)
    assert isinstance(info.value, LLMClientError)
    await client.force_close()


async def test_generate_coupe_par_max_tokens_signale_sans_lever() -> None:
    client = make_client(json_transport(chat_payload("texte presque complet", "length")))
    result = await client.generate_result(MESSAGES, max_tokens=5)
    assert result.text == "texte presque complet"
    assert result.truncated is True
    assert client.last_finish_reason == "length"
    assert client.last_truncated is True
    await client.force_close()


async def test_generate_contenu_vide_et_coupe_leve() -> None:
    client = make_client(json_transport(chat_payload(None, "length")))
    with pytest.raises(LLMEmptyContentError):
        await client.generate(MESSAGES)
    assert client.last_truncated is True
    await client.force_close()


async def test_erreur_500_reessayee() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, json={"error": "boom"}, headers={"retry-after-ms": "1"})
        return httpx.Response(200, json=chat_payload("apres reessai"))

    client = make_client(httpx.MockTransport(handler), max_retries=2)
    assert await client.generate(MESSAGES) == "apres reessai"
    assert calls["n"] == 2
    await client.force_close()


async def test_erreur_500_persistante_devient_llm_client_error() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"error": "boom"}, headers={"retry-after-ms": "1"})

    client = make_client(httpx.MockTransport(handler), max_retries=2)
    with pytest.raises(LLMClientError):
        await client.generate(MESSAGES)
    assert calls["n"] == 3
    await client.force_close()


async def test_echeance_totale_depassee() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=chat_payload("trop tard"))

    client = make_client(httpx.MockTransport(handler), total_timeout=0.2)
    with pytest.raises(LLMTimeoutError):
        await client.generate(MESSAGES)
    await client.force_close()


async def test_echeance_totale_par_appel() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=chat_payload("trop tard"))

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(LLMTimeoutError):
        await client.generate(MESSAGES, total_timeout=0.2)
    await client.force_close()


async def test_options_client_non_transmises_a_create() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request_body(request))
        return httpx.Response(200, json=chat_payload("ok"))

    client = make_client(httpx.MockTransport(handler))
    await client.generate(
        MESSAGES, max_retries=1, timeout=10.0, total_timeout=30.0, temperature=0.1
    )
    assert seen["temperature"] == 0.1
    assert "max_retries" not in seen and "timeout" not in seen and "total_timeout" not in seen
    await client.force_close()


async def test_qwen_desactive_le_raisonnement() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request_body(request))
        return httpx.Response(200, json=chat_payload("ok"))

    client = make_client(httpx.MockTransport(handler), model="qwen3-test")
    await client.generate(MESSAGES)
    assert seen["chat_template_kwargs"] == {"enable_thinking": False}
    await client.force_close()


@pytest.mark.parametrize("structured", [False, True])
async def test_modele_par_appel(structured: bool) -> None:
    # Verificateur distinct du redacteur (verification_model) : meme point
    # d'acces, autre modele, le temps d'un appel.
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request_body(request))
        return httpx.Response(200, json=chat_payload('{"a": 1}'))

    client = make_client(httpx.MockTransport(handler), model="qwen3-redacteur")
    if structured:
        await client.generate_structured(MESSAGES, schema=SCHEMA, model="qwen3-verificateur")
        await client.generate_structured(MESSAGES, schema=SCHEMA)
    else:
        await client.generate(MESSAGES, model="qwen3-verificateur")
        await client.generate(MESSAGES)
    assert [b["model"] for b in seen] == ["qwen3-verificateur", "qwen3-redacteur"]
    await client.force_close()


async def test_structured_nominal_envoie_le_schema() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request_body(request))
        return httpx.Response(200, json=chat_payload('{"a": 1}'))

    client = make_client(httpx.MockTransport(handler))
    assert await client.generate_structured(MESSAGES, SCHEMA, schema_name="s") == {"a": 1}
    assert seen["response_format"]["json_schema"]["name"] == "s"
    assert seen["response_format"]["json_schema"]["strict"] is True
    await client.force_close()


async def test_structured_contenu_none_leve_llm_client_error() -> None:
    client = make_client(json_transport(chat_payload(None)))
    with pytest.raises(LLMEmptyContentError):
        await client.generate_structured(MESSAGES, SCHEMA)
    await client.force_close()


async def test_structured_tronque_distingue_du_schema_non_respecte() -> None:
    client = make_client(json_transport(chat_payload('{"a": 1, "b": "abc', "length")))
    with pytest.raises(LLMTruncatedError):
        await client.generate_structured(MESSAGES, SCHEMA, max_tokens=5)
    await client.force_close()


async def test_structured_json_invalide_sans_troncature_est_schema_non_respecte() -> None:
    client = make_client(json_transport(chat_payload("pas du json", "stop")))
    with pytest.raises(LLMSchemaError) as info:
        await client.generate_structured(MESSAGES, SCHEMA)
    assert not isinstance(info.value, LLMTruncatedError)
    await client.force_close()


async def test_structured_schema_refuse_par_le_proxy_est_journalise(caplog) -> None:
    body = {"error": {"message": "json_schema non supporte", "type": "invalid_request_error"}}
    client = make_client(json_transport(body, status=400))
    with (
        caplog.at_level(logging.ERROR, logger="app.core.llm.openai"),
        pytest.raises(LLMSchemaError),
    ):
        await client.generate_structured(MESSAGES, SCHEMA, schema_name="plan")
    assert "plan" in caplog.text and "refuse" in caplog.text
    await client.force_close()


async def test_structured_erreur_reseau_enveloppee() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("injoignable", request=request)

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(LLMClientError):
        await client.generate_structured(MESSAGES, SCHEMA)
    await client.force_close()


async def test_client_ferme_est_reconstruit() -> None:
    client = make_client(json_transport(chat_payload("ok")))
    await client.force_close()
    assert await client.generate(MESSAGES) == "ok"
    await client.force_close()


def test_langchain_wrapper_n_ecrit_pas_dans_environ(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    client = make_client(json_transport(chat_payload("ok")))
    wrapper = client.get_langchain_wrapper()
    assert "OPENAI_API_KEY" not in os.environ
    assert "OPENAI_BASE_URL" not in os.environ
    assert wrapper.openai_api_key.get_secret_value() == "cle-test"
    assert wrapper.openai_api_base == "https://llm.test/api"


def test_capacites_declarees() -> None:
    client = make_client(json_transport(chat_payload("ok")))
    assert client.supports_structured and client.supports_embeddings and client.supports_vision
