"""Pages de verification anti-robot et domaines qui refusent l'extraction.

Mesure citecheck du 12/11/2026 : une page "Client Challenge" de 226
caracteres etait lue comme le contenu d'un article (8 citations jugees a
tort). Le seuil de l'extraction directe (100 caracteres) la laissait passer
aussi dans le service : ses passages pouvaient etre cites. Meme detection
que l'instrument (benchmarks/citecheck/pages.py).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from urllib.parse import urlsplit

_BLOCK_PAGE = re.compile(
    r"client challenge|just a moment\.\.\.|checking your browser|attention required|"
    r"verify(?:ing)? (?:that )?you are (?:a )?human|are you a robot|captcha|access denied|"
    r"request blocked|unusual traffic|enable javascript and cookies",
    re.IGNORECASE,
)
BLOCK_PAGE_MAX_CHARS = 3000

# Refus d'un domaine : statuts HTTP qui signalent un blocage du client, pas
# une page absente (404, 410 restent propres a la page).
REFUSAL_STATUSES = (401, 403, 429)
REFUSAL_THRESHOLD = 2
REFUSAL_COOLDOWN_SECONDS = 1800.0
_HTTP_STATUS = re.compile(r"Erreur HTTP (\d{3})")


def block_reason(text: str) -> str | None:
    """Motif de blocage si le texte est une page de verification, sinon None.

    Seules les pages courtes sont examinees : un article qui mentionne un
    captcha reste un article.
    """
    if not text or len(text) > BLOCK_PAGE_MAX_CHARS:
        return None
    match = _BLOCK_PAGE.search(text)
    return match.group(0) if match else None


def is_refusal_error(error: str | None) -> bool:
    """Vrai si l'erreur d'extraction est un refus du site (HTTP 401/403/429,
    page de verification)."""
    if not error:
        return False
    if "anti-robot" in error:
        return True
    match = _HTTP_STATUS.search(error)
    return bool(match) and int(match.group(1)) in REFUSAL_STATUSES


def domain_of(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


class DomainRefusals:
    """Memoire des domaines qui refusent l'extraction, le temps d'un delai.

    Disponibilite seulement, jamais pertinence (meme principe que le
    catalogue de moteurs) : un domaine qui a refuse deux pages en 30 minutes
    n'est plus sollicite pendant 30 minutes ; une page lue le rehabilite.
    """

    def __init__(
        self,
        threshold: int = REFUSAL_THRESHOLD,
        cooldown_s: float = REFUSAL_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._refusals: dict[str, list[float]] = {}

    def _recent(self, domain: str) -> list[float]:
        now = self._clock()
        recent = [t for t in self._refusals.get(domain, []) if now - t <= self.cooldown_s]
        if recent:
            self._refusals[domain] = recent
        else:
            self._refusals.pop(domain, None)
        return recent

    def record_refusal(self, url: str) -> None:
        domain = domain_of(url)
        if domain:
            self._refusals.setdefault(domain, []).append(self._clock())

    def record_success(self, url: str) -> None:
        self._refusals.pop(domain_of(url), None)

    def is_refusing(self, url: str) -> bool:
        return len(self._recent(domain_of(url))) >= self.threshold

    def clear(self) -> None:
        self._refusals.clear()


# Instance du processus, partagee par toutes les extractions.
domain_refusals = DomainRefusals()
