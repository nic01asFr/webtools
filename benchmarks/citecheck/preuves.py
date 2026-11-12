"""Passages de la page soumis au juge.

Une page courte est donnee en entier : aucune selection, donc aucun risque
d'ecarter le passage qui soutient l'affirmation. Une page longue est decoupee
en passages ; les plus proches de l'affirmation sont gardes dans un budget de
caracteres, dans l'ordre de la page. La proximite combine plongements
(multilingues : une affirmation en francais citant une page en anglais),
recouvrement lexical et chiffres communs.

Le risque residuel est mesure, pas suppose : l'etalonnage contient des pages
longues et des affirmations traduites (voir benchmarks/CITATIONS.md).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Protocol

from citecheck.texte import numbers, tokens

MAX_EVIDENCE_CHARS = 14_000
PASSAGE_CHARS = 900
QUERY_INSTRUCTION = (
    "Instruct: Given a factual claim, retrieve the passage that supports or contradicts it\nQuery: "
)


class Embedder(Protocol):
    async def __call__(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class Evidence:
    text: str
    method: str  # page_entiere | plongements | lexicale
    passages_total: int
    passages_kept: int


def split_passages(text: str, size: int = PASSAGE_CHARS) -> list[str]:
    """Passages d'environ `size` caracteres, coupes aux fins de ligne puis de phrase."""
    pieces: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) <= size:
            pieces.append(line)
            continue
        current = ""
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            if current and len(current) + len(sentence) + 1 > size:
                pieces.append(current)
                current = ""
            current = f"{current} {sentence}".strip()
            while len(current) > size:
                pieces.append(current[:size])
                current = current[size:]
        if current:
            pieces.append(current)
    passages: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 1 > size:
            passages.append(current)
            current = ""
        current = f"{current}\n{piece}".strip()
    if current:
        passages.append(current)
    return passages


def _content_words(text: str) -> set[str]:
    return {t for t in tokens(text) if len(t) >= 4 or t.isdigit()}


def lexical_score(claim: str, passage: str) -> float:
    words = _content_words(claim)
    if not words:
        return 0.0
    return len(words & _content_words(passage)) / len(words)


def number_score(claim: str, passage: str) -> float:
    claim_numbers = numbers(claim)
    if not claim_numbers:
        return 0.0
    passage_values = set()
    for _raw, values in numbers(passage, minimum=0):
        passage_values |= values
    hits = sum(1 for _raw, values in claim_numbers if values & passage_values)
    return hits / len(claim_numbers)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class PassageIndex:
    """Passages d'une page et leurs plongements, calcules une fois par page."""

    def __init__(self, text: str) -> None:
        self.passages = split_passages(text)
        self.vectors: list[list[float]] | None = None

    async def ensure_vectors(self, embedder: Embedder) -> None:
        if self.vectors is None:
            self.vectors = await embedder(self.passages)


async def select_evidence(
    claim: str,
    page_text: str,
    index: PassageIndex | None = None,
    embedder: Embedder | None = None,
    budget: int = MAX_EVIDENCE_CHARS,
) -> Evidence:
    if len(page_text) <= budget:
        return Evidence(page_text, "page_entiere", 1, 1)
    index = index or PassageIndex(page_text)
    passages = index.passages
    scores = [lexical_score(claim, p) + 0.5 * number_score(claim, p) for p in passages]
    method = "lexicale"
    if embedder is not None:
        try:
            await index.ensure_vectors(embedder)
            query = (await embedder([QUERY_INSTRUCTION + claim]))[0]
            scores = [
                _cosine(query, v) + 0.3 * s for v, s in zip(index.vectors or [], scores)
            ]
            method = "plongements"
        except Exception:  # noqa: BLE001 - repli lexical, la methode est consignee
            method = "lexicale"
    ranked = sorted(range(len(passages)), key=lambda i: -scores[i])
    kept: list[int] = []
    used = 0
    for i in ranked:
        if used + len(passages[i]) > budget:
            continue
        kept.append(i)
        used += len(passages[i])
    kept.sort()
    text = "\n[...]\n".join(passages[i] for i in kept)
    return Evidence(text, method, len(passages), len(kept))
