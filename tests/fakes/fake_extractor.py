"""Faux gestionnaire d'extraction : pages par URL, appels enregistres."""

from dataclasses import dataclass
from typing import Any

from app.api.models import WebResult


@dataclass
class ExtractCall:
    """Une extraction demandee."""

    url: str
    prompt: str | None
    extraction_type: str
    llm_client: Any
    options: Any


class FakeExtractorManager:
    """Double de ExtractorManager.extract, sans reseau ni navigateur.

    Pages : dict URL -> page, ou add_page(url, contenu, titre). Une page est
    un WebResult, un texte (contenu), un couple (titre, contenu) ou une
    exception (levee). Une URL inconnue rend WebResult.from_error, comme un
    echec d'extraction reel.

    Enregistrement : `calls` (liste de ExtractCall), urls_requested().
    """

    def __init__(self, pages: dict[str, Any] | None = None) -> None:
        self.pages: dict[str, Any] = dict(pages or {})
        self.calls: list[ExtractCall] = []

    def add_page(self, url: str, content: str, title: str = "") -> "FakeExtractorManager":
        self.pages[url] = (title or url, content)
        return self

    def urls_requested(self) -> list[str]:
        return [c.url for c in self.calls]

    async def extract(
        self,
        url: str,
        prompt: str | None = None,
        extraction_type: str = "general",
        llm_client: Any = None,
        options: Any = None,
    ) -> WebResult:
        self.calls.append(ExtractCall(url, prompt, extraction_type, llm_client, options))
        page = self.pages.get(url)
        if page is None:
            return WebResult.from_error(url=url, error_message="page inconnue du faux extracteur")
        if isinstance(page, BaseException):
            raise page
        if isinstance(page, WebResult):
            return page
        title, content = page if isinstance(page, tuple) else (url, page)
        return WebResult.from_success(
            url=url, content_type="text/html", title=title, content=content
        )
