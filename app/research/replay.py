"""Enregistrement et rejeu de la collecte (recherche et extraction).

Deux enveloppes reproduisent les interfaces reelles :
- ReplaySearch.search(...)  <-> SearXNGClient.search (app/services/search_service.py)
- ReplayExtractor.extract(...) <-> ExtractorManager.extract (app/manager.py)

Modes :
- "record" : l'appel est delegue au client reel, puis ecrit sur disque.
- "replay" : l'appel est servi depuis le disque ; une requete inconnue leve
  ReplayMissError (et incremente `missed`, car le pipeline avale souvent les
  exceptions de collecte : un test verifie `missed == 0`).

Format sur disque : un repertoire par tache, contenant
    search/<empreinte>.json   {"key": {...}, "results": [SearchResult...]}
    pages/<empreinte>.json    {"key": {...}, "result": WebResult}

Cles deterministes :
- recherche : empreinte (sha256) des parametres normalises : requete (espaces
  reduits, casse conservee), langue, moteurs, categories, plage temporelle,
  nombre de resultats, nombre de pages.
- extraction : URL normalisee SEULE. Les options d'extraction (timeout,
  prompt, type, palier) varient d'un appel a l'autre sans changer la page.
"""

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.api.models import WebResult
from app.services.search_service import SearchResult

logger = logging.getLogger(__name__)

MODE_RECORD = "record"
MODE_REPLAY = "replay"
ENV_MODE = "WEBTOOLS_REPLAY_MODE"
ENV_DIR = "WEBTOOLS_REPLAY_DIR"


class ReplayMissError(Exception):
    """Requete absente de l'enregistrement en mode replay."""


def read_replay_env() -> tuple[str | None, Path | None]:
    """Lit WEBTOOLS_REPLAY_MODE et WEBTOOLS_REPLAY_DIR.

    Rend (None, None) si le mode est absent ou vide. Leve ValueError si le
    mode est inconnu ou si le repertoire manque.
    """
    mode = os.environ.get(ENV_MODE, "").strip().lower()
    if not mode:
        return None, None
    if mode not in (MODE_RECORD, MODE_REPLAY):
        raise ValueError(f"{ENV_MODE} invalide : {mode!r} (attendu : record ou replay)")
    directory = os.environ.get(ENV_DIR, "").strip()
    if not directory:
        raise ValueError(f"{ENV_DIR} est requis quand {ENV_MODE} est defini")
    return mode, Path(directory)


def normalize_url(url: str) -> str:
    """Schema et hote en minuscules, sans fragment ni barre finale du chemin."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def search_key(
    query: str,
    max_results: int,
    language: str,
    categories: str | None,
    engines: str | None,
    time_range: str | None,
    max_pages: int,
) -> dict[str, Any]:
    return {
        "query": " ".join(query.split()),
        "language": language,
        "engines": engines,
        "categories": categories,
        "time_range": time_range,
        "max_results": max_results,
        "max_pages": max_pages,
    }


def _fingerprint(key: dict[str, Any]) -> str:
    raw = json.dumps(key, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _check_mode(mode: str) -> None:
    if mode not in (MODE_RECORD, MODE_REPLAY):
        raise ValueError(f"mode de rejeu inconnu : {mode!r}")


class ReplaySearch:
    """Enveloppe de SearXNGClient.search."""

    def __init__(self, mode: str, directory: Path, inner: Any = None) -> None:
        _check_mode(mode)
        if mode == MODE_RECORD and inner is None:
            raise ValueError("le mode record exige un client de recherche")
        self.mode = mode
        self.directory = Path(directory)
        self.inner = inner
        self.missed = 0
        self.served = 0
        self.recorded = 0

    async def search(
        self,
        query: str,
        max_results: int = 10,
        language: str = "all",
        categories: str | None = None,
        engines: str | None = None,
        time_range: str | None = None,
        max_pages: int = 1,
    ) -> list[SearchResult]:
        key = search_key(query, max_results, language, categories, engines, time_range, max_pages)
        path = self.directory / "search" / f"{_fingerprint(key)}.json"
        if self.mode == MODE_RECORD:
            results = await self.inner.search(
                query,
                max_results=max_results,
                language=language,
                categories=categories,
                engines=engines,
                time_range=time_range,
                max_pages=max_pages,
            )
            _write(path, {"key": key, "results": [r.model_dump() for r in results]})
            self.recorded += 1
            return results
        if not path.exists():
            self.missed += 1
            logger.error("replay : recherche inconnue %r dans %s", query, self.directory)
            raise ReplayMissError(
                f"recherche inconnue en mode replay : {query!r} (repertoire {self.directory})"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        self.served += 1
        return [SearchResult.model_validate(r) for r in data["results"]]

    async def health_check(self) -> bool:
        return True


class ReplayExtractor:
    """Enveloppe de ExtractorManager.extract."""

    def __init__(self, mode: str, directory: Path, inner: Any = None) -> None:
        _check_mode(mode)
        if mode == MODE_RECORD and inner is None:
            raise ValueError("le mode record exige un gestionnaire d'extraction")
        self.mode = mode
        self.directory = Path(directory)
        self.inner = inner
        self.missed = 0
        self.served = 0
        self.recorded = 0

    async def extract(
        self,
        url: str,
        prompt: str | None = None,
        extraction_type: str = "general",
        llm_client: Any = None,
        options: Any = None,
    ) -> WebResult:
        key = {"url": normalize_url(url)}
        path = self.directory / "pages" / f"{_fingerprint(key)}.json"
        if self.mode == MODE_RECORD:
            result = await self.inner.extract(
                url,
                prompt=prompt,
                extraction_type=extraction_type,
                llm_client=llm_client,
                options=options,
            )
            _write(path, {"key": key, "result": result.model_dump()})
            self.recorded += 1
            return result
        if not path.exists():
            self.missed += 1
            logger.error("replay : page inconnue %r dans %s", url, self.directory)
            raise ReplayMissError(
                f"page inconnue en mode replay : {url!r} (repertoire {self.directory})"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        self.served += 1
        return WebResult.model_validate(data["result"])


def build_replay_clients(
    mode: str, directory: Path | str, search_client: Any, extractor_manager: Any
) -> tuple[ReplaySearch, ReplayExtractor]:
    """Rend le couple (recherche, extracteur) a injecter dans l'orchestrateur."""
    path = Path(directory)
    return (
        ReplaySearch(mode, path, search_client),
        ReplayExtractor(mode, path, extractor_manager),
    )
