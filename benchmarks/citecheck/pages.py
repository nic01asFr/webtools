"""Recuperation independante des pages citees.

Le service n'est jamais utilise ici : ses extracteurs sont ce que l'on
evalue. La page est lue par une requete HTTP simple et son texte visible est
garde en entier (rappel maximal : un tableau ou un encadre que l'extraction
d'article ecarterait peut etre exactement ce qui est cite). Un PDF est lu par
pypdf.

Une page inaccessible (erreur HTTP, blocage, contenu vide ou non textuel)
rend la citation invérifiable : elle est comptee a part, jamais comme fausse.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
# Wikimedia refuse (403) tout client programmatique sans contact dans le
# User-Agent, API officielle comprise (politique https://w.wiki/4wJS). Ces
# domaines recoivent donc un User-Agent descriptif avec contact ; les autres
# sites, dont beaucoup bloquent les robots declares, le User-Agent navigateur.
DEFAULT_CONTACT = "https://github.com/nic01asFr/webtools"
DECLARED_HOSTS = (
    "wikipedia.org", "wikimedia.org", "wiktionary.org", "wikidata.org",
    "wikisource.org", "wikibooks.org", "wikinews.org", "wikiquote.org", "wikiversity.org",
)
MAX_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 400_000
MIN_TEXT_CHARS = 200
TIMEOUT_S = 45.0
STATUS_OK = "ok"


@dataclass
class Page:
    url: str
    status: str  # ok | http_error | vide | non_textuel | erreur
    http_status: int | None = None
    final_url: str | None = None
    content_type: str = ""
    text: str = ""
    error: str = ""
    method: str = "http"  # http | navigateur | http+navigateur (les deux ont echoue)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    @property
    def browser_may_help(self) -> bool:
        """Une page vide (rendue en JavaScript), bloquee ou en erreur peut
        s'ouvrir dans un navigateur ; une page absente (404, 410) ou non
        textuelle non."""
        if self.ok or self.status == "non_textuel":
            return False
        return self.http_status not in (404, 410)


def html_to_text(html: str) -> str:
    """Texte visible d'une page HTML, un bloc par ligne."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "template", "iframe"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _decode(response: httpx.Response) -> str:
    try:
        return response.text
    except (LookupError, UnicodeDecodeError):
        return response.content.decode("utf-8", errors="replace")


def page_from_response(url: str, response: httpx.Response) -> Page:
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    page = Page(
        url=url, status="erreur", http_status=response.status_code,
        final_url=str(response.url), content_type=content_type,
    )
    if response.status_code >= 400:
        page.status = "http_error"
        page.error = f"HTTP {response.status_code}"
        return page
    data = response.content
    try:
        if content_type == "application/pdf" or data[:5] == b"%PDF-":
            text = pdf_to_text(data)
        elif content_type in ("", "text/html", "application/xhtml+xml") or "html" in content_type:
            text = html_to_text(_decode(response))
        elif content_type.startswith("text/") or content_type in ("application/json", "application/xml"):
            text = _decode(response)
        else:
            page.status = "non_textuel"
            page.error = f"type {content_type}"
            return page
    except Exception as exc:  # noqa: BLE001 - document illisible : page invérifiable
        page.error = f"lecture : {type(exc).__name__}: {exc}"[:300]
        return page
    text = text.strip()[:MAX_TEXT_CHARS]
    if len(text) < MIN_TEXT_CHARS:
        page.status = "vide"
        page.error = f"{len(text)} caracteres"
        page.text = text
        return page
    page.status = STATUS_OK
    page.text = text
    return page


def declared_user_agent() -> str:
    contact = os.environ.get("CITECHECK_CONTACT", DEFAULT_CONTACT).strip()
    return f"citecheck/1.0 (+{contact})" if contact else "citecheck/1.0"


def request_headers(url: str) -> dict[str, str]:
    """En-tetes propres a l'URL : User-Agent declare pour Wikimedia."""
    host = (urlsplit(url).hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in DECLARED_HOSTS):
        return {"User-Agent": declared_user_agent()}
    return {}


async def fetch_page(client: httpx.AsyncClient, url: str) -> Page:
    """Une requete, un nouvel essai sur 429/503 ou erreur reseau."""
    last_error = ""
    for attempt in range(2):
        try:
            response = await client.get(
                url, timeout=TIMEOUT_S, follow_redirects=True, headers=request_headers(url)
            )
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"[:300]
            await asyncio.sleep(2 + attempt * 3)
            continue
        if len(response.content) > MAX_BYTES:
            return Page(url=url, status="erreur", http_status=response.status_code,
                        error=f"plus de {MAX_BYTES} octets")
        if response.status_code in (429, 503) and attempt == 0:
            await asyncio.sleep(3)
            continue
        return page_from_response(url, response)
    return Page(url=url, status="erreur", error=last_error)


def default_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr,en;q=0.8",
        },
        transport=transport,
    )


class BrowserFetcher:
    """Repli par un Chromium headless ordinaire (Playwright), comme un lecteur
    qui ouvre la page. Aucune technique de contournement : une page qui
    bloque aussi le navigateur reste inverifiable."""

    def __init__(self, concurrency: int = 2, settle_ms: int = 2500) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self.settle_ms = settle_ms
        self._playwright = None
        self._browser = None

    async def __aenter__(self) -> "BrowserFetcher":
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def __call__(self, url: str) -> Page:
        status = None
        content_type = ""
        final_url = None
        async with self._semaphore:
            context = await self._browser.new_context(
                locale="fr-FR", extra_http_headers=request_headers(url) or None
            )
            try:
                page = await context.new_page()
                response = await page.goto(url, wait_until="domcontentloaded", timeout=int(TIMEOUT_S * 1000))
                if response is not None:
                    status = response.status
                    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if content_type == "application/pdf" and response is not None:
                    text = pdf_to_text(await response.body())
                else:
                    await page.wait_for_timeout(self.settle_ms)
                    text = html_to_text(await page.content())
                final_url = page.url
            except Exception as exc:  # noqa: BLE001 - echec du navigateur : page inverifiable
                return Page(url=url, status="erreur", error=f"navigateur : {type(exc).__name__}: {exc}"[:300],
                            method="navigateur")
            finally:
                await context.close()
        result = Page(url=url, status="erreur", http_status=status, final_url=final_url,
                      content_type=content_type, method="navigateur")
        if status is not None and status >= 400:
            result.status, result.error = "http_error", f"HTTP {status}"
            return result
        text = text.strip()[:MAX_TEXT_CHARS]
        result.text = text
        if len(text) < MIN_TEXT_CHARS:
            result.status, result.error = "vide", f"{len(text)} caracteres"
            return result
        result.status = STATUS_OK
        return result


class PageStore:
    """Pages recuperees, en cache disque : deux evaluations d'un meme run
    jugent exactement le meme texte."""

    def __init__(
        self,
        directory: Path,
        client: httpx.AsyncClient,
        concurrency: int = 6,
        browser=None,
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.client = client
        self.browser = browser
        self._semaphore = asyncio.Semaphore(concurrency)
        self._locks: dict[str, asyncio.Lock] = {}

    def _path(self, url: str) -> Path:
        return self.directory / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()[:24]}.json"

    async def get(self, url: str) -> Page:
        lock = self._locks.setdefault(url, asyncio.Lock())
        async with lock:
            path = self._path(url)
            page = None
            if path.exists():
                page = Page(**json.loads(path.read_text(encoding="utf-8")))
                if page.ok or self.browser is None or page.method != "http":
                    return page
            if page is None:
                async with self._semaphore:
                    page = await fetch_page(self.client, url)
            if self.browser is not None and page.browser_may_help:
                second = await self.browser(url)
                if second.ok:
                    page = second
                else:
                    page.method = "http+navigateur"
                    page.error = f"{page.error} ; {second.error}"[:300]
            path.write_text(json.dumps(asdict(page), ensure_ascii=False), encoding="utf-8")
            return page
