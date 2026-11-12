"""Etape 1.1 : exploration initiale restreinte pour evaluer le champ avant le plan."""

import logging
from typing import Any, Dict, List, Optional

from app.research.context import ExecutionContext
from app.research.pipeline_config import PipelineConfig
from app.research.stages.services import StageServices
from app.research.stages.queries import (
    build_search_query,
    cascade_engines,
    filter_by_constraints,
    search_pool_size,
)

logger = logging.getLogger(__name__)


async def exploratory_phase(
    svc: StageServices,
    query: str,
    context: Dict,
    ctx: Optional["ExecutionContext"] = None,
) -> Dict[str, Any]:
    """
    PHASE 1.1: Exploration initiale restreinte pour évaluer le champ.

    But: Découvrir rapidement les principales sources disponibles,
    identifier les directions possibles, évaluer la richesse du sujet.

    Retourne:
    - sources: Liste des URLs découvertes (5-10 max)
    - field_assessment: Analyse du champ (rich/moderate/limited)
    - topics_found: Sous-thèmes découverts
    - data_preview: Aperçu rapide du contenu
    """
    logger.info("  🔎 Exploration initiale (recherche restreinte)")

    cfg = ctx.config if ctx is not None else svc.config
    max_results = cfg.exploration_max_results  # Restreint pour exploration rapide
    search_query = query
    engines_param: Optional[str] = cfg.exploration_engines
    if cfg.exploration_uses_plan_query:
        # La consigne entiere n'est pas une requete : meme reduction que
        # pour les sections, et moteurs de la cascade plutot que les deux
        # moteurs scrapes les plus fragiles.
        search_query = build_search_query(svc, query=query, section_name="")
        _engines = await cascade_engines(svc, search_query, ctx=ctx)
        engines_param = ",".join(_engines) if _engines else None
    language = "all"
    if cfg.search_language_from_query:
        if ctx is not None:
            language = ctx.search_language
        elif isinstance(context, dict):
            language = context.get("search_language", "all")

    try:
        search_results = await svc.search_client.search(
            query=search_query,
            max_results=search_pool_size(svc, max_results, ctx),
            language=language,
            categories="general",
            engines=engines_param,
        )

        urls_found = []
        if search_results:
            urls_found = [
                {
                    "url": r.url,
                    "title": r.title,
                    "snippet": r.content[: cfg.exploration_snippet_chars] if r.content else "",
                }
                for r in search_results
            ]
            # L'exploration alimente le PLAN : sans filtrage ici, des
            # domaines exclus orienteraient la structure du rapport avant
            # meme la phase de recherche par section. Le filtre precede la
            # troncature, sinon des resultats exclus occupent des places.
            urls_found = filter_by_constraints(urls_found, ctx)[:max_results]

        # Analyser les snippets pour identifier sous-thèmes
        topics_found = extract_topics_from_snippets(svc, urls_found, config=cfg)

        # Évaluer la richesse du champ
        field_assessment = (
            "rich"
            if len(urls_found) >= cfg.exploration_rich_min_sources
            else (
                "moderate" if len(urls_found) >= cfg.exploration_moderate_min_sources else "limited"
            )
        )

        logger.info(f"     → {len(urls_found)} sources, champ: {field_assessment}")
        logger.info(f"     → Sous-thèmes: {', '.join(topics_found[:3])}")

        return {
            "sources": urls_found,
            "field_assessment": field_assessment,
            "topics_found": topics_found,
            "data_preview": {
                "total_sources": len(urls_found),
                "snippets": [u["snippet"] for u in urls_found[: cfg.exploration_preview_snippets]],
            },
        }

    except Exception as e:
        logger.error(f"  ❌ Erreur exploration: {e}")
        if ctx is not None:
            ctx.diagnostics.note("collecte.exploration_echec", error=str(e))
        return {
            "sources": [],
            "field_assessment": "unknown",
            "topics_found": [],
            "data_preview": {},
        }


def extract_topics_from_snippets(
    svc: StageServices, urls_data: List[Dict], config: PipelineConfig | None = None
) -> List[str]:
    """Extrait les sous-thèmes des snippets de recherche."""
    # Analyse simple par mots-clés fréquents
    from collections import Counter
    import re

    words = []
    for url_data in urls_data:
        text = f"{url_data.get('title', '')} {url_data.get('snippet', '')}"
        # Extraire mots de 4+ lettres
        words.extend(re.findall(r"\b[a-zA-Z]{4,}\b", text.lower()))

    # Mots les plus fréquents (hors stopwords basiques)
    stopwords = {
        "this",
        "that",
        "with",
        "from",
        "have",
        "more",
        "will",
        "your",
        "about",
        "what",
        "when",
        "where",
        "which",
        "their",
        "there",
    }
    filtered = [w for w in words if w not in stopwords]
    cfg = config if config is not None else svc.config
    common = Counter(filtered).most_common(cfg.exploration_topics_max)

    return [word for word, count in common if count >= cfg.exploration_topic_min_count]
