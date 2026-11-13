"""Verification d'une reponse par le modele : chaque phrase contre ses sources.

Mesure a l'origine (outil benchmarks/citecheck, 12/11/2026) : en recherche
rapide, le modele ecrivait "quarantieme jour" quand la source disait
"quatre-vingt-dixieme", declarait absente une donnee presente, ou prenait un
chiffre dans ses propres connaissances contre la source. L'etalonnage du meme
modele comme verificateur (59 cas au verdict connu) donne 57/59 verdicts
exacts et aucune fausse acceptation : il lit mieux en verifiant phrase par
phrase qu'en redigeant.

La verification rend les phrases fautives ; l'appelant decide quoi en faire
(reecriture unique avec la critique, en recherche rapide).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_HEADING_OR_RULE = re.compile(r"^(?:#{1,6}\s|\|?\s*:?-{3,})")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"«(\[*]?[A-ZÀ-ÖØ-Þ0-9])")
_CITATION = re.compile(r"\[(\d+(?:\s*[,;–-]\s*\d+)*)\]")
# Etiquettes de source de la redaction v3 : [S1], [S1, S3], [S2; S4].
_ID_CITATION = re.compile(r"\[\s*S\s?\d+(?:\s*[,;]\s*S?\s?\d+)*\s*\]", re.IGNORECASE)
# Appui rendu par le verificateur : "S2", "[S2]", "S2 Titre de la page".
_SUPPORT_LABEL = re.compile(r"\s*\[?\s*S\s?(\d+)\b", re.IGNORECASE)
MIN_WORDS = 4
FAULTY = ("partielle", "contredite", "non_soutenue")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "phrases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "numero": {"type": "integer"},
                    "verdict": {
                        "type": "string",
                        "enum": ["soutenue", "partielle", "contredite", "non_soutenue"],
                    },
                    "probleme": {"type": "string"},
                    "appuis": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["numero", "verdict", "probleme", "appuis"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["phrases"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Tu verifies une reponse redigee a partir de sources numerotees. Pour chaque phrase numerotee de la reponse, dis si les sources qu'elle cite la soutiennent.

Tu juges uniquement d'apres le texte des sources : tes propres connaissances ne comptent pas, meme si la phrase est vraie par ailleurs. Une phrase sans numero de source est jugee contre l'ensemble des sources.

Verdicts :
- soutenue : chaque element verifiable (acteurs, chiffres, dates, delais, lieux, portee) figure dans les sources citees, eventuellement reformule ou traduit.
- partielle : l'essentiel figure, mais un detail (date precise, chiffre, nom) ne figure dans aucune source citee.
- contredite : une source citee dit autre chose sur le meme point (autre chiffre, autre date, autre delai, sens inverse).
- non_soutenue : les sources citees ne contiennent pas l'essentiel de la phrase, ou la phrase affirme que les sources ne disent pas quelque chose qu'elles disent.

Une phrase sans element verifiable (transition, annonce du plan) est soutenue.

Pour chaque phrase qui n'est pas soutenue, "probleme" dit en une phrase ce qui manque ou differe, en citant la valeur de la source quand elle existe. Pour une phrase soutenue, "probleme" est une chaine vide.

"appuis" donne les etiquettes des sources qui soutiennent la phrase, telles qu'elles figurent en tete de chaque source (par exemple "S2" ou "2") ; liste vide si aucune ne la soutient ou si la phrase n'a rien a verifier."""


@dataclass
class SentenceVerdict:
    number: int
    sentence: str
    verdict: str
    problem: str
    supports: tuple[str, ...] = ()


def split_sentences(text: str) -> list[str]:
    """Phrases d'une reponse courte, appels de citation compris."""
    sentences: list[str] = []
    for paragraph in (text or "").split("\n"):
        paragraph = paragraph.strip()
        if not paragraph or _HEADING_OR_RULE.match(paragraph):
            continue
        for piece in _SENTENCE_END.split(paragraph):
            piece = piece.strip()
            if piece:
                sentences.append(piece)
    return sentences


def build_messages(
    sentences: list[str], sources: list[tuple[str, str]], label_style: str = "numero"
) -> list[dict[str, str]]:
    """sources : (titre ou URL, texte). label_style "numero" : les phrases
    citent [1], [2]... dans l'ordre des sources ; "url" : elles citent
    [SOURCE:url] (texte brut de la recherche approfondie) ; "id" : elles
    citent l'etiquette de la source ([S1], [S2]...), donnee comme titre."""
    if label_style == "url":
        sources_block = "\n\n".join(f"[SOURCE:{label}]\n{text}" for label, text in sources)
    elif label_style == "id":
        sources_block = "\n\n".join(f"[{label}]\n{text}" for label, text in sources)
    else:
        sources_block = "\n\n".join(
            f"[Source {i}: {title}]\n{text}" for i, (title, text) in enumerate(sources, start=1)
        )
    sentences_block = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences, start=1))
    user = f"SOURCES :\n{sources_block}\n\nPHRASES DE LA REPONSE :\n{sentences_block}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


async def check_answer(
    llm_client: Any,
    answer: str,
    sources: list[tuple[str, str]],
    label_style: str = "numero",
    model: str = "",
) -> list[SentenceVerdict]:
    """Verdict de chaque phrase de la reponse (phrases trop courtes exclues).

    model : modele du verificateur, vide pour celui du client.
    Leve l'exception du client LLM si la verification echoue : l'appelant
    garde alors la reponse non verifiee et le signale.
    """
    sentences = [
        s for s in split_sentences(answer)
        if len(_ID_CITATION.sub("", _CITATION.sub("", s)).split()) >= MIN_WORDS
    ]
    if not sentences:
        return []
    extra: dict[str, Any] = {"model": model} if model else {}
    raw = await llm_client.generate_structured(
        build_messages(sentences, sources, label_style), schema=SCHEMA, schema_name="verification",
        temperature=0, **extra,
    )
    verdicts: list[SentenceVerdict] = []
    for item in (raw or {}).get("phrases") or []:
        number = item.get("numero")
        if not isinstance(number, int) or not 1 <= number <= len(sentences):
            continue
        verdicts.append(SentenceVerdict(
            number=number,
            sentence=sentences[number - 1],
            verdict=str(item.get("verdict") or ""),
            problem=str(item.get("probleme") or "").strip(),
            supports=tuple(str(s).strip() for s in (item.get("appuis") or []) if str(s).strip()),
        ))
    return verdicts


def add_missing_citations(
    text: str, verdicts: list[SentenceVerdict], labels: set[str]
) -> tuple[str, int]:
    """Ajoute l'etiquette de source aux phrases soutenues qui n'en portent pas.

    Le verificateur designe la source qui soutient la phrase : la citation
    est posee par le code, sans reecriture par le modele. Seules les
    etiquettes connues (labels, ex. {"S1", "S2"}) sont retenues. Rend le
    texte et le nombre de phrases completees.
    """
    added = 0
    for v in verdicts:
        if v.verdict != "soutenue" or _ID_CITATION.search(v.sentence) or "[SOURCE:" in v.sentence:
            continue
        supports = []
        for raw in v.supports:
            match = _SUPPORT_LABEL.match(raw)
            if match:
                supports.append("S" + match.group(1))
        known = [s for s in dict.fromkeys(supports) if s in labels]
        if not known or v.sentence not in text:
            continue
        body = v.sentence.rstrip()
        end = ""
        while body and body[-1] in ".!?":
            end = body[-1] + end
            body = body[:-1]
        cited = f"{body.rstrip()} [{', '.join(known)}]{end}"
        text = text.replace(v.sentence, cited, 1)
        added += 1
    return text, added


def correction_request(faulty: list[SentenceVerdict], citation_format: str = "[1], [2]...") -> str:
    """Message demandant la reecriture de la reponse, critique jointe."""
    lines = [
        "Verification de ta reponse contre les sources : les phrases suivantes ne sont pas "
        "soutenues telles quelles.",
    ]
    for v in faulty:
        lines.append(f"- « {v.sentence} » ({v.verdict}) : {v.problem or 'non soutenue'}")
    lines.append(
        "Reecris la reponse complete : corrige ces phrases d'apres les sources, ou retire-les si "
        "les sources ne permettent pas de les soutenir. Garde les phrases correctes et les memes "
        f"regles (uniquement les sources fournies, citations {citation_format}). Rends uniquement la "
        "reponse reecrite."
    )
    return "\n".join(lines)
