"""Etapes 2.1 a 2.2.5 : recherche, extraction et complement de toutes les sections."""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.research.citations import url_key
from app.research.context import ExecutionContext
from app.research.stages.services import StageServices
from app.research.stages.extract import section_extraction_phase
from app.research.stages.required import add_required_chunks
from app.research.stages.search import section_research_phase, targeted_search_with_cause

logger = logging.getLogger(__name__)


def distinct_sources(items: List[Dict]) -> Dict[str, str]:
    """Sources distinctes des passages {url_key: url}.

    Une page longue donne plusieurs passages (morceaux de 2 000 caracteres) :
    compter les passages faisait passer une seule page pour plusieurs sources.
    """
    found: Dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("source")
        if url:
            found.setdefault(url_key(str(url)), str(url))
    return found


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

    async def _research_and_extract(section_name: str, section_config: Dict, pages: List[Dict]):
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
            svc, section_name=section_name, section_data=section_data, context=ctx, out=pages
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
    # une section trop lente garde les pages deja extraites (completee par
    # le complement pre-redaction ci-dessous), les autres aboutissent
    # normalement. Coherent avec return_exceptions=True.
    section_budget = max(
        ctx.config.section_budget_min_seconds,
        int(svc.timeout * ctx.config.section_budget_timeout_ratio),
    )

    # Echeance globale (posee par l'orchestrateur, absente sinon) : le
    # budget fixe par section ignorait le temps restant. Une collecte de
    # ceil(sections / places) x budget, plus un complement sans borne,
    # depassait l'echeance avant toute redaction (rapport partiel vide). La
    # collecte s'arrete donc au plus tard a une part du temps restant, le
    # reste est garde pour la redaction.
    loop = asyncio.get_running_loop()
    deadline_at = getattr(ctx, "deadline_at", None)
    collect_deadline: Optional[float] = None
    if deadline_at is not None:
        left_at_start = max(deadline_at - loop.time(), 0.0)
        collect_deadline = loop.time() + left_at_start * (
            1.0 - ctx.config.collection_writing_reserve_ratio
        )

    def _collect_left() -> Optional[float]:
        return None if collect_deadline is None else collect_deadline - loop.time()

    # Le budget court a partir de l'obtention d'une place du semaphore, pas
    # de la mise en file : avec 5 sections et 3 places, les deux dernieres
    # consommaient leur budget a attendre et sortaient vides (rejeu d08 du
    # 13/11/2026, deux sections sans source).
    async def _research_and_extract_bounded(name: str, cfg: Dict):
        async with extraction_semaphore:
            budget: float = section_budget
            left = _collect_left()
            if left is not None and left < budget:
                budget = max(left, 0.0)
                logger.warning(
                    f"  Section '{name}' : budget reduit a {budget:.0f}s par l'echeance globale"
                )
                ctx.diagnostics.note(
                    "collecte.budget_reduit_echeance",
                    section=name,
                    budget_s=round(budget, 1),
                    budget_initial_s=section_budget,
                )
            # Liste possedee ici, alimentee page par page : a l'expiration,
            # les pages deja extraites ne sont pas perdues avec la coroutine.
            pages: List[Dict] = []
            try:
                return await asyncio.wait_for(
                    _research_and_extract(name, cfg, pages), timeout=budget
                )
            except asyncio.TimeoutError:
                kept = list(pages)
                logger.warning(
                    f"  Section '{name}' : budget de {budget:.0f}s depasse, "
                    f"poursuite avec {len(kept)} passage(s) deja extrait(s)"
                )
                ctx.diagnostics.note(
                    "collecte.budget_section_depasse",
                    section=name,
                    budget_s=round(budget, 1),
                    passages_gardes=len(kept),
                    sources_gardees=len(distinct_sources(kept)),
                )
                return (name, kept)
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
        known = distinct_sources(extracted_by_section.get(section_name, []))
        n_sources = len(known)
        depth = section_config.get("depth", "moderate")
        threshold = max(
            ctx.config.complement_min_threshold,
            base_threshold + DEPTH_MODULATION.get(depth, 0),
        )

        if n_sources < threshold:
            logger.info(
                f"  🔎 Complément pré-rédaction pour '{section_name}': {n_sources}/{threshold} sources"
            )
            left = _collect_left()
            if left is not None and left <= 0:
                logger.warning(f"  Complement de '{section_name}' saute : echeance globale")
                ctx.diagnostics.note(
                    "collecte.complement_saute_echeance",
                    section=section_name,
                    sources=n_sources,
                    seuil=threshold,
                )
                continue
            key_questions = section_config.get("key_questions", [])
            search = targeted_search_with_cause(
                svc,
                query=query,
                section_title=section_name,
                missing=key_questions,
                ctx=ctx,
                known_urls=list(known.values()),
            )
            try:
                if left is None:
                    extra, cause = await search
                else:
                    extra, cause = await asyncio.wait_for(search, timeout=left)
            except asyncio.TimeoutError:
                logger.warning(f"  Complement de '{section_name}' interrompu : echeance globale")
                ctx.diagnostics.note(
                    "collecte.complement_saute_echeance",
                    section=section_name,
                    sources=n_sources,
                    seuil=threshold,
                    interrompu=True,
                )
                extra, cause = None, "echeance globale atteinte"
            if extra and extra.get("sources"):
                extracted_by_section[section_name] = (
                    extracted_by_section.get(section_name, []) + extra["sources"]
                )
                logger.info(
                    f"     +{len(extra['sources'])} passage(s), total: {len(extracted_by_section[section_name])}"
                )
            details: Dict[str, Any] = {
                "section": section_name,
                "sources_avant": n_sources,
                "sources_apres": len(distinct_sources(extracted_by_section[section_name])),
                "seuil": threshold,
            }
            if cause:
                details["cause"] = cause
            ctx.diagnostics.note("collecte.complement", **details)

    # Sources requises (V4-4) : candidates pour toutes les sections, apres le
    # complement pour ne pas fausser le decompte de sources de chaque section.
    add_required_chunks(ctx, extracted_by_section)

    return extracted_by_section
