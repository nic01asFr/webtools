"""Extraction directe : statut HTTP et garde SSRF avec une fabrique de page simulee."""

from typing import Any

from app.extractors.direct_extractor import DirectExtractor
from helpers_extractors import FakePage, make_resolver, page_factory_for

URL = "https://site.test/article"
HTML = (
    "<html><head><title>T</title></head><body><article><p>"
    + ("Un texte assez long pour passer le seuil. " * 20)
    + "</p></article></body></html>"
)


async def _run(page: FakePage, resolver: Any | None = None):  # type: ignore[no-untyped-def]
    return await DirectExtractor().extract(
        URL, "", page_factory=page_factory_for(page), resolver=resolver or make_resolver()
    )


async def test_page_200_reussit() -> None:
    res = await _run(FakePage(status=200, html=HTML))
    assert res.success
    assert res.metadata["http_status"] == 200


async def test_page_404_est_un_echec() -> None:
    res = await _run(FakePage(status=404, html=HTML))
    assert not res.success
    assert "404" in res.error


async def test_page_503_est_un_echec() -> None:
    res = await _run(FakePage(status=503, html=HTML))
    assert not res.success
    assert "503" in res.error


async def test_redirection_interne_bloquee() -> None:
    page = FakePage(status=200, html=HTML, requests=["http://interne.test/admin"])
    res = await _run(page, make_resolver({"interne.test": "10.0.0.1"}))
    assert not res.success
    assert "interne" in res.error
