"""
Convertit un rapport webtools (JSON structure) en markdown au format attendu
par DeepResearch Bench.

Point critique : FACT extrait des triplets (fact, ref_idx, url) en lisant
"la liste de references en fin de rapport ou les parentheses au point de
citation". Notre pipeline produit deja des [1], [2] inline convertis depuis
[SOURCE:url], plus une bibliographie numerotee : le format est donc
nativement compatible, A CONDITION que la bibliographie soit rendue avec
l'index ET l'URL en clair, dans l'ordre.

Toute rupture de correspondance entre les [n] du texte et les entrees de la
bibliographie fait chuter FACT sans que RACE ne bouge - c'est le mode de
panne silencieux a surveiller.
"""
from typing import Any, Dict


def report_to_markdown(payload: Dict[str, Any]) -> str:
    """
    payload : reponse JSON de /api/v1/research/deep
    retour  : markdown unique (le champ `article` attendu par le benchmark)
    """
    # L'API /research/deep renvoie le rapport sous "result" (et non "report",
    # qui est le nom interne cote orchestrateur). Chercher la mauvaise cle
    # produisait un article reduit au seul titre - 12 mots la ou le serveur
    # avait bien genere 5 234 mots et 23 references. On accepte les deux, plus
    # le payload nu, pour ne pas dependre d'un detail de nommage.
    report = payload.get("result") or payload.get("report") or payload

    parts = []

    title = report.get("title") or payload.get("topic") or ""
    if title:
        parts.append(f"# {title}\n")

    summary = report.get("summary")
    if summary:
        parts.append(f"{summary}\n")

    for section in report.get("sections", []):
        sec_title = (section.get("title") or "").strip()
        content = (section.get("content") or "").strip()
        if not content:
            continue
        if sec_title:
            parts.append(f"\n## {sec_title}\n")
        parts.append(content + "\n")

    biblio = report.get("bibliography", [])
    if biblio:
        parts.append("\n## References\n")
        for i, item in enumerate(biblio, start=1):
            url = (item.get("url") or "").strip()
            btitle = (item.get("title") or url or "").strip()
            if not url:
                continue
            # Format explicite [n] Titre. URL  -> lisible par l'extracteur FACT
            parts.append(f"[{i}] {btitle}. {url}")

    return "\n".join(parts).strip()


def sanity_check(markdown: str) -> Dict[str, Any]:
    """
    Verifie qu'un article converti est exploitable par le benchmark.
    Retourne un diagnostic, ne leve pas : un article imparfait doit etre
    enregistre (et compte comme tel), pas silencieusement perdu.
    """
    import re

    inline_refs = set(int(n) for n in re.findall(r"\[(\d{1,3})\]", markdown))
    biblio_lines = re.findall(r"^\[(\d{1,3})\]\s+.*?(https?://\S+)", markdown, re.M)
    biblio_idx = set(int(n) for n, _ in biblio_lines)

    words = len(markdown.split())
    orphans = sorted(inline_refs - biblio_idx)

    return {
        "words": words,
        "inline_citations": len(inline_refs),
        "bibliography_entries": len(biblio_idx),
        "orphan_citations": orphans,
        "has_urls": bool(biblio_lines),
        "usable": words >= 200 and bool(biblio_lines),
    }
