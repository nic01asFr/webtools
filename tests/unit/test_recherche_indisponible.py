"""Une panne de SearXNG n'est pas un zero resultat (audit du 14/11/2026)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.services.search_service import SearchUnavailableError, SearXNGClient


class _Down:
    """Client httpx dont chaque requete echoue."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_Down":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def get(self, *_args: Any, **_kwargs: Any) -> Any:
        raise httpx.ConnectError("connexion refusee")


async def test_panne_leve_au_lieu_de_rendre_vide(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SearXNGClient(base_url="http://searxng.invalide")

    async def _no_wait(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(client, "_respect_rate_limit", _no_wait)
    monkeypatch.setattr(SearXNGClient, "_retry_catalog_load", _no_wait)
    monkeypatch.setattr(httpx, "AsyncClient", _Down)
    with pytest.raises(SearchUnavailableError):
        await client.search("requete quelconque")
