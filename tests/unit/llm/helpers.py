"""Fabriques de reponses HTTP simulees pour les tests des clients LLM."""

import json
from typing import Any

import httpx


def chat_payload(content: str | None, finish_reason: str = "stop") -> dict:
    """Corps d'une reponse /chat/completions au format OpenAI."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-test",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def anthropic_payload(text: str, stop_reason: str = "end_turn") -> dict:
    """Corps d'une reponse /v1/messages au format Anthropic."""
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def json_transport(body: Any, status: int = 200) -> httpx.MockTransport:
    """Transport qui rend toujours le meme corps JSON."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def request_body(request: httpx.Request) -> dict:
    return json.loads(request.content)
