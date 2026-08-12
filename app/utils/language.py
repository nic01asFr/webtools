"""
Detection de la langue d'une consigne, sans dependance externe.

Pourquoi pas le LLM : le prompt du plan lui demandait de declarer la langue
de la consigne, avec des exemples explicites. Teste deux fois sur une
consigne anglaise ("What are the main advantages of the Rust programming
language..."), il a repondu "fr" les deux fois. Le prompt porte deja
beaucoup (structure, objectifs, questions cles, requetes, profondeur) et
cette decision-la s'y perd.

Une detection deterministe est ici superieure : la langue d'un texte est
une propriete objective, elle n'a pas besoin d'etre "jugee". Elle est aussi
reproductible, ce qui compte pour un benchmark.

Methode : frequence des mots-outils (articles, prepositions, conjonctions).
Ce sont les mots les plus frequents d'une langue et les plus discriminants
entre langues proches. Suffisant pour trancher entre les langues courantes
sur une consigne de quelques dizaines de mots.
"""

import re
from collections import Counter
from typing import Dict, Set

# Mots-outils par langue. Volontairement courts et tres frequents : sur une
# consigne de 20 mots, quelques occurrences suffisent a trancher.
STOPWORD_SETS: Dict[str, Set[str]] = {
    "fr": {"le", "la", "les", "des", "du", "un", "une", "et", "ou", "dans",
           "sur", "pour", "par", "avec", "sans", "que", "qui", "quel",
           "quelle", "quels", "quelles", "est", "sont", "au", "aux", "ce",
           "cette", "ces", "son", "leur", "plus", "comment", "pourquoi",
           "quelles", "entre", "chez", "dont", "vers"},
    "en": {"the", "of", "and", "or", "in", "on", "for", "by", "with",
           "without", "that", "which", "what", "how", "why", "is", "are",
           "be", "to", "from", "their", "there", "this", "these", "at",
           "as", "an", "about", "between", "into", "over"},
    "es": {"el", "la", "los", "las", "de", "del", "un", "una", "y", "o",
           "en", "para", "por", "con", "sin", "que", "cual", "como",
           "es", "son", "su", "sus", "este", "esta", "entre", "sobre"},
    "de": {"der", "die", "das", "den", "dem", "des", "ein", "eine", "und",
           "oder", "in", "auf", "fur", "von", "mit", "ohne", "dass",
           "welche", "wie", "warum", "ist", "sind", "zu", "uber"},
    "it": {"il", "lo", "la", "gli", "le", "di", "del", "un", "una", "e",
           "o", "in", "su", "per", "con", "senza", "che", "quale", "come",
           "perche", "sono", "questo", "questa", "tra"},
    "pt": {"o", "a", "os", "as", "de", "do", "da", "um", "uma", "e", "ou",
           "em", "para", "por", "com", "sem", "que", "qual", "como",
           "porque", "sao", "este", "esta", "entre", "sobre"},
}

DEFAULT_LANGUAGE = "all"


def detect_language(text: str, min_words: int = 4) -> str:
    """
    Retourne un code ISO 639-1, ou "all" si indetermine.

    "all" plutot qu'un choix par defaut arbitraire : ne restreindre aucune
    recherche vaut mieux que la restreindre a la mauvaise langue.
    """
    if not text:
        return DEFAULT_LANGUAGE

    words = re.findall(r"[a-zA-ZÀ-ÿ']+", text.lower())
    if len(words) < min_words:
        return DEFAULT_LANGUAGE

    counts = Counter()
    for lang, stops in STOPWORD_SETS.items():
        counts[lang] = sum(1 for w in words if w in stops)

    best, best_score = counts.most_common(1)[0]
    if best_score == 0:
        return DEFAULT_LANGUAGE

    # Marge requise sur le second : sans elle, une consigne technique
    # truffee de termes anglais serait classee "en" alors qu'elle est
    # redigee en francais (ou l'inverse), sur un ecart d'une occurrence.
    second_score = counts.most_common(2)[1][1] if len(counts) > 1 else 0
    if best_score - second_score < 2 and best_score < 4:
        return DEFAULT_LANGUAGE

    return best
