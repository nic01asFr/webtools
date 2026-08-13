"""
Verification de fidelite des donnees chiffrees.

Diagnostic FACT (56.9% de citations validees) : les echecs ne viennent PAS
d'hallucination — le modele ne fabrique ni sources ni faits. Ils viennent
d'une DERIVE NUMERIQUE a la reformulation. Cas reel observe :

  rapport : "en 2020, la proportion des 65+ atteignait 28,7%"
  source  : "en 2021, la proportion etait de 28,9%"

Bon sujet, bonne source, bon ordre de grandeur — mais l'annee et le chiffre
sont faux, et le validateur rejette a juste titre.

Ce module detecte les chiffres presents dans un texte redige mais absents
des sources qui l'ont nourri. Il ne corrige pas : il signale, parce qu'un
chiffre peut legitimement etre calcule (une somme, un pourcentage derive)
sans figurer tel quel dans la source.
"""

import re
from typing import Dict, List

# Nombres significatifs : au moins 2 chiffres, ou un decimal, ou un
# pourcentage. On ignore les petits entiers isoles (1, 2, 3...) qui sont
# le plus souvent des enumerations, pas des donnees.
_NUMBER_RE = re.compile(r"\b\d{1,3}(?:[  ,]\d{3})*(?:[.,]\d+)?\s*%?")


def _normalise(num: str) -> str:
    """Ramene un nombre a une forme comparable : 28,7 / 28.7 / 28,70 -> 287"""
    n = num.strip().rstrip("%").strip()
    n = n.replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        f = float(n)
    except ValueError:
        return ""
    # Suffisamment tolerant pour absorber les ecarts de formatage, assez
    # strict pour distinguer 28.7 de 28.9
    return f"{f:.4g}"


def extract_numbers(text: str, min_value: float = 10.0) -> List[str]:
    """Nombres significatifs d'un texte, sous forme normalisee."""
    out = []
    for m in _NUMBER_RE.finditer(text or ""):
        norm = _normalise(m.group())
        if not norm:
            continue
        try:
            if abs(float(norm)) < min_value:
                continue  # trop petit pour etre une donnee identifiante
        except ValueError:
            continue
        out.append(norm)
    return out


# Nombres qui ne sont pas des DONNEES : seuils d'age, tranches, references
# courantes. Les signaler produirait du bruit — "65 ans et plus" n'a pas a
# figurer dans la source pour que la phrase soit exacte.
_COMMON_NON_DATA = {"65", "60", "70", "75", "80", "18", "21", "50", "100",
                    "2020", "2021", "2022", "2023", "2024", "2025", "2030",
                    "2040", "2050"}


def check_numeric_fidelity(written: str, sources_text: str) -> Dict:
    """
    Compare les chiffres du texte redige a ceux des sources.

    Retourne un diagnostic, ne modifie rien : un chiffre absent des sources
    n'est pas forcement faux (il peut etre calcule), mais un taux eleve de
    chiffres non retrouves signale une reformulation approximative.
    """
    in_text = extract_numbers(written)
    in_src = set(extract_numbers(sources_text))

    if not in_text:
        return {"numbers_written": 0, "unmatched": [], "fidelity_rate": 1.0}

    # Les annees sont exclues du controle : une annee peut etre citee
    # legitimement sans figurer telle quelle dans la source (contexte,
    # projection). C'est la VALEUR associee qui compte.
    unmatched = [n for n in in_text if n not in in_src and n not in _COMMON_NON_DATA]
    return {
        "numbers_written": len(in_text),
        "unmatched": unmatched[:10],
        "unmatched_count": len(unmatched),
        "fidelity_rate": round(1 - len(unmatched) / len(in_text), 3),
    }
