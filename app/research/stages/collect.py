"""Etapes 2.1 a 2.2.5 : recherche, extraction et complement de toutes les sections."""

import asyncio
import logging
from typing import Any, Dict, List

from app.research.context import ExecutionContext
from app.research.stages.services import StageServices
from app.research.stages.extract import section_extraction_phase
from app.research.stages.required import add_required_chunks
from app.research.stages.search import section_research_phase, targeted_search

logger = logging.getLogger(__name__)


async def collect_section_data(
    svc: StageServices,
    ctx: ExecutionContext,
    query: str,
    plan: Dict[str, Any],
    exploration_data: Dict[str, Any],
) -> Dict[str, List[Dict]]:
    """Recherche et extrait les sources de chaque section, puis comble les manques.

    Les sections sont traitees en parallele (concurrence plafonnee), une
    section en echec ou trop lente devient une section sans donnees. Rend
    {nom de section: contenus extraits}, avec une entree par section du plan.
    """
    # Étape 2.1+2.2 (recherche + extraction) : ces deux étapes sont
    # independantes entre sections (chacune n'ecrit que dans sa propre
    # cle de ctx.final_content), contrairement au croisement (2.3) qui
    # lit l'etat des AUTRES sections deja traitees pour reperer les
    # sources partagees. On parallelise donc uniquement 2.1+2.2, avec
    # un plafond de concurrence explicite (semaphore) : sans lui, N
    # sections x jusqu'a 5 extractions chacune pourrait lancer des
    # dizaines de navigateurs Chromium simultanement et saturer la
    # memoire du pod. 2.3+2.4 restent sequentiels ensuite pour
    # preserver le signal de corroboration inter-sections.
    section_targets = plan.get("section_targets", {})
    from app.core.config import settings as _settings

    extraction_semaphore = asyncio.Semaphore(_settings.max_concurrent_extractions)

    async def _research_and_extract(section_name: str, section_config: Dict):
        logger.info(f"  📝 Section (recherche+extraction): {section_name}")
        section_data = await section_research_phase(
            svc,
            query=query,
            section_name=section_name,
            section_config=section_config,
            exploration_data=exploration_data,
            context=ctx,
        )
        extracted_data = await section_extraction_phase(
            svc, section_name=section_name, section_data=section_data, context=ctx
        )
        logger.info(f"     ✓ {section_name}: {len(extracted_data)} contenus extraits")
        return section_name, extracted_data

    # return_exceptions=True : sans lui, UNE section qui leve annule tout
    # le lot et fait echouer le rapport entier, alors que les autres
    # sections avaient abouti. On isole l'echec a sa propre section.
    # Budget de temps PAR SECTION, pas sur le lot entier.
    #
    # Une premiere version bornait tout le gather avec asyncio.wait_for.
    # A l'expiration, wait_for annule le gather : toutes les extractions
    # en cours sont interrompues en plein vol, avec leurs connexions
    # Playwright et HTTP. Les appels LLM suivants heritaient d'un
    # environnement asynchrone degrade et echouaient tous en
    # "Connection error" — TOUTES les syntheses perdues, rapport vide
    # rendu avec success=true. Le LLM etait hors de cause : teste
    # isolement, 15 appels enchaines et jusqu'a 30 000 caracteres de
    # prompt passaient sans un echec.
    #
    # Borner chaque section separement evite l'annulation collective :
    # une section trop lente devient une section sans donnees (reprise
    # par le complement pre-redaction ci-dessous), les autres aboutissent
    # normalement. Coherent avec return_exceptions=True.
    section_budget = max(
        ctx.config.section_budget_min_seconds,
        int(svc.timeout * ctx.config.section_budget_timeout_ratio),
    )

    # Le budget court a partir de l'obtention d'une place du semaphore, pas
    # de la mise en file : avec 5 sections et 3 places, les deux dernieres
    # consommaient leur budget a attendre et sortaient vides (rejeu d08 du
    # 13/11/2026, deux sections sans source).
    async def _research_and_extract_bounded(name: str, cfg: Dict):
        async with extraction_semaphore:
            try:
                return await asyncio.wait_for(
                    _research_and_extract(name, cfg), timeout=section_budget
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"  ⏱️  Section '{name}' : budget de {section_budget}s dépassé, "
                    f"poursuite sans ses données"
                )
                ctx.diagnostics.note(
                    "collecte.budget_section_depasse", section=name, budget_s=section_budget
                )
                return (name, [])
            except Exception as error:
                ctx.diagnostics.note("collecte.section_echec", section=name, error=str(error))
                raise

    extraction_results = await asyncio.gather(
        *[_research_and_extract_bounded(name, cfg) for name, cfg in section_targets.items()],
        return_exceptions=True,
    )

    extracted_by_section = {}
    for item in extraction_results:
        if isinstance(item, Exception):
            logger.error(f"  ❌ Une section a échoué pendant recherche/extraction: {item}")
            continue
        name, data = item
        extracted_by_section[name] = data

    # Les sections en echec restent presentes dans le plan avec zero
    # donnee : elles beneficieront du complement pre-redaction ci-dessous
    # plutot que de faire echouer tout le rapport.
    for name in section_targets:
        extracted_by_section.setdefault(name, [])

    # Étape 2.2.5 (verification + complement) : verifie chaque section
    # au regard d'un seuil calibre par le LLM DES LA PHASE 1 (plan.
    # search_strategy.sources_per_section, deja calcule au regard de la
    # requete et de l'exploration initiale - jamais exploite jusqu'ici),
    # module localement par la profondeur (depth) deja decidee pour
    # CETTE section. Aucun nouvel appel LLM pour la decision de seuil
    # elle-meme - seule l'action de combler (recherche ciblee) declenche
    # un appel, seulement si necessaire. Agit sur les DONNEES avant
    # toute redaction, pas sur du texte deja ecrit (contrairement a
    # l'ajustement local de words_target et a l'enrichissement Phase 4,
    # qui restent les filets de securite en aval si ceci ne suffit pas).
    _strategy = plan.get("search_strategy", {})

    base_threshold = _strategy.get(
        "sources_per_section", ctx.config.complement_default_sources_per_section
    )
    DEPTH_MODULATION = {
        "light": ctx.config.complement_depth_light_delta,
        "moderate": ctx.config.complement_depth_moderate_delta,
        "deep": ctx.config.complement_depth_deep_delta,
    }

    for section_name, section_config in section_targets.items():
        n_sources = len(extracted_by_section.get(section_name, []))
        depth = section_config.get("depth", "moderate")
        threshold = max(
            ctx.config.complement_min_threshold,
            base_threshold + DEPTH_MODULATION.get(depth, 0),
        )

        if n_sources < threshold:
            logger.info(
                f"  🔎 Complément pré-rédaction pour '{section_name}': {n_sources}/{threshold} sources"
            )
            key_questions = section_config.get("key_questions", [])
            extra = await targeted_search(
                svc, query=query, section_title=section_name, missing=key_questions, ctx=ctx
            )
            if extra and extra.get("sources"):
                extracted_by_section[section_name] = (
                    extracted_by_section.get(section_name, []) + extra["sources"]
                )
                logger.info(
                    f"     ✓ +{len(extra['sources'])} source(s), total: {len(extracted_by_section[section_name])}"
                )

    # Sources requises (V4-4) : candidates pour toutes les sections, apres le
    # complement pour ne pas fausser le decompte de sources de chaque section.
    add_required_chunks(ctx, extracted_by_section)

    return extracted_by_section
