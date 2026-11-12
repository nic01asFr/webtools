"""Verdict du juge sur une affirmation et le texte de sa source.

Le juge est un LLM : son verdict n'est accepte que s'il est etaye. Un
verdict "soutenue", "partielle" ou "contredite" doit citer un extrait qui
figure reellement dans le texte (controle deterministe, texte.quote_found) ;
sinon le statut devient "non_etaye" et la citation n'est pas comptee comme
juste. L'exactitude du juge lui-meme est mesuree sur un jeu d'etalonnage
(cli etalonner) avant toute mesure du service.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from citecheck.llm import Endpoint, LLMError
from citecheck.preuves import Embedder, PassageIndex, select_evidence
from citecheck.texte import fold, missing_numbers, quote_found

VERDICTS = ("soutenue", "partielle", "contredite", "non_soutenue")
# Statuts finaux en plus des verdicts : non_etaye (extrait introuvable),
# inverifiable (page inaccessible), orpheline (numero sans source),
# erreur_juge (aucune reponse exploitable).
STATUSES = VERDICTS + ("non_etaye", "inverifiable", "orpheline", "erreur_juge")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "extrait": {"type": "string"},
        "analyse": {"type": "string"},
        "verdict": {"type": "string", "enum": list(VERDICTS)},
    },
    "required": ["extrait", "analyse", "verdict"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Tu verifies des citations. On te donne une AFFIRMATION tiree d'un rapport et le TEXTE de la page web que le rapport cite pour cette affirmation. Tu dis si ce texte soutient l'affirmation.

Tu juges uniquement d'apres le texte fourni : tes propres connaissances ne comptent pas, meme si l'affirmation est vraie par ailleurs. L'affirmation et la page peuvent etre dans des langues differentes.

Verdicts :
- soutenue : chaque element verifiable de l'affirmation (acteurs, objets, chiffres, dates, lieux, portee, relation de cause) figure dans le texte, eventuellement reformule ou traduit.
- partielle : l'element principal figure dans le texte, mais au moins un element secondaire n'y figure pas, sans etre contredit.
- contredite : le texte donne, sur le meme point, une information incompatible (autre chiffre, autre date, autre acteur, sens inverse).
- non_soutenue : le texte ne contient pas l'element principal de l'affirmation.

Regles :
- Un chiffre ou une date doit correspondre exactement, aux conventions d'ecriture pres (1 500 = 1,500 ; 28,7 % = 28.7%). La date d'un autre evenement presente dans la page n'est pas un soutien : si l'affirmation attribue 2013 a un evenement que la page date de 2018, le verdict est contredite.
- Le CONTEXTE sert seulement a comprendre de quoi parle l'affirmation (pronoms, sujet) ; ne le verifie pas.
- extrait : recopie MOT POUR MOT, sans le traduire ni le resumer, le passage du texte (une a trois phrases) sur lequel repose ton verdict. Chaine vide si le texte ne contient rien d'approchant.
- analyse : une ou deux phrases en francais : ce qui correspond, ce qui manque ou differe.

Reponds uniquement par l'objet JSON demande, avec les cles extrait, analyse, verdict."""


@dataclass
class Judgement:
    status: str
    verdict: str = ""
    quote: str = ""
    quote_found: bool = False
    analysis: str = ""
    missing_numbers: list[str] = field(default_factory=list)
    evidence_method: str = ""
    passages_total: int = 0
    passages_kept: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_verdict(value: Any) -> str | None:
    text = fold(str(value or "")).replace(" ", "_").replace("-", "_")
    aliases = {"non_supportee": "non_soutenue", "supportee": "soutenue", "partiellement_soutenue": "partielle"}
    text = aliases.get(text, text)
    return text if text in VERDICTS else None


def build_messages(claim: str, context: str, page_text: str, url: str) -> list[dict[str, str]]:
    user = (
        f"CONTEXTE (non verifie) : {context or '(aucun)'}\n\n"
        f"AFFIRMATION : {claim}\n\n"
        f"TEXTE DE LA PAGE (source : {url}) :\n<<<\n{page_text}\n>>>"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def settle(raw: dict[str, Any], claim: str, full_text: str) -> Judgement:
    """Statut final a partir de la reponse brute du juge et du texte complet."""
    verdict = normalize_verdict(raw.get("verdict"))
    quote = str(raw.get("extrait") or "").strip()
    judgement = Judgement(
        status="erreur_juge",
        verdict=verdict or "",
        quote=quote,
        analysis=str(raw.get("analyse") or "").strip(),
        missing_numbers=missing_numbers(claim, full_text),
    )
    if verdict is None:
        judgement.error = f"verdict inconnu : {raw.get('verdict')!r}"
        return judgement
    judgement.quote_found = bool(quote) and quote_found(quote, full_text)
    if verdict in ("soutenue", "partielle", "contredite") and not judgement.quote_found:
        judgement.status = "non_etaye"
    else:
        judgement.status = verdict
    return judgement


class Judge:
    def __init__(self, endpoint: Endpoint, model: str, embedder: Embedder | None = None) -> None:
        self.endpoint = endpoint
        self.model = model
        self.embedder = embedder
        self._indexes: dict[str, PassageIndex] = {}

    async def judge(self, claim: str, context: str, text: str, url: str, key: str | None = None) -> Judgement:
        """Juge une affirmation contre un texte ; `key` partage le decoupage
        de la page entre ses citations."""
        index = None
        if key is not None:
            index = self._indexes.setdefault(key, PassageIndex(text))
        evidence = await select_evidence(claim, text, index=index, embedder=self.embedder)
        try:
            raw, _ = await self.endpoint.chat_json(
                self.model, build_messages(claim, context, evidence.text, url), schema=SCHEMA
            )
        except LLMError as exc:
            return Judgement(status="erreur_juge", error=str(exc)[:300],
                             evidence_method=evidence.method,
                             passages_total=evidence.passages_total,
                             passages_kept=evidence.passages_kept)
        judgement = settle(raw, claim, text)
        judgement.evidence_method = evidence.method
        judgement.passages_total = evidence.passages_total
        judgement.passages_kept = evidence.passages_kept
        return judgement


def embedder_for(endpoint: Endpoint, model: str) -> Embedder:
    async def embed(texts: list[str]) -> list[list[float]]:
        return await endpoint.embed(model, texts)

    return embed
