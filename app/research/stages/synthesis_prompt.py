"""Redaction v3 d'une section : sources etiquetees, consignes, faits etablis (C93).

Mesure citecheck (12-13/11/2026, benchmarks/CITATIONS.md) : en recherche
approfondie, 162 fautes sur 181 venaient de la redaction et 47 % des phrases
ne portaient aucune citation. Le prompt precedent :
- faisait recopier des URL completes au modele ([SOURCE:url]) ;
- transmettait les passages en JSON, avec des "Confirme independamment par X
  (similarite 0,81)" qui presentaient une proximite de sujet comme une
  confirmation ;
- demandait "6-10 paragraphes detailles explorant tous les aspects" puis
  "la longueur est une consequence, jamais un objectif" ;
- montrait un tableau d'exemple sans aucune citation dans ses lignes ;
- imposait de repondre aux questions du plan, fixees avant toute lecture,
  meme quand les passages n'en parlaient pas.

Ici chaque source porte une etiquette courte ([S1], [S2]...) que le code
convertit en marqueur [SOURCE:url] : l'URL n'est jamais recopiee par le
modele. Le reste du pipeline (bibliographie, invariants) est inchange.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.research.answer_check import split_sentences
from app.research.citations import CITATION_PATTERN

# [S1], [S1, S3], [S2; S4], [S 2] ; insensible a la casse.
ID_CITATION = re.compile(r"\[\s*S\s?\d+(?:\s*[,;]\s*S?\s?\d+)*\s*\]", re.IGNORECASE)
_ID_NUMBER = re.compile(r"\d+")

DEPTH_GUIDANCE = {
    "light": "Va a l'essentiel : les deux ou trois points principaux que les sources etablissent.",
    "moderate": "Couvre les points que les sources etablissent, sans detail secondaire.",
    "deep": (
        "Detaille chaque point que les sources etablissent : chiffres, dates, acteurs, "
        "nuances, et divergences entre sources."
    ),
}

LANGUAGE_NAMES = {
    "fr": "français",
    "en": "anglais",
    "es": "espagnol",
    "de": "allemand",
    "it": "italien",
    "pt": "portugais",
}


@dataclass
class SourceLabels:
    """Etiquette courte de chaque page fournie, dans l'ordre de premiere apparition."""

    urls: List[str] = field(default_factory=list)
    titles: Dict[str, str] = field(default_factory=dict)
    passages: Dict[str, List[str]] = field(default_factory=dict)

    @classmethod
    def from_passages(cls, selected_data: List[Dict[str, Any]]) -> "SourceLabels":
        labels = cls()
        for item in selected_data:
            url = str(item.get("source") or "").strip()
            if not url:
                continue
            if url not in labels.passages:
                labels.urls.append(url)
                labels.passages[url] = []
                labels.titles[url] = str(item.get("title") or "").strip()
            content = str(item.get("content") or "").strip()
            if content:
                labels.passages[url].append(content)
        return labels

    def label(self, url: str) -> str:
        return f"S{self.urls.index(url) + 1}"

    @property
    def known(self) -> set[str]:
        return {f"S{i}" for i in range(1, len(self.urls) + 1)}

    def url_for(self, label: str) -> Optional[str]:
        match = _ID_NUMBER.search(label)
        if not match:
            return None
        index = int(match.group()) - 1
        return self.urls[index] if 0 <= index < len(self.urls) else None

    def block(self) -> str:
        """Sources en texte : etiquette, titre, URL, puis les passages."""
        parts = []
        for url in self.urls:
            head = f"[{self.label(url)}] {self.titles.get(url) or 'Sans titre'} — {url}"
            parts.append(head + "\n" + "\n[...]\n".join(self.passages[url]))
        return "\n\n".join(parts)

    def for_verification(self) -> List[tuple[str, str]]:
        """(etiquette, texte) de chaque source, pour le verificateur."""
        return [
            (f"{self.label(url)} {self.titles.get(url) or ''}".strip(), "\n[...]\n".join(self.passages[url]))
            for url in self.urls
        ]


def ids_to_markers(text: str, labels: SourceLabels) -> tuple[str, List[str]]:
    """Remplace chaque etiquette [Sn] par [SOURCE:url]. Rend le texte et les
    etiquettes inconnues, retirees du texte."""
    unknown: List[str] = []

    def replace(match: re.Match[str]) -> str:
        markers = []
        for number in _ID_NUMBER.findall(match.group(0)):
            url = labels.url_for(number)
            if url is None:
                unknown.append(f"S{number}")
                continue
            marker = f"[SOURCE:{url}]"
            if marker not in markers:
                markers.append(marker)
        return " ".join(markers)

    converted = ID_CITATION.sub(replace, text)
    # Etiquette inconnue retiree : pas d'espace orphelin avant la ponctuation.
    converted = re.sub(r"[ \t]+(?=[.,;:!?])", "", converted)
    return converted, unknown


def established_facts(
    sections: Dict[str, Dict[str, Any]],
    current: str,
    total_chars: int,
    per_section_chars: int,
) -> List[str]:
    """Faits deja etablis : phrases citees des sections deja redigees.

    Memoire partagee entre sections : la redaction d'une section voit ce que
    les precedentes ont affirme avec une source, pour ne pas le repeter ni le
    contredire. Remplace l'apercu des 180 premiers caracteres de chaque
    section, qui ne disait rien des faits.
    """
    facts: List[str] = []
    used = 0
    for name, data in sections.items():
        if name == current:
            continue
        content = str((data or {}).get("content") or "")
        if not content or content.startswith("[SECTION_SANS_SOURCE]") or content.startswith("[SYNTHESE_ECHOUEE]"):
            continue
        taken = 0
        for sentence in split_sentences(content):
            if "[SOURCE:" not in sentence:
                continue
            plain = CITATION_PATTERN.sub("", sentence).strip()
            if not plain or taken + len(plain) > per_section_chars:
                continue
            if used + len(plain) > total_chars:
                return facts
            facts.append(f"- ({name}) {plain}")
            taken += len(plain)
            used += len(plain)
    return facts


def build_prompt_v3(
    *,
    query: str,
    section_name: str,
    language: str,
    depth: str,
    objectives: List[str],
    key_questions: List[str],
    labels: SourceLabels,
    facts: List[str],
) -> str:
    """Prompt de redaction v3 d'une section."""
    if language in LANGUAGE_NAMES:
        lang = (
            f"LANGUE : rédige intégralement en {LANGUAGE_NAMES[language]}, la langue de la demande. "
            "Les sources peuvent être dans d'autres langues : traduis ce que tu en reprends."
        )
    else:
        lang = "LANGUE : rédige dans la langue de la demande ci-dessous."

    lines = [
        lang,
        "",
        f'Rédige la section "{section_name}" d\'un rapport qui répond à : "{query}"',
        "",
        f"SOURCES ({len(labels.urls)}), chacune avec son étiquette :",
        "",
        labels.block(),
        "",
    ]
    if objectives or key_questions:
        lines.append("CE QUE LA SECTION COUVRE, dans la mesure où les sources le permettent :")
        lines += [f"- {o}" for o in objectives]
        lines += [f"- {q}" for q in key_questions]
        lines += [
            "Un objectif ou une question que les sources ne documentent pas est omis : n'y réponds "
            "pas de mémoire et ne signale pas son absence.",
            "",
        ]
    if facts:
        lines.append(
            "FAITS DÉJÀ ÉTABLIS DANS LES SECTIONS PRÉCÉDENTES (ne les répète pas ; ne les contredis "
            "pas sans source) :"
        )
        lines += facts
        lines.append("")
    lines += [
        "RÈGLES",
        "1. Chaque phrase qui affirme un fait (chiffre, date, nom, acteur, lieu, résultat, cause) se "
        "termine par l'étiquette de la source qui l'établit : « ... en 2018 [S2]. » Plusieurs "
        "sources : [S1, S3]. N'écris jamais d'URL. Une phrase de transition sans fait n'a pas "
        "d'étiquette ; elle reste rare.",
        "2. Uniquement ce que disent les sources. Ce qu'elles ne disent pas n'est pas écrit, même si "
        "tu le sais. Ne complète jamais une donnée partielle (année, chiffre, pays, nom) par tes "
        "connaissances.",
        "3. Chiffres, dates et montants sont recopiés exactement, avec leur unité et leur année de "
        "référence. Si deux sources divergent, donne les deux valeurs avec leurs étiquettes.",
        f"4. Longueur : ce que les sources établissent, sans remplissage. {DEPTH_GUIDANCE.get(depth, DEPTH_GUIDANCE['moderate'])}",
        "5. Présentation : prose par défaut. Un tableau markdown quand tu compares au moins trois "
        "éléments sur les mêmes critères, avec une dernière colonne Source portant l'étiquette de "
        "chaque ligne :",
        "   | Élément | Critère tiré de la source | Source |",
        "   |---|---|---|",
        "   | élément A | valeur donnée par la source | [S1] |",
        "   | élément B | valeur donnée par la source | [S2] |",
        "   Une liste courte pour des éléments parallèles ; un sous-titre ### si la section couvre "
        "deux angles distincts.",
        "6. Si les sources ne traitent pas du sujet de la section, rends seulement : "
        "« Les sources fournies ne traitent pas de cette section. »",
        "",
        "Rends uniquement le texte de la section, sans son titre.",
    ]
    return "\n".join(lines)
