"""Normalisation de texte, chiffres et recherche d'extrait.

Tout est deterministe : ces fonctions servent a controler le juge, elles ne
doivent pas dependre d'un modele.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

_TRANSLATE = str.maketrans({
    "’": "'", "‘": "'", "ʼ": "'", "“": '"', "”": '"',
    "«": '"', "»": '"', "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ", " ": " ",
})
_SPACES = re.compile(r"\s+")
_TOKEN = re.compile(r"\w+", re.UNICODE)


def fold(text: str) -> str:
    """Minuscules, sans accents, ponctuation typographique unifiee, espaces reduits."""
    text = unicodedata.normalize("NFKC", text or "").translate(_TRANSLATE).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return _SPACES.sub(" ", text).strip()


def tokens(text: str) -> list[str]:
    """Mots du texte replie, dans l'ordre."""
    return _TOKEN.findall(fold(text))


# --- Chiffres ---------------------------------------------------------------

# Milliers separes par une espace (francais) : "12 000", "1 200 000,5".
_SPACED = r"(?<![\d.,])\d{1,3}(?:[   ]\d{3})+(?:,\d+)?(?![\d])"
# Nombre ordinaire, separateurs . ou , : "2018", "28,7", "299,792,458".
_PLAIN = r"(?<![\d.,])\d+(?:[.,]\d+)*(?![\d])"
_NUMBER = re.compile(f"{_SPACED}|{_PLAIN}")
_SCALE = re.compile(
    r"\s*(millions?|milliards?|billions?|thousand|mille|k\b|md\b|bn\b)", re.IGNORECASE
)
_SCALES = {
    "million": 10**6, "millions": 10**6,
    "milliard": 10**9, "milliards": 10**9, "billion": 10**9, "billions": 10**9,
    "md": 10**9, "bn": 10**9,
    "thousand": 10**3, "mille": 10**3, "k": 10**3,
}


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text).normalize()
    except InvalidOperation:
        return None


def interpretations(raw: str) -> set[Decimal]:
    """Valeurs possibles d'un nombre ecrit, sans presumer de la langue.

    "1,500" vaut 1500 (anglais) ou 1,5 (francais) : les deux sont rendus.
    Deux chiffres ecrits se correspondent si leurs interpretations se
    recouvrent.
    """
    raw = raw.replace(" ", " ").replace(" ", " ")
    values: set[Decimal] = set()
    if " " in raw:
        whole, _, frac = raw.replace(" ", "").partition(",")
        value = _decimal(f"{whole}.{frac}" if frac else whole)
        return {value} if value is not None else set()
    seps = set(re.findall(r"[.,]", raw))
    if not seps:
        value = _decimal(raw)
        return {value} if value is not None else set()
    groups = re.split(r"[.,]", raw)
    if len(seps) == 2:
        # "1,234.5" ou "1.234,5" : le dernier separateur est decimal.
        last = max(raw.rfind("."), raw.rfind(","))
        whole = re.sub(r"[.,]", "", raw[:last])
        value = _decimal(f"{whole}.{raw[last + 1:]}")
        return {value} if value is not None else set()
    if all(len(g) == 3 for g in groups[1:]):
        value = _decimal("".join(groups))
        if value is not None:
            values.add(value)
    if len(groups) == 2:
        value = _decimal(f"{groups[0]}.{groups[1]}")
        if value is not None:
            values.add(value)
    return values


def numbers(text: str, minimum: int = 10) -> list[tuple[str, set[Decimal]]]:
    """Chiffres du texte : (ecriture, interpretations), multiplicateurs inclus.

    Les petits entiers (< `minimum`) sont ignores : enumerations, ordinaux et
    quantites ecrites en lettres ailleurs en font un signal trop bruite. Un
    decimal est toujours garde.
    """
    found: list[tuple[str, set[Decimal]]] = []
    for match in _NUMBER.finditer(text or ""):
        raw = match.group(0)
        values = interpretations(raw)
        if not values:
            continue
        is_decimal = any(v != v.to_integral_value() for v in values)
        if not is_decimal and all(v < minimum for v in values):
            continue
        scale = _SCALE.match(text, match.end())
        if scale:
            factor = _SCALES.get(scale.group(1).lower())
            if factor:
                values = values | {(v * factor).normalize() for v in values}
        found.append((raw, values))
    return found


def missing_numbers(claim: str, page: str) -> list[str]:
    """Chiffres de l'affirmation dont aucune interpretation n'est dans la page."""
    page_values: set[Decimal] = set()
    for _raw, values in numbers(page, minimum=0):
        page_values |= values
    return [raw for raw, values in numbers(claim) if not values & page_values]


# --- Extrait cite par le juge -----------------------------------------------

def quote_found(quote: str, text: str, min_coverage: float = 0.8) -> bool:
    """Vrai si l'extrait figure dans le texte, a la typographie pres.

    Un extrait court doit figurer tel quel (texte replie). Un extrait long
    est accepte si au moins `min_coverage` de ses trigrammes de mots figurent
    dans le texte : tolere une coupure ou une ellipse du juge, pas une
    invention.
    """
    quote_tokens = tokens(quote)
    if not quote_tokens:
        return False
    text_tokens = tokens(text)
    if " ".join(quote_tokens) in " ".join(text_tokens):
        return True
    # Sans espaces : un exposant ou un indice HTML (<sup>14</sup>C) est
    # rendu "14 C" dans le texte de la page et "14C" par le juge.
    if len(quote_tokens) >= 4 and "".join(quote_tokens) in "".join(text_tokens):
        return True
    if len(quote_tokens) < 6:
        return False
    text_grams = {tuple(text_tokens[i:i + 3]) for i in range(len(text_tokens) - 2)}
    quote_grams = [tuple(quote_tokens[i:i + 3]) for i in range(len(quote_tokens) - 2)]
    hits = sum(1 for g in quote_grams if g in text_grams)
    return hits / len(quote_grams) >= min_coverage
