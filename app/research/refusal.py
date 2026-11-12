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
    # Anglais
    r"\bi (?:cannot|can't|am unable to|am not able to) (?:write|draft|produce)",
    r"it is (?:not possible|impossible) to (?:write|draft|produce)",
    r"the (?:provided|supplied|given) (?:data|sources|documents|passages) (?:do|does) not contain",
    r"none of the (?:provided|supplied) (?:sources|data|documents)",
]
_REFUSAL = re.compile("|".join(_PATTERNS), re.IGNORECASE)


def is_refusal(text: str | None) -> bool:
    """Vrai si le texte commence par un refus de rediger."""
    if not text:
        return False
    return bool(_REFUSAL.search(text.strip()[:HEAD_CHARS]))
