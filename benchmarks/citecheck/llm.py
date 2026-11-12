"""Client minimal d'un point d'acces compatible OpenAI (juge et plongements).

httpx direct plutot que le SDK : le harnais ne depend pas du client LLM du
service qu'il evalue, et chaque requete envoyee est explicite.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_TAG = re.compile(r"</?think>", re.IGNORECASE)


class LLMError(Exception):
    """Reponse inexploitable apres tous les essais."""


def strip_reasoning(text: str) -> str:
    return _THINK_TAG.sub("", _THINK_BLOCK.sub("", text or "")).strip()


def parse_json_object(text: str) -> dict[str, Any]:
    """Premier objet JSON du texte (tolere un bloc ``` ou du texte autour)."""
    text = strip_reasoning(text)
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(value, dict):
                        return value
                    break
        start = text.find("{", start + 1)
    raise ValueError("aucun objet JSON dans la reponse")


class Endpoint:
    """Point d'acces /chat/completions et /embeddings."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 300.0,
        attempts: int = 3,
        chat_extra: dict[str, Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.attempts = attempts
        # Champs ajoutes a chaque /chat/completions (ex. desactiver le raisonnement).
        self.chat_extra = dict(chat_extra or {})
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
            transport=transport,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        last = ""
        for attempt in range(self.attempts):
            try:
                response = await self.client.post(f"{self.base_url}{path}", json=body)
                if response.status_code < 400:
                    return response.json()
                last = f"HTTP {response.status_code} : {response.text[:300]}"
                if response.status_code in (400, 401, 403, 404, 422):
                    break
            except (httpx.HTTPError, ValueError) as exc:
                last = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(2 * (attempt + 1))
        raise LLMError(f"{path} : {last}")

    async def chat_json(
        self,
        model: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None = None,
        max_tokens: int = 600,
    ) -> tuple[dict[str, Any], str]:
        """Rend (objet JSON, texte brut). Un JSON invalide est redemande."""
        body: dict[str, Any] = {
            "model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens,
            **self.chat_extra,
        }
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "verdict", "schema": schema, "strict": True},
            }
        last_raw = ""
        for _ in range(self.attempts):
            data = await self._post("/chat/completions", body)
            try:
                last_raw = data["choices"][0]["message"].get("content") or ""
            except (KeyError, IndexError, TypeError):
                last_raw = json.dumps(data)[:500]
                continue
            try:
                return parse_json_object(last_raw), last_raw
            except ValueError:
                continue
        raise LLMError(f"JSON invalide du juge : {last_raw[:300]!r}")

    async def embed(self, model: str, texts: list[str], batch: int = 32) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), batch):
            data = await self._post("/embeddings", {"model": model, "input": texts[i:i + batch]})
            items = sorted(data.get("data") or [], key=lambda d: d.get("index", 0))
            if len(items) != len(texts[i:i + batch]):
                raise LLMError("/embeddings : nombre de vecteurs inattendu")
            vectors.extend(item["embedding"] for item in items)
        return vectors
