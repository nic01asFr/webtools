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
from app.api.models import ExtractionOptions
from app.core.data_extractor import GenericDataExtractor
from app.research.diagnostics import Diagnostics
from app.research.pipeline_config import PipelineConfig

logger = logging.getLogger(__name__)


def _cosine_similarity(a: list, b: list) -> float:
    """Similarite cosinus entre deux vecteurs d'embedding (0-1)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return max(0.0, dot / (norm_a * norm_b))


def _extract_domain(url: str) -> str:
    """Domaine racine d'une URL, pour juger de l'independance de deux sources."""
    from urllib.parse import urlparse
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return url


class ExecutionContext:
    """Contexte d'exécution avec historique et apprentissage."""

    def __init__(self, query: str, config: PipelineConfig | None = None):
        self.query = query
        # Configuration effective et journal des degradations de CETTE requete.
        self.config: PipelineConfig = config if config is not None else PipelineConfig.from_env()
        self.diagnostics: Diagnostics = Diagnostics()
        # Langue de recherche et de redaction, decidee par le plan (langue de
        # la consigne). "all" ne restreint rien - c'est le defaut sur, alors
        # que "auto" fait tomber PubMed de 20 a 0 resultats (verifie).
        self.search_language: str = "all"
        self.steps: List[Dict] = []
        self.datasets: Dict[str, List[Dict]] = {}
        self.discovered_sources: List[str] = []
        self.tool_success_rate: Dict[str, Dict] = {}
        # Cache d'extraction partage sur toute la duree d'une requete research_deep :
        # un meme article pertinent pour plusieurs sections (frequent) n'est
        # extrait qu'une seule fois, pas une fois par section qui le reference.
        self.extraction_cache: Dict[str, Any] = {}

        # Cache d'embeddings partage entre le croisement de sources (recherche
        # de corroboration) et la selection semantique de chunks - un meme
        # contenu ne fait jamais l'objet de deux appels d'embedding distincts,
        # peu importe l'ordre d'appel des deux fonctions qui en ont besoin.
        self.embedding_cache: Dict[str, list] = {}

        # Historique de tous les chunks extraits, tous sujets confondus, pour
        # pouvoir chercher des corroborations cross-sections (pas seulement
        # au sein d'une section) : {url, domain, content, embedding}
        self.all_extracted_chunks: List[Dict[str, Any]] = []

        # NOUVEAU: Contenu final structuré avec accumulation par section
        self.final_content: Dict[str, Any] = {
            "sections": {},  # {section_name: {"raw_data": [], "content": "", "metadata": {}}}
            "global_metadata": {
                "sources_used": [],
                "extraction_timestamps": [],
                "all_structured_data": []
            }
        }

    def initialize_sections(self, section_names: List[str]):
        """Initialise les sections du rapport."""
        for section_name in section_names:
            self.final_content["sections"][section_name] = {
                "raw_data": [],
                "content": "",
                "metadata": {"data_count": 0, "sources": []}
            }
        logger.info(f"📋 Sections initialisées: {', '.join(section_names)}")

    def add_step(self, tool: str, action: str, input_data: Any, output_data: Any, success: bool):
        """Enregistre une étape."""
        preview_chars = self.config.step_preview_chars
        input_str = str(input_data)[:preview_chars]
        output_str = str(output_data)[:preview_chars] if output_data else ""

        self.steps.append({
            "step_number": len(self.steps) + 1,
            "tool": tool,
            "action": action,
            "success": success,
            "timestamp": datetime.now().isoformat(),
            "input_preview": input_str,
            "output_preview": output_str
        })

        # Mettre à jour taux de succès
        if tool not in self.tool_success_rate:
            self.tool_success_rate[tool] = {"success": 0, "failures": 0}

        if success:
            self.tool_success_rate[tool]["success"] += 1
        else:
            self.tool_success_rate[tool]["failures"] += 1

    def add_dataset(self, name: str, data: List[Dict]):
        """Ajoute un dataset."""
        self.datasets[name] = data
        logger.info(f"💾 Dataset '{name}' ajouté: {len(data)} items")

    def get_all_data(self) -> List[Dict]:
        """Récupère toutes les données."""
        all_data = []
        for dataset in self.datasets.values():
            all_data.extend(dataset)
        return all_data


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

    def _set_search_language(self, ctx: "ExecutionContext", context: Any, lang: str) -> None:
        """Pose la langue de la requete sur son contexte.

        self._search_language reste synchronise pour les lectures des autres
        etapes (synthese) qui ne sont pas encore migrees vers ctx.
        """
        ctx.search_language = lang
        if isinstance(context, dict):
            context['search_language'] = lang
        self._search_language = lang

    async def execute_intelligent_research(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None
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

        ctx = ExecutionContext(query, config=self.config)
        # Contexte vivant de cette requete : execute_with_deadline y lit ce qui
        # est deja redige quand le delai global expire.
        self._active_ctx = ctx
        context = context or {}

        # Contraintes de sources fournies par l'appelant. Elles arrivaient
        # jusqu'ici (sources_exclusions, domains_whitelist sont dans le
        # context depuis l'endpoint) mais n'etaient jamais lues : l'API
        # documentait une capacite que le pipeline n'honorait pas.
        # Lues ICI, avant l'exploration : celle-ci alimente le plan, donc un
        # filtrage plus tardif laisserait des domaines exclus orienter la
        # structure du rapport.
        _ctx = context if isinstance(context, dict) else {}
        self._domains_whitelist = [d.lower().strip() for d in (_ctx.get("domains_whitelist") or []) if d]
        self._domains_exclusions = [d.lower().strip() for d in (_ctx.get("sources_exclusions") or []) if d]
        if self._domains_whitelist:
            logger.info(f"  🔒 Whitelist active : {self._domains_whitelist}")
        if self._domains_exclusions:
            logger.info(f"  🚫 Exclusions actives : {self._domains_exclusions}")

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
        self._set_search_language(ctx, context, _detected)

        # Étape 1.1: Exploration initiale restreinte pour évaluer le champ
        exploration_data = await self._exploratory_phase(query, context, ctx=ctx)
        logger.info(f"  ✓ Exploration: {len(exploration_data.get('sources', []))} sources découvertes")
        logger.info(f"  ✓ Champ évalué: {exploration_data.get('field_assessment', 'N/A')}")

        # Étape 1.2: Planification avec canvas détaillé basé sur exploration
        plan = await self._create_detailed_plan(query, context, exploration_data, ctx=ctx)
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
        self._set_search_language(ctx, context, _lang)
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
                section_data = await self._section_research_phase(
                    query=query, section_name=section_name,
                    section_config=section_config, exploration_data=exploration_data,
                    context=ctx
                )
                extracted_data = await self._section_extraction_phase(
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
                extra = await self._targeted_search(
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

            enriched_data = await self._cross_reference_data(
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
        self._active_ctx = None
        started = datetime.now()
        task = asyncio.ensure_future(self.execute_intelligent_research(query, context))
        try:
            done, _ = await asyncio.wait({task}, timeout=deadline_seconds)
        except asyncio.CancelledError:
            task.cancel()
            raise
        ctx: Optional[ExecutionContext] = self._active_ctx
        if ctx is None:
            ctx = ExecutionContext(query, config=self.config)

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

    def _score_relevance(
        self,
        content: str,
        query: str,
        structured_data: Optional[Dict] = None,
        config: PipelineConfig | None = None,
    ) -> float:
        """
        Score la pertinence d'un chunk de contenu par rapport à la requête.

        Args:
            content: Contenu textuel à scorer
            query: Requête utilisateur
            structured_data: Données structurées extraites (boost le score)

        Returns:
            Score de pertinence (0-100)
        """
        if not content:
            return 0.0

        cfg = config if config is not None else self.config
        score = 0.0
        content_lower = content.lower()
        query_lower = query.lower()

        # 1. Keyword matching (max 40 points)
        query_words = set(query_lower.split())
        query_words = {w for w in query_words if len(w) > 3}  # Mots > 3 chars

        if query_words:
            matched_words = sum(1 for word in query_words if word in content_lower)
            # La normalisation par len(query_words) rendait le score
            # INVERSEMENT PROPORTIONNEL a la precision de la demande : sur une
            # consigne de 350 caracteres (~50 mots significatifs), il fallait
            # qu'un chunk contienne 44% de ces mots pour franchir le seuil de
            # 35. Un chunk valide plafonnait donc a 30 points (20 longueur +
            # 10 chiffres) et etait systematiquement rejete — sections vides.
            # Ca passait inapercu sur des sujets courts type "SearXNG moteur".
            # On sature desormais sur un nombre de correspondances ABSOLU :
            # 8 mots distincts retrouves valent le maximum, quelle que soit la
            # longueur de la consigne.
            keyword_score = min(
                cfg.selection_keyword_max_points,
                (matched_words / cfg.selection_keyword_match_saturation)
                * cfg.selection_keyword_max_points,
            )
            score += keyword_score

        # 2. Structured data boost (max 30 points)
        if structured_data:
            num_count = len(structured_data.get("numerical", []))
            temp_count = len(structured_data.get("temporal", []))
            ent_count = len(structured_data.get("entities", []))

            # Plus de données structurées = plus pertinent
            struct_score = min(
                cfg.selection_structured_max_points,
                (num_count * cfg.selection_structured_numerical_weight)
                + (temp_count * cfg.selection_structured_temporal_weight)
                + (ent_count * cfg.selection_structured_entity_weight),
            )
            score += struct_score

        # 3. Content length quality (max 20 points)
        # Ni trop court (< 500 chars) ni trop long (> 5000 chars)
        content_len = len(content)
        len_min = cfg.selection_length_ideal_min_chars
        len_max = cfg.selection_length_ideal_max_chars
        len_points = cfg.selection_length_max_points
        if len_min <= content_len <= len_max:
            length_score = len_points
        elif content_len < len_min:
            length_score = (content_len / len_min) * len_points
        else:  # > len_max
            length_score = max(
                cfg.selection_length_floor_points,
                len_points - ((content_len - len_max) / cfg.selection_length_decay_chars),
            )
        score += length_score

        # 4. Presence of numbers/dates (max 10 points)
        import re
        numbers = re.findall(r'\d+(?:[.,]\d+)?', content)
        dates = re.findall(r'\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4}', content)

        data_score = min(
            cfg.selection_numbers_max_points,
            len(numbers) + len(dates) * cfg.selection_dates_weight,
        )
        score += data_score

        return min(100.0, score)

    async def _semantic_chunk_selection(
        self,
        all_data: List[Dict],
        query: str,
        max_chars: int | None = None,
        depth: str = "moderate",
        min_score_threshold: float = None,
        context: Optional["ExecutionContext"] = None,
    ) -> List[Dict]:
        """
        Sélectionne intelligemment les chunks les plus pertinents selon la profondeur demandée.

        APPROCHE QUALITATIVE:
        - Au lieu de fixer une longueur arbitraire, on adapte les critères de sélection
        - Profondeur "light": peu de chunks, seulement les plus pertinents
        - Profondeur "deep": plus de chunks, exploration large

        Args:
            all_data: Toutes les données collectées
            query: Requête utilisateur pour contexte
            max_chars: Budget maximum de caractères (selection_default_max_chars si None)
            depth: "light" | "moderate" | "deep" - Profondeur d'analyse attendue
            min_score_threshold: Score minimum pour inclure un chunk (auto si None)
            context: contexte de la requete (cache d'embeddings, diagnostics,
                configuration) ; sans contexte, cache local et aucune note

        Returns:
            Liste triée des chunks les plus pertinents
        """
        cfg = context.config if context is not None else self.config
        if max_chars is None:
            max_chars = cfg.selection_default_max_chars

        # Filtrer les items valides d'abord (necessaire pour le batch d'embeddings)
        valid_items = [
            item for item in all_data
            if isinstance(item, dict) and item.get("content")
        ]

        # Scoring semantique par embeddings, en complement du scoring
        # mots-cles existant : capture les synonymes/reformulations qu'un
        # simple "le mot exact apparait-il dans le texte" ne peut pas voir.
        # Repli silencieux sur le scoring mots-cles seul si l'appel
        # d'embedding echoue (jamais bloquant pour la selection).
        semantic_scores = {}
        embedding_cache = context.embedding_cache if context is not None else {}
        if not self.llm_client.supports_embeddings:
            if valid_items and context is not None:
                context.diagnostics.note(
                    "selection.repli_mots_cles",
                    etape="selection",
                    raison="embeddings non supportes",
                )
        elif valid_items:
            try:
                from app.research.embedding_cache import embed_with_cache
                vectors = await embed_with_cache(
                    self.llm_client,
                    embedding_cache,
                    [query] + [item["content"] for item in valid_items],
                )
                query_vec = vectors[0]
                for item, vec in zip(valid_items, vectors[1:]):
                    semantic_scores[id(item)] = _cosine_similarity(query_vec, vec)
                logger.info(f"  🧠 Scoring sémantique par embeddings : {len(semantic_scores)} chunks scorés")
            except Exception as e:
                logger.warning(f"Embeddings indisponibles pour la selection semantique, repli sur mots-cles seuls: {e}")
                if context is not None:
                    context.diagnostics.note(
                        "selection.repli_mots_cles",
                        etape="selection",
                        raison=f"embeddings en erreur : {e}",
                    )

        scored_items = []

        for item in valid_items:
            content = item.get("content", "")

            # Score mots-cles + donnees structurees (existant)
            structured_data = item.get("structured_data")
            keyword_score = self._score_relevance(content, query, structured_data, config=cfg)

            # Score semantique (0-1 -> 0-100) vs la requete
            sem = semantic_scores.get(id(item))

            # Bonus de corroboration : un chunk confirme par des sources
            # independantes (calcule dans _cross_reference_data, cf.
            # corroboration_count) merite de remonter, meme si son score
            # de pertinence brut est un peu plus faible - c'est le meme
            # principe que le croisement de sources en fact-checking.
            corrob = (
                min(item.get("corroboration_count", 0), cfg.selection_corroboration_cap)
                / float(cfg.selection_corroboration_cap)
            )  # plafonne a selection_corroboration_cap sources

            if sem is not None:
                # Densite factuelle : 4e composante, en BONUS additif plutot
                # qu'en redistribution des poids.
                #
                # Le scoring de pertinence mesure si un fragment PARLE DU BON
                # SUJET ; il ne mesure pas s'il contient des faits
                # VERIFIABLES. Un paragraphe d'introduction qui reformule le
                # sujet score tres bien (mots-cles + similarite) sans porter
                # aucune donnee citable ; un tableau statistique score moins
                # bien alors qu'il porte l'information qu'on veut citer.
                # C'est une cause plausible du taux de citations validees de
                # 61% : on retenait de la prose generale, et le modele
                # completait les chiffres manquants de memoire.
                #
                # EN BONUS et non en poids : un contenu explicatif legitime
                # (ex. le fonctionnement du borrow checker de Rust) ne
                # contient aucun chiffre et ne doit pas etre penalise. La
                # densite fait REMONTER les fragments citables, elle
                # n'ecarte personne.
                # Plafonne a 10 points sur 100 : assez pour departager deux
                # fragments de pertinence voisine, trop peu pour faire
                # remonter un fragment hors sujet.
                fact_bonus = 0.0
                try:
                    from app.utils.factual_density import factual_density
                    fact_bonus = (
                        factual_density(item.get("content", ""))["score"]
                        * cfg.selection_fact_bonus_max
                    )
                except Exception as e:
                    if context is not None:
                        context.diagnostics.note(
                            "selection.densite_factuelle_indisponible", erreur=str(e)
                        )

                score = (
                    (keyword_score * cfg.selection_weight_keyword)
                    + (sem * 100 * cfg.selection_weight_semantic)
                    + (corrob * 100 * cfg.selection_weight_corroboration)
                    + fact_bonus
                )
            else:
                score = (
                    (keyword_score * cfg.selection_weight_keyword_only)
                    + (corrob * 100 * cfg.selection_weight_corroboration)
                )

            scored_items.append({
                "score": score,
                "item": item,
                "content_length": len(content)
            })

        # Trier par score décroissant
        scored_items.sort(key=lambda x: x["score"], reverse=True)

        # Adapter seuil et stratégie selon profondeur
        #
        # Ces cibles ont ete calibrees quand un "chunk" etait un DOCUMENT
        # entier tronque a 3 000 caracteres. Depuis le decoupage, un chunk
        # est un fragment de ~2 000 caracteres : il en faut plusieurs pour
        # couvrir un meme document, et les anciennes valeurs plafonnaient
        # mecaniquement la matiere (4 chunks x 2 000 = 4 000 chars, quel que
        # soit le budget max_chars accorde).
        # Doublees pour que max_chars redevienne la contrainte effective.
        if min_score_threshold is None:
            if depth == "light":
                min_score_threshold = cfg.selection_min_score_light
                max_chunks_target = cfg.selection_max_chunks_light
            elif depth == "deep":
                min_score_threshold = cfg.selection_min_score_deep
                max_chunks_target = cfg.selection_max_chunks_deep
            else:  # moderate
                min_score_threshold = cfg.selection_min_score_moderate
                max_chunks_target = cfg.selection_max_chunks_moderate
        else:
            max_chunks_target = cfg.selection_max_chunks_explicit_threshold

        # Sélectionner les meilleurs chunks
        selected = []
        total_chars = 0
        chunks_selected = 0
        max_per_source = context.config.max_chunks_per_source if context is not None else 0  # 0 sans contexte : pas de plafond
        per_source: Dict[str, int] = {}

        for scored in scored_items:
            # Arrêter si on a atteint le nombre cible ET que le score diminue trop
            if chunks_selected >= max_chunks_target and scored["score"] < min_score_threshold:
                break

            # Filtrer les chunks avec score trop faible
            if scored["score"] < min_score_threshold:
                continue

            item = scored["item"]
            content_len = scored["content_length"]

            # Plafond par source (0 = aucun) : un seul document long ne doit
            # pas remplir tout le budget.
            if max_per_source > 0:
                source_key = str(item.get("source", ""))
                if per_source.get(source_key, 0) >= max_per_source:
                    continue
                per_source[source_key] = per_source.get(source_key, 0) + 1

            # Vérifier si on peut ajouter ce chunk
            if total_chars + content_len > max_chars:
                # Pour profondeur deep, essayer de prendre au moins un extrait
                if depth == "deep":
                    remaining = max_chars - total_chars
                    if remaining > cfg.selection_deep_min_remaining_chars:  # budget restant suffisant
                        # Truncate ce chunk pour fit
                        item_copy = item.copy()
                        item_copy["content"] = item["content"][:remaining]
                        selected.append(item_copy)
                        total_chars += remaining
                        chunks_selected += 1
                break

            selected.append(item)
            total_chars += content_len
            chunks_selected += 1

        # DEGRADATION GRACIEUSE : si AUCUN chunk ne franchit le seuil alors que
        # des chunks valides existent, on retient quand meme les meilleurs.
        # Rediger une section sur des sources moyennement pertinentes vaut
        # toujours mieux que rendre une section vide — et un rapport vide
        # sortait jusqu'ici avec success=true. Le seuil sert a classer, il ne
        # doit pas pouvoir aneantir la production.
        if not selected and scored_items:
            fallback_n = min(cfg.selection_fallback_chunks, len(scored_items))
            for scored in scored_items[:fallback_n]:
                selected.append(scored["item"])
                total_chars += scored["content_length"]
            logger.warning(
                f"  🛟 Repli: {fallback_n} chunk(s) retenus malgré un score sous "
                f"le seuil — une section vide serait pire qu'une section imparfaite"
            )

        # Le log precedent calculait la moyenne sur scored_items[:len(selected)] :
        # quand rien n'est selectionne, la tranche est vide et il affichait
        # "score moyen: 0.0" — laissant croire que les chunks scoraient zero,
        # alors qu'ils scoraient simplement sous le seuil. Diagnostic fausse.
        # On affiche desormais le meilleur score REELLEMENT obtenu et le seuil
        # applique : c'est ce couple qui explique un rejet.
        best_score = max((s["score"] for s in scored_items), default=0.0)
        mean_selected = (
            sum(s["score"] for s in scored_items[:len(selected)]) / len(selected)
            if selected else 0.0
        )
        logger.info(
            f"  📊 Sélection intelligente: {len(selected)}/{len(all_data)} chunks, "
            f"{total_chars:,} chars (retenus: {mean_selected:.1f}/100, "
            f"meilleur disponible: {best_score:.1f}, seuil: {min_score_threshold:.0f})"
        )
        if not selected and scored_items:
            logger.warning(
                f"  ⚠️  Aucun chunk retenu alors que {len(scored_items)} étaient "
                f"disponibles (meilleur: {best_score:.1f} < seuil {min_score_threshold:.0f})"
            )

        return selected

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
                enrichment_data = await self._targeted_search(query, section_title, missing, ctx)

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

    async def _targeted_search(
        self,
        query: str,
        section_title: str,
        missing: List[str],
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        """Recherche ciblée pour combler les manques d'une section."""

        # Construire une requête spécifique pour les manques
        search_query = self._build_search_query(
            query=query, section_name=section_title, key_questions=missing
        )

        logger.info(f"    🔎 Recherche ciblée: {search_query}")

        cfg = ctx.config
        try:
            # Meme cascade de moteurs que la recherche de section.
            _engines = await self._cascade_engines(search_query, ctx=ctx)
            results = await self.search_client.search(
                search_query,
                max_results=self._search_pool_size(cfg.targeted_search_pool_size),
                language=ctx.search_language if cfg.search_language_from_query else "all",
                engines=",".join(_engines) if _engines else None,
            )

            # Meme filtrage que la recherche de section : sans cela, le
            # complement pre-redaction reintroduisait des domaines exclus.
            if results:
                _as_dicts = [{"url": r.url, "title": r.title} for r in results]
                _kept = {d["url"] for d in self._filter_by_constraints(_as_dicts)}
                results = [r for r in results if r.url in _kept]

            if not results:
                return None

            # Extraire les 2 premiers résultats les plus pertinents
            urls_to_extract = [r.url for r in results[:cfg.targeted_search_max_urls]]

            extracted_data = []
            for url in urls_to_extract:
                try:
                    extract_result = await self.extractor_manager.extract(
                        url=url,
                        llm_client=self.llm_client,
                        options=ExtractionOptions(
                            timeout=cfg.targeted_extract_timeout,
                            use_agent=False,
                            headless=True,
                            rss_tier_root_only=cfg.rss_tier_root_only,
                            single_llm_pass_enabled=cfg.single_llm_pass_enabled,
                        )
                    )

                    if extract_result.success and extract_result.content:
                        # Extraction données structurées (enrichissement ciblé) :
                        # son resultat ne sert qu'a un bonus de score.
                        structured = None
                        if not cfg.targeted_search_skip_structured:
                            structured = await self.data_extractor.extract_structured_data(
                                content=extract_result.content[:cfg.targeted_structured_input_chars],
                                source_url=url,
                                topic_context=f"{query} - {section_title}"
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
                    ctx.diagnostics.note("collecte.recherche_ciblee_extraction_echec", url=url, error=str(e))
                    continue

            return {
                "query": search_query,
                "sources": extracted_data,
                "missing": missing
            }

        except Exception as e:
            logger.error(f"Erreur recherche ciblée: {e}")
            ctx.diagnostics.note("collecte.recherche_ciblee_echec", error=str(e))
            return None

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

        lang = getattr(ctx, "search_language", None) or getattr(self, "_search_language", "all")
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

    async def _exploratory_phase(
        self,
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

        cfg = ctx.config if ctx is not None else self.config
        max_results = cfg.exploration_max_results  # Restreint pour exploration rapide
        search_query = query
        engines_param: Optional[str] = cfg.exploration_engines
        if cfg.exploration_uses_plan_query:
            # La consigne entiere n'est pas une requete : meme reduction que
            # pour les sections, et moteurs de la cascade plutot que les deux
            # moteurs scrapes les plus fragiles.
            search_query = self._build_search_query(query=query, section_name="")
            _engines = await self._cascade_engines(search_query, ctx=ctx)
            engines_param = ",".join(_engines) if _engines else None
        language = "all"
        if cfg.search_language_from_query:
            if ctx is not None:
                language = ctx.search_language
            elif isinstance(context, dict):
                language = context.get("search_language", "all")

        try:
            search_results = await self.search_client.search(
                query=search_query,
                max_results=self._search_pool_size(max_results),
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
                        "snippet": r.content[:cfg.exploration_snippet_chars] if r.content else ""
                    }
                    for r in search_results
                ]
                # L'exploration alimente le PLAN : sans filtrage ici, des
                # domaines exclus orienteraient la structure du rapport avant
                # meme la phase de recherche par section. Le filtre precede la
                # troncature, sinon des resultats exclus occupent des places.
                urls_found = self._filter_by_constraints(urls_found)[:max_results]

            # Analyser les snippets pour identifier sous-thèmes
            topics_found = self._extract_topics_from_snippets(urls_found, config=cfg)

            # Évaluer la richesse du champ
            field_assessment = (
                "rich" if len(urls_found) >= cfg.exploration_rich_min_sources
                else ("moderate" if len(urls_found) >= cfg.exploration_moderate_min_sources else "limited")
            )

            logger.info(f"     → {len(urls_found)} sources, champ: {field_assessment}")
            logger.info(f"     → Sous-thèmes: {', '.join(topics_found[:3])}")

            return {
                "sources": urls_found,
                "field_assessment": field_assessment,
                "topics_found": topics_found,
                "data_preview": {
                    "total_sources": len(urls_found),
                    "snippets": [u["snippet"] for u in urls_found[:cfg.exploration_preview_snippets]]
                }
            }

        except Exception as e:
            logger.error(f"  ❌ Erreur exploration: {e}")
            if ctx is not None:
                ctx.diagnostics.note("collecte.exploration_echec", error=str(e))
            return {
                "sources": [],
                "field_assessment": "unknown",
                "topics_found": [],
                "data_preview": {}
            }

    _SEARCH_OPERATORS = ("filetype:", "site:", "inurl:", "intitle:", '"')

    def _search_pool_size(self, wanted: int) -> int:
        """Nombre de resultats a demander au moteur pour en garder `wanted`.

        Avec des contraintes de domaines, le filtre s'applique APRES la
        recherche : demander exactement `wanted` resultats laisserait des
        domaines exclus occuper les places avant le filtre (C35).
        """
        if getattr(self, "_domains_whitelist", None) or getattr(self, "_domains_exclusions", None):
            return wanted * self.config.search_pool_multiplier
        return wanted

    async def _cascade_engines(
        self,
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

        cfg = ctx.config if ctx is not None else self.config
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
            selected_engines = engine_catalog.cascade(per_axis=cfg.cascade_engines_per_axis, axes=[axis])
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
        if any(op in search_query for op in self._SEARCH_OPERATORS):
            logger.info(
                f"       🔍 requête à opérateur — moteurs avant exemption : "
                f"{selected_engines or '(aucun)'}"
            )
            return []
        return selected_engines

    def _extract_topics_from_snippets(
        self, urls_data: List[Dict], config: PipelineConfig | None = None
    ) -> List[str]:
        """Extrait les sous-thèmes des snippets de recherche."""
        # Analyse simple par mots-clés fréquents
        from collections import Counter
        import re

        words = []
        for url_data in urls_data:
            text = f"{url_data.get('title', '')} {url_data.get('snippet', '')}"
            # Extraire mots de 4+ lettres
            words.extend(re.findall(r'\b[a-zA-Z]{4,}\b', text.lower()))

        # Mots les plus fréquents (hors stopwords basiques)
        stopwords = {'this', 'that', 'with', 'from', 'have', 'more', 'will', 'your', 'about', 'what', 'when', 'where', 'which', 'their', 'there'}
        filtered = [w for w in words if w not in stopwords]
        cfg = config if config is not None else self.config
        common = Counter(filtered).most_common(cfg.exploration_topics_max)

        return [word for word, count in common if count >= cfg.exploration_topic_min_count]

    # Titres de sections qui decrivent le TRAVAIL et non le sujet. Observe
    # sur la tache 55 du benchmark : le modele avait ajoute "Structure Finale
    # du Rapport", "Strategie de Recherche Detaillee" et "Criteres de
    # Validation" comme sections a part entiere - 3 sections sur 11 de notes
    # de travail livrees au lecteur. C'est la tache qui a obtenu le plus
    # mauvais score (0.306) malgre le PLUS de mots (4905) et de references
    # (29) : ni le volume ni le sourcage ne compensent du hors-sujet.
    _META_SECTION_PATTERNS = (
        "structure du rapport", "structure finale", "plan du rapport",
        "plan de travail", "strategie de recherche", "stratégie de recherche",
        "methodologie de recherche", "méthodologie de recherche",
        "criteres de validation", "critères de validation",
        "sources consultees", "sources consultées", "demarche", "démarche",
    )

    def _filter_by_constraints(self, sources: List[Dict]) -> List[Dict]:
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
        wl = getattr(self, "_domains_whitelist", None) or []
        ex = getattr(self, "_domains_exclusions", None) or []
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

    @classmethod
    def _is_meta_section(cls, name: str) -> bool:
        """
        Filet de securite derriere la regle du prompt : une consigne ne
        suffit pas toujours (verifie sur la langue, ou le modele ignorait
        une instruction explicite avec exemples). Ici le cout d'un faux
        positif est faible - un titre legitime rejete - alors qu'un faux
        negatif livre des notes de travail dans un rapport publie.
        """
        low = (name or "").lower()
        return any(p in low for p in cls._META_SECTION_PATTERNS)

    async def _create_detailed_plan(
        self,
        query: str,
        context: Dict,
        exploration_data: Dict,
        ctx: Optional["ExecutionContext"] = None
    ) -> Dict[str, Any]:
        """
        PHASE 1.2: Planification détaillée avec canvas complet.

        ctx (optionnel) porte les diagnostics de la requete : sans lui, les
        repli et troncatures du plan ne sont pas notes (appel historique).

        Utilise les données d'exploration pour créer un plan précis incluant:
        - Structure complète (sections, sous-sections)
        - Enchaînements narratifs
        - Objectifs par section
        - Profondeur adaptée
        """
        logger.info("  📋 Création du plan détaillé avec canvas")

        cfg = ctx.config if ctx is not None else self.config

        # Construire le contexte enrichi
        topics_found = exploration_data.get("topics_found", [])
        field_richness = exploration_data.get("field_assessment", "moderate")
        sources_count = len(exploration_data.get("sources", []))

        prompt = f"""Tu es un planificateur expert. Crée un plan détaillé pour répondre à cette requête.

REQUÊTE: "{query}"

DONNÉES D'EXPLORATION:
- Sources découvertes: {sources_count}
- Richesse du champ: {field_richness}
- Sous-thèmes identifiés: {', '.join(topics_found[:cfg.plan_topics_in_prompt])}
- Aperçu: {exploration_data.get('data_preview', {}).get('snippets', [])[0][:cfg.plan_snippet_chars] if exploration_data.get('data_preview', {}).get('snippets') else 'N/A'}

CONTEXTE UTILISATEUR:
{json.dumps(context, indent=2) if context else "Aucun"}

Ta mission: Créer un CANVAS DÉTAILLÉ avec structure complète et enchaînements.

ÉTAPE 1: ANALYSE MULTI-CRITÈRES
Évalue la complexité nécessaire (1-5 pour chaque critère):

1. **Complexité du sujet**: Simple (1) → Multidimensionnel (5)
2. **Spécificité demandée**: Large (1) → Très précis (5)
3. **Format**: Résumé (1) → Étude approfondie (5)
4. **Profondeur temporelle**: Point dans le temps (1) → Évolution + projection (5)
5. **Interconnexions**: Sujet isolé (1) → Analyse systémique (5)

Score moyen = profondeur globale

ÉTAPE 2: DÉTERMINER AMPLEUR ET STRUCTURE

Selon score_profondeur:
- 1.0-2.0: Rapport CONCIS (1-2 sections, 500-1000 mots)
- 2.1-3.0: Rapport STANDARD (2-3 sections, 1000-1500 mots)
- 3.1-4.0: Rapport DÉTAILLÉ (3-5 sections, 1500-2500 mots)
- 4.1-5.0: Étude APPROFONDIE (4-7 sections, 2500-4000 mots)

ÉTAPE 3: DÉFINIR ENCHAÎNEMENTS NARRATIFS

Pour chaque transition entre sections, définis:
- Lien logique (pourquoi cette section après la précédente)
- Type de transition (cause-effet, chronologique, zoom-in, comparaison, etc.)

Retourne JSON:
{{
  "complexity_analysis": {{
    "topic_complexity": 1-5,
    "specificity": 1-5,
    "format_depth": 1-5,
    "temporal_depth": 1-5,
    "interconnections": 1-5,
    "overall_score": moyenne,
    "target_length": "concis|standard|détaillé|approfondi",
    "estimated_words": nombre_total,
    "justification": "Pourquoi ce niveau"
  }},

  "sections": ["Titre Section 1", "Titre Section 2", ...],

  "section_targets": {{
    "Titre Section 1": {{
      "words_target": nombre,
      "depth": "light|moderate|deep",
      "objectives": ["objectif 1", "objectif 2"],
      "key_questions": ["question à explorer 1", "question 2"],
      "search_terms": ["requête moteur courte", "autre angle"],
      "source_axis": "generaliste|academique|actualite|technique"
    }},
    ...
  }},

  "narrative_flow": [
    {{
      "from_section": "Section 1",
      "to_section": "Section 2",
      "transition_type": "zoom-in|cause-effect|chronological|comparison",
      "rationale": "Pourquoi cet enchaînement"
    }},
    ...
  ],

  "search_strategy": {{
    "total_sources_needed": nombre (adapté au score),
    "sources_per_section": nombre,
    "search_depth": "quick|standard|exhaustive"
  }}
}}

RÈGLE — SECTIONS INTERDITES:
Les sections décrivent le SUJET, jamais ton travail. N'invente JAMAIS de
section du type "Structure du Rapport", "Stratégie de Recherche",
"Méthodologie", "Critères de Validation", "Plan de Travail" : ce sont des
notes de travail, pas une réponse à la demande. Un lecteur veut le contenu,
pas la description de comment tu comptes le produire.
Chaque titre de section doit pouvoir figurer dans la table des matières
d'un rapport publié.

RÈGLE — OPÉRATEURS DE RECHERCHE (search_terms):
Tu peux utiliser des opérateurs quand ils ciblent vraiment mieux :
  filetype:pdf   → rapports officiels, études, documents institutionnels
  site:domaine   → source précise et identifiée (ex: site:europa.eu,
                   site:who.int, site:insee.fr)
  "expression"   → terminologie exacte, nom de méthode, citation
Règles d'usage :
- Au maximum UNE requête sur opérateur par section, jamais toutes. Un
  opérateur restreint : si la source ciblée n'a rien, la requête rend zéro.
  Les autres requêtes de la section restent en mots-clés simples.
- N'utilise site: que si tu es sûr du domaine. Un domaine inventé ne rend
  rien et fait perdre la recherche.
- Pas d'opérateur sur les sections d'introduction ou de synthèse : elles ont
  besoin de vue d'ensemble, pas de ciblage.

RÈGLE — source_axis (par section):
Choisis l'axe de sources adapté au CONTENU de la section :
  "academique"  -> études, données chiffrées, publications scientifiques,
                   statistiques, projections (Google Scholar, PubMed, arXiv)
  "actualite"   -> événements récents, décisions, tendances de marché
  "technique"   -> logiciel, code, outils, documentation technique
  "generaliste" -> contexte général, définitions, vue d'ensemble
Une même étude mélange souvent les axes : une section "projections
démographiques" est "academique", une section "réactions du marché" est
"actualite". Choisis par section, pas une fois pour tout le rapport.

EXEMPLES:

Pour "c'est quoi Rust":
{{
  "complexity_analysis": {{"overall_score": 1.2, "target_length": "concis", "estimated_words": 600}},
  "sections": ["Définition", "Principaux usages"],
  "section_targets": {{
    "Définition": {{"words_target": 300, "depth": "light", "objectives": ["Expliquer Rust simplement"], "key_questions": ["Qu'est-ce que Rust?"]}},
    "Principaux usages": {{"words_target": 300, "depth": "light", "objectives": ["Lister cas d'usage"], "key_questions": ["Pour quoi utiliser Rust?"]}}
  }},
  "narrative_flow": [{{"from_section": "Définition", "to_section": "Principaux usages", "transition_type": "zoom-in", "rationale": "Après avoir défini, montrer applications concrètes"}}],
  "search_strategy": {{"total_sources_needed": 5, "sources_per_section": 2, "search_depth": "quick"}}
}}

Pour "Écosystème Rust 2024, adoption, roadmap":
{{
  "complexity_analysis": {{"overall_score": 4.6, "target_length": "approfondi", "estimated_words": 3500}},
  "sections": ["Vue d'ensemble", "Écosystème technique", "Adoption entreprise", "Cas d'usage", "Roadmap", "Défis"],
  "section_targets": {{
    "Vue d'ensemble": {{"words_target": 400, "depth": "moderate", "objectives": ["Situer Rust en 2024"], "key_questions": ["Quelle position actuelle?", "Pourquoi pertinent?"]}},
    "Écosystème technique": {{"words_target": 700, "depth": "deep", "objectives": ["Analyser tooling, libs, communauté"], "key_questions": ["Quels outils?", "Maturité?"]}},
    ...
  }},
  "narrative_flow": [
    {{"from_section": "Vue d'ensemble", "to_section": "Écosystème technique", "transition_type": "zoom-in", "rationale": "Après contexte global, détailler aspects techniques"}},
    {{"from_section": "Écosystème technique", "to_section": "Adoption entreprise", "transition_type": "cause-effect", "rationale": "Maturité technique → adoption business"}},
    ...
  ],
  "search_strategy": {{"total_sources_needed": 15, "sources_per_section": 3, "search_depth": "exhaustive"}}
}}

IMPORTANT: Adapte la structure aux sous-thèmes découverts: {', '.join(topics_found[:cfg.plan_topics_in_prompt])}
"""

        def _note(event: str, **details: Any) -> None:
            if ctx is not None:
                ctx.diagnostics.note(event, **details)

        # Sortie structuree forcee : plus de clefs dynamiques (l'ancien format
        # section_targets = {nom_section: {...}} etait incompatible avec un
        # JSON Schema strict). "sections" devient une liste d'objets, chacun
        # avec son nom en attribut plutot qu'en clef de dictionnaire - le
        # nombre de sections reste totalement libre pour le LLM (1 a 10),
        # seule la FORME de chaque section est garantie.
        plan_schema = {
            "type": "object",
            "properties": {
                "complexity_analysis": {
                    "type": "object",
                    "properties": {
                        "overall_score": {"type": "number", "minimum": 1.0, "maximum": 5.0},
                        "target_length": {"type": "string", "enum": ["concis", "standard", "détaillé", "approfondi"]},
                        "estimated_words": {"type": "integer", "minimum": 100},
                        "justification": {"type": "string"}
                    },
                    "required": ["overall_score", "target_length", "estimated_words", "justification"],
                    "additionalProperties": False
                },
                "sections": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": cfg.plan_max_sections,
                    # Le modele produisait 4-5 sections quelle que soit
                    # l'ampleur de la demande. Mesure sur 20 taches :
                    # correlation -0.29 entre longueur de consigne et score —
                    # plus la demande est detaillee, moins le pipeline s'en
                    # sort. Cas extreme : une consigne enumerant 6 aspects sur
                    # une dizaine d'entreprises a recu 3 sections et 1 770
                    # mots, le plus court des 20 rapports, et le plus mauvais
                    # score (0.154).
                    # La contrainte va dans la description : c'est le seul
                    # endroit ou une exigence porte sur ce modele (verifie
                    # trois fois : langue, source_axis, operateurs).
                    "description": (
                        "Sections du rapport. COUVRE TOUS LES ASPECTS "
                        "EXPLICITEMENT DEMANDES dans la consigne : releve-les "
                        "un par un et donne a chacun sa section. Une consigne "
                        "qui enumere six aspects appelle au moins six "
                        "sections, pas quatre. Une consigne courte et generale "
                        "en appelle trois ou quatre. Ne fusionne pas deux "
                        "aspects distincts pour tenir dans un format."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "words_target": {
                                "type": "integer",
                                "minimum": cfg.plan_words_target_min,
                                "maximum": cfg.plan_words_target_max,
                            },
                            "depth": {"type": "string", "enum": ["light", "moderate", "deep"]},
                            "objectives": {"type": "array", "items": {"type": "string"}},
                            "key_questions": {"type": "array", "items": {"type": "string"}},
                            # Requetes de recherche formulees PAR LE LLM.
                            # La v1 (aujourd'hui morte) faisait deja generer
                            # input.query par etape ; la refonte v2 l'a perdu et
                            # l'a remplace par la concatenation brute
                            # f"{query} {section_name}", qui produit des requetes
                            # de 400+ caracteres sur consigne realiste — donc
                            # zero source, donc rapport vide. Le LLM sait
                            # formuler une requete ; l'extraction de mots-cles
                            # n'est qu'un repli.
                            "source_axis": {
                                "type": "string",
                                "enum": ["generaliste", "academique", "actualite", "technique"],
                                "description": "Axe de sources adapte au contenu de CETTE section"
                            },
                            "search_terms": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": cfg.plan_max_search_terms,
                                # La consigne en prose ne suffit pas : le
                                # modele ignorait la regle sur les operateurs,
                                # comme il avait ignore celle sur la langue.
                                # La description du schema est le seul endroit
                                # ou une contrainte de FORMAT porte.
                                "description": (
                                    "Requetes de moteur, 1 a 3. Formule-en UNE "
                                    "avec un operateur quand le sujet s'y prete : "
                                    "filetype:pdf pour des rapports ou etudes, "
                                    "site:domaine pour une source institutionnelle "
                                    "sure (site:europa.eu, site:who.int, site:oecd.org), "
                                    "\"expression exacte\" pour une terminologie precise. "
                                    "Les autres restent en mots-cles simples. "
                                    "Pas d'operateur sur les sections d'introduction "
                                    "ou de synthese."
                                ),
                                "items": {"type": "string", "maxLength": cfg.plan_search_term_max_chars}
                            }
                        },
                        # source_axis en required : sans cela le modele omet
                        # simplement le champ (constate en run reel - le plan
                        # structure etait bien genere, 6 sections, mais aucun
                        # axe declare). Un champ optionnel dans un schema
                        # JSON n'est pas une suggestion polie : c'est une
                        # permission de ne pas repondre.
                        "required": ["name", "words_target", "depth", "objectives", "key_questions", "search_terms", "source_axis"],
                        "additionalProperties": False
                    }
                },
                "narrative_flow": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "from_section": {"type": "string"},
                            "to_section": {"type": "string"},
                            "transition_type": {"type": "string", "enum": ["zoom-in", "cause-effect", "chronological", "comparison"]},
                            "rationale": {"type": "string"}
                        },
                        "required": ["from_section", "to_section", "transition_type", "rationale"],
                        "additionalProperties": False
                    }
                },
                "search_strategy": {
                    "type": "object",
                    "properties": {
                        "total_sources_needed": {"type": "integer", "minimum": 1},
                        "sources_per_section": {"type": "integer", "minimum": 1},
                        "search_depth": {"type": "string", "enum": ["quick", "standard", "exhaustive"]},
                        "language": {
                            "type": "string",
                            "description": "Code langue ISO 639-1 de la consigne (fr, en, es...) ou 'all' si indifferent"
                        }
                    },
                    "required": ["total_sources_needed", "sources_per_section", "search_depth", "language"],
                    "additionalProperties": False
                }
            },
            "required": ["complexity_analysis", "sections", "narrative_flow", "search_strategy"],
            "additionalProperties": False
        }

        FALLBACK_PLAN = {
            "complexity_analysis": {"overall_score": 3.0, "target_length": "standard", "estimated_words": 1200},
            "sections": ["Introduction", "Analyse", "Conclusion"],
            "section_targets": {
                "Introduction": {"words_target": cfg.plan_fallback_intro_words, "depth": "moderate", "objectives": ["Introduire le sujet"], "key_questions": []},
                "Analyse": {"words_target": cfg.plan_fallback_analysis_words, "depth": "moderate", "objectives": ["Analyser le sujet"], "key_questions": []},
                "Conclusion": {"words_target": cfg.plan_fallback_conclusion_words, "depth": "light", "objectives": ["Conclure"], "key_questions": []}
            },
            "narrative_flow": [],
            "search_strategy": {
                "total_sources_needed": cfg.plan_fallback_total_sources,
                "sources_per_section": cfg.plan_fallback_sources_per_section,
                "search_depth": "standard",
            }
        }

        if self.config.plan_prompt_v2:
            prompt = self._build_plan_prompt_v2(query, context, exploration_data, cfg)
            if not self.llm_client.supports_structured:
                # Sans sortie structuree, le schema devient la seule description
                # du format attendu.
                prompt += (
                    "\nReponds uniquement par un objet JSON conforme a ce schema :\n"
                    + json.dumps(plan_schema, ensure_ascii=False)
                )

        fallback_reason = ""
        fallback_error = ""
        try:
            if self.llm_client.supports_structured:
                structured = await self.llm_client.generate_structured(
                    [{"role": "user", "content": prompt}],
                    schema=plan_schema, schema_name="research_plan",
                    # 6000 et non 3000 : le plan porte desormais un champ de
                    # plus par section (source_axis). A 3000, la reponse etait
                    # tronquee en plein JSON ("Unterminated string at char
                    # 10963"), tout le plan structure etait rejete et le
                    # pipeline retombait sur le plan de secours — sans axes,
                    # sans search_terms. Un depassement silencieux de ce genre
                    # annule tout le travail de structuration en amont.
                    max_tokens=cfg.plan_max_tokens_structured, temperature=cfg.plan_temperature
                )
                # Conversion vers le format interne historique (sections=liste
                # de strings, section_targets=dict) attendu par tout le reste
                # du pipeline (Phase 2, _final_assembly, etc.) - zero
                # changement requis ailleurs dans le fichier.
                plan = {
                    "complexity_analysis": structured["complexity_analysis"],
                    "sections": [s["name"] for s in structured["sections"]
                                 if not self._is_meta_section(s["name"])],
                    "section_targets": {
                        s["name"]: {
                            "words_target": s["words_target"],
                            "depth": s["depth"],
                            "objectives": s["objectives"],
                            "key_questions": s["key_questions"],
                            # Transmis jusqu'a _section_research_phase, sinon le
                            # champ serait genere par le LLM puis jamais lu —
                            # exactement le defaut de cablage rencontre ailleurs
                            # (sources_per_section, categories/engines...).
                            "search_terms": s.get("search_terms", []),
                            "source_axis": s.get("source_axis")
                        } for s in structured["sections"]
                        if not self._is_meta_section(s["name"])
                    },
                    "narrative_flow": structured["narrative_flow"],
                    "search_strategy": structured["search_strategy"]
                }
                if plan["sections"]:
                    logger.info(f"     → Plan créé (sortie structurée): {len(plan['sections'])} sections")
                    return plan
                # Toutes les sections ont ete filtrees comme meta : un plan
                # vide donnerait un rapport vide, on prefere le repli.
                fallback_reason = "sections_filtrees"
            else:
                # Repli non-structure pour un client sans generate_structured (ex: Albert)
                response = await self.llm_client.generate(
                    [{"role": "user", "content": prompt}],
                    max_tokens=cfg.plan_max_tokens_unstructured,
                    temperature=cfg.plan_temperature,
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
                                plan = json.loads(response[start_idx:i + 1])
                                logger.info(f"     → Plan créé (parsing manuel): {len(plan.get('sections', []))} sections")
                                return plan
                fallback_reason = "json_introuvable"

        except Exception as e:
            from app.core.llm.base import LLMTruncatedError
            logger.error(f"  ❌ Erreur planification: {e}")
            fallback_reason = "exception"
            fallback_error = type(e).__name__
            if isinstance(e, LLMTruncatedError):
                # Cause historique d'un repli silencieux (voir max_tokens).
                _note("plan.sortie_tronquee", error=str(e))

        _note("plan.repli", cause=fallback_reason, error_type=fallback_error)
        logger.warning(f"  ⚠️  Plan de repli generique utilise ({fallback_reason})")
        import copy
        fallback = copy.deepcopy(FALLBACK_PLAN)
        # Marqueur lu par l'appelant pour rendre le rapport degraded.
        fallback["fallback"] = True
        fallback["fallback_reason"] = (
            f"{fallback_reason}:{fallback_error}" if fallback_error else fallback_reason
        )
        return fallback

    @staticmethod
    def _build_plan_prompt_v2(
        query: str,
        context: dict,
        exploration_data: dict,
        config: PipelineConfig | None = None,
    ) -> str:
        """
        Prompt de planification v2 (drapeau plan_prompt_v2).

        Les contraintes de forme (couverture des aspects demandes, operateurs,
        source_axis, nombre de sections) vivent dans le schema de sortie et
        n'y sont pas repetees. Le prompt ne porte ni format JSON ni exemples.
        Il ne recoit que les champs utiles du contexte, pas le dictionnaire
        entier.
        """
        cfg = config if config is not None else PipelineConfig()
        ctx_in = context if isinstance(context, dict) else {}
        topics = exploration_data.get("topics_found", [])
        snippets = (exploration_data.get("data_preview") or {}).get("snippets") or []
        lines = [
            (
                "Tu es un planificateur de rapports de recherche. Construis le plan "
                "du rapport qui repond a la consigne ci-dessous."
            ),
            "",
            f'CONSIGNE: "{query}"',
        ]
        objectives = ctx_in.get("objectives")
        if objectives:
            lines.append("OBJECTIFS: " + "; ".join(str(o) for o in objectives))
        imposed = ctx_in.get("output_sections")
        if imposed:
            lines.append(
                "SECTIONS IMPOSEES (a reprendre telles quelles): "
                + "; ".join(str(x) for x in imposed)
            )
        for key, label in (
            ("sources_required", "SOURCES OBLIGATOIRES"),
            ("sources_suggested", "SOURCES SUGGEREES"),
            ("sources_exclusions", "DOMAINES EXCLUS"),
            ("domains_whitelist", "DOMAINES AUTORISES UNIQUEMENT"),
        ):
            value = ctx_in.get(key)
            if value:
                lines.append(f"{label}: " + ", ".join(str(v) for v in value))
        lines += [
            "",
            "EXPLORATION:",
            f"- Sources decouvertes: {len(exploration_data.get('sources', []))}",
            f"- Richesse du champ: {exploration_data.get('field_assessment', 'moderate')}",
            f"- Sous-themes identifies: {', '.join(str(t) for t in topics[:cfg.plan_topics_in_prompt]) or 'N/A'}",
        ]
        if snippets:
            lines.append(f"- Apercu: {str(snippets[0])[:cfg.plan_snippet_chars]}")
        lines += [
            "",
            "Regles:",
            (
                "- Les sections decrivent le SUJET, jamais ton travail : pas de "
                "section du type Structure du rapport, Strategie de recherche, "
                "Methodologie, Criteres de validation ou Plan de travail. Chaque "
                "titre doit pouvoir figurer dans la table des matieres d'un "
                "rapport publie."
            ),
            (
                "- Adapte l'ampleur du plan a la complexite de la consigne et aux "
                "sous-themes decouverts."
            ),
            "- Definis l'enchainement logique entre sections consecutives.",
        ]
        return "\n".join(lines)

    # Mots vides : retires lors de la reduction d'une consigne longue en
    # requete de recherche. FR + EN car les consignes arrivent dans les deux
    # langues et le plan peut etre genere dans l'une ou l'autre.
    _STOPWORDS = {
        "le", "la", "les", "un", "une", "des", "du", "de", "et", "ou", "a", "au",
        "aux", "en", "dans", "sur", "pour", "par", "avec", "sans", "sous", "que",
        "qui", "quoi", "dont", "est", "sont", "etre", "avoir", "plus", "moins",
        "ce", "cette", "ces", "son", "leur", "leurs", "quel", "quelle", "quels",
        "quelles", "comment", "pourquoi", "veuillez", "merci", "sil", "vous",
        "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "by",
        "with", "without", "that", "which", "what", "how", "why", "is", "are",
        "be", "been", "will", "would", "their", "there", "please", "produce",
        "based", "various", "such", "as", "across", "from", "about", "can",
    }

    def _build_search_query(
        self,
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

        cfg = self.config
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
                if len(w) <= 2 or w in self._STOPWORDS or w in seen:
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

        return result or query[:cfg.search_query_fallback_chars]

    def _section_search_queries(
        self,
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
            for term in cleaned[:config.search_terms_max_queries]:
                q = self._build_search_query(
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
            self._build_search_query(
                query=query,
                section_name=section_name,
                key_questions=key_questions,
                search_terms=terms,
            )
        ]

    async def _section_research_phase(
        self,
        query: str,
        section_name: str,
        section_config: Dict,
        exploration_data: Dict,
        context: ExecutionContext
    ) -> Dict[str, Any]:
        """
        PHASE 2.1: Recherches ciblées pour une section spécifique.

        Retourne sources et données brutes pour cette section.
        """
        logger.info(f"       🔍 Recherches pour section '{section_name}'")

        queries = self._section_search_queries(query, section_name, section_config, context.config)
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
            engines = await self._cascade_engines(q, axis=axis, ctx=context)
            engines_param = ",".join(engines) if engines else None
            pool = self._search_pool_size(max_sources)
            found = await self.search_client.search(
                query=q, max_results=pool, language=language, engines=engines_param
            )

            # Repli si un operateur de recherche n'a rien donne.
            #
            # Un dork (filetype:, site:, "expression") restreint fortement :
            # si la source ciblee n'a rien sur le sujet, la requete rend zero
            # alors que les memes mots-cles sans operateur auraient trouve.
            # Le benefice du ciblage ne doit pas se payer d'une section vide.
            if not found and any(op in q for op in self._SEARCH_OPERATORS):
                import re as _re_op
                _plain = _re_op.sub(r'\b(?:filetype|site|inurl|intitle):\S+', '', q)
                _plain = _plain.replace('"', '').strip()
                _plain = " ".join(_plain.split())
                if _plain and _plain != q:
                    logger.info(f"       ↩️  opérateur infructueux, repli sans dork : '{_plain[:60]}'")
                    context.diagnostics.note("collecte.repli_sans_operateur", section=section_name, query=q)
                    found = await self.search_client.search(
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
                        section=section_name, query=q, error=str(term_error),
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
                        "snippet": r.content[:context.config.search_snippet_chars] if r.content else "",
                        # Moteur d'origine : SearXNG l'expose sur chaque
                        # resultat ; sans lui, impossible de savoir quels
                        # moteurs alimentent reellement un rapport ni de
                        # detecter qu'un moteur ne repond plus.
                        "engine": getattr(r, "engine", "") or ""
                    }
                    for r in search_results
                ]

                # Le filtre de domaines precede la troncature : sinon des
                # resultats exclus occupent les places demandees (C35).
                sources = self._filter_by_constraints(sources)[:max_sources]

                # Tracker la recherche
                context.add_step(
                    "searxng",
                    f"search_for_{section_name}",
                    search_query,
                    {"results_count": len(sources), "urls": [s["url"] for s in sources]},
                    True
                )
            else:
                context.add_step(
                    "searxng",
                    f"search_for_{section_name}",
                    search_query,
                    None,
                    False
                )

            return {"sources": sources, "query_used": search_query}

        except Exception as e:
            logger.error(f"       ❌ Erreur recherche section: {e}")
            context.diagnostics.note("collecte.recherche_section_echec", section=section_name, error=str(e))
            context.add_step(
                "searxng",
                f"search_for_{section_name}",
                search_query,
                str(e),
                False
            )
            return {"sources": [], "query_used": search_query}

    async def _section_extraction_phase(
        self,
        section_name: str,
        section_data: Dict,
        context: ExecutionContext
    ) -> List[Dict]:
        """
        PHASE 2.2: Extraction du contenu des sources découvertes.

        Retourne liste de contenus extraits et nettoyés.
        """
        logger.info(f"       📥 Extraction sources pour '{section_name}'")

        sources = section_data.get("sources", [])
        urls = [s["url"] for s in sources]

        if not urls:
            return []

        # Utiliser webextractor - reutilise l'instance partagee de
        # l'orchestrateur (self.extractor_manager) plutot que d'en recreer
        # une nouvelle a chaque section, ce qui permettra un futur cache
        # au niveau du manager de beneficier a toutes les sections/requetes.
        try:
            extractor_manager = self.extractor_manager
            # extract_images, extract_links et clean_html etaient passes ici
            # sans exister dans ExtractionOptions : ignores en silence, puis
            # refuses depuis que le modele interdit les champs inconnus (C60).
            cfg = context.config
            options = ExtractionOptions(
                use_agent=True,
                headless=True,
                rss_tier_root_only=cfg.rss_tier_root_only,
                single_llm_pass_enabled=cfg.single_llm_pass_enabled,
            )

            # Extraire chaque URL séquentiellement, en reutilisant le cache
            # partage si cette URL a deja ete extraite pour une autre section
            # (frequent : un meme article pertinent pour plusieurs sections).
            extracted_data = []
            # Plafond d'URL extraites : la valeur demandee par la requete
            # (V3-3) prime ; sinon, avec le drapeau, il suit la profondeur deja
            # appliquee a la recherche (3, 5 ou 8) ; sinon 5 (evite le timeout).
            if cfg.max_sources_per_section:
                url_cap = cfg.max_sources_per_section
            elif cfg.extraction_cap_follows_depth:
                url_cap = len(urls)
            else:
                url_cap = cfg.extraction_url_cap_default
            for url in urls[:url_cap]:
                try:
                    if url in context.extraction_cache:
                        result = context.extraction_cache[url]
                        logger.info(f"       ♻️  Reutilisation cache extraction pour {url}")
                    else:
                        result = await extractor_manager.extract(
                            url=url,
                            llm_client=self.llm_client,
                            options=options
                        )
                        context.extraction_cache[url] = result

                    if result.success and result.content:
                        # DECOUPAGE au lieu de TRONCATURE.
                        #
                        # Auparavant : result.content[:3000]. Un article de
                        # recherche de 100 000 caracteres perdait 97% de son
                        # contenu DES L'EXTRACTION, avant tout scoring — et le
                        # chiffre precis cite par le rapport se trouvait
                        # souvent dans la partie jetee. C'est la cause
                        # mecanique du taux de citations valides de 61% : le
                        # modele voyait le debut d'un article et completait le
                        # reste avec ce qu'il savait du sujet.
                        #
                        # Desormais le document entier est decoupe en chunks
                        # qui se chevauchent ; la selection semantique
                        # (_semantic_chunk_selection) choisit ensuite les plus
                        # pertinents. La TOTALITE du document est donc
                        # candidate, quelle que soit sa taille.
                        from app.utils.chunking import document_to_chunk_items
                        _items = document_to_chunk_items(
                            source=url,
                            title=result.title or "",
                            content=result.content,
                            v2=cfg.chunking_v2,
                        )
                        extracted_data.extend(_items)
                        if len(_items) > 1:
                            logger.info(
                                f"          ✂️  {url[:55]} : {len(result.content):,} chars "
                                f"→ {len(_items)} chunks"
                            )

                        # Tracker l'extraction
                        if url not in context.discovered_sources:
                            context.discovered_sources.append(url)
                        context.add_step(
                            "webextractor",
                            f"extract_content_for_{section_name}",
                            url,
                            {"title": result.title, "content_length": len(result.content)},
                            True
                        )
                    else:
                        context.add_step(
                            "webextractor",
                            f"extract_content_for_{section_name}",
                            url,
                            None,
                            False
                        )
                except Exception as url_error:
                    logger.warning(f"       ⚠️ Extraction failed for {url}: {url_error}")
                    context.diagnostics.note("collecte.extraction_url_echec", section=section_name, url=url, error=str(url_error))
                    context.add_step(
                        "webextractor",
                        f"extract_content_for_{section_name}",
                        url,
                        str(url_error),
                        False
                    )
                    continue

            logger.info(f"       → {len(extracted_data)}/{len(urls)} sources extraites")
            return extracted_data

        except Exception as e:
            logger.error(f"       ❌ Erreur extraction: {e}")
            context.diagnostics.note("collecte.extraction_section_echec", section=section_name, error=str(e))
            return []

    async def _cross_reference_data(
        self,
        section_name: str,
        new_data: List[Dict],
        context: ExecutionContext
    ) -> List[Dict]:
        """
        PHASE 2.3: Croisement avec données existantes - a deux niveaux.

        1. Niveau source : meme URL deja citee ailleurs (signal faible -
           c'est la meme source, pas une confirmation independante).
        2. Niveau contenu : similarite semantique entre le contenu de CE
           chunk et des chunks precedents venant d'un DOMAINE DIFFERENT
           (signal fort - deux redactions independantes disent la meme
           chose = corroboration reelle). Une similarite extreme (>0.92)
           est au contraire suspecte (reprise verbatim d'un communique,
           pas une confirmation independante) et n'est pas comptee.
        """
        logger.info(f"       🔗 Croisement données pour '{section_name}'")

        CORROBORATION_MIN = context.config.corroboration_min_similarity
        CORROBORATION_MAX = context.config.corroboration_max_similarity  # au-dela : probable reprise verbatim, pas une vraie confirmation

        enriched = []

        # Embeddings des nouveaux chunks, avec cache partage (evite un
        # recalcul si _semantic_chunk_selection traite le meme contenu ensuite)
        new_embeddings = {}
        if not self.llm_client.supports_embeddings:
            if new_data:
                context.diagnostics.note(
                    "selection.repli_mots_cles",
                    etape="croisement",
                    section=section_name,
                    raison="embeddings non supportes",
                )
        elif new_data:
            try:
                from app.research.embedding_cache import embed_with_cache
                vectors = await embed_with_cache(
                    self.llm_client,
                    context.embedding_cache,
                    [dp.get("content", "") for dp in new_data],
                )
                for dp, vec in zip(new_data, vectors):
                    new_embeddings[id(dp)] = vec
            except Exception as e:
                logger.warning(f"       Embeddings indisponibles pour le croisement semantique: {e}")
                context.diagnostics.note(
                    "selection.repli_mots_cles",
                    etape="croisement",
                    section=section_name,
                    raison=f"embeddings en erreur : {e}",
                )

        for data_point in new_data:
            enriched_point = data_point.copy()
            enriched_point["cross_references"] = []
            source_domain = _extract_domain(data_point.get("source", ""))
            vec = new_embeddings.get(id(data_point))

            # Niveau 1 : meme URL reutilisee dans une autre section
            for other_section, section_content in context.final_content['sections'].items():
                if other_section == section_name:
                    continue
                for existing_data in section_content.get('raw_data', []):
                    if isinstance(existing_data, dict) and existing_data.get('source') == data_point.get('source'):
                        enriched_point["cross_references"].append({
                            "section": other_section,
                            "type": "meme_source",
                            "note": "Même source utilisée"
                        })

            # Niveau 2 : corroboration semantique par une source independante
            # (domaine different, contenu similaire mais pas identique)
            corroborating_domains = set()
            if vec:
                for prior in context.all_extracted_chunks:
                    if prior["domain"] == source_domain or not prior.get("embedding"):
                        continue
                    sim = _cosine_similarity(vec, prior["embedding"])
                    if CORROBORATION_MIN <= sim <= CORROBORATION_MAX:
                        corroborating_domains.add(prior["domain"])
                        enriched_point["cross_references"].append({
                            "section": prior["section"],
                            "type": "corroboration_semantique",
                            "note": f"Confirmé indépendamment par {prior['domain']} (similarité {sim:.2f})"
                        })

            enriched_point["corroboration_count"] = len(corroborating_domains)
            enriched.append(enriched_point)

            # Alimente l'historique global pour les corroborations futures
            context.all_extracted_chunks.append({
                "domain": source_domain,
                "section": section_name,
                "embedding": vec,
            })

        cross_ref_count = sum(1 for e in enriched if e.get("cross_references"))
        corroborated_count = sum(1 for e in enriched if e.get("corroboration_count", 0) > 0)
        if cross_ref_count > 0:
            logger.info(f"       → {cross_ref_count} données avec croisements, {corroborated_count} corroborées par une source indépendante")

        return enriched

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
        scoring_query = self._build_search_query(
            query=query,
            section_name=section_name,
            search_terms=(section_config or {}).get("search_terms"),
        )
        selected_data = await self._semantic_chunk_selection(
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
        _lang = getattr(self, "_search_language", "all")
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
