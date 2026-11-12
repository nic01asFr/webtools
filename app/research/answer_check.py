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

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"«(\[*]?[A-ZÀ-ÖØ-Þ0-9])")
_CITATION = re.compile(r"\[(\d+(?:\s*[,;–-]\s*\d+)*)\]")
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
                },
                "required": ["numero", "verdict", "probleme"],
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

Pour chaque phrase qui n'est pas soutenue, "probleme" dit en une phrase ce qui manque ou differe, en citant la valeur de la source quand elle existe. Pour une phrase soutenue, "probleme" est une chaine vide."""


@dataclass
class SentenceVerdict:
    number: int
    sentence: str
    verdict: str
    problem: str


def split_sentences(text: str) -> list[str]:
    """Phrases d'une reponse courte, appels de citation compris."""
    sentences: list[str] = []
    for paragraph in (text or "").split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for piece in _SENTENCE_END.split(paragraph):
            piece = piece.strip()
            if piece:
                sentences.append(piece)
    return sentences


def build_messages(sentences: list[str], sources: list[tuple[str, str]]) -> list[dict[str, str]]:
    """sources : (titre, texte) dans l'ordre de leur numero."""
    sources_block = "\n\n".join(
        f"[Source {i}: {title}]\n{text}" for i, (title, text) in enumerate(sources, start=1)
    )
    sentences_block = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences, start=1))
    user = f"SOURCES :\n{sources_block}\n\nPHRASES DE LA REPONSE :\n{sentences_block}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


async def check_answer(llm_client: Any, answer: str, sources: list[tuple[str, str]]) -> list[SentenceVerdict]:
    """Verdict de chaque phrase de la reponse (phrases trop courtes exclues).

    Leve l'exception du client LLM si la verification echoue : l'appelant
    garde alors la reponse non verifiee et le signale.
    """
    sentences = [s for s in split_sentences(answer) if len(_CITATION.sub("", s).split()) >= MIN_WORDS]
    if not sentences:
        return []
    raw = await llm_client.generate_structured(
        build_messages(sentences, sources), schema=SCHEMA, schema_name="verification", temperature=0
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
        ))
    return verdicts


def correction_request(faulty: list[SentenceVerdict]) -> str:
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
        "regles (uniquement les sources fournies, citations [1], [2]...). Rends uniquement la "
        "reponse reecrite."
    )
    return "\n".join(lines)
