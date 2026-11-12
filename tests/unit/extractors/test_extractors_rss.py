"""Extracteur RSS : flux nominal et redirection vers une adresse interne."""

import httpx
from app.extractors.rss_extractor import RssExtractor
from helpers_extractors import make_resolver

FEED = (
    '<?xml version="1.0"?><rss version="2.0"><channel>'
    "<item><title>Premier</title><link>https://site.test/1</link></item></channel></rss>"
)
RSS_HEADERS = {"content-type": "application/rss+xml"}


async def test_flux_trouve() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/feed":
            return httpx.Response(200, text=FEED, headers=RSS_HEADERS)
        return httpx.Response(404)

    res = await RssExtractor().try_extract(
        "https://site.test/page", transport=httpx.MockTransport(handler), resolver=make_resolver()
    )
    assert res is not None and res.success
    assert res.metadata["items_count"] == 1


async def test_redirection_interne_ignoree() -> None:
    appels_internes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "site.test":
            return httpx.Response(302, headers={"location": "http://interne.test/feed"})
        appels_internes.append(str(request.url))
        return httpx.Response(200, text=FEED, headers=RSS_HEADERS)

    res = await RssExtractor().try_extract(
        "https://site.test/page",
        transport=httpx.MockTransport(handler),
        resolver=make_resolver({"interne.test": "10.1.2.3"}),
    )
    assert res is None
    assert appels_internes == []
