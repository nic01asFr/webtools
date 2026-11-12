"""Vision : l'image est telechargee par le service et transmise en data URL.

Observe en production : le proxy LLM allait chercher lui-meme l'image et
upload.wikimedia.org lui repondait 403 ; l'analyse echouait en 502.
"""

from __future__ import annotations

import base64

import httpx
import pytest

from app.api.v1.endpoints import vision
from app.utils.url_safety import UnsafeURLError

pytestmark = pytest.mark.telechargement_image

PNG = b"\x89PNG\r\n\x1a\nfaux-contenu"


@pytest.fixture(autouse=True)
def _resolution_publique(monkeypatch: pytest.MonkeyPatch) -> None:
    # Resolution DNS simulee : les hotes de test sont publics, sauf "interne".
    import app.utils.url_safety as us

    def resolver(host: str) -> set[str]:
        return {"10.0.0.5"} if host.startswith("interne") else {"93.184.216.34"}

    monkeypatch.setattr(us, "_default_resolver", resolver)


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_image_rendue_en_data_url(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    monkeypatch.setattr(vision, "_IMAGE_TRANSPORT", _transport(handler))
    url = await vision.fetch_image_as_data_url("https://images.example.org/a.png")
    assert url == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert "Mozilla" in seen["ua"]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, content=b"interdit"),
        httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}),
    ],
)
async def test_echec_rend_none_pour_repli_sur_l_url(monkeypatch: pytest.MonkeyPatch, response) -> None:
    monkeypatch.setattr(vision, "_IMAGE_TRANSPORT", _transport(lambda request: response))
    assert await vision.fetch_image_as_data_url("https://images.example.org/a.png") is None


async def test_image_trop_lourde_refusee(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", 10)
    monkeypatch.setattr(
        vision,
        "_IMAGE_TRANSPORT",
        _transport(lambda request: httpx.Response(200, content=b"x" * 50, headers={"content-type": "image/png"})),
    )
    assert await vision.fetch_image_as_data_url("https://images.example.org/a.png") is None


async def test_redirection_vers_adresse_interne_refusee(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "images.example.org":
            return httpx.Response(302, headers={"location": "http://interne.local/secret.png"})
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    monkeypatch.setattr(vision, "_IMAGE_TRANSPORT", _transport(handler))
    with pytest.raises(UnsafeURLError):
        await vision.fetch_image_as_data_url("https://images.example.org/a.png")
