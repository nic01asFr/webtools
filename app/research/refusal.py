"""Detection des refus du modele de redaction.

Observe en production le 12/11/2026 : quand les passages retenus pour une
section sont hors sujet, le modele refuse a juste titre ("Il est impossible de
rediger la section demandee ... les donnees fournies ne contiennent aucune
information pertinente"), mais ce texte etait publie comme contenu de la
section, et meme repris comme resume du rapport. Un refus n'est pas du
contenu : la section est traitee comme une section sans source.

La detection ne regarde que le debut du texte, ou le modele formule son
refus : une phrase analogue au milieu d'une section argumentee ne doit pas
la faire rejeter.
"""

from __future__ import annotations

import re

# Debut de texte examine, en caracteres.
HEAD_CHARS = 700

_PATTERNS = [
    # Francais
    r"il (?:est|m'est) impossible de r[ée]diger",
    r"je ne (?:peux|suis pas en mesure de) (?:pas )?r[ée]diger",
    r"(?:les|ces) (?:donn[ée]es|sources|documents|passages|extraits) (?:disponibles|fourni(?:e)?s)(?: \S+){0,5} ne (?:contiennent|comportent|permettent|mentionnent|traitent) (?:aucune?|pas|ni)",
    r"aucune (?:des )?(?:donn[ée]es|sources) fournies? ne (?:mentionne|traite|contient|porte)",
    # Variante reelle du 12/11/2026 (recul du trait de cote, sources sur le
    # recul de l'emploi industriel).
    r"incompatibilit[ée] (?:\S+ )?entre les (?:donn[ée]es|sources) fournies et la demande",
    r"(?:elles|ils|celles-ci|ceux-ci) ne contiennent aucune information sur",
    # Variante reelle du 12/11/2026 (ZFE en France, sources sur les poussieres
    # au Nord du Senegal) : publiee comme premiere phrase de section.
    r"(?:les|ces) (?:\S+ )?(?:sources|donn[ée]es|documents|passages|extraits) fourni(?:e)?s(?: \S+){0,4} "
    r"(?:portent|traitent|concernent) (?:exclusivement|uniquement|seulement)",
    # Anglais
    r"\bi (?:cannot|can't|am unable to|am not able to) (?:write|draft|produce)",
    r"it is (?:not possible|impossible) to (?:write|draft|produce)",
    r"the (?:provided|supplied|given) (?:data|sources|documents|passages) (?:do|does) not contain",
    r"none of the (?:provided|supplied) (?:sources|data|documents)",
]
_REFUSAL = re.compile("|".join(_PATTERNS), re.IGNORECASE)
# Marqueurs de citation du texte brut ([SOURCE:url]) : une URL longue peut
# repousser le refus hors de la fenetre examinee. Observe le 12/11/2026 : le
# refus passait le filtre a la redaction, puis etait detecte par l'invariant
# une fois les marqueurs convertis en [n].
_SOURCE_MARKER = re.compile(r"\[SOURCE:[^\]]*\]?")


def is_refusal(text: str | None) -> bool:
    """Vrai si le texte commence par un refus de rediger."""
    if not text:
        return False
    head = _SOURCE_MARKER.sub("[n]", text).strip()[:HEAD_CHARS]
    return bool(_REFUSAL.search(head))
