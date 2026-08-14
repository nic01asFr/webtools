"""
Densite factuelle d'un fragment de texte.

POURQUOI CE MODULE

Le scoring de pertinence (mots-cles + similarite semantique + corroboration)
mesure si un fragment PARLE DU BON SUJET. Il ne mesure pas s'il contient des
faits VERIFIABLES.

Consequence observee : un paragraphe d'introduction qui reformule le sujet
en termes generaux score tres bien — forte correspondance de mots-cles,
forte similarite semantique — mais ne contient ni chiffre, ni date, ni
donnee citable. Un tableau statistique score moins bien semantiquement (peu
de phrases, vocabulaire pauvre) alors que c'est lui qui porte l'information
qu'on veut citer.

Le pipeline retenait donc preferentiellement de la prose generale, et le
modele, faute de donnees precises dans ce qu'on lui donnait, completait avec
ce qu'il savait du sujet — d'ou des chiffres plausibles mais faux, et un
taux de citations validees de 61%.

CE QUE FAIT CE MODULE

Il mesure la densite de marqueurs factuels : nombres, pourcentages, dates,
unites, montants, comparatifs quantifies. C'est deliberement une heuristique
lexicale, sans appel LLM : le cout doit rester nul puisque le calcul
s'applique a chaque fragment de chaque source.

CE QU'IL NE FAIT PAS

Il ne juge NI la veracite NI la pertinence. Un fragment dense en chiffres
mais hors sujet reste ecarte par le scoring de pertinence — la densite est
un FACTEUR parmi d'autres, pas un critere autonome. C'est important : sinon
on privilegierait des tableaux de donnees sans rapport avec la section.

ETAT DE L'ART

Les travaux 2025-2026 sur le chunking optimisent la PERTINENCE de la
recuperation (Vectara/NAACL 2025, benchmark Vecta 2026, late chunking,
parent-child). Aucun, dans ce qui a ete consulte, ne score explicitement la
CITABILITE d'un fragment — alors que c'est elle qui determine la validite
des citations en aval, et donc la metrique FACT.
"""

import re
from typing import Dict

# Marqueurs de fait verifiable. Chacun est un indice qu'une affirmation
# construite sur ce fragment pourra etre confrontee a la source.
_PATTERNS = {
    # 28,9% / 45 % — le marqueur le plus fort : un pourcentage est presque
    # toujours une donnee reprise telle quelle
    "percent": re.compile(r"\d+(?:[.,]\d+)?\s*%"),
    # 2021, 1998 — ancrage temporel, indispensable pour qu'un chiffre soit
    # verifiable (le cas d'erreur observe : bon chiffre, mauvaise annee)
    "year": re.compile(r"\b(?:19|20)\d{2}\b"),
    # 36.2 million / 1,500 / 45 000
    "large_number": re.compile(r"\b\d{1,3}(?:[  ,]\d{3})+(?:[.,]\d+)?\b|\b\d+[.,]\d+\b"),
    # 12 km, 3.5 GB, 45 kg, 200 MW
    "unit": re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:km|m|cm|mm|kg|g|t|ha|MW|GW|kWh|GB|TB|Mo|Go|°C|%)\b", re.I),
    # $1.5 trillion, 45 M€, EUR 200
    "money": re.compile(r"(?:[$€£]\s?\d|\d+\s?(?:M€|Md€|EUR|USD|milliards?|millions?))", re.I),
    # "a augmente de", "hausse de", "compare a" — comparatifs quantifies
    "comparative": re.compile(
        r"\b(?:augment\w+|diminu\w+|hauss\w+|baiss\w+|croissance|recul|"
        r"increase[ds]?|decrease[ds]?|growth|decline|rose|fell)\b.{0,30}?\d",
        re.I),
}

# Marqueurs de prose generale : leur presence n'est pas penalisante en soi,
# mais un fragment qui n'a QUE cela est peu citable.
_HEDGING = re.compile(
    r"\b(?:generalement|globalement|souvent|parfois|certains|plusieurs|"
    r"generally|typically|often|various|several|many|some)\b", re.I)


def factual_density(text: str) -> Dict:
    """
    Retourne un score de 0 a 1 et le detail des marqueurs trouves.

    Le score sature : au-dela d'une certaine densite, un fragment n'est pas
    "plus citable" — un tableau de 200 nombres ne vaut pas 40 fois un
    paragraphe qui en contient 5.
    """
    t = (text or "").strip()
    if len(t) < 100:
        return {"score": 0.0, "markers": {}, "density_per_1k": 0.0}

    markers = {name: len(rx.findall(t)) for name, rx in _PATTERNS.items()}
    total = sum(markers.values())

    # Densite pour 1 000 caracteres, pour ne pas favoriser les longs
    # fragments a densite egale.
    per_1k = total / (len(t) / 1000.0)

    # Saturation a 12 marqueurs / 1 000 chars : au-dela, c'est du tableau
    # brut, dont la valeur citable ne croit plus.
    score = min(per_1k / 12.0, 1.0)

    # Bonus : un fragment qui associe un chiffre A une annee est nettement
    # plus citable (c'est exactement l'erreur observee : "28,7% en 2020"
    # ecrit pour "28,9% en 2021").
    if markers.get("year", 0) and (markers.get("percent", 0) or markers.get("large_number", 0)):
        score = min(score * 1.25, 1.0)

    # Malus leger si le fragment est surtout du hedging sans donnees.
    if total == 0 and len(_HEDGING.findall(t)) >= 3:
        score = 0.0

    return {
        "score": round(score, 3),
        "markers": {k: v for k, v in markers.items() if v},
        "density_per_1k": round(per_1k, 2),
    }
