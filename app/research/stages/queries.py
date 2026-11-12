"""Requetes de recherche, moteurs et contraintes de domaines (communs a l'exploration et a la recherche par section)."""

import logging
from typing import Dict, List, Optional

from app.research.context import ExecutionContext
from app.research.pipeline_config import PipelineConfig
from app.research.stages.services import StageServices


logger = logging.getLogger(__name__)


SEARCH_OPERATORS = ("filetype:", "site:", "inurl:", "intitle:", '"')


def search_pool_size(
    svc: StageServices, wanted: int, ctx: Optional[ExecutionContext] = None
) -> int:
    """Nombre de resultats a demander au moteur pour en garder `wanted`.

    Avec des contraintes de domaines, le filtre s'applique APRES la
    recherche : demander exactement `wanted` resultats laisserait des
    domaines exclus occuper les places avant le filtre (C35).
    """
    if ctx is not None and (ctx.domains_whitelist or ctx.domains_exclusions):
        return wanted * svc.config.search_pool_multiplier
    return wanted


async def cascade_engines(
    svc: StageServices,
    search_query: str,
    axis: Optional[str] = None,
    ctx: Optional["ExecutionContext"] = None,
) -> List[str]:
    """Moteurs de la cascade pour une requete ; liste vide si pas de selection.

    Cascade de moteurs : sans selection, SearXNG interroge tous les
    moteurs actifs de la categorie (74 sources par recherche, dont des
    banques d'images et des torrents). La cascade retient les 3 meilleurs
    DISPONIBLES de chaque axe. Si le catalogue n'a pas pu etre charge,
    la liste est vide et le comportement est celui d'avant (best-effort).
    """
    from app.services.engine_catalog import engine_catalog

    cfg = ctx.config if ctx is not None else svc.config
    if not engine_catalog.available:
        await engine_catalog.ensure_loaded()
    if not engine_catalog.available:
        if ctx is not None:
            ctx.diagnostics.note("collecte.catalogue_moteurs_indisponible")
        return []

    selected_engines: List[str] = []
    # Axe declare par le plan pour CETTE section : une section
    # "projections demographiques" interroge l'axe academique, une
    # section "reactions du marche" l'axe actualite.
    if axis:
        selected_engines = engine_catalog.cascade(
            per_axis=cfg.cascade_engines_per_axis, axes=[axis]
        )
        if selected_engines:
            logger.info(f"       🎯 axe '{axis}' → {len(selected_engines)} moteur(s)")

    # Repli elargi : si l'axe demande ne rend aucun moteur DISPONIBLE, on
    # elargit plutot que de laisser la section sans source.
    if not selected_engines:
        selected_engines = engine_catalog.cascade(per_axis=cfg.cascade_engines_per_axis)
        if axis:
            logger.info(f"       ↪ axe '{axis}' indisponible, cascade complète")

    # Une requete a OPERATEUR ne doit pas etre restreinte aux moteurs de
    # l'axe. Mesure : "...report filetype:pdf" rend 61 resultats sans
    # restriction, et 0 sur les 3 moteurs de l'axe generaliste
    # (encyclopedies). Un dork cible deja par lui-meme : SearXNG
    # interroge l'ensemble des moteurs, qui filtrent selon l'operateur.
    if any(op in search_query for op in SEARCH_OPERATORS):
        logger.info(
            f"       🔍 requête à opérateur — moteurs avant exemption : "
            f"{selected_engines or '(aucun)'}"
        )
        return []
    return selected_engines


def filter_by_constraints(
    sources: List[Dict], ctx: Optional[ExecutionContext] = None
) -> List[Dict]:
    """
    Applique whitelist et exclusions de domaines aux resultats de
    recherche.

    Ces contraintes sont documentees dans l'API (SourcesConstraints) et
    transmises jusqu'a l'orchestrateur depuis l'endpoint, mais n'etaient
    jamais lues : l'interface promettait une capacite que le pipeline
    n'honorait pas.

    Whitelist et exclusions sont volontairement appliquees APRES la
    recherche plutot que dans la requete au moteur : un operateur
    "site:" mal supporte par certains moteurs rendrait zero resultat
    sans qu'on sache pourquoi, alors qu'un filtrage a posteriori est
    explicite et journalisable.
    """
    wl = ctx.domains_whitelist if ctx is not None else []
    ex = ctx.domains_exclusions if ctx is not None else []
    if not wl and not ex:
        return sources

    from urllib.parse import urlparse

    def domain_of(url: str) -> str:
        try:
            return (urlparse(url).netloc or "").lower()
        except Exception:
            return ""

    def matches(dom: str, pattern: str) -> bool:
        # "example.com" couvre aussi "www.example.com" et les
        # sous-domaines ; "*.gouv.fr" est accepte par commodite.
        p = pattern.lstrip("*.").lower()
        return dom == p or dom.endswith("." + p)

    kept, rejected = [], 0
    for s in sources:
        dom = domain_of(s.get("url", ""))
        if not dom:
            continue
        if wl and not any(matches(dom, p) for p in wl):
            rejected += 1
            continue
        if ex and any(matches(dom, p) for p in ex):
            rejected += 1
            continue
        kept.append(s)

    if rejected:
        logger.info(f"       🔎 {rejected} source(s) écartée(s) par les contraintes de domaines")
    return kept


# Mots vides : retires lors de la reduction d'une consigne longue en
# requete de recherche. FR + EN car les consignes arrivent dans les deux
# langues et le plan peut etre genere dans l'une ou l'autre.
STOPWORDS = {
    "le",
    "la",
    "les",
    "un",
    "une",
    "des",
    "du",
    "de",
    "et",
    "ou",
    "a",
    "au",
    "aux",
    "en",
    "dans",
    "sur",
    "pour",
    "par",
    "avec",
    "sans",
    "sous",
    "que",
    "qui",
    "quoi",
    "dont",
    "est",
    "sont",
    "etre",
    "avoir",
    "plus",
    "moins",
    "ce",
    "cette",
    "ces",
    "son",
    "leur",
    "leurs",
    "quel",
    "quelle",
    "quels",
    "quelles",
    "comment",
    "pourquoi",
    "veuillez",
    "merci",
    "sil",
    "vous",
    "the",
    "a",
    "an",
    "of",
    "and",
    "or",
    "to",
    "in",
    "on",
    "for",
    "by",
    "with",
    "without",
    "that",
    "which",
    "what",
    "how",
    "why",
    "is",
    "are",
    "be",
    "been",
    "will",
    "would",
    "their",
    "there",
    "please",
    "produce",
    "based",
    "various",
    "such",
    "as",
    "across",
    "from",
    "about",
    "can",
}


def build_search_query(
    svc: StageServices,
    query: str,
    section_name: str,
    key_questions: List[str] = None,
    search_terms: List[str] = None,
    max_words: int | None = None,
) -> str:
    """
    Construit une requete de recherche EXPLOITABLE.

    Le comportement precedent concatenait la consigne complete, le nom de
    section et les questions cles. Sur une consigne courte ("SearXNG
    moteur de recherche") cela passait ; sur une consigne realiste de
    350 caracteres cela produisait une requete de 400+ caracteres a
    laquelle aucun moteur ne repond - 0 source sur toutes les sections,
    rapport vide. Detecte par le benchmark, invisible pour nos tests
    maison qui utilisaient des sujets courts.

    Ordre de preference :
    1. search_terms fourni par le plan (le LLM a formule la requete)
    2. reduction par mots-cles : mots significatifs de la consigne +
       nom de section, plafonne a max_words

    Le plafond est le point essentiel : au-dela d'une dizaine de mots,
    un moteur generaliste renvoie du bruit ou rien.
    """
    import re

    cfg = svc.config
    if max_words is None:
        max_words = cfg.search_query_max_words

    if search_terms:
        terms = " ".join(str(t) for t in search_terms if t).strip()
        if terms:
            return " ".join(terms.split()[:max_words])

    def salient(text: str, limit: int) -> List[str]:
        words = re.findall(r"[\w'-]+", text.lower())
        out, seen = [], set()
        for w in words:
            if len(w) <= 2 or w in STOPWORDS or w in seen:
                continue
            seen.add(w)
            out.append(w)
            if len(out) >= limit:
                break
        return out

    # Le nom de section porte l'angle specifique : prioritaire sur la
    # consigne generale, qui est commune a toutes les sections.
    section_words = salient(section_name, cfg.search_query_section_words)
    budget = max_words - len(section_words)
    query_words = salient(query, max(cfg.search_query_min_query_words, budget))

    combined = query_words + [w for w in section_words if w not in query_words]
    result = " ".join(combined[:max_words]).strip()

    return result or query[: cfg.search_query_fallback_chars]


def section_search_queries(
    svc: StageServices,
    query: str,
    section_name: str,
    section_config: Dict,
    config: PipelineConfig,
) -> List[str]:
    """Requetes de recherche d'une section.

    Par defaut une seule requete (les termes du plan concatenes puis
    coupes). Avec separate_search_terms, une requete par terme : un
    operateur `filetype:pdf` ou une expression entre guillemets ne se
    melange plus aux mots des autres termes (C34).
    """
    key_questions = section_config.get("key_questions", [])
    terms = section_config.get("search_terms")
    if config.separate_search_terms and terms:
        cleaned = [str(t).strip() for t in terms if t and str(t).strip()]
        queries: List[str] = []
        for term in cleaned[: config.search_terms_max_queries]:
            q = build_search_query(
                svc,
                query=query,
                section_name=section_name,
                key_questions=key_questions,
                search_terms=[term],
            )
            if q and q not in queries:
                queries.append(q)
        if queries:
            return queries
    # Requete bornee (voir _build_search_query) : la concatenation brute
    # produisait des requetes de 400+ caracteres sur consigne longue.
    return [
        build_search_query(
            svc,
            query=query,
            section_name=section_name,
            key_questions=key_questions,
            search_terms=terms,
        )
    ]
