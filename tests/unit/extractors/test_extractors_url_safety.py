"""Garde SSRF : plages bloquees, URL mal formees, crochet httpx, route navigateur."""

import socket

import httpx
import pytest
from app.core.browser.playwright_manager import install_ssrf_route
from app.utils.url_safety import (
    UnsafeURLError,
    assert_safe_url,
    assert_safe_url_async,
    safe_request_hooks,
)
from helpers_extractors import FakePage, FakeRequest, FakeRoute, make_resolver


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "100.127.255.254",
        "2002:c0a8:0101::1",
        "::1",
        "::ffff:10.0.0.1",
        "0.0.0.0",
    ],
)
def test_adresses_internes_refusees(ip: str) -> None:
    with pytest.raises(UnsafeURLError):
        assert_safe_url("https://exemple.test/x", resolver=make_resolver({"exemple.test": ip}))


def test_adresse_publique_acceptee() -> None:
    assert_safe_url("https://exemple.test/x", resolver=make_resolver())


@pytest.mark.parametrize(
    "url",
    ["http://[::1", "http://exemple.test:abc/", "", "ftp://exemple.test/", "http:///x", "http://"],
)
def test_url_mal_formee_donne_un_refus(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        assert_safe_url(url, resolver=make_resolver())


async def test_version_asynchrone() -> None:
    await assert_safe_url_async("https://exemple.test/", resolver=make_resolver())
    with pytest.raises(UnsafeURLError):
        await assert_safe_url_async(
            "https://interne.test/", resolver=make_resolver({"interne.test": "100.64.0.9"})
        )
    with pytest.raises(UnsafeURLError):
        await assert_safe_url_async("http://[::1", resolver=make_resolver())


async def test_resolution_dns_en_echec_donne_un_refus() -> None:
    def echec(host: str) -> list[str]:
        raise socket.gaierror("introuvable")

    with pytest.raises(UnsafeURLError):
        await assert_safe_url_async("https://exemple.test/", resolver=echec)


async def test_crochet_httpx_revalide_les_redirections() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "public.test":
            return httpx.Response(302, headers={"location": "http://interne.test/secret"})
        return httpx.Response(200, text="secret")

    resolver = make_resolver({"interne.test": "10.0.0.1"})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        event_hooks=safe_request_hooks(resolver),
    ) as client:
        with pytest.raises(UnsafeURLError):
            await client.get("http://public.test/")


async def test_route_navigateur_bloque_les_requetes_internes() -> None:
    page = FakePage()
    blocked = await install_ssrf_route(page, make_resolver({"interne.test": "10.0.0.1"}))
    assert page.handler is not None

    cas = [
        ("https://public.test/", "continue"),
        ("data:text/html,<p>x</p>", "continue"),
        ("http://interne.test/admin", "abort:blockedbyclient"),
        ("http://[::1", "abort:blockedbyclient"),
    ]
    for url, expected in cas:
        route = FakeRoute()
        await page.handler(route, FakeRequest(url))
        assert route.action == expected, url
    assert blocked == ["http://interne.test/admin", "http://[::1"]
