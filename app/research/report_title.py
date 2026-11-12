"""Titre court d'un rapport, derive de la consigne sans appel LLM.

Le titre reste dans la langue de la consigne : aucun mot n'est ajoute ni
traduit, la consigne est seulement raccourcie proprement.
"""

import re

MAX_TITLE_CHARS = 80

# Fin de la premiere phrase : ponctuation forte suivie d'un espace.
_SENTENCE_END = re.compile(r"(?<=[.!?\u2026])\s+")


def short_title(query: str, max_chars: int = MAX_TITLE_CHARS) -> str:
    """Premiere phrase de la consigne, coupee a une frontiere de mot.

    Retourne une chaine vide si la consigne est vide.
    """
    text = " ".join((query or "").split())
    if not text:
        return ""
    first = _SENTENCE_END.split(text, maxsplit=1)[0].strip()
    first = first.rstrip(".!?\u2026:;,- ")
    if len(first) > max_chars:
        cut = first[:max_chars]
        # Coupe au dernier espace si la coupure tombe dans un mot.
        if first[max_chars] != " " and " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        first = cut.rstrip(" ,;:-")
    return first[:1].upper() + first[1:]
