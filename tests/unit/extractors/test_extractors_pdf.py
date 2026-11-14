"""Extracteur PDF : echecs explicites, taille, troncature, redirections."""

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from app.extractors import pdf_extractor
from app.extractors.pdf_extractor import PdfExtractor
from helpers_extractors import make_pdf, make_resolver

URL = "https://docs.test/rapport.pdf"
PDF_HEADERS = {"content-type": "application/pdf"}


def _pdf_handler(data: bytes) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        body = b"" if request.method == "HEAD" else data
        return httpx.Response(200, content=body, headers=PDF_HEADERS)

    return handler


async def _run(handler: Callable[[httpx.Request], httpx.Response], url: str = URL, **kw: Any):  # type: ignore[no-untyped-def]
    resolver = kw.pop("resolver", make_resolver())
    return await PdfExtractor().try_extract(
        url, transport=httpx.MockTransport(handler), resolver=resolver, **kw
    )


async def test_pdf_valide_extrait() -> None:
    res = await _run(_pdf_handler(make_pdf(2)))
    assert res is not None and res.success
    assert "Texte de test" in res.content
    assert res.metadata["pages_total"] == 2
    assert res.metadata["truncated"] is False


async def test_pdf_tronque_est_signale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_extractor, "MAX_PAGES", 3)
    res = await _run(_pdf_handler(make_pdf(5)))
    assert res is not None and res.success
    assert res.metadata["pages_total"] == 5
    assert res.metadata["pages_extracted"] == 3
    assert res.metadata["truncated"] is True


async def test_pdf_403_donne_un_echec_explicite() -> None:
    res = await _run(lambda request: httpx.Response(403, text="interdit"))
    assert res is not None and not res.success
    assert "403" in res.error


async def test_head_refuse_puis_get_reussit() -> None:
    data = make_pdf(1)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(405)
        return httpx.Response(200, content=data, headers=PDF_HEADERS)

    res = await _run(handler)
    assert res is not None and res.success


async def test_taille_excessive_annoncee() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = b"" if request.method == "HEAD" else b"%PDF" + b"0" * 10
        return httpx.Response(200, content=body, headers={**PDF_HEADERS, "content-length": "5000"})

    res = await _run(handler, max_bytes=1000)
    assert res is not None and not res.success
    assert "volumineux" in res.error


async def test_taille_excessive_pendant_le_telechargement() -> None:
    # Sans content-length, seul le compte des octets recus arrete le flux.
    class Flux(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            for _ in range(10):
                yield b"%PDF" + b"0" * 496

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers=PDF_HEADERS)
        return httpx.Response(200, stream=Flux(), headers=PDF_HEADERS)

    res = await _run(handler, max_bytes=1000)
    assert res is not None and not res.success
    assert "volumineux" in res.error


async def test_non_pdf_rend_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

    assert await _run(handler, url="https://docs.test/page") is None


async def test_redirection_vers_adresse_interne_refusee() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "docs.test":
            return httpx.Response(302, headers={"location": "http://interne.test/x.pdf"})
        return httpx.Response(200, content=make_pdf(1), headers=PDF_HEADERS)

    res = await _run(handler, resolver=make_resolver({"interne.test": "192.168.0.10"}))
    assert res is not None and not res.success
    assert "interne" in res.error


async def test_url_mal_formee_donne_un_refus_sans_exception() -> None:
    res = await _run(_pdf_handler(b""), url="http://[::1/x.pdf")
    assert res is not None and not res.success


# --- budget d'analyse ---------------------------------------------------------


async def test_analyse_arretee_par_le_budget_rend_le_texte_partiel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    import pdfplumber.page

    original = pdfplumber.page.Page.extract_text

    def slow_extract_text(self, *args: Any, **kwargs: Any) -> str:  # type: ignore[no-untyped-def]
        time.sleep(0.25)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pdfplumber.page.Page, "extract_text", slow_extract_text)
    start = time.monotonic()
    res = await _run(_pdf_handler(make_pdf(12)), budget_s=1.5)
    elapsed = time.monotonic() - start
    # Marge de 0,5 s : l'analyse rend la main avant le budget de l'appelant.
    assert elapsed < 1.5
    assert res is not None and res.success
    assert "Texte de test" in res.content
    assert 0 < res.metadata["pages_extracted"] < 12
    assert res.metadata["pages_total"] == 12
    assert res.metadata["truncated"] is True
    assert res.metadata["truncated_reason"] == "budget"


async def test_sans_budget_l_analyse_lit_tout() -> None:
    res = await _run(_pdf_handler(make_pdf(4)))
    assert res is not None and res.success
    assert res.metadata["pages_extracted"] == 4
    assert "truncated_reason" not in res.metadata
