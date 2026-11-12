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
import json
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
from app.research.stages.crossref import cross_reference_data
from app.research.stages.explore import exploratory_phase
from app.research.stages.extract import section_extraction_phase
from app.research.stages.plan import create_detailed_plan
from app.research.stages.queries import build_search_query
from app.research.stages.search import section_research_phase, targeted_search
from app.research.stages.select import semantic_chunk_selection

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

            section_content = await self._synthesize_single_section(
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
        await self._apply_coherence_improvements(
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
            final_answer = await self._iterative_enrichment(
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

    async def _iterative_enrichment(
        self,
        query: str,
        ctx: ExecutionContext,
        report: Dict[str, Any],
        max_iterations: int = 2
    ) -> Dict[str, Any]:
        """
        Enrichissement itératif du rapport:
        1. Analyse qualité de chaque section
        2. Détecte les manques (chiffres, précisions, cohérence)
        3. Relance recherches ciblées si nécessaire
        4. Régénère sections faibles
        """

        if not ctx.config.enrichment_enabled:
            ctx.diagnostics.note("enrichissement.desactive")
            return report

        for iteration in range(max_iterations):
            logger.info(f"  🔄 Itération {iteration + 1}/{max_iterations}")

            # Analyser qualité de chaque section
            quality_analysis = await self._analyze_report_quality(query, report, ctx)

            overall_score = quality_analysis.get('overall_score')
            logger.info(
                f"  📊 Qualité globale: "
                f"{overall_score if overall_score is not None else 'non mesurée'}/100"
            )

            # Volume reel du rapport, mesure sans LLM. Sert de garde-fou au
            # score de qualite : un rapport quasi vide ne doit jamais pouvoir
            # sortir sur un bon score, quelle que soit la note rendue par
            # l'analyse (qui peut echouer, halluciner, ou noter le plan plutot
            # que le contenu).
            total_chars = sum(
                len(s.get('content', '') or '') for s in report.get('sections', [])
            )
            n_sections = max(1, len(report.get('sections', [])))
            report_is_thin = total_chars < ctx.config.enrichment_thin_chars_per_section * n_sections

            # Si qualité suffisante, arrêter
            if (
                overall_score is not None
                and overall_score >= ctx.config.enrichment_stop_score
                and not report_is_thin
            ):
                logger.info("  ✅ Qualité suffisante atteinte")
                break

            if report_is_thin:
                logger.warning(
                    f"  ⚠️  Rapport anormalement court ({total_chars} chars pour "
                    f"{n_sections} section(s)) — enrichissement malgré le score"
                )

            # Identifier sections à enrichir (score < 75 OU longueur < 2000 chars)
            weak_sections = []

            # GARDE-FOU STRUCTUREL, indépendant du jugement LLM.
            # Si sections_analysis revient vide (analyse échouée, tronquée ou
            # mal formée), la boucle ci-dessous ne s'exécute pas, weak_sections
            # reste vide et l'absence de signal était interprétée comme un
            # signal positif : "Toutes les sections sont de qualité" sur un
            # rapport de 78 mots. Un rapport vide sortait avec success=true.
            # La longueur se mesure directement, sans avoir besoin d'un LLM.
            analysed_titles = {
                sa.get('title') for sa in quality_analysis.get('sections_analysis', [])
            }
            for sec in report.get('sections', []):
                if sec.get('title') in analysed_titles:
                    continue  # traitée par la boucle ci-dessous
                content_length = len(sec.get('content', '') or '')
                if content_length < ctx.config.enrichment_min_section_chars:
                    logger.warning(
                        f"  ⚠️  Section '{sec.get('title')}' non analysée et trop courte "
                        f"({content_length} chars) — enrichissement forcé"
                    )
                    weak_sections.append({
                        "title": sec.get('title'),
                        "score": 0,
                        "missing_elements": ["contenu absent ou insuffisant"],
                    })

            for i, section_analysis in enumerate(quality_analysis.get('sections_analysis', [])):
                section_title = section_analysis.get('title')
                score = section_analysis.get('score', 100)

                # Trouver la section correspondante dans le rapport
                actual_section = None
                for sec in report.get('sections', []):
                    if sec.get('title') == section_title:
                        actual_section = sec
                        break

                # Critères: score < 75 OU longueur < 2000 chars
                content_length = len(actual_section.get('content', '')) if actual_section else 0
                if (
                    score < ctx.config.enrichment_weak_score
                    or content_length < ctx.config.enrichment_min_section_chars
                ):
                    weak_sections.append(section_analysis)
                    if content_length < ctx.config.enrichment_min_section_chars:
                        logger.info(f"  📏 Section '{section_title}' trop courte: {content_length} chars")

            if not weak_sections:
                logger.info("  ✅ Toutes les sections sont de qualité et longueur suffisantes")
                break

            logger.info(f"  🎯 {len(weak_sections)} section(s) à enrichir")
            ctx.diagnostics.note(
                "enrichissement.iteration",
                iteration=iteration + 1,
                weak_sections=len(weak_sections),
                regenerated_max=min(ctx.config.enrichment_max_sections_per_iteration, len(weak_sections)),
                overall_score=overall_score,
            )

            # Enrichir les sections faibles (max 2 par itération)
            for section_analysis in weak_sections[:ctx.config.enrichment_max_sections_per_iteration]:
                section_title = section_analysis.get('title')
                missing = section_analysis.get('missing', [])

                logger.info(f"  🔍 Enrichissement: {section_title}")
                logger.info(f"    Manques: {', '.join(missing)}")

                # Recherche ciblée pour combler les manques
                enrichment_data = await targeted_search(self.services(), query, section_title, missing, ctx)

                # Régénérer la section avec les nouvelles données
                if enrichment_data:
                    from app.research.enrichment_utils import count_citations
                    _before = next(
                        (s for s in report.get('sections', []) if s.get('title') == section_title),
                        None,
                    ) or {}
                    updated_section = await self._regenerate_section(
                        query, section_title, report, enrichment_data, ctx
                    )
                    _after = updated_section if isinstance(updated_section, dict) else {}
                    ctx.diagnostics.note(
                        "enrichissement.section_regeneree",
                        title=section_title,
                        chars_before=len(_before.get('content', '') or ''),
                        chars_after=len(_after.get('content', '') or ''),
                        citations_before=count_citations(_before.get('content')),
                        citations_after=count_citations(_after.get('content')),
                        unchanged=updated_section is _before,
                    )

                    # Remplacer la section dans le rapport (jamais par None :
                    # une section introuvable ne doit pas vider son emplacement)
                    if not isinstance(updated_section, dict):
                        continue
                    for i, section in enumerate(report.get('sections', [])):
                        if section.get('title') == section_title:
                            report['sections'][i] = updated_section
                            break

        return report

    async def _analyze_report_quality(
        self,
        query: str,
        report: Dict[str, Any],
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        """Analyse la qualité de chaque section du rapport."""

        sections_text = []
        for section in report.get('sections', []):
            sections_text.append(f"## {section.get('title')}\n{section.get('content', '')}")

        prompt = f"""Analyse la qualité de ce rapport de recherche:

REQUÊTE: "{query}"

RAPPORT:
{chr(10).join(sections_text)[:ctx.config.enrichment_report_chars]}

Pour CHAQUE section, évalue:

1. **Présence de données factuelles**: Chiffres, dates, noms, statistiques
2. **Précision**: Détails concrets vs généralités
3. **Cohérence**: Logique et liens entre informations
4. **Complétude**: Répond-elle pleinement à son objectif?

Retourne JSON:
{{
  "overall_score": 0-100,
  "sections_analysis": [
    {{
      "title": "Nom section",
      "score": 0-100,
      "missing": ["chiffres précis", "dates", "noms d'initiatives", "données quantitatives"],
      "strengths": ["points forts"],
      "weaknesses": ["points faibles"]
    }}
  ],
  "gaps_to_fill": [
    {{
      "section": "Nom section",
      "gap": "Description du manque",
      "search_needed": "Requête de recherche suggérée"
    }}
  ]
}}

CRITÈRES:
- Score 90-100: Excellent, détaillé, chiffré, sourcé
- Score 75-89: Bon mais peut être enrichi
- Score 50-74: Manque de précision/chiffres
- Score <50: Trop générique, nécessite enrichissement
"""

        try:
            response = await self.llm_client.generate(
                [{"role": "user", "content": prompt}],
                # 4000 et non 2000 : cette fonction REGENERE une section en
                # demandant explicitement un contenu "PLUS detaille que
                # l'original". Un plafond de 2000 jetons (~450 mots en
                # francais) coupait donc en pleine phrase les sections
                # qu'elle etait censee enrichir — meme defaut que la
                # synthese initiale, ou 30% des rapports finissaient sur un
                # mot tronque.
                max_tokens=ctx.config.enrichment_analysis_max_tokens,
                temperature=ctx.config.enrichment_analysis_temperature
            )

            # Parser JSON
            start_idx = response.find('{')
            if start_idx != -1:
                bracket_depth = 0
                for i, char in enumerate(response[start_idx:], start=start_idx):
                    if char == '{':
                        bracket_depth += 1
                    elif char == '}':
                        bracket_depth -= 1
                        if bracket_depth == 0:
                            return json.loads(response[start_idx:i + 1])
        except Exception as e:
            logger.error(f"Erreur analyse qualité: {e}")
            ctx.diagnostics.note("enrichissement.analyse_echouee", error=repr(e))

        # Fallback : aucune mesure. Le score est absent (None), pas invente.
        ctx.diagnostics.note("enrichissement.score_absent")
        return {
            "overall_score": None,
            "sections_analysis": [],
            "gaps_to_fill": []
        }

    async def _regenerate_section(
        self,
        query: str,
        section_title: str,
        report: Dict[str, Any],
        enrichment_data: Dict[str, Any],
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        """Régénère une section avec les données enrichies."""

        # Récupérer la section actuelle
        current_section = None
        for section in report.get('sections', []):
            if section.get('title') == section_title:
                current_section = section
                break

        if not current_section:
            return None

        if ctx.config.regenerate_v2:
            return await self._regenerate_section_v2(
                query, section_title, report, current_section, enrichment_data, ctx
            )

        # Données d'enrichissement
        enrichment_content = json.dumps(
            enrichment_data.get('sources', []),
            indent=2,
            ensure_ascii=False
        )[:ctx.config.enrichment_data_chars]

        prompt = f"""Améliore cette section de rapport avec les nouvelles données collectées:

REQUÊTE GLOBALE: "{query}"

SECTION À AMÉLIORER: {section_title}

CONTENU ACTUEL:
{current_section.get('content', '')}

NOUVELLES DONNÉES COLLECTÉES:
{enrichment_content}

MANQUES IDENTIFIÉS: {', '.join(enrichment_data.get('missing', []))}

Ta mission:
1. ENRICHIR le contenu existant avec les nouvelles données
2. AJOUTER des chiffres précis, dates, noms trouvés
3. MAINTENIR la cohérence avec le reste du rapport
4. CITER les nouvelles sources utilisées

Retourne JSON:
{{
  "title": "{section_title}",
  "content": "Contenu enrichi (2-5 paragraphes avec détails factuels)",
  "sources": [{{"url": "...", "title": "..."}}]
}}

IMPORTANT: Le contenu doit être PLUS détaillé et factuel que l'original.
"""

        try:
            response = await self.llm_client.generate(
                [{"role": "user", "content": prompt}],
                max_tokens=ctx.config.enrichment_regenerate_max_tokens,
                temperature=ctx.config.enrichment_regenerate_temperature
            )

            # Parser JSON
            start_idx = response.find('{')
            if start_idx != -1:
                bracket_depth = 0
                for i, char in enumerate(response[start_idx:], start=start_idx):
                    if char == '{':
                        bracket_depth += 1
                    elif char == '}':
                        bracket_depth -= 1
                        if bracket_depth == 0:
                            result = json.loads(response[start_idx:i + 1])
                            logger.info(f"    ✅ Section enrichie: {section_title}")
                            return result
        except Exception as e:
            logger.error(f"Erreur régénération section: {e}")
            ctx.diagnostics.note(
                "enrichissement.regeneration_echouee", title=section_title, error=repr(e)
            )

        # Fallback: retourner la section actuelle
        return current_section

    async def _regenerate_section_v2(
        self,
        query: str,
        section_title: str,
        report: Dict[str, Any],
        current_section: Dict[str, Any],
        enrichment_data: Dict[str, Any],
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        """Regeneration de section (drapeau regenerate_v2, C43).

        Au moment de l'enrichissement les sections portent des [n] (la
        conversion a eu lieu dans _final_assembly) : on les remet en
        [SOURCE:url] avant l'appel, le modele les conserve, et la conversion
        finale reprend les memes numeros (bibliographie existante conservee).
        Toute erreur ou perte de citation rend la section d'origine.
        """
        from app.research.enrichment_utils import (
            cited_urls,
            count_citations,
            numbered_to_source,
            strip_unknown_citations,
        )

        lang = ctx.search_language or "all"
        bibliography = report.get('bibliography') or []
        current_content = current_section.get('content', '') or ''
        current_with_urls = numbered_to_source(current_content, bibliography)
        original_urls = cited_urls(current_with_urls)

        sources = enrichment_data.get('sources', [])
        allowed_urls = set(original_urls)
        for item in sources:
            if isinstance(item, dict):
                for key in ("url", "source"):
                    if isinstance(item.get(key), str):
                        allowed_urls.add(item[key])
        enrichment_content = json.dumps(sources, indent=2, ensure_ascii=False)

        if lang and lang != "all":
            language_rule = f"Redige en langue '{lang}' (code ISO), comme le reste du rapport."
        else:
            language_rule = "Redige dans la meme langue que le CONTENU ACTUEL."

        prompt = f"""Ameliore cette section de rapport avec les nouvelles donnees collectees.

REQUETE GLOBALE: "{query}"

SECTION A AMELIORER: {section_title}

CONTENU ACTUEL:
{current_with_urls}

NOUVELLES DONNEES COLLECTEES:
{enrichment_content}

MANQUES IDENTIFIES: {', '.join(enrichment_data.get('missing', []))}

Consignes:
1. {language_rule}
2. Enrichis le contenu actuel avec les nouvelles donnees (chiffres, dates, noms).
3. Conserve chaque citation [SOURCE:url] du contenu actuel, avec la meme URL.
4. Cite les nouvelles donnees au format [SOURCE:url], avec une URL presente dans les donnees ci-dessus. N'invente aucune URL.
5. Aucun nombre de paragraphes n'est impose : garde la longueur et la structure que le contenu exige.
"""
        schema = {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "Texte complet de la section enrichie, citations au "
                        "format [SOURCE:url]."
                    ),
                }
            },
            "required": ["content"],
            "additionalProperties": False,
        }

        def _unchanged(reason: str, **details: Any) -> Dict[str, Any]:
            ctx.diagnostics.note(
                "enrichissement.regeneration_v2_repli", title=section_title, raison=reason, **details
            )
            return current_section

        if not getattr(self.llm_client, "supports_structured", False):
            return _unchanged("sortie_structuree_indisponible")

        try:
            result = await self.llm_client.generate_structured(
                [{"role": "user", "content": prompt}],
                schema=schema,
                schema_name="section_regeneree",
                max_tokens=ctx.config.enrichment_regenerate_v2_max_tokens,
                temperature=ctx.config.enrichment_regenerate_temperature,
            )
        except Exception as e:
            logger.error(f"Erreur régénération section (v2): {e}")
            return _unchanged("erreur_llm", error=repr(e))

        new_content = result.get('content') if isinstance(result, dict) else None
        if not isinstance(new_content, str) or not new_content.strip():
            return _unchanged("contenu_vide")

        new_content, invented = strip_unknown_citations(new_content, allowed_urls)
        if invented:
            ctx.diagnostics.note(
                "enrichissement.citations_inventees_retirees",
                title=section_title, urls=invented,
            )

        lost = original_urls - cited_urls(new_content)
        if lost:
            return _unchanged("citations_perdues", urls=sorted(lost))

        updated = dict(current_section)
        updated['content'] = new_content.strip()
        metadata = dict(current_section.get('metadata') or {})
        if 'word_count' in metadata:
            metadata['word_count'] = len(updated['content'].split())
        updated['metadata'] = metadata
        ctx.diagnostics.note(
            "enrichissement.regeneration_v2",
            title=section_title,
            data_chars=len(enrichment_content),
            citations_before=count_citations(current_with_urls),
            citations_after=count_citations(new_content),
        )
        logger.info(f"    Section enrichie (v2): {section_title}")
        return updated

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

    async def _synthesize_single_section(
        self,
        query: str,
        section_name: str,
        section_config: Dict,
        enriched_data: List[Dict],
        context: ExecutionContext
    ) -> str:
        """
        PHASE 2.4: Synthèse d'une section unique.

        Génère le contenu rédigé de la section en utilisant:
        - Les données enrichies
        - Les objectifs de la section
        - La profondeur cible
        """
        logger.info(f"       ✍️  Synthèse section '{section_name}'")

        depth = section_config.get("depth", "moderate")
        cfg = context.config
        words_target = section_config.get("words_target", cfg.synthesis_default_words_target)
        objectives = section_config.get("objectives", [])
        key_questions = section_config.get("key_questions", [])

        # Ajustement local et leger de l'ambition de CETTE section, au
        # regard du materiau reellement disponible - le plan reste globalement
        # fige (pas de replanification complete), mais n'est pas rigide au
        # point d'imposer un objectif de mots deconnecte de ce qui a ete
        # trouve. Sans ca, une section pauvre en sources produit un contenu
        # dilue/generique pour atteindre un objectif arbitraire, ou echoue.
        n_sources = len(enriched_data)
        if n_sources == 0:
            words_target = min(words_target, cfg.synthesis_no_source_words_cap)
            logger.info(f"       ⚠️  Aucune source pour '{section_name}', objectif réduit à {words_target} mots")
        elif (
            n_sources <= cfg.synthesis_few_sources_max
            and words_target > cfg.synthesis_few_sources_words_threshold
        ):
            original_target = words_target
            words_target = max(
                cfg.synthesis_few_sources_words_floor,
                words_target // cfg.synthesis_few_sources_words_divisor,
            )
            depth = "light"  # coherence : ne pas demander 6-10 paragraphes "deep" sur 250 mots
            logger.info(f"       ⚠️  Peu de sources ({n_sources}) pour '{section_name}', objectif ajusté {original_target}→{words_target} mots, profondeur réduite")

        # Sélection sémantique adaptée
        # Requete bornee plutot que la consigne brute concatenee : le scoring
        # compare les mots de la requete au contenu des chunks, une consigne de
        # 350 caracteres noie les termes discriminants dans du remplissage.
        scoring_query = build_search_query(self.services(),
            query=query,
            section_name=section_name,
            search_terms=(section_config or {}).get("search_terms"),
        )
        selected_data = await semantic_chunk_selection(self.services(),
            all_data=enriched_data,
            query=scoring_query,
            # Budget de MATIERE, independant de la longueur du texte a
            # ecrire. Auparavant words_target * 10 : une section de 300 mots
            # ne recevait que 3 000 caracteres de sources — indexer la
            # matiere premiere sur la taille du produit fini n'a aucun sens,
            # et c'est ce qui affamait les sections courtes.
            # 16 000 caracteres : assez pour porter plusieurs passages
            # substantiels de documents differents, tout en restant loin du
            # point ou le contexte se degrade (Chroma, juillet 2025 : la
            # performance baisse quand le contexte s'allonge). Verifie par
            # ailleurs : ce client accepte des prompts de 30 000 caracteres
            # sans difficulte.
            max_chars=cfg.synthesis_source_budget_chars,
            depth=depth,
            context=context,
        )

        # Charge utile du bloc DONNEES DISPONIBLES. Par defaut les passages
        # complets (cross_references, metadata, compteurs compris) ; en mode
        # minimal, seulement ce que la redaction cite.
        if context.config.prompt_payload_minimal:
            prompt_payload = [
                {
                    "source": d.get("source", ""),
                    "title": d.get("title", ""),
                    "content": d.get("content", ""),
                    "corroboration_count": d.get("corroboration_count", 0),
                }
                for d in selected_data
            ]
        else:
            prompt_payload = selected_data

        # Construire le prompt de synthèse
        depth_instructions = {
            "light": "Rédige 2-3 paragraphes concis allant à l'essentiel.",
            "moderate": "Rédige 4-6 paragraphes équilibrés et informatifs.",
            "deep": "Rédige 6-10 paragraphes détaillés explorant tous les aspects."
        }.get(depth, "Rédige un contenu équilibré.")

        # Apercu des sections DEJA REDIGEES (celles qui precedent dans la
        # boucle sequentielle) - sans nouvel appel LLM, juste les premiers
        # caracteres du contenu deja ecrit. Objectif : que la redaction
        # de CETTE section evite proactivement la redondance avec ce qui
        # a deja ete dit, plutot que de compter uniquement sur la Phase 3
        # (detection de redondance) pour la corriger apres coup - priorite
        # a la coherence construite, pas seulement patchee en aval.
        already_written = []
        for other_name, other_data in context.final_content['sections'].items():
            other_content = other_data.get('content', '')
            if other_content and '[Erreur' not in other_content:
                already_written.append(f"- \"{other_name}\": {other_content[:cfg.synthesis_previous_sections_chars].strip()}...")

        already_written_block = ""
        if already_written:
            already_written_block = f"""
SECTIONS DÉJÀ RÉDIGÉES DANS CE RAPPORT (pour éviter les répétitions, construis sur ce qui a déjà été dit plutôt que de le reformuler) :
{chr(10).join(already_written[:cfg.synthesis_previous_sections_max])}
"""

        # Langue de redaction : la meme que celle des recherches, decidee par
        # detection sur la consigne. Sans cette consigne, le modele redigeait
        # systematiquement en francais, y compris sur une consigne anglaise —
        # or le respect de la consigne est l'un des quatre criteres notes par
        # RACE : un rapport dans la mauvaise langue echoue sur ce critere
        # quelle que soit la qualite du fond.
        _lang = context.search_language or "all"
        _lang_names = {
            "fr": "français", "en": "anglais", "es": "espagnol",
            "de": "allemand", "it": "italien", "pt": "portugais",
        }
        if _lang in _lang_names:
            lang_instruction = (
                f"\n⚠️ LANGUE : rédige INTÉGRALEMENT en {_lang_names[_lang]}, "
                f"la langue de la demande. Les sources peuvent être dans "
                f"d'autres langues : traduis ce que tu en reprends.\n"
            )
        else:
            lang_instruction = (
                "\n⚠️ LANGUE : rédige dans la langue de la demande ci-dessous.\n"
            )

        prompt = f"""{lang_instruction}
Rédige la section "{section_name}" pour répondre à: "{query}"

OBJECTIFS DE CETTE SECTION:
{chr(10).join(f'- {obj}' for obj in objectives)}

QUESTIONS CLÉS À EXPLORER:
{chr(10).join(f'- {q}' for q in key_questions)}

INSTRUCTIONS:
{depth_instructions}
{already_written_block}
DONNÉES DISPONIBLES ({len(selected_data)} sources):
{json.dumps(prompt_payload, indent=2, ensure_ascii=False)}

RÈGLES:
1. CITATIONS — chaque affirmation factuelle porte sa source, en fin de
   phrase, au format [SOURCE:url_complète] (crochet fermant OBLIGATOIRE).
   Une section de 400 mots doit contenir plusieurs citations, pas une seule
   en conclusion. Un paragraphe sans citation n'est pas verifiable : soit
   il reformule une source et doit la citer, soit il n'apporte rien.
   Copie l'URL exactement telle qu'elle apparaît dans les données, sans la
   raccourcir ni l'abréger.
2. La longueur est une CONSÉQUENCE de la matière disponible, jamais un
   objectif. Écris ce que les sources permettent d'établir, puis arrête-toi.
   Mieux vaut une section dense et courte qu'une section étirée pour
   atteindre un volume.
3. Structure et PRÉSENTATION DES DONNÉES (critère noté explicitement) :
   La prose reste le mode principal, mais mets en forme ce qui se lit mal en
   paragraphe :
   - un TABLEAU markdown dès que tu compares 3 éléments ou plus sur les mêmes
     critères, ou que tu alignes des séries chiffrées (années, montants,
     parts) — c'est plus clair et plus vérifiable qu'une énumération noyée
     dans une phrase ;
   - une LISTE courte pour des éléments réellement parallèles (conditions,
     étapes, facteurs) ;
   - un SOUS-TITRE (###) quand la section couvre deux angles nettement
     distincts.
   Ne fabrique pas de tableau pour du contenu qui n'en est pas : deux
   chiffres dans une phrase restent une phrase.

   EXEMPLE de ce qui est attendu quand tu compares trois éléments :

   Les trois langages divergent nettement sur la gestion mémoire [SOURCE:url].

   | Critère | Rust | Go | C++ |
   |---|---|---|---|
   | Sûreté mémoire | garantie à la compilation | ramasse-miettes | manuelle |
   | Latence | prévisible | pauses GC | prévisible |
   | Maturité | écosystème jeune | stable | très mature |

   Cette différence explique pourquoi Rust est retenu pour les systèmes
   temps réel [SOURCE:url].

   Le tableau ne remplace pas l'analyse : il la précède ou la suit.
4. Ton: informatif, précis, fluide
5. Ne répète pas ce qui est déjà couvert par les autres sections listées ci-dessus

FIDÉLITÉ DES DONNÉES CHIFFRÉES (règle prioritaire) :
Tout chiffre, pourcentage, date ou montant doit être RECOPIÉ EXACTEMENT
depuis la source, avec son année de référence. Ne reformule jamais une
donnée de mémoire, ne l'arrondis pas, ne l'harmonise pas entre sources.
  ✗ "en 2020, la proportion atteignait 28,7%"  alors que la source dit
  ✓ "en 2021, la proportion était de 28,9% [SOURCE:url]"
Si deux sources donnent des valeurs différentes, cite les deux avec leurs
années respectives plutôt que d'en fabriquer une moyenne.
Si tu n'es pas certain du chiffre exact, écris la tendance sans chiffre —
une affirmation vague et vraie vaut mieux qu'un chiffre précis et faux.
Donne toujours les chiffres avec leur unité et leur année, dans la phrase
elle-même — pas en note ni en aparté.

Rédige uniquement le contenu (pas de titre de section, pas de métadonnées).
"""

        # Diagnostic : une synthese qui rend 13-17 mots au lieu de ~400 vient
        # soit d'un prompt sans donnees exploitables, soit d'un refus du
        # modele. Journaliser la taille reelle du prompt et le volume de
        # donnees transmis permet de trancher sans relancer un run complet.
        # Pas de re-limitation ici : _semantic_chunk_selection a deja applique
        # max_chars ET max_chunks_target. Un plafond supplementaire annulerait
        # silencieusement le budget accorde — c'est exactement le defaut
        # corrige plus haut (max_chunks_target=4 plafonnait a 4 000 chars
        # quel que soit max_chars).
        _payload_chars = sum(len(str(d.get("content", ""))) for d in selected_data)

        # REFUS DE REDIGER SANS SOURCE.
        #
        # Sans donnees, le LLM redigeait quand meme depuis ses connaissances
        # internes : des rapports de 1 200 a 1 500 mots sortaient avec ZERO
        # citation et zero entree de bibliographie. C'est exactement
        # l'hallucination que tout le pipeline (corroboration, scoring,
        # citations [SOURCE:]) existe pour empecher - un rapport non source
        # est pire qu'une section absente, parce qu'il est indiscernable d'un
        # rapport documente pour qui le lit.
        #
        # L'ajustement d'ambition (objectif reduit a 150 mots) attenuait le
        # symptome sans traiter la cause. On refuse desormais franchement, et
        # la section est marquee pour que l'echec remonte au lieu d'etre
        # invisible.
        if not selected_data or _payload_chars < cfg.synthesis_min_payload_chars:
            logger.warning(
                f"       ⛔ Section '{section_name}' non rédigée : "
                f"{len(selected_data)} source(s), {_payload_chars} chars de données — "
                f"rédiger sans matière produirait du contenu non sourcé"
            )
            return (
                "[SECTION_SANS_SOURCE] Aucune donnée exploitable n'a pu être collectée "
                "pour cette section."
            )

        logger.info(
            f"       📝 Prompt synthèse '{section_name}': {len(prompt)} chars, "
            f"{len(selected_data)} source(s), {_payload_chars} chars de données, "
            f"objectif {words_target} mots (max_tokens={int(words_target * cfg.synthesis_tokens_per_word)})"
        )

        # Reessai avec attente progressive : la synthese est le seul endroit du
        # pipeline ou un echec LLM est irrattrapable (sans texte, pas de
        # section). Les "Connection error" observes sur SSPCloud sont
        # intermittents et souvent resolus au 2e essai ; sans reessai, une
        # coupure de quelques secondes vidait TOUT le rapport - 5 sections
        # perdues d'un coup, pour un rapport rendu avec success=true.
        last_error = None
        max_attempts = cfg.synthesis_max_attempts
        for attempt in range(max_attempts):
            try:
                response = await self.llm_client.generate(
                    [{"role": "user", "content": prompt}],
                    # 4.5 jetons/mot et non 2.5.
                    #
                    # Le ratio 2.5 coupait les sections en pleine phrase :
                    # 30% des rapports du run v9 se terminaient sur un mot
                    # tronque, contre 0% sur la reference. Mesure directe :
                    # des sections de 576, 635, 913 et 1003 mots pour un
                    # budget de 1000 a 1375 jetons.
                    #
                    # Deux causes cumulees : le francais consomme plus de
                    # jetons par mot que l'anglais (accents, elisions), et
                    # depuis que le budget de sources est passe de 3 000 a
                    # 16 000 caracteres le modele a bien plus de matiere,
                    # donc redige plus long que words_target.
                    # words_target reste un OBJECTIF indicatif ; max_tokens
                    # doit etre un plafond de securite, pas une contrainte
                    # qui mutile la derniere phrase.
                    max_tokens=int(words_target * cfg.synthesis_tokens_per_word),
                    temperature=cfg.synthesis_temperature
                )
                if self.llm_client.last_truncated:
                    context.diagnostics.note(
                        "selection.synthese_tronquee",
                        section=section_name,
                        max_tokens=int(words_target * cfg.synthesis_tokens_per_word),
                    )
                if response and response.strip():
                    # Controle de fidelite numerique : signale les chiffres
                    # ecrits qui n'apparaissent dans aucune source de la
                    # section. Diagnostic FACT : les citations invalides ne
                    # viennent pas d'hallucination mais de derive a la
                    # reformulation (28,7% en 2020 ecrit pour 28,9% en 2021).
                    # On journalise sans corriger : un chiffre peut etre
                    # legitimement calcule sans figurer tel quel.
                    try:
                        from app.utils.numeric_fidelity import check_numeric_fidelity
                        _src_text = " ".join(str(d.get("content", "")) for d in selected_data)
                        _fid = check_numeric_fidelity(response, _src_text)
                        if _fid.get("unmatched_count"):
                            logger.warning(
                                f"       🔢 '{section_name}': {_fid['unmatched_count']}/"
                                f"{_fid['numbers_written']} chiffre(s) absent(s) des sources "
                                f"(fidélité {_fid['fidelity_rate']}) — {_fid['unmatched'][:5]}"
                            )
                            context.diagnostics.note(
                                "selection.chiffres_absents",
                                section=section_name,
                                nombre=_fid["unmatched_count"],
                                ecrits=_fid["numbers_written"],
                            )
                    except Exception as e:
                        logger.debug(f"Contrôle de fidélité non appliqué : {e}")
                        context.diagnostics.note(
                            "selection.controle_fidelite_indisponible",
                            section=section_name,
                            erreur=str(e),
                        )

                    word_count = len(response.split())
                    if word_count < 50 and words_target >= 150:
                        logger.warning(
                            f"       ⚠️  Réponse anormalement courte ({word_count} mots "
                            f"pour {words_target} demandés) — brut: {response.strip()[:300]!r}"
                        )
                    logger.info(f"       → {word_count} mots générés")
                    return response.strip()
                last_error = "reponse vide"
            except Exception as e:
                last_error = e

            if attempt < max_attempts - 1:
                delay = cfg.synthesis_retry_backoff_base ** attempt  # 1s, 2s par defaut
                logger.warning(
                    f"       ⏳ Synthèse '{section_name}' échouée "
                    f"({last_error}) — nouvel essai dans {delay}s"
                )
                await asyncio.sleep(delay)

        # Echec definitif : marqueur reconnaissable en aval (permet de
        # compter les sections perdues et de ne pas rendre un rapport vide
        # avec success=true, cf. _final_assembly).
        logger.error(f"       ❌ Synthèse '{section_name}' abandonnée après {max_attempts} essais: {last_error}")
        return f"[SYNTHESE_ECHOUEE] Section '{section_name}' non générée ({last_error})."

    async def _analyze_intersections(
        self,
        query: str,
        plan: Dict,
        context: ExecutionContext
    ) -> Dict[str, Any]:
        """
        PHASE 3.1: Analyse les liens entre sections et identifie améliorations.

        Retourne:
        - improvements: Liste d'améliorations de cohérence à appliquer
        - transitions_needed: Transitions manquantes
        - redundancies: Redondances détectées
        """
        logger.info("  🔍 Analyse inter-sections")

        sections_content = []
        for section_name, section_data in context.final_content['sections'].items():
            sections_content.append({
                "title": section_name,
                "content_preview": section_data.get('content', '')[:context.config.coherence_preview_chars],
                "word_count": len(section_data.get('content', '').split()),
                "sources_count": len(section_data.get('raw_data', []))
            })

        prompt = f"""Analyse la cohérence globale de ce rapport en cours de construction.

REQUÊTE INITIALE: "{query}"

SECTIONS GÉNÉRÉES:
{json.dumps(sections_content, indent=2, ensure_ascii=False)}

ENCHAÎNEMENTS PRÉVUS:
{json.dumps(plan.get('narrative_flow', []), indent=2)}

Ta mission: Identifier améliorations de cohérence.

Retourne JSON:
{{
  "improvements": [
    {{
      "type": "transition|link|structure",
      "between_sections": ["Section A", "Section B"],
      "issue": "Description du problème",
      "suggestion": "Comment améliorer",
      "priority": "high|medium|low"
    }}
  ],
  "redundancies": [
    {{"sections": ["Section X", "Section Y"], "description": "..."}}
  ],
  "coherence_score": 0-100
}}
"""

        intersections_schema = {
            "type": "object",
            "properties": {
                "improvements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["transition", "link", "structure"]},
                            "between_sections": {"type": "array", "items": {"type": "string"}},
                            "issue": {"type": "string"},
                            "suggestion": {"type": "string"},
                            "priority": {"type": "string", "enum": ["high", "medium", "low"]}
                        },
                        "required": ["type", "between_sections", "issue", "suggestion", "priority"],
                        "additionalProperties": False
                    }
                },
                "redundancies": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sections": {"type": "array", "items": {"type": "string"}},
                            "description": {"type": "string"}
                        },
                        "required": ["sections", "description"],
                        "additionalProperties": False
                    }
                },
                "coherence_score": {"type": "integer", "minimum": 0, "maximum": 100}
            },
            "required": ["improvements", "redundancies", "coherence_score"],
            "additionalProperties": False
        }

        try:
            if self.llm_client.supports_structured:
                analysis = await self.llm_client.generate_structured(
                    [{"role": "user", "content": prompt}],
                    schema=intersections_schema, schema_name="coherence_analysis",
                    max_tokens=context.config.coherence_max_tokens,
                    temperature=context.config.coherence_temperature,
                )
                return analysis
            else:
                response = await self.llm_client.generate(
                    [{"role": "user", "content": prompt}],
                    max_tokens=context.config.coherence_max_tokens,
                    temperature=context.config.coherence_temperature,
                )
                start_idx = response.find('{')
                if start_idx != -1:
                    bracket_depth = 0
                    for i, char in enumerate(response[start_idx:], start=start_idx):
                        if char == '{':
                            bracket_depth += 1
                        elif char == '}':
                            bracket_depth -= 1
                            if bracket_depth == 0:
                                analysis = json.loads(response[start_idx:i + 1])
                                return analysis

        except Exception as e:
            logger.error(f"  ❌ Erreur analyse cohérence: {e}")
            context.diagnostics.note("coherence.analyse_echouee", error=repr(e))

        # Aucune mesure : le score est absent (None), pas invente.
        context.diagnostics.note("coherence.score_absent")
        return {"improvements": [], "redundancies": [], "coherence_score": None}

    async def _apply_coherence_improvements(
        self,
        coherence_analysis: Dict,
        context: ExecutionContext
    ) -> None:
        """
        PHASE 3.2: Applique les améliorations de cohérence identifiées.

        Modifie les sections in-place pour ajouter transitions, liens, etc.
        """
        improvements = coherence_analysis.get("improvements", [])
        high_priority = [i for i in improvements if i.get("priority") == "high"]

        if not high_priority:
            logger.info("  ✓ Aucune amélioration critique nécessaire")
            return

        if not context.config.coherence_append_enabled:
            context.diagnostics.note(
                "coherence.append_desactive", suggestions_ignorees=len(high_priority[:context.config.coherence_max_improvements])
            )
            return

        logger.info(f"  🔧 Application de {len(high_priority)} améliorations")

        for improvement in high_priority[:context.config.coherence_max_improvements]:  # plafond d'ameliorations
            imp_type = improvement.get("type")
            sections_involved = improvement.get("between_sections", [])
            suggestion = improvement.get("suggestion", "")

            if imp_type == "transition" and len(sections_involved) == 2:
                # Ajouter transition entre deux sections
                section_a, section_b = sections_involved
                if section_a in context.final_content['sections'] and section_b in context.final_content['sections']:
                    # Ajouter phrase de transition à la fin de section A
                    current_content = context.final_content['sections'][section_a]['content']
                    transition_text = f"\n\n{suggestion}"
                    context.final_content['sections'][section_a]['content'] = current_content + transition_text
                    context.diagnostics.note(
                        "coherence.transition_collee",
                        section=section_a,
                        suggestion_chars=len(suggestion or ""),
                        chars_before=len(current_content or ""),
                        chars_after=len(current_content or "") + len(transition_text),
                    )
                    logger.info(f"     → Transition ajoutée: {section_a} → {section_b}")

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
