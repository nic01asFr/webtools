"""Outils communs aux tests des extracteurs (sans reseau ni navigateur)."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

PUBLIC_IP = "93.184.216.34"


def make_resolver(mapping: dict[str, str] | None = None) -> Callable[[str], list[str]]:
    """Faux resolveur DNS : hote -> IP ; tout hote inconnu est public."""
    table = mapping or {}

    def _resolve(hostname: str) -> list[str]:
        return [table.get(hostname, PUBLIC_IP)]

    return _resolve


def make_pdf(
    pages: int = 1,
    text: str = "Texte de test du document PDF, assez long pour passer le seuil, page numero",
) -> bytes:
    """PDF minimal valide, une ligne de texte par page."""
    objs: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    for i in range(pages):
        page_id = 3 + 2 * i
        content_id = page_id + 1
        objs.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
                f"/Contents {content_id} 0 R "
                f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
                f"/BaseFont /Helvetica >> >> >> >>"
            ).encode()
        )
        stream = f"BT /F1 12 Tf 20 150 Td ({text} {i + 1}) Tj ET".encode()
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class FakeRoute:
    def __init__(self) -> None:
        self.action: str | None = None

    async def continue_(self) -> None:
        self.action = "continue"

    async def abort(self, reason: str = "failed") -> None:
        self.action = f"abort:{reason}"


class FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class FakePage:
    """Page simulee : `requests` liste les URL que la navigation declenche en plus."""

    def __init__(
        self, status: int = 200, html: str = "", requests: list[str] | None = None
    ) -> None:
        self.status = status
        self.html = html
        self.requests = requests or []
        self.handler: Callable[..., Any] | None = None
        self.closed = False

    async def route(self, pattern: str, handler: Callable[..., Any]) -> None:
        assert pattern == "**/*"
        self.handler = handler

    async def goto(self, url: str, **kwargs: Any) -> FakeResponse:
        assert self.handler is not None, "la garde SSRF doit etre installee avant goto"
        for target in [url, *self.requests]:
            route = FakeRoute()
            await self.handler(route, FakeRequest(target))
            if route.action and route.action.startswith("abort"):
                raise RuntimeError("net::ERR_BLOCKED_BY_CLIENT")
        return FakeResponse(self.status)

    async def wait_for_timeout(self, ms: int) -> None:
        return None

    async def content(self) -> str:
        return self.html

    async def title(self) -> str:
        return "Titre"

    async def close(self) -> None:
        self.closed = True


def page_factory_for(page: FakePage) -> Callable[..., Any]:
    @asynccontextmanager
    async def _factory(headless: bool = True) -> AsyncIterator[FakePage]:
        yield page

    return _factory
