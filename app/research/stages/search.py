"""Etape 2.1 : recherches ciblees par section et recherche de complement."""

import logging
from typing import Any, Dict, List, Optional

from app.api.models import ExtractionOptions
from app.research.context import ExecutionContext
from app.research.stages.services import StageServices
from app.research.stages.queries import (
    SEARCH_OPERATORS,
    build_search_query,
    cascade_engines,
    filter_by_constraints,
    search_pool_size,
    section_search_queries,
)

logger = logging.getLogger(__name__)


async def targeted_search(
    svc: StageServices, query: str, section_title: str, missing: List[str], ctx: ExecutionContext
) -> Dict[str, Any]:
    """Recherche ciblée pour combler les manques d'une section."""

    # Construire une requête spécifique pour les manques
    search_query = build_search_query(
        svc, query=query, section_name=section_title, key_questions=missing
    )

    logger.info(f"    🔎 Recherche ciblée: {search_query}")

    cfg = ctx.config
    try:
        # Meme cascade de moteurs que la recherche de section.
        _engines = await cascade_engines(svc, search_query, ctx=ctx)
        results = await svc.search_client.search(
            search_query,
            max_results=search_pool_size(svc, cfg.targeted_search_pool_size, ctx),
            language=ctx.search_language if cfg.search_language_from_query else "all",
            engines=",".join(_engines) if _engines else None,
        )

        # Meme filtrage que la recherche de section : sans cela, le
        # complement pre-redaction reintroduisait des domaines exclus.
        if results:
            _as_dicts = [{"url": r.url, "title": r.title} for r in results]
            _kept = {d["url"] for d in filter_by_constraints(_as_dicts, ctx)}
            results = [r for r in results if r.url in _kept]

        if not results:
            return None

        # Extraire les 2 premiers résultats les plus pertinents
        urls_to_extract = [r.url for r in results[: cfg.targeted_search_max_urls]]

        extracted_data = []
        for url in urls_to_extract:
            try:
                extract_result = await svc.extractor_manager.extract(
                    url=url,
                    llm_client=svc.llm_client,
                    options=ExtractionOptions(
                        timeout=cfg.targeted_extract_timeout,
                        use_agent=False,
                        headless=True,
                        rss_tier_root_only=cfg.rss_tier_root_only,
                        single_llm_pass_enabled=cfg.single_llm_pass_enabled,
                    ),
                )

                if extract_result.success and extract_result.content:
                    # Extraction données structurées (enrichissement ciblé) :
                    # son resultat ne sert qu'a un bonus de score.
                    structured = None
                    if not cfg.targeted_search_skip_structured:
                        structured = await svc.data_extractor.extract_structured_data(
                            content=extract_result.content[: cfg.targeted_structured_input_chars],
                            source_url=url,
                            topic_context=f"{query} - {section_title}",
                        )

                    # Meme decoupage qu'a l'extraction de section : cette
                    # recherche ciblee est declenchee quand une section
                    # MANQUE de sources — c'est precisement le moment ou
                    # tronquer a 2 000 caracteres est le plus dommageable.
                    from app.utils.chunking import document_to_chunk_items

                    _items = document_to_chunk_items(
                        source=url,
                        title=extract_result.title or "",
                        content=extract_result.content,
                        v2=cfg.chunking_v2,
                    )
                    for _it in _items:
                        _it["url"] = url  # ce chemin attend "url", pas "source"
                        if structured is not None:
                            _it["structured_data"] = structured.to_dict()
                    extracted_data.extend(_items)
                    # Ajouter aux sources découvertes (avec déduplication)
                    if url not in ctx.discovered_sources:
                        ctx.discovered_sources.append(url)
            except Exception as e:
                logger.warning(f"    ⚠️ Échec extraction {url}: {e}")
                ctx.diagnostics.note(
                    "collecte.recherche_ciblee_extraction_echec", url=url, error=str(e)
                )
                continue

        return {"query": search_query, "sources": extracted_data, "missing": missing}

    except Exception as e:
        logger.error(f"Erreur recherche ciblée: {e}")
        ctx.diagnostics.note("collecte.recherche_ciblee_echec", error=str(e))
        return None


async def section_research_phase(
    svc: StageServices,
    query: str,
    section_name: str,
    section_config: Dict,
    exploration_data: Dict,
    context: ExecutionContext,
) -> Dict[str, Any]:
    """
    PHASE 2.1: Recherches ciblées pour une section spécifique.

    Retourne sources et données brutes pour cette section.
    """
    logger.info(f"       🔍 Recherches pour section '{section_name}'")

    queries = section_search_queries(svc, query, section_name, section_config, context.config)
    search_query = " | ".join(queries)
    for q in queries:
        logger.info(f"       ↳ requête: '{q}'")

    # Nombre de sources selon profondeur, sauf plafond impose par l'API
    depth = section_config.get("depth", "moderate")
    max_sources = {
        "light": context.config.search_sources_light,
        "moderate": context.config.search_sources_moderate,
        "deep": context.config.search_sources_deep,
    }.get(depth, context.config.search_sources_moderate)
    # Plafond explicite de la requete (max_sources_per_section, V3-3).
    _override = context.config.max_sources_per_section
    if _override is not None and _override > 0:
        max_sources = _override

    # La langue n'est transmise aux moteurs que si le drapeau l'autorise
    language = context.search_language if context.config.search_language_from_query else "all"
    axis = section_config.get("source_axis")

    async def _search_one(q: str) -> List[Any]:
        engines = await cascade_engines(svc, q, axis=axis, ctx=context)
        engines_param = ",".join(engines) if engines else None
        pool = search_pool_size(svc, max_sources, context)
        found = await svc.search_client.search(
            query=q, max_results=pool, language=language, engines=engines_param
        )

        # Repli si un operateur de recherche n'a rien donne.
        #
        # Un dork (filetype:, site:, "expression") restreint fortement :
        # si la source ciblee n'a rien sur le sujet, la requete rend zero
        # alors que les memes mots-cles sans operateur auraient trouve.
        # Le benefice du ciblage ne doit pas se payer d'une section vide.
        if not found and any(op in q for op in SEARCH_OPERATORS):
            import re as _re_op

            _plain = _re_op.sub(r"\b(?:filetype|site|inurl|intitle):\S+", "", q)
            _plain = _plain.replace('"', "").strip()
            _plain = " ".join(_plain.split())
            if _plain and _plain != q:
                logger.info(f"       ↩️  opérateur infructueux, repli sans dork : '{_plain[:60]}'")
                context.diagnostics.note(
                    "collecte.repli_sans_operateur", section=section_name, query=q
                )
                found = await svc.search_client.search(
                    query=_plain, max_results=pool, language=language, engines=engines_param
                )
        return list(found or [])

    try:
        per_query: List[List[Any]] = []
        last_error: Optional[Exception] = None
        for q in queries:
            try:
                per_query.append(await _search_one(q))
            except Exception as term_error:
                last_error = term_error
                if len(queries) == 1:
                    raise
                logger.warning(f"       ⚠️ Recherche échouée pour '{q}': {term_error}")
                context.diagnostics.note(
                    "collecte.recherche_terme_echec",
                    section=section_name,
                    query=q,
                    error=str(term_error),
                )
        if not per_query and last_error is not None:
            raise last_error

        # Fusion par alternance (un resultat de chaque terme tour a tour),
        # dedoublonnee par URL : chaque terme garde sa part des places.
        search_results: List[Any] = []
        seen_urls: set = set()
        for rank in range(max((len(r) for r in per_query), default=0)):
            for results in per_query:
                if rank < len(results) and results[rank].url not in seen_urls:
                    seen_urls.add(results[rank].url)
                    search_results.append(results[rank])

        sources = []
        if search_results:
            sources = [
                {
                    "url": r.url,
                    "title": r.title,
                    "snippet": r.content[: context.config.search_snippet_chars]
                    if r.content
                    else "",
                    # Moteur d'origine : SearXNG l'expose sur chaque
                    # resultat ; sans lui, impossible de savoir quels
                    # moteurs alimentent reellement un rapport ni de
                    # detecter qu'un moteur ne repond plus.
                    "engine": getattr(r, "engine", "") or "",
                }
                for r in search_results
            ]

            # Le filtre de domaines precede la troncature : sinon des
            # resultats exclus occupent les places demandees (C35).
            sources = filter_by_constraints(sources, context)[:max_sources]

            # Tracker la recherche
            context.add_step(
                "searxng",
                f"search_for_{section_name}",
                search_query,
                {"results_count": len(sources), "urls": [s["url"] for s in sources]},
                True,
            )
        else:
            context.add_step("searxng", f"search_for_{section_name}", search_query, None, False)

        return {"sources": sources, "query_used": search_query}

    except Exception as e:
        logger.error(f"       ❌ Erreur recherche section: {e}")
        context.diagnostics.note(
            "collecte.recherche_section_echec", section=section_name, error=str(e)
        )
        context.add_step("searxng", f"search_for_{section_name}", search_query, str(e), False)
        return {"sources": [], "query_used": search_query}
