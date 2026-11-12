"""Client OpenAI en flux : reponses SSE simulees par httpx.MockTransport.

Le flux est ce qui permet a une generation longue d'aboutir sur un modele
lent : mesure sur le proxy SSPCloud a ~10 jetons/s, un plan de ~2 000 jetons
depasse le delai par tentative de 90 s et n'aboutissait jamais sans flux.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.llm.base import LLMEmptyContentError, LLMTruncatedError
from app.core.llm.openai import OpenAILLMClient

MESSAGES = [{"role": "user", "content": "bonjour"}]
SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "integer"}},
    "required": ["a"],
    "additionalProperties": False,
}


def _sse(pieces: list[str | None], finish_reason: str = "stop") -> bytes:
    """Corps SSE au format chat.completions.chunk, termine par [DONE]."""
    events = []
    for i, piece in enumerate(pieces):
        delta = {"role": "assistant"} if i == 0 else {}
        if piece is not None:
            delta["content"] = piece
        last = i == len(pieces) - 1
        chunk = {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [
                {"index": 0, "delta": delta, "finish_reason": finish_reason if last else None}
            ],
        }
        events.append(f"data: {json.dumps(chunk)}\n\n")
    events.append("data: [DONE]\n\n")
    return "".join(events).encode()


def _client(body: bytes, seen: list[dict] | None = None) -> OpenAILLMClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    return OpenAILLMClient(
        api_key="cle-test",
        base_url="https://llm.test/api",
        model="gpt-test",
        max_retries=0,
        transport=httpx.MockTransport(handler),
        stream=True,
    )


async def test_generate_en_flux_assemble_les_morceaux_et_envoie_stream() -> None:
    seen: list[dict] = []
    client = _client(_sse(["Bon", "jour ", "le monde"]), seen)
    assert await client.generate(MESSAGES) == "Bonjour le monde"
    assert seen[0]["stream"] is True
    assert client.last_finish_reason == "stop"
    await client.force_close()


async def test_generate_en_flux_coupe_par_max_tokens_signale() -> None:
    client = _client(_sse(["texte ", "presque"], finish_reason="length"))
    result = await client.generate_result(MESSAGES, max_tokens=5)
    assert result.text == "texte presque"
    assert result.truncated is True
    await client.force_close()


async def test_generate_en_flux_contenu_vide_leve() -> None:
    client = _client(_sse([None]))
    with pytest.raises(LLMEmptyContentError):
        await client.generate(MESSAGES)
    await client.force_close()


async def test_structure_en_flux_parse_le_json_assemble() -> None:
    seen: list[dict] = []
    client = _client(_sse(['{"a"', ": 4", "2}"]), seen)
    assert await client.generate_structured(MESSAGES, schema=SCHEMA, schema_name="t") == {"a": 42}
    assert seen[0]["stream"] is True
    assert seen[0]["response_format"]["type"] == "json_schema"
    await client.force_close()


async def test_structure_en_flux_tronquee_leve() -> None:
    client = _client(_sse(['{"a": ', "4"], finish_reason="length"))
    with pytest.raises(LLMTruncatedError):
        await client.generate_structured(MESSAGES, schema=SCHEMA, schema_name="t")
    await client.force_close()


async def test_client_partage_en_flux_par_defaut(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.llm as llm

    monkeypatch.setattr(llm, "_shared_clients", {})
    monkeypatch.setattr(llm, "resolve_llm_config", lambda: ("openai", "m", "k", "https://llm.test/api"))
    monkeypatch.delenv("DEFAULT_LLM_STREAM", raising=False)
    assert (await llm.get_llm_client()).stream is True

    monkeypatch.setattr(llm, "_shared_clients", {})
    monkeypatch.setenv("DEFAULT_LLM_STREAM", "0")
    assert (await llm.get_llm_client()).stream is False


async def test_client_ferme_pendant_l_appel_reconstruit_et_rejoue() -> None:
    # Observe en production : le client HTTP partage etait ferme a la fin d'une
    # tache browser-use, et l'appel en vol echouait.
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        return httpx.Response(
            200, content=_sse(["apres ", "rejeu"]), headers={"content-type": "text/event-stream"}
        )

    client = OpenAILLMClient(
        api_key="cle-test",
        base_url="https://llm.test/api",
        model="gpt-test",
        max_retries=0,
        transport=httpx.MockTransport(handler),
        stream=True,
    )
    assert await client.generate(MESSAGES) == "apres rejeu"
    assert calls["n"] == 2
    await client.force_close()


async def test_autre_erreur_non_rejouee() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"message": "mauvaise requete"}})

    client = OpenAILLMClient(
        api_key="cle-test", base_url="https://llm.test/api", model="gpt-test",
        max_retries=0, transport=httpx.MockTransport(handler),
    )
    with pytest.raises(Exception):
        await client.generate(MESSAGES)
    assert calls["n"] == 1
    await client.force_close()
