"""Affirmations citees d'une reponse webtools.

Une affirmation est l'unite de texte qui porte un appel de citation [n] :
une phrase, une ligne de tableau ou un element de liste. Chaque numero cite
donne un triplet (affirmation, numero, URL). Un numero sans entree dans la
liste des sources est une citation orpheline.

Formats lus :
- recherche approfondie : payload de /api/v1/research/deep, rapport sous
  "result" ; numero = `id` de l'entree de bibliographie, sinon sa position
  (meme regle que benchmarks/convert.py) ;
- recherche rapide : payload de /api/v1/research/quick ; numero = position
  dans `sources` (le prompt numerote les sources dans cet ordre).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# [1], [1][2], [1, 2], [1-3], [Source 1]
_MARKER = re.compile(
    r"\[(?:sources?\s*)?(\d+(?:\s*(?:[,;]|-|–)\s*(?:sources?\s*)?\d+)*)\]", re.IGNORECASE
)
_LEADING_MARKERS = re.compile(rf"^(?:\s*{_MARKER.pattern})+", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"«(\[]?[A-ZÀ-ÖØ-Þ0-9])")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{3,}")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
# Marqueurs internes du pipeline : une section qui en porte n'est pas un contenu.
INTERNAL_MARKERS = ("[SECTION_SANS_SOURCE]", "[SYNTHESE_ECHOUEE]")
MIN_WORDS_UNIT = 6


@dataclass(frozen=True)
class Citation:
    """Un triplet (affirmation, numero, URL) ; url None = citation orpheline."""

    task_id: str
    location: str
    claim: str
    context: str
    ref: int
    url: str | None


@dataclass
class ParsedAnswer:
    """Triplets d'une reponse et comptes de structure."""

    task_id: str
    mode: str
    citations: list[Citation] = field(default_factory=list)
    units_total: int = 0
    units_cited: int = 0
    skipped_sections: int = 0
    sources: dict[int, str] = field(default_factory=dict)

    @property
    def orphans(self) -> list[Citation]:
        return [c for c in self.citations if c.url is None]


def parse_refs(group: str) -> list[int]:
    """Numeros d'un appel : "1, 3" -> [1, 3] ; "2-4" -> [2, 3, 4]."""
    refs: list[int] = []
    for part in re.split(r"\s*[,;]\s*", re.sub(r"(?i)sources?\s*", "", group)):
        bounds = re.split(r"\s*[-–]\s*", part.strip())
        if len(bounds) == 2 and bounds[0].isdigit() and bounds[1].isdigit():
            low, high = int(bounds[0]), int(bounds[1])
            if 0 < high - low < 20:
                refs.extend(range(low, high + 1))
                continue
        refs.extend(int(b) for b in bounds if b.isdigit())
    return refs


def strip_markers(text: str) -> str:
    """Texte sans appels de citation, espaces et ponctuation recolles."""
    text = _MARKER.sub("", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _units(text: str) -> list[tuple[str, str]]:
    """Decoupe un texte markdown en unites (texte, contexte).

    Le contexte est l'unite precedente du meme paragraphe, ou l'en-tete du
    tableau pour une ligne de tableau : il aide le juge a resoudre un pronom.
    """
    units: list[tuple[str, str]] = []
    paragraph: list[str] = []
    table_header = ""

    def flush() -> None:
        if not paragraph:
            return
        joined = " ".join(paragraph)
        paragraph.clear()
        pieces = [p for p in _SENTENCE_END.split(joined) if p.strip()]
        merged: list[str] = []
        for piece in pieces:
            lead = _LEADING_MARKERS.match(piece)
            if lead and merged:
                merged[-1] = merged[-1] + " " + lead.group(0).strip()
                piece = piece[lead.end():]
            if piece.strip():
                merged.append(piece.strip())
        previous = ""
        for sentence in merged:
            units.append((sentence, previous))
            previous = strip_markers(sentence)

    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
            table_header = ""
            continue
        if stripped.startswith("#"):
            flush()
            continue
        if stripped.startswith("|"):
            flush()
            if _TABLE_RULE.match(stripped):
                continue
            row = " | ".join(c.strip() for c in stripped.strip("|").split("|"))
            if not table_header:
                table_header = row
                continue
            units.append((row, f"En-tete du tableau : {table_header}"))
            continue
        bullet = _BULLET.match(stripped)
        if bullet:
            flush()
            paragraph.append(stripped[bullet.end():])
            flush()
            continue
        paragraph.append(stripped)
    flush()
    return units


def _collect(parsed: ParsedAnswer, location: str, text: str) -> None:
    for unit, context in _units(text):
        refs = [r for m in _MARKER.finditer(unit) for r in parse_refs(m.group(1))]
        claim = strip_markers(unit)
        if len(claim.split()) >= MIN_WORDS_UNIT or refs:
            parsed.units_total += 1
        if not refs:
            continue
        parsed.units_cited += 1
        for ref in dict.fromkeys(refs):
            parsed.citations.append(Citation(
                task_id=parsed.task_id, location=location, claim=claim,
                context=context, ref=ref, url=parsed.sources.get(ref),
            ))


def _report(payload: dict[str, Any]) -> dict[str, Any]:
    report = payload.get("result") or payload.get("report") or payload
    return report if isinstance(report, dict) else {}


def deep_sources(payload: dict[str, Any]) -> dict[int, str]:
    """Numero -> URL de la bibliographie d'un rapport approfondi."""
    sources: dict[int, str] = {}
    for i, item in enumerate(_report(payload).get("bibliography") or [], start=1):
        if not isinstance(item, dict):
            continue
        number = item.get("id") if isinstance(item.get("id"), int) else i
        url = (item.get("url") or "").strip()
        if url:
            sources[number] = url
    return sources


def parse_deep(task_id: str, payload: dict[str, Any]) -> ParsedAnswer:
    parsed = ParsedAnswer(task_id=task_id, mode="deep", sources=deep_sources(payload))
    report = _report(payload)
    summary = report.get("summary") or ""
    if summary and not any(m in summary for m in INTERNAL_MARKERS):
        _collect(parsed, "resume", summary)
    for index, section in enumerate(report.get("sections") or [], start=1):
        if not isinstance(section, dict):
            continue
        content = section.get("content") or ""
        if any(m in content for m in INTERNAL_MARKERS):
            parsed.skipped_sections += 1
            continue
        title = (section.get("title") or "").strip()
        _collect(parsed, f"section {index} : {title}", content)
    return parsed


def parse_quick(task_id: str, payload: dict[str, Any]) -> ParsedAnswer:
    sources = {
        i: (s.get("url") or "").strip()
        for i, s in enumerate(payload.get("sources") or [], start=1)
        if isinstance(s, dict) and (s.get("url") or "").strip()
    }
    parsed = ParsedAnswer(task_id=task_id, mode="quick", sources=sources)
    _collect(parsed, "reponse", payload.get("answer") or "")
    return parsed


def parse_answer(task_id: str, mode: str, payload: dict[str, Any]) -> ParsedAnswer:
    if mode == "deep":
        return parse_deep(task_id, payload)
    if mode == "quick":
        return parse_quick(task_id, payload)
    raise ValueError(f"mode inconnu : {mode!r}")


def pipeline_texts(mode: str, payload: dict[str, Any]) -> dict[str, str]:
    """URL -> texte que le pipeline avait pour cette source, s'il est joint.

    Approfondie : champ `data` des sections (include_section_data), texte
    extrait avant selection des passages. Rapide : champ `content` des
    sources (include_source_content), texte exact transmis au modele.
    """
    texts: dict[str, str] = {}
    if mode == "deep":
        for section in _report(payload).get("sections") or []:
            for item in (section or {}).get("data") or []:
                if not isinstance(item, dict):
                    continue
                url = (item.get("source") or item.get("url") or "").strip()
                content = str(item.get("content") or "").strip()
                # Une source reprise dans plusieurs sections n'est comptee qu'une fois.
                if url and content and content not in texts.get(url, ""):
                    texts[url] = (texts.get(url, "") + "\n" + content).strip()
    elif mode == "quick":
        for source in payload.get("sources") or []:
            if isinstance(source, dict) and source.get("url") and source.get("content"):
                texts[source["url"].strip()] = str(source["content"])
    return texts
