"""
Decoupage d'un document en chunks pour la selection semantique.

PROBLEME RESOLU
Le contenu extrait etait tronque a 3 000 caracteres DES L'EXTRACTION, avant
tout scoring. Un article de recherche de 100 000 caracteres perdait donc 97%
de son contenu, definitivement : le chiffre precis cite par le rapport se
trouvait souvent dans la partie jetee. C'est la cause mecanique du taux de
citations valides de 61% — le modele voyait le debut d'un article et
completait le reste avec ce qu'il savait du sujet.

CHOIX DE CONCEPTION (etat de l'art 2025-2026)
- Decoupage recursif ~512 tokens avec 15% de chevauchement. Le benchmark
  Vecta de fevrier 2026 place cette configuration en tete (69% de precision)
  ; le decoupage semantique produisait des fragments de 43 tokens en moyenne,
  qui se recuperaient proprement mais donnaient trop peu de contexte au
  modele (54%). NVIDIA a mesure 15% comme chevauchement optimal sur
  FinanceBench.
- Frontieres respectees : paragraphes d'abord, puis phrases. Couper au
  milieu d'une phrase detruit l'information que l'embedding doit capturer.
- Pattern parent-enfant : chaque chunk garde l'identite de son document
  d'origine (source, titre, position). La selection se fait au niveau du
  chunk pour la precision, mais on peut ensuite elargir la fenetre autour
  d'un chunk retenu — c'est le document parent qui porte le contexte.

CE QU'ON NE FAIT PAS
Injecter le document entier : Chroma a montre (juillet 2025, 18 modeles)
que la performance se degrade quand le contexte s'allonge, meme sur des
taches simples, et que multiplier les passages distrait le modele avec des
"negatifs difficiles" — des extraits proches de la requete mais trompeurs.
"""

import re
from typing import Dict, List

# ~512 tokens. Le ratio caracteres/token varie selon la langue (~4 en
# anglais, ~3.5 en francais) ; 2000 caracteres est un compromis sur.
DEFAULT_CHUNK_CHARS = 2000
DEFAULT_OVERLAP_RATIO = 0.15

# En deca, un fragment ne porte pas assez de contexte pour etre utile.
MIN_CHUNK_CHARS = 200


def split_into_chunks(
    content: str,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
) -> List[str]:
    """
    Decoupe un texte en fragments qui se chevauchent, en respectant les
    frontieres naturelles (paragraphes, puis phrases).
    """
    content = (content or "").strip()
    if not content:
        return []
    if len(content) <= chunk_chars:
        return [content]

    overlap = int(chunk_chars * overlap_ratio)

    # Unites de base : paragraphes. Un paragraphe trop long est redecoupe
    # en phrases plutot que coupe arbitrairement.
    units: List[str] = []
    for para in re.split(r"\n\s*\n", content):
        para = para.strip()
        if not para:
            continue
        if len(para) <= chunk_chars:
            units.append(para)
        else:
            buf = ""
            for sent in re.split(r"(?<=[.!?])\s+", para):
                if len(buf) + len(sent) + 1 <= chunk_chars:
                    buf = f"{buf} {sent}".strip()
                else:
                    if buf:
                        units.append(buf)
                    # Une phrase seule depassant la taille cible est coupee
                    # net : cas rare (listes, tableaux mal convertis).
                    while len(sent) > chunk_chars:
                        units.append(sent[:chunk_chars])
                        sent = sent[chunk_chars:]
                    buf = sent
            if buf:
                units.append(buf)

    # Assemblage des unites en chunks, avec chevauchement par report de la
    # fin du chunk precedent.
    chunks: List[str] = []
    current = ""
    for u in units:
        if len(current) + len(u) + 2 <= chunk_chars:
            current = f"{current}\n\n{u}".strip()
        else:
            if current:
                chunks.append(current)
                tail = current[-overlap:] if overlap else ""
                # Reprendre au debut d'une phrase pour ne pas commencer un
                # chunk au milieu d'un mot.
                m = re.search(r"[.!?]\s+", tail)
                current = (tail[m.end():] + "\n\n" + u).strip() if m else u
            else:
                current = u
    if current:
        chunks.append(current)

    return [c for c in chunks if len(c) >= MIN_CHUNK_CHARS] or [content[:chunk_chars]]


def document_to_chunk_items(
    source: str,
    title: str,
    content: str,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
) -> List[Dict]:
    """
    Transforme un document extrait en items de chunk, chacun conservant
    l'identite de son parent (pattern parent-enfant).
    """
    parts = split_into_chunks(content, chunk_chars=chunk_chars)
    total = len(parts)
    return [
        {
            "source": source,
            "title": title or "",
            "content": part,
            "metadata": {
                "chunk_index": i,
                "chunk_total": total,
                "parent_length": len(content or ""),
            },
        }
        for i, part in enumerate(parts)
    ]
