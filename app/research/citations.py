"""Citations [SOURCE:url] et bibliographie d'un rapport.

Fonctions pures : aucune entree-sortie, aucun etat global. Le rapport est
modifie en place par construire_bibliographie (meme contrat que l'ancienne
methode de l'orchestrateur) ; tout le reste ne fait que lire et calculer.

Une citation est remplacee en UNE passe d'expression reguliere. Les
substitutions de chaines successives de l'ancienne version faisaient d'une
URL qui est le prefixe d'une autre ("https://x.org/a" et "https://x.org/a/b")
un residu : "[SOURCE:https://x.org/a/b]" devenait "[1]/b]" (C44).
"""

import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

# Crochet fermant OPTIONNEL : quand la generation s'arrete en limite de
# jetons, l'URL reste ouverte ("[SOURCE:https://..." sans "]"). L'URL
# s'arrete au premier espace, saut de ligne ou crochet. Le groupe "lead"
# capte l'espace qui precede, pour ne pas laisser de double espace quand la
# citation est retiree.
CITATION_PATTERN = re.compile(r"(?P<lead>\s?)\[SOURCE:\s*(?P<url>https?://[^\s\]]+)(?P<close>\])?")

TEXT_KEYS = ("summary", "introduction", "conclusion")

_TRAILING_PUNCTUATION = ".,;:!?'\""


def split_trailing_punctuation(raw: str) -> tuple[str, str]:
    """Separe une URL de la ponctuation de phrase qui la suit.

    Sert aux marqueurs non fermes, ou la ponctuation finale est collee a
    l'URL. Une parenthese fermante n'est retiree que si elle n'est pas
    appariee dans l'URL (cas des adresses Wikipedia).
    """
    url = raw
    while url:
        last = url[-1]
        if last in _TRAILING_PUNCTUATION or (last == ")" and url.count(")") > url.count("(")):
            url = url[:-1]
            continue
        break
    return url, raw[len(url) :]


def _host(netloc: str) -> str:
    host = netloc.lower()
    return host.removeprefix("www.")


def url_key(url: str) -> str:
    """Cle d'identite d'une URL : sans fragment ni barre finale, hote en minuscules."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}{query}"


def url_domain(url: str) -> str:
    """Domaine d'une URL, sans 'www.'. Rend l'URL elle-meme si elle n'en a pas."""
    host = _host(urlsplit(url).netloc)
    return host or url


def _segments(url: str) -> list[str]:
    return [s for s in urlsplit(url).path.split("/") if s]


def closest_source(cited: str, allowed: Iterable[str]) -> str | None:
    """Source fournie la plus proche d'une URL citee, ou None.

    Retenue : meme domaine et chemins dont l'un est prefixe de l'autre (par
    segments, au moins un segment commun). La plus longue partie commune
    gagne, puis l'ecart de longueur le plus faible, puis l'ordre de fourniture.
    """
    host = _host(urlsplit(cited).netloc)
    cited_segments = _segments(cited)
    best: tuple[int, int] | None = None
    best_url: str | None = None
    for candidate in allowed:
        if _host(urlsplit(candidate).netloc) != host:
            continue
        cand_segments = _segments(candidate)
        common = 0
        for a, b in zip(cited_segments, cand_segments):
            if a != b:
                break
            common += 1
        if common == 0 or common != min(len(cited_segments), len(cand_segments)):
            continue
        score = (common, -abs(len(cited_segments) - len(cand_segments)))
        if best is None or score > best:
            best, best_url = score, candidate
    return best_url


def find_citations(text: str) -> list[str]:
    """URLs citees dans le texte, dans l'ordre d'apparition (doublons conserves).

    La ponctuation finale d'un marqueur non ferme est retiree de l'URL.
    """
    urls: list[str] = []
    for match in CITATION_PATTERN.finditer(text):
        raw = match.group("url")
        urls.append(raw if match.group("close") else split_trailing_punctuation(raw)[0])
    return urls


def convert_text(text: str, numbers: Mapping[str, int | None]) -> str:
    """Remplace chaque citation par son numero, en une seule passe.

    numbers associe url_key(url) a un numero, ou a None pour retirer la
    citation. Une citation absente de numbers est laissee telle quelle. La
    ponctuation qui suivait une URL non fermee reste dans le texte.
    """

    def replace(match: re.Match[str]) -> str:
        raw = match.group("url")
        closed = bool(match.group("close"))
        url, trailing = (raw, "") if closed else split_trailing_punctuation(raw)
        key = url_key(url)
        if key not in numbers:
            return match.group(0)
        number = numbers[key]
        if number is None:
            return trailing
        return f"{match.group('lead')}[{number}]{trailing}"

    return CITATION_PATTERN.sub(replace, text)


def _section_sources(section: Mapping[str, Any]) -> list[str]:
    """URLs des passages fournis a la section (cle 'source' de chaque passage)."""
    urls: list[str] = []
    for passage in section.get("data") or []:
        if isinstance(passage, Mapping) and passage.get("source"):
            urls.append(str(passage["source"]))
    return urls


def _passage_titles(sections: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Titre reel de chaque page fournie, par url_key (premier titre non vide)."""
    titles: dict[str, str] = {}
    for section in sections:
        for passage in section.get("data") or []:
            if not isinstance(passage, Mapping) or not passage.get("source"):
                continue
            title = str(passage.get("title") or "").strip()
            if title:
                titles.setdefault(url_key(str(passage["source"])), title)
    return titles


def construire_bibliographie(
    report: dict[str, Any],
    *,
    accessed: str,
    validate_cited_urls: bool = False,
    cited_only: bool = False,
    real_titles: bool = False,
    diagnostics: Any | None = None,
    provided_urls: Iterable[str] = (),
) -> dict[str, Any]:
    """Convertit [SOURCE:url] en [n] et construit la bibliographie du rapport.

    Idempotente : la bibliographie existante est reprise et completee, ses
    numeros ne changent pas. Un second appel, sans marqueur restant, la rend
    intacte.

    validate_cited_urls : une URL citee absente des passages fournis a la
        section est rapprochee de la source fournie la plus proche, sinon la
        citation est retirée (notee dans diagnostics). Une section sans
        passage est validee contre l'ensemble des sources du rapport, car une
        section regeneree perd ses passages.
    cited_only : ne pas ajouter les sources jamais citees dans le texte.
    real_titles : titre de la page au lieu de "Source n - domaine".
    diagnostics : objet avec une methode note(evenement, **details), ou None.
    provided_urls : URL fournies au modele pour la requete en dehors des
        passages des sections (par exemple extraites par la recherche ciblee
        de l'enrichissement) ; elles comptent comme fournies pour toutes les
        sections lors de la validation.
    """

    def note(event: str, **details: Any) -> None:
        if diagnostics is not None:
            diagnostics.note(event, **details)

    sections: list[dict[str, Any]] = report.get("sections") or []
    existing: list[dict[str, Any]] = list(report.get("bibliography") or [])
    bibliography = list(existing)
    numbers: dict[str, int | None] = {}
    for entry in existing:
        if entry.get("url") and entry.get("id"):
            numbers[url_key(entry["url"])] = entry["id"]
    counter = max((e.get("id") or 0 for e in existing), default=0) + 1
    titles = _passage_titles(sections) if real_titles else {}

    global_sources: dict[str, str] = {}
    for entry in existing:
        if entry.get("url"):
            global_sources.setdefault(url_key(entry["url"]), entry["url"])
    section_sources: list[dict[str, str]] = []
    for section in sections:
        own: dict[str, str] = {}
        for url in _section_sources(section):
            own.setdefault(url_key(url), url)
            global_sources.setdefault(url_key(url), url)
        section_sources.append(own)
    for url in provided_urls:
        if not url:
            continue
        global_sources.setdefault(url_key(url), url)
        for own in section_sources:
            if own:  # une section sans passage se valide deja contre l'ensemble
                own.setdefault(url_key(url), url)

    def register(url: str) -> None:
        nonlocal counter
        key = url_key(url)
        if key in numbers:
            return
        numbers[key] = counter
        title = titles.get(key) or f"Source {counter} - {url_domain(url)}"
        bibliography.append({"id": counter, "url": url, "title": title, "accessed": accessed})
        counter += 1

    def resolve(url: str, allowed: Mapping[str, str], where: str) -> str | None:
        """URL a citer, ou None si la citation doit etre retiree."""
        if not validate_cited_urls or not allowed or url_key(url) in allowed:
            return url
        near = closest_source(url, allowed.values())
        if near is not None:
            note("citations.url_rapprochee", cited=url, source=near, where=where)
            return near
        note("citations.citation_retiree", cited=url, where=where)
        return None

    # Collecte des citations, sections puis resume, dans l'ordre du texte.
    texts: list[tuple[str, Mapping[str, str]]] = []
    for index, section in enumerate(sections):
        allowed = section_sources[index] or global_sources
        texts.append((f"section:{index}", allowed))
    resolved: dict[tuple[str, str], str | None] = {}

    def collect(text: str, allowed: Mapping[str, str], where: str) -> None:
        for url in find_citations(text):
            if (where, url_key(url)) in resolved:
                continue
            target = resolve(url, allowed, where)
            resolved[(where, url_key(url))] = target
            if target is not None:
                register(target)

    for (where, allowed), section in zip(texts, sections):
        collect(str(section.get("content") or ""), allowed, where)
    for key in TEXT_KEYS:
        text = report.get(key)
        if isinstance(text, str):
            collect(text, global_sources, key)

    # Sources des passages jamais citees : conservees sauf cited_only.
    if not cited_only:
        for section in sections:
            for url in _section_sources(section):
                register(url)

    def numbers_for(where: str) -> dict[str, int | None]:
        local = dict(numbers)
        for (w, key), target in resolved.items():
            if w == where:
                local[key] = None if target is None else numbers[url_key(target)]
        return local

    for (where, _allowed), section in zip(texts, sections):
        if "content" in section:
            section["content"] = convert_text(str(section["content"] or ""), numbers_for(where))
    for key in TEXT_KEYS:
        text = report.get(key)
        if isinstance(text, str) and "[SOURCE:" in text:
            report[key] = convert_text(text, numbers_for(key))

    report["bibliography"] = bibliography
    return report
