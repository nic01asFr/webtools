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

# En deca, un fragment ne porte pas assez de contexte pour etre selectionne
# seul : il est rattache a son voisin, jamais jete.
MIN_CHUNK_CHARS = 200


def _split_on_whitespace(text: str, chunk_chars: int, overlap: int) -> list[str]:
    """Coupe un texte sans ponctuation sur une espace, avec chevauchement.

    La coupe nette a chunk_chars tranche un nombre ou un mot en deux et ne
    laisse aucun contexte commun entre deux morceaux."""
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_chars, len(text))
        if end < len(text):
            ws = text.rfind(" ", start + chunk_chars // 2, end)
            if ws > start:
                end = ws
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        # Reprise un peu avant la coupe, sur une frontiere de mot.
        nxt = text.find(" ", max(end - overlap, start + 1), end)
        start = nxt + 1 if nxt != -1 else end
    return pieces


def _merge_short_chunks(chunks: list[str]) -> list[str]:
    """Rattache chaque fragment trop court a son voisin au lieu de le perdre."""
    out: list[str] = []
    carry = ""
    for c in chunks:
        if carry:
            c = f"{carry}\n\n{c}"
            carry = ""
        if len(c) < MIN_CHUNK_CHARS:
            carry = c
        else:
            out.append(c)
    if carry:
        if not out:
            out.append(carry)
        elif carry not in out[-1]:
            out[-1] = f"{out[-1]}\n\n{carry}"
    return out


def _split_long_line(line: str, chunk_chars: int, overlap: int) -> list[str]:
    """Coupe une ligne trop longue en phrases, puis sur une espace.

    Une coupe nette a chunk_chars tranchait un nombre ou un mot en deux.
    Seul un mot plus long que la taille cible (URL, chaine sans espace) est
    encore coupe net : il n'a pas d'autre frontiere."""
    pieces: list[str] = []
    buf = ""
    for sent in re.split(r"(?<=[.!?])\s+", line):
        if len(buf) + len(sent) + 1 <= chunk_chars:
            buf = f"{buf} {sent}".strip()
            continue
        if buf:
            pieces.append(buf)
        if len(sent) > chunk_chars:
            parts = _split_on_whitespace(sent, chunk_chars, overlap)
            pieces.extend(parts[:-1])
            sent = parts[-1]
        buf = sent
    if buf:
        pieces.append(buf)
    return pieces


# Ligne separatrice d'un tableau Markdown : |---|:--:|---| (barres de bord
# facultatives).
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _is_table_separator(line: str) -> bool:
    return "|" in line and bool(_TABLE_SEPARATOR_RE.match(line))


def _split_block_on_lines(block: str, chunk_chars: int, overlap: int) -> list[str]:
    """Decoupe un bloc multi-lignes aux limites de ligne.

    Un tableau (une ligne par rangee, sans point) formait une seule phrase
    coupee net, et seul le premier morceau gardait l'en-tete : le redacteur
    attribuait des valeurs a la mauvaise colonne. Ici chaque morceau finit sur
    une rangee entiere et, pour un tableau Markdown, repete l'en-tete et sa
    ligne separatrice.
    """
    lines = [line.rstrip() for line in block.split("\n") if line.strip()]
    pieces: list[str] = []
    buf: list[str] = []
    buf_len = 0
    has_body = False  # le tampon porte autre chose que l'en-tete repete
    header = ""  # en-tete + separateur du tableau en cours, "" hors tableau

    def flush() -> None:
        nonlocal buf, buf_len, has_body
        if buf and has_body:
            pieces.append("\n".join(buf))
        buf, buf_len, has_body = [], 0, False

    for i, line in enumerate(lines):
        next_is_separator = i + 1 < len(lines) and _is_table_separator(lines[i + 1])
        if _is_table_separator(line) and i > 0 and "|" in lines[i - 1]:
            candidate = f"{lines[i - 1]}\n{line}"
            # Un en-tete demesure ne peut pas etre repete sans etouffer les
            # rangees : il reste alors dans le seul premier morceau.
            header = candidate if len(candidate) <= chunk_chars // 2 else ""
            is_row = False
        elif "|" in line and not next_is_separator:
            is_row = bool(header)
        else:
            # Une ligne sans barre clot le tableau ; une ligne suivie d'un
            # separateur est l'en-tete d'un nouveau tableau.
            if "|" not in line or next_is_separator:
                header = ""
            is_row = False

        prefix = header if is_row else ""
        room = max(chunk_chars - (len(prefix) + 1 if prefix else 0), chunk_chars // 2)
        if len(line) > room:
            flush()
            for part in _split_long_line(line, room, overlap):
                pieces.append(f"{prefix}\n{part}" if prefix else part)
            continue
        if buf and buf_len + 1 + len(line) > chunk_chars:
            flush()
            if prefix:
                buf, buf_len = [prefix], len(prefix)
        buf_len += len(line) + (1 if buf else 0)
        buf.append(line)
        has_body = True
    flush()
    return pieces


def _carry_overlap(tail: str) -> str:
    """Fin du chunk precedent reprise en tete du suivant, depuis un debut de phrase.

    Rien n'est repris si cette fin contient une rangee de tableau : placee
    avant l'en-tete repete, elle serait lue sans ses colonnes."""
    m = re.search(r"[.!?]\s+", tail)
    if not m:
        return ""
    carried = tail[m.end():]
    if "|" in carried:
        return ""
    return carried


def split_into_chunks(
    content: str,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
    v2: bool = False,
) -> List[str]:
    """
    Decoupe un texte en fragments qui se chevauchent, en respectant les
    frontieres naturelles (paragraphes, lignes, phrases, puis mots).

    Aucun fragment n'est jete : un fragment de moins de MIN_CHUNK_CHARS est
    rattache a son voisin (un chapeau date ou une note finale courte portent
    souvent le chiffre cite). Un bloc multi-lignes (tableau, liste) est coupe
    aux limites de ligne, l'en-tete d'un tableau Markdown repete dans chaque
    morceau ; une ligne trop longue est coupee sur une espace.

    v2 : garde pour la compatibilite de la configuration (chunking_v2) ; ses
    deux effets (rattachement, coupe sur espace) sont desormais ceux du mode
    par defaut.
    """
    content = (content or "").strip()
    if not content:
        return []
    if len(content) <= chunk_chars:
        return [content]

    overlap = int(chunk_chars * overlap_ratio)

    # Unites de base : paragraphes. Un paragraphe trop long est redecoupe
    # aux limites de ligne s'il en a, sinon en phrases, plutot que coupe
    # arbitrairement.
    units: List[str] = []
    for para in re.split(r"\n\s*\n", content):
        para = para.strip()
        if not para:
            continue
        if len(para) <= chunk_chars:
            units.append(para)
        elif "\n" in para:
            units.extend(_split_block_on_lines(para, chunk_chars, overlap))
        else:
            units.extend(_split_long_line(para, chunk_chars, overlap))

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
                carried = _carry_overlap(tail)
                current = (carried + "\n\n" + u).strip() if carried else u
            else:
                current = u
    if current:
        chunks.append(current)

    return _merge_short_chunks(chunks)


def document_to_chunk_items(
    source: str,
    title: str,
    content: str,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    v2: bool = False,
) -> List[Dict]:
    """
    Transforme un document extrait en items de chunk, chacun conservant
    l'identite de son parent (pattern parent-enfant).
    """
    parts = split_into_chunks(content, chunk_chars=chunk_chars, v2=v2)
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
