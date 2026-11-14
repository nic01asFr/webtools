"""Invariants d'un rapport de recherche approfondie.

check_report est une fonction pure : elle lit le rapport tel que produit par
l'orchestrateur (cles title, summary, sections[{title, content}],
bibliography[{id, url}]) et rend la liste des violations, sans rien corriger.

Codes de violation (stables, a reutiliser tels quels dans les tests) :
- ORPHAN_CITATION     : citation [n] sans entree de bibliographie
- BIBLIO_WITHOUT_URL  : entree de bibliographie sans URL
- INTERNAL_MARKER     : marqueur interne dans le titre, le resume ou le texte
- EMPTY_SECTION       : section sans contenu
- META_SECTION        : titre de section qui decrit le travail et non le sujet
- TRUNCATED_SECTION   : derniere phrase de la section non terminee
- CITATION_RESIDUE    : residu d'URL collee a une citation, ex. "[1]/b]"
- REFUSAL_TEXT        : texte de refus du modele publie comme contenu ou resume
"""

import re
from typing import Any

from app.research.refusal import is_refusal

INTERNAL_MARKERS = ("[SOURCE:", "[SECTION_SANS_SOURCE]", "[SYNTHESE_ECHOUEE]")

# Titres qui decrivent la demarche de travail. Meme liste que
# stages.plan_prompt.META_SECTION_PATTERNS, dupliquee pour que ce module
# reste sans dependance.
META_SECTION_PATTERNS = (
    "structure du rapport",
    "structure finale",
    "plan du rapport",
    "plan de travail",
    "strategie de recherche",
    "stratégie de recherche",
    "methodologie de recherche",
    "méthodologie de recherche",
    "criteres de validation",
    "critères de validation",
    "sources consultees",
    "sources consultées",
    "demarche",
    "démarche",
)

# Mots ou expressions entiers, comme stages.plan_prompt.is_meta_section :
# "Demarches administratives" decrit le sujet, pas le travail.
_META_SECTION_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(p) for p in META_SECTION_PATTERNS) + r")(?!\w)"
)

_CITATION = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")
_RESIDUE = re.compile(r"\[\d+\][^\s\[\]]+\]")
_TRAILING_CITATIONS = re.compile(r"(?:\s*\[\d+(?:\s*[,;]\s*\d+)*\])+\s*$")
# Fin de phrase admise : ponctuation forte, guillemet ou parenthese fermants,
# barre de tableau markdown.
_TERMINATORS = ".!?\u2026\u00bb\"')|"


def _texts(report: dict[str, Any]) -> list[tuple[str, str]]:
    """Paires (emplacement, texte) pour le titre, le resume et chaque section."""
    out: list[tuple[str, str]] = []
    for key in ("title", "summary"):
        value = report.get(key)
        if isinstance(value, str):
            out.append((key, value))
    for i, section in enumerate(report.get("sections") or []):
        if not isinstance(section, dict):
            continue
        label = f"section[{i}] {section.get('title') or ''}".strip()
        for key in ("title", "content"):
            value = section.get(key)
            if isinstance(value, str):
                out.append((f"{label}.{key}", value))
    return out


def _is_truncated(content: str) -> bool:
    """Vrai si le texte ne se termine pas sur une fin de phrase."""
    tail = _TRAILING_CITATIONS.sub("", content.rstrip()).rstrip()
    return bool(tail) and tail[-1] not in _TERMINATORS


def check_report(report: dict[str, Any]) -> dict[str, Any]:
    """Verifie les invariants du rapport.

    Retourne {"ok": bool, "violations": [{"code": str, "detail": str}]}.
    """
    violations: list[dict[str, str]] = []

    def add(code: str, detail: str) -> None:
        violations.append({"code": code, "detail": detail})

    bibliography = [b for b in (report.get("bibliography") or []) if isinstance(b, dict)]
    known_ids = {b.get("id") for b in bibliography}

    for entry in bibliography:
        if not str(entry.get("url") or "").strip():
            add("BIBLIO_WITHOUT_URL", f"entree {entry.get('id')!r} sans url")

    for where, text in _texts(report):
        for marker in INTERNAL_MARKERS:
            if marker in text:
                add("INTERNAL_MARKER", f"{marker} dans {where}")
        if where.endswith(".content") or where == "summary":
            if is_refusal(text):
                add("REFUSAL_TEXT", f"refus du modele dans {where}")
            for match in _CITATION.finditer(text):
                for number in re.split(r"\s*[,;]\s*", match.group(1)):
                    if int(number) not in known_ids:
                        add("ORPHAN_CITATION", f"[{number}] dans {where}")
            for match in _RESIDUE.finditer(text):
                add("CITATION_RESIDUE", f"{match.group(0)!r} dans {where}")

    for i, section in enumerate(report.get("sections") or []):
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "")
        content = str(section.get("content") or "")
        label = f"section[{i}] {title}".strip()
        if not content.strip():
            add("EMPTY_SECTION", label)
        elif _is_truncated(content):
            add("TRUNCATED_SECTION", f"{label} se termine par {content.rstrip()[-40:]!r}")
        if _META_SECTION_RE.search(title.lower()):
            add("META_SECTION", label)

    return {"ok": not violations, "violations": violations}
