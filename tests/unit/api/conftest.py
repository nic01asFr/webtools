"""Fixtures communes aux tests d'endpoints : aucun appel reseau."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _pas_de_telechargement_d_image(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """La vision telecharge l'image elle-meme ; neutralise hors tests dedies."""
    if request.node.get_closest_marker("telechargement_image"):
        return
    from app.api.v1.endpoints import vision

    async def aucun(url: str) -> None:
        return None

    monkeypatch.setattr(vision, "fetch_image_as_data_url", aucun)
