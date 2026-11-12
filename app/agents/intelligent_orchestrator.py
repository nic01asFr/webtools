"""
Intelligent Orchestrator - Orchestrateur Intelligent Multi-Outils

Pipeline de recherche profonde en phases :
1. Exploration (SearXNG) et plan detaille par sections
2. Recherche et extraction par section
3. Enrichissement iteratif et assemblage final du rapport

Dependances : client de recherche, ExtractorManager, GenericDataExtractor
(extraction structuree) et client LLM.
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Sequence
from datetime import datetime

from app.core.llm.base import BaseLLMClient
from app.services.search_service import searxng_client
from app.manager import ExtractorManager
from app.core.data_extractor import GenericDataExtractor
from app.research.context import ExecutionContext
from app.research.diagnostics import Diagnostics
from app.research.pipeline_config import PipelineConfig
from app.research.stages.services import StageServices
from app.research.stages.coherence import analyze_intersections, apply_coherence_improvements
from app.research.stages.crossref import cross_reference_data
from app.research.stages.enrich import iterative_enrichment
from app.research.stages.explore import exploratory_phase
from app.research.stages.extract import section_extraction_phase
from app.research.stages.plan import create_detailed_plan
from app.research.stages.search import section_research_phase, targeted_search
from app.research.stages.synthesize import synthesize_single_section

logger = logging.getLogger(__name__)


class IntelligentOrchestrator:
    """
    Orchestrateur intelligent qui connaît ses outils et s'adapte dynamiquement.
    """

    def __init__(
        self,
        llm_client: BaseLLMClient,
        timeout: int = 300,
        *,
        search_client: Any | None = None,
        extractor_manager: Any | None = None,
        config: PipelineConfig | None = None,
    ):
        self.llm_client = llm_client
        self.timeout = timeout
        self.config: PipelineConfig = config if config is not None else PipelineConfig.from_env()

        # Coutures d'injection : par defaut les objets de production, des
        # doublures dans les tests.
        self.search_client = search_client if search_client is not None else searxng_client

        # Outils injectes ou construits par defaut
        self.extractor_manager = extractor_manager if extractor_manager is not None else ExtractorManager()
        self.data_extractor = GenericDataExtractor(llm_client)

    def services(self) -> StageServices:
        """Services injectes dans les etapes, lus a l'instant de l'appel.

        Lus a chaque appel (et non fixes a la construction) : un appelant peut
        remplacer `config` ou `search_client` sur l'instance entre deux
        requetes. Les etapes n'ecrivent jamais sur l'orchestrateur.
        """
        return StageServices(
            llm_client=self.llm_client,
            search_client=self.search_client,
            extractor_manager=self.extractor_manager,
            data_extractor=self.data_extractor,
            config=self.config,
            timeout=self.timeout,
            sleep=asyncio.sleep,
        )

    async def _exploratory_phase(
        self, query: str, context: Dict, ctx: Optional[ExecutionContext] = None
    ) -> Dict[str, Any]:
        """Etape 1.1 (stages.explore). Point de remplacement des tests de delai."""
        return await exploratory_phase(self.services(), query, context, ctx=ctx)

    async def _analyze_intersections(
        self, query: str, plan: Dict, context: ExecutionContext
    ) -> Dict[str, Any]:
        """Etape 3.1 (stages.coherence). Point de remplacement des tests de delai."""
        return await analyze_intersections(self.services(), query, plan, context)

    async def execute_intelligent_research(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Recherche intelligente adaptative : cree le contexte de la requete.

        Tout l'etat de la requete vit dans l'ExecutionContext ; aucun attribut
        de l'orchestrateur n'est ecrit (deux requetes successives sur la meme
        instance n'interferent pas).
        """
        return await self._run_pipeline(query, context, ExecutionContext(query, config=self.config))

    async def _run_pipeline(
        self,
        query: str,
        context: Optional[Dict[str, Any]],
        ctx: ExecutionContext,
    ) -> Dict[str, Any]:
        """
        Recherche intelligente adaptative - ARCHITECTURE REFONDÉE.

        Nouveau processus en 4 phases (vision utilisateur):
        1. PHASE 1: EXPLORATION + PLAN DÉTAILLÉ
           - Recherches exploratoires ciblées et restreintes
           - Évaluation du champ disponible
           - Plan avec structure complète, enchaînements, canvas détaillé

        2. PHASE 2: CONSTRUCTION ITÉRATIVE SECTION PAR SECTION
           - Pour chaque section: recherches, extraction, analyse, croisement
           - Canvas rempli progressivement

        3. PHASE 3: COHÉRENCE GLOBALE PARTIE PAR PARTIE
           - Revue inter-sections
           - Cohérence narrative

        4. PHASE 4: FINALISATION
           - Bibliographie et traces complètes
        """
        logger.info(f"🧠 Recherche intelligente REFONDÉE: {query}")
        start_time = datetime.now()

        # Le contexte est cree par l'appelant : execute_with_deadline y lit ce
        # qui est deja redige quand le delai global expire.
        context = context or {}

        # Contraintes de sources fournies par l'appelant. Elles arrivaient
        # jusqu'ici (sources_exclusions, domains_whitelist sont dans le
        # context depuis l'endpoint) mais n'etaient jamais lues : l'API
        # documentait une capacite que le pipeline n'honorait pas.
        # Lues ICI, avant l'exploration : celle-ci alimente le plan, donc un
        # filtrage plus tardif laisserait des domaines exclus orienter la
        # structure du rapport.
        ctx.set_domain_constraints(context)

        # ===================================================================
        # PHASE 1: EXPLORATION + PLAN DÉTAILLÉ avec canvas complet
        # ===================================================================
        logger.info("🔍 PHASE 1: Exploration + Planification détaillée")

        # Langue detectee AVANT la premiere recherche, portee par le contexte de
        # la requete. La detection est deterministe (stopwords) : elle ne
        # depend que de la consigne. Si elle ne tranche pas, la langue
        # declaree par le plan est retenue plus bas.
        from app.utils.language import detect_language
        _detected = detect_language(query)
        ctx.set_search_language(context, _detected)

        # Étape 1.1: Exploration initiale restreinte pour évaluer le champ
        exploration_data = await self._exploratory_phase(query, context, ctx=ctx)
        logger.info(f"  ✓ Exploration: {len(exploration_data.get('sources', []))} sources découvertes")
        logger.info(f"  ✓ Champ évalué: {exploration_data.get('field_assessment', 'N/A')}")

        # Étape 1.2: Planification avec canvas détaillé basé sur exploration
        plan = await create_detailed_plan(self.services(), query, context, exploration_data, ctx=ctx)
        logger.info(f"  ✓ Canvas structure: {len(plan.get('sections', []))} sections")
        logger.info(f"  ✓ Profondeur: {plan.get('complexity_analysis', {}).get('target_length', 'N/A')}")
        logger.info(f"  ✓ Enchaînements définis: {len(plan.get('narrative_flow', []))} transitions")

        # Le LLM ne sert que si la detection n'a pas tranche ("auto" fait
        # tomber PubMed de 20 a 0 resultats : traite comme "all").
        _declared = (plan.get('search_strategy', {}) or {}).get('language') or 'all'
        if _declared == 'auto':
            _declared = 'all'
        _lang = _detected if _detected != 'all' else _declared
        if _detected != 'all' and _declared not in ('all', _detected):
            logger.debug(f"Langue: detectee '{_detected}', LLM disait '{_declared}' - detection retenue")
        ctx.set_search_language(context, _lang)
        logger.info(f"  🌐 Langue de recherche et rédaction : {_lang}")

        # Initialiser les sections du canvas
        if 'sections' in plan and plan['sections']:
            ctx.initialize_sections(plan['sections'])
            logger.info(f"  ✓ Canvas initialisé: {len(plan['sections'])} sections")

        # ===================================================================
        # PHASE 2: CONSTRUCTION ITÉRATIVE SECTION PAR SECTION
        # ===================================================================
        logger.info("🏗️  PHASE 2: Construction itérative section par section")

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
        section_targets = plan.get('section_targets', {})
        from app.core.config import settings as _settings
        extraction_semaphore = asyncio.Semaphore(_settings.max_concurrent_extractions)

        async def _research_and_extract(section_name: str, section_config: Dict):
            async with extraction_semaphore:
                logger.info(f"  📝 Section (recherche+extraction): {section_name}")
                section_data = await section_research_phase(self.services(),
                    query=query, section_name=section_name,
                    section_config=section_config, exploration_data=exploration_data,
                    context=ctx
                )
                extracted_data = await section_extraction_phase(self.services(),
                    section_name=section_name, section_data=section_data, context=ctx
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
            int(self.timeout * ctx.config.section_budget_timeout_ratio),
        )

        async def _research_and_extract_bounded(name: str, cfg: Dict):
            try:
                return await asyncio.wait_for(
                    _research_and_extract(name, cfg), timeout=section_budget
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"  ⏱️  Section '{name}' : budget de {section_budget}s dépassé, "
                    f"poursuite sans ses données"
                )
                return (name, [])

        extraction_results = await asyncio.gather(*[
            _research_and_extract_bounded(name, cfg)
            for name, cfg in section_targets.items()
        ], return_exceptions=True)

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
        _strategy = plan.get('search_strategy', {})

        base_threshold = _strategy.get(
            'sources_per_section', ctx.config.complement_default_sources_per_section
        )
        DEPTH_MODULATION = {
            "light": ctx.config.complement_depth_light_delta,
            "moderate": ctx.config.complement_depth_moderate_delta,
            "deep": ctx.config.complement_depth_deep_delta,
        }

        for section_name, section_config in section_targets.items():
            n_sources = len(extracted_by_section.get(section_name, []))
            depth = section_config.get('depth', 'moderate')
            threshold = max(
                ctx.config.complement_min_threshold,
                base_threshold + DEPTH_MODULATION.get(depth, 0),
            )

            if n_sources < threshold:
                logger.info(f"  🔎 Complément pré-rédaction pour '{section_name}': {n_sources}/{threshold} sources")
                key_questions = section_config.get('key_questions', [])
                extra = await targeted_search(self.services(),
                    query=query, section_title=section_name,
                    missing=key_questions, ctx=ctx
                )
                if extra and extra.get('sources'):
                    extracted_by_section[section_name] = extracted_by_section.get(section_name, []) + extra['sources']
                    logger.info(f"     ✓ +{len(extra['sources'])} source(s), total: {len(extracted_by_section[section_name])}")

        # Étape 2.3+2.4 (croisement + synthèse) : sequentiel, car le
        # croisement doit voir les sections precedentes deja finalisees.
        for section_name, section_config in section_targets.items():
            logger.info(f"\n  ✍️  Section (croisement+synthèse): {section_name}")
            extracted_data = extracted_by_section.get(section_name, [])

            enriched_data = await cross_reference_data(self.services(),
                section_name=section_name,
                new_data=extracted_data,
                context=ctx
            )
            logger.info(f"     ✓ {len(enriched_data)} données enrichies")

            section_content = await synthesize_single_section(self.services(),
                query=query,
                section_name=section_name,
                section_config=section_config,
                enriched_data=enriched_data,
                context=ctx
            )
            logger.info(f"     ✓ Contenu généré: {len(section_content.split())} mots")

            # Stocker dans le canvas. setdefault plutot qu'un acces direct :
            # le nom de section utilise ici peut diverger legerement (ponctuation,
            # espaces) de celui pose par initialize_sections() plus tot dans le
            # pipeline, provoquant sinon un KeyError qui fait echouer tout le
            # rapport alors que le contenu de CETTE section a bien ete genere.
            ctx.final_content['sections'].setdefault(section_name, {
                "raw_data": [], "content": "", "metadata": {"data_count": 0, "sources": []}
            })
            ctx.final_content['sections'][section_name]['content'] = section_content
            ctx.final_content['sections'][section_name]['raw_data'] = enriched_data

        # ===================================================================
        # PHASE 3: COHÉRENCE GLOBALE PARTIE PAR PARTIE
        # ===================================================================
        logger.info("\n🔗 PHASE 3: Revue de cohérence globale")

        # Étape 3.1: Analyse inter-sections
        coherence_analysis = await self._analyze_intersections(
            query=query,
            plan=plan,
            context=ctx
        )
        logger.info(f"  ✓ {len(coherence_analysis.get('improvements', []))} améliorations identifiées")

        # Étape 3.2: Application des améliorations de cohérence
        await apply_coherence_improvements(
            coherence_analysis=coherence_analysis,
            context=ctx
        )
        logger.info("  ✓ Cohérence narrative améliorée")

        # ===================================================================
        # PHASE 4: FINALISATION
        # ===================================================================
        logger.info("\n✅ PHASE 4: Finalisation")

        # Étape 4.1: Assemblage final
        final_answer = await self._final_assembly(query, plan, ctx)

        # Étape 4.1.5 (nouveau) : enrichissement iteratif des sections faibles
        # ou trop courtes - mecanisme complet mais jusqu'ici jamais branche.
        # Repond directement au probleme observe en session : des sections
        # en echec ("[Erreur lors de la generation...]") qui restaient dans
        # le rapport final sans jamais etre retentees. max_iterations=1 par
        # prudence (defaut=2) pour ne pas alourdir excessivement le temps
        # total d'un rapport deja long (55-280s observes).
        try:
            final_answer = await iterative_enrichment(self.services(),
                query, ctx, final_answer, max_iterations=ctx.config.enrichment_max_iterations
            )
        except Exception as e:
            logger.warning(f"Enrichissement iteratif echoue, rapport initial conserve: {e}")
            ctx.diagnostics.note("enrichissement.etape_echouee", error=repr(e))

        # Étape 4.2: Génération bibliographie complète
        #
        # Cette etape ANNONCAIT un travail qu'elle ne faisait pas : un simple
        # logger.info, sans aucun appel a la conversion. Consequence observee
        # sur ce chemin (le principal) : des marqueurs [SOURCE:url] bruts
        # restaient dans le texte livre — 5 dans un rapport, y compris dans le
        # resume — alors que les URLs figuraient correctement en [1], [2]...
        # dans la bibliographie.
        # Pour FACT, ces affirmations n'avaient aucune source rattachee :
        # l'extraction des triplets se fait sur les [n], pas sur les marqueurs
        # internes.
        # Les URL extraites par la recherche ciblee de l'enrichissement ne sont
        # ni dans les data des sections ni dans la bibliographie : elles comptent
        # comme fournies au modele (ctx.discovered_sources).
        final_answer = self._convert_sources_to_bibliography(
            final_answer, ctx.diagnostics, provided_urls=ctx.discovered_sources
        )

        # Un rapport bati sur le plan generique de repli (V3-7) ne repond pas a
        # la consigne : il est annonce degrade, avec la cause.
        if plan.get("fallback"):
            final_answer["degraded"] = True
            final_answer["plan_fallback_reason"] = plan.get("fallback_reason")

        # Nettoyage FINAL des marqueurs residuels, apres toutes les etapes.
        # Le filet pose dans _final_assembly ne suffit pas : l'enrichissement
        # iteratif puis cette conversion s'executent APRES lui et peuvent
        # reintroduire un resume non nettoye. Ici, plus rien ne s'execute
        # ensuite — c'est le dernier point ou agir.
        import re as _re_final
        _marker = _re_final.compile(r'\s*\[SOURCE:[^\]]*\]?')
        for _k in ("summary", "introduction", "conclusion"):
            if isinstance(final_answer.get(_k), str):
                final_answer[_k] = _marker.sub('', final_answer[_k]).strip()
        for _s in (final_answer.get("sections") or []):
            if isinstance(_s.get("content"), str) and "[SOURCE:" in _s["content"]:
                _s["content"] = _marker.sub('', _s["content"]).strip()

        # Étape 4.3: Génération des traces complètes
        final_answer = self._add_complete_traces(final_answer, ctx, exploration_data, plan)

        processing_time = (datetime.now() - start_time).total_seconds()

        return {
            "success": True,
            "query": query,
            "answer": final_answer,
            "execution_context": self._execution_summary(ctx),
            "diagnostics": self._diagnostics_block(ctx, final_answer),
            "processing_time": processing_time
        }

    @staticmethod
    def _execution_summary(ctx: ExecutionContext) -> Dict[str, Any]:
        """Compteurs d'execution d'une requete, pour la reponse de l'API."""
        return {
            "steps_executed": len(ctx.steps),
            "datasets_collected": len(ctx.datasets),
            "sources_discovered": ctx.discovered_sources,  # Liste complète, pas len()
            "tool_performance": ctx.tool_success_rate
        }

    @staticmethod
    def _diagnostics_block(
        ctx: ExecutionContext, report: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Bloc diagnostics de la reponse : configuration, evenements, invariants.

        Lu tel quel par le harnais de benchmark (diagnostics.config) et par le
        serveur MCP.
        """
        from app.research.invariants import check_report

        invariants: Optional[Dict[str, Any]] = None
        if isinstance(report, dict):
            try:
                invariants = check_report(report)
            except Exception as exc:  # noqa: BLE001 - un controle ne doit pas perdre le rapport
                ctx.diagnostics.note("api.invariants_failed", error=f"{type(exc).__name__}: {exc}")
        return {
            "config": ctx.config.to_dict(),
            "events": ctx.diagnostics.to_dict(),
            "invariants": invariants,
        }

    @staticmethod
    def _report_title(query: str, context: ExecutionContext) -> str:
        """Titre du rapport (commun a l'assemblage final et au rapport partiel)."""
        title = f"Analyse: {query.title()}"
        if context.config.clean_title:
            from app.research.report_title import short_title
            title = short_title(query)
            if not title:
                title = f"Analyse: {query.title()}"
                context.diagnostics.note("assemblage.titre_repli", raison="consigne vide")
        return title

    @staticmethod
    def _apply_summary(report: Dict[str, Any], context: ExecutionContext) -> None:
        """Resume du rapport selon summary_mode (commun aux deux assemblages).

        Construit APRES la conversion des sources (le contenu porte des [n]) et
        APRES le retrait des sections marquees : un marqueur interne ne doit
        jamais devenir le resume (C41). Retire tout residu "[SOURCE:" orphelin."""
        import re as _re
        secs = report.get("sections") or []
        if context.config.summary_mode == "none":
            report["summary"] = ""
        elif secs:
            first = (secs[0].get("content") or "").strip()
            sentences = first.split('. ')[:context.config.summary_sentences]
            summ = (
                '. '.join(sentences).rstrip('.') + '.'
                if sentences and first else "Rapport de recherche approfondie."
            )
            summ = _re.sub(r'\s*\[SOURCE:[^\]]*\]?', '', summ).strip()
            report["summary"] = summ or "Rapport de recherche approfondie."

    def _partial_report(self, query: str, ctx: ExecutionContext) -> Dict[str, Any]:
        """Rapport construit avec les sections deja redigees (delai global depasse)."""
        import re as _re_partial

        sections: List[Dict[str, Any]] = []
        missing: List[str] = []
        failed: List[str] = []
        unsourced: List[str] = []
        for name, data in ctx.final_content["sections"].items():
            content = (data.get("content") or "").strip()
            if "[SYNTHESE_ECHOUEE]" in content:
                failed.append(name)
                continue
            if "[SECTION_SANS_SOURCE]" in content:
                unsourced.append(name)
                continue
            if not content:
                missing.append(name)
                continue
            raw = data.get("raw_data", [])
            sections.append({
                "title": name,
                "content": content,
                "data": raw,
                "metadata": {"word_count": len(content.split()), "sources_count": len(raw)},
            })
        report: Dict[str, Any] = {
            "type": "report",
            "title": self._report_title(query, ctx),
            "summary": "",
            "sections": sections,
            "metadata": {
                "sections_count": len(sections),
                "partial": True,
                "missing_sections_count": len(missing),
            },
            "degraded": True,
            "missing_sections": missing,
        }
        if failed:
            report["failed_sections"] = failed
            report["metadata"]["failed_sections_count"] = len(failed)
        if unsourced:
            report["unsourced_sections"] = unsourced
            report["metadata"]["unsourced_sections_count"] = len(unsourced)
        report = self._convert_sources_to_bibliography(
            report, ctx.diagnostics, provided_urls=ctx.discovered_sources
        )
        marker = _re_partial.compile(r'\s*\[SOURCE:[^\]]*\]?')
        for section in report["sections"]:
            section["content"] = marker.sub('', section["content"]).strip()
        self._apply_summary(report, ctx)
        return report

    async def execute_with_deadline(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
        deadline_seconds: float = 600.0,
    ) -> Dict[str, Any]:
        """Execute la recherche avec un delai global (C48).

        Passe le delai, le pipeline est interrompu et la reponse porte les
        sections deja redigees avec degraded=True. Toute exception du pipeline
        devient une reponse success=False qui garde les diagnostics : la forme
        du resultat est la meme dans tous les cas.
        """
        started = datetime.now()
        # Contexte vivant de cette requete, cree ici pour que le rapport
        # partiel lise ce qui est deja redige quand le delai expire.
        ctx = ExecutionContext(query, config=self.config)
        task = asyncio.ensure_future(self._run_pipeline(query, context, ctx))
        try:
            done, _ = await asyncio.wait({task}, timeout=deadline_seconds)
        except asyncio.CancelledError:
            task.cancel()
            raise

        if task in done:
            try:
                return task.result()
            except Exception as exc:  # noqa: BLE001 - l'echec est rendu dans la reponse
                ctx.diagnostics.note(
                    "api.orchestrator_exception", error=f"{type(exc).__name__}: {exc}"
                )
                logger.error(f"Pipeline en echec: {exc}", exc_info=True)
                return {
                    "success": False,
                    "query": query,
                    "answer": None,
                    "error": f"{type(exc).__name__}: {exc}",
                    "execution_context": self._execution_summary(ctx),
                    "diagnostics": self._diagnostics_block(ctx, None),
                    "processing_time": (datetime.now() - started).total_seconds(),
                }

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        ctx.diagnostics.note("api.deadline_exceeded", deadline_seconds=deadline_seconds)
        logger.warning(f"Delai global de {deadline_seconds}s depasse : rapport partiel")
        report = self._partial_report(query, ctx)
        return {
            "success": True,
            "query": query,
            "answer": report,
            "timed_out": True,
            "execution_context": self._execution_summary(ctx),
            "diagnostics": self._diagnostics_block(ctx, report),
            "processing_time": (datetime.now() - started).total_seconds(),
        }

    # ===================================================================
    # NOUVELLE ARCHITECTURE OPTIMISÉE
    # Phase 3: Synthèse par section
    # Phase 4: Assemblage global et évaluation
    # Phase 5: Comblement des manques
    # ===================================================================

    def _convert_sources_to_bibliography(
        self,
        report: Dict[str, Any],
        diagnostics: Optional[Diagnostics] = None,
        provided_urls: Sequence[str] = (),
    ) -> Dict[str, Any]:
        """Convertit [SOURCE:url] en [n] et construit la bibliographie.

        Idempotente (la conversion est appelee deux fois sur le chemin
        principal). La logique est dans app.research.citations.
        """
        from app.research.citations import construire_bibliographie

        report = construire_bibliographie(
            report,
            accessed=datetime.now().strftime("%Y-%m-%d"),
            validate_cited_urls=self.config.validate_cited_urls,
            cited_only=self.config.bibliography_cited_only,
            real_titles=self.config.bibliography_real_titles,
            diagnostics=diagnostics,
            provided_urls=provided_urls,
        )
        logger.info(f"  Bibliographie generee: {len(report['bibliography'])} sources")
        return report

    # ========================================================================
    # NOUVELLES MÉTHODES POUR ARCHITECTURE REFONDÉE
    # ========================================================================

    async def _final_assembly(
        self,
        query: str,
        plan: Dict,
        context: ExecutionContext
    ) -> Dict[str, Any]:
        """
        PHASE 4.1: Assemblage final du rapport complet.

        Combine toutes les sections en rapport structuré.
        """
        logger.info("  📦 Assemblage final")

        sections_list = []
        total_words = 0

        for section_name in plan.get("sections", []):
            if section_name in context.final_content['sections']:
                section_data = context.final_content['sections'][section_name]
                content = section_data.get('content', '')
                word_count = len(content.split())
                total_words += word_count

                sections_list.append({
                    "title": section_name,
                    "content": content,
                    "data": section_data.get('raw_data', []),
                    "metadata": {
                        "word_count": word_count,
                        "sources_count": len(section_data.get('raw_data', []))
                    }
                })

        # Générer titre et summary
        complexity = plan.get("complexity_analysis", {})
        title = self._report_title(query, context)

        # Le summary est un EXTRAIT de la premiere section. Il etait construit
        # ICI, avant _convert_sources_to_bibliography : il embarquait donc des
        # marqueurs [SOURCE:url] bruts, que la conversion ne rattrapait pas
        # ensuite (l'URL avait deja ete remplacee dans la section, donc elle
        # n'etait plus cherchee sous cette forme).
        # Il est desormais construit APRES la conversion, a partir du contenu
        # deja normalise — voir plus bas.
        summary = ""

        final_report = {
            "type": "report",
            "title": title,
            "summary": summary,
            "sections": sections_list,
            "metadata": {
                "total_word_count": total_words,
                "sections_count": len(sections_list),
                "complexity_score": complexity.get("overall_score", 3.0),
                "target_length": complexity.get("target_length", "standard")
            }
        }

        # Conversion sources → bibliographie (journal de la requete transmis :
        # sans lui les notes citations.* sont perdues)
        final_report = self._convert_sources_to_bibliography(
            final_report, getattr(context, "diagnostics", None)
        )

        # Comptabiliser les sections perdues par echec LLM. Sans cela, un
        # rapport dont TOUTES les sections ont echoue sortait avec
        # success=true et 88 mots (5 messages d'erreur) : l'appelant ne
        # pouvait pas distinguer un sujet pauvre d'une panne d'infra, et un
        # run de benchmark aurait mesure la disponibilite du LLM plutot que
        # la qualite du pipeline.
        failed = [
            s["title"] for s in sections_list
            if "[SYNTHESE_ECHOUEE]" in (s.get("content") or "")
        ]
        if failed:
            final_report["degraded"] = True
            final_report["failed_sections"] = failed
            final_report["metadata"]["failed_sections_count"] = len(failed)
            logger.error(
                f"  ⚠️  {len(failed)}/{len(sections_list)} section(s) non générée(s) "
                f"par échec LLM: {', '.join(failed[:3])}"
            )

        # Sections refusees faute de source : distinctes d'un echec LLM (la
        # cause est en amont, dans la collecte) mais tout aussi importantes a
        # signaler. Un rapport majoritairement non source doit etre annonce
        # comme tel plutot que rendu comme un resultat ordinaire.
        unsourced = [
            s["title"] for s in sections_list
            if "[SECTION_SANS_SOURCE]" in (s.get("content") or "")
        ]

        # Les marqueurs internes ne doivent JAMAIS atteindre le lecteur.
        # Observe sur 3 rapports du run de reference : "[SECTION_SANS_SOURCE]
        # Aucune donnee exploitable..." apparaissait tel quel dans le rapport
        # final, y compris comme PREMIERE PHRASE du rapport le mieux note
        # (#56, 0.434) - qui aurait donc pu scorer plus haut encore.
        # Ces marqueurs servent au pipeline pour remonter un echec ; une fois
        # l'information extraite dans unsourced/failed, la section vide est
        # retiree du rapport plutot que livree.
        _before = len(sections_list)
        sections_list = [
            s for s in sections_list
            if "[SECTION_SANS_SOURCE]" not in (s.get("content") or "")
            and "[SYNTHESE_ECHOUEE]" not in (s.get("content") or "")
        ]
        if len(sections_list) < _before:
            logger.info(
                f"  🧹 {_before - len(sections_list)} section(s) vide(s) retirée(s) du rapport "
                f"(échec déjà signalé dans les métadonnées)"
            )
            final_report["sections"] = sections_list
            # sections_count est calcule avant ce filtrage : le remettre a
            # jour, sinon les metadonnees annoncent des sections absentes.
            if isinstance(final_report.get("metadata"), dict):
                final_report["metadata"]["sections_count"] = len(sections_list)
        if unsourced:
            final_report["unsourced_sections"] = unsourced
            final_report["metadata"]["unsourced_sections_count"] = len(unsourced)
            logger.warning(
                f"  ⚠️  {len(unsourced)}/{len(sections_list)} section(s) sans source "
                f"exploitable: {', '.join(unsourced[:3])}"
            )
            # Majorite non sourcee -> le rapport n'est pas un resultat fiable.
            if len(unsourced) > len(sections_list) * context.config.unsourced_degraded_ratio:
                final_report["degraded"] = True

        # Summary construit APRES conversion (le contenu porte des [n]) et
        # APRES le retrait des sections marquees : un marqueur interne ne doit
        # jamais devenir le resume (C41). Le filet final retire tout residu
        # "[SOURCE:" orphelin issu d'une coupure en milieu de marqueur.
        self._apply_summary(final_report, context)

        logger.info(f"  ✓ Rapport assemblé: {total_words} mots, {len(sections_list)} sections")

        return final_report

    def _add_complete_traces(
        self,
        report: Dict[str, Any],
        context: ExecutionContext,
        exploration_data: Dict,
        plan: Dict
    ) -> Dict[str, Any]:
        """
        PHASE 4.3: Ajoute traces complètes du processus au rapport.

        Permet de suivre toute la recherche de A à Z.
        """
        logger.info("  📋 Ajout traces complètes")

        report["research_traces"] = {
            "exploration_phase": {
                "sources_discovered": len(exploration_data.get("sources", [])),
                "field_assessment": exploration_data.get("field_assessment"),
                "topics_identified": exploration_data.get("topics_found", [])
            },
            "planning_phase": {
                "complexity_analysis": plan.get("complexity_analysis", {}),
                "sections_planned": plan.get("sections", []),
                "narrative_flow": plan.get("narrative_flow", []),
                "search_strategy": plan.get("search_strategy", {})
            },
            "construction_phase": {
                "sections_built": list(context.final_content['sections'].keys()),
                "total_sources_collected": sum(
                    len(s.get('raw_data', []))
                    for s in context.final_content['sections'].values()
                ),
                "steps_executed": len(context.steps)
            },
            "coherence_phase": {
                "improvements_applied": "See coherence analysis"
            }
        }

        logger.info("  ✓ Traces complètes ajoutées")

        return report
