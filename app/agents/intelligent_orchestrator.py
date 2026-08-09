"""
Intelligent Orchestrator - Orchestrateur Intelligent Multi-Outils

Système qui:
1. Connaît tous les outils disponibles et leurs capacités
2. Analyse la requête et le contexte
3. Planifie la meilleure stratégie
4. Exécute en s'adaptant aux résultats
5. Traite et enrichit les données
6. Ajuste et poursuit jusqu'à obtenir la réponse

Outils disponibles:
- SearXNG: Recherche web pour découvrir sources
- ExtractAgent: Extraction de contenu web
- AdaptiveNavigator: API/Sites avec stratégies adaptatives
- SearchSite: Recherche interactive sur sites
- DataProcessor: Traitement datasets
- Vision: Analyse d'images
"""

import asyncio
import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime
from enum import Enum

from app.agents.adaptive_navigator import AdaptiveNavigator, StrategyType
from app.agents.data_processor import DataProcessor, DataProcessorFactory
from app.core.llm.base import BaseLLMClient
from app.services.search_service import searxng_client
from app.manager import ExtractorManager
from app.api.models import ExtractionOptions
from app.core.data_extractor import GenericDataExtractor, DataValidator

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


class ToolType(str, Enum):
    """Types d'outils disponibles."""
    SEARCH_WEB = "search_web"          # SearXNG pour découvrir sources
    EXTRACT_CONTENT = "extract_content"  # Extraction contenu web
    API_NAVIGATE = "api_navigate"      # Navigation API
    SEARCH_SITE = "search_site"        # Recherche sur site
    PROCESS_DATA = "process_data"      # Traitement données
    ANALYZE_IMAGE = "analyze_image"    # Analyse d'images


class Tool:
    """Représentation d'un outil avec ses capacités."""

    def __init__(
        self,
        name: str,
        tool_type: ToolType,
        capabilities: List[str],
        input_formats: List[str],
        output_format: str,
        best_for: List[str],
        limitations: List[str]
    ):
        self.name = name
        self.tool_type = tool_type
        self.capabilities = capabilities
        self.input_formats = input_formats
        self.output_format = output_format
        self.best_for = best_for
        self.limitations = limitations


class ExecutionContext:
    """Contexte d'exécution avec historique et apprentissage."""

    def __init__(self, query: str):
        self.query = query
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
        input_str = str(input_data)[:100]
        output_str = str(output_data)[:100] if output_data else ""

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

    def __init__(self, llm_client: BaseLLMClient, timeout: int = 300):
        self.llm_client = llm_client
        self.timeout = timeout

        # Initialiser les outils
        self.adaptive_navigator = AdaptiveNavigator(llm_client, timeout)
        self.extractor_manager = ExtractorManager()
        self.data_extractor = GenericDataExtractor(llm_client)

        # Catalogue d'outils
        self.tools = self._init_tools_catalog()

    def _init_tools_catalog(self) -> Dict[str, Tool]:
        """Initialise le catalogue d'outils."""
        return {
            "searxng": Tool(
                name="SearXNG",
                tool_type=ToolType.SEARCH_WEB,
                capabilities=["découvrir_sources", "recherche_web", "trouver_urls"],
                input_formats=["query_text"],
                output_format="list_of_urls",
                best_for=["exploration", "découverte", "sources_multiples"],
                limitations=["pas_de_contenu_direct", "qualité_variable"]
            ),
            "extract": Tool(
                name="WebExtractor",
                tool_type=ToolType.EXTRACT_CONTENT,
                capabilities=["extraire_contenu", "suivre_liens", "parser_html"],
                input_formats=["url"],
                output_format="structured_content",
                best_for=["contenu_pages", "articles", "documentation"],
                limitations=["sites_complexes", "javascript_lourd"]
            ),
            "api_navigator": Tool(
                name="API Navigator",
                tool_type=ToolType.API_NAVIGATE,
                capabilities=["appeler_apis", "parser_docs", "construire_requêtes"],
                input_formats=["api_url", "query"],
                output_format="structured_data",
                best_for=["apis_rest", "données_structurées", "classements"],
                limitations=["nécessite_doc", "apis_complexes"]
            ),
            "search_site": Tool(
                name="SearchSite",
                tool_type=ToolType.SEARCH_SITE,
                capabilities=["recherche_interactive", "formulaires", "navigation"],
                input_formats=["site_url", "query"],
                output_format="search_results",
                best_for=["sites_avec_recherche", "exploration_site"],
                limitations=["sites_sans_formulaire", "lent"]
            ),
            "data_processor": Tool(
                name="DataProcessor",
                tool_type=ToolType.PROCESS_DATA,
                capabilities=["filtrer", "trier", "grouper", "joindre", "transformer"],
                input_formats=["list_of_dicts"],
                output_format="processed_data",
                best_for=["post_traitement", "agrégations", "nettoyage"],
                limitations=["nécessite_données_structurées"]
            )
        }

    async def execute_intelligent_research(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
        max_steps: int = 10
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

        ctx = ExecutionContext(query)
        context = context or {}
        self.current_context = context

        # ===================================================================
        # PHASE 1: EXPLORATION + PLAN DÉTAILLÉ avec canvas complet
        # ===================================================================
        logger.info("🔍 PHASE 1: Exploration + Planification détaillée")

        # Étape 1.1: Exploration initiale restreinte pour évaluer le champ
        exploration_data = await self._exploratory_phase(query, context)
        logger.info(f"  ✓ Exploration: {len(exploration_data.get('sources', []))} sources découvertes")
        logger.info(f"  ✓ Champ évalué: {exploration_data.get('field_assessment', 'N/A')}")

        # Étape 1.2: Planification avec canvas détaillé basé sur exploration
        plan = await self._create_detailed_plan(query, context, exploration_data)
        logger.info(f"  ✓ Canvas structure: {len(plan.get('sections', []))} sections")
        logger.info(f"  ✓ Profondeur: {plan.get('complexity_analysis', {}).get('target_length', 'N/A')}")
        logger.info(f"  ✓ Enchaînements définis: {len(plan.get('narrative_flow', []))} transitions")

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
        extraction_semaphore = asyncio.Semaphore(3)

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

        extraction_results = await asyncio.gather(*[
            _research_and_extract(name, cfg) for name, cfg in section_targets.items()
        ])
        extracted_by_section = dict(extraction_results)

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
        base_threshold = plan.get('search_strategy', {}).get('sources_per_section', 2)
        DEPTH_MODULATION = {"light": -1, "moderate": 0, "deep": 1}

        for section_name, section_config in section_targets.items():
            n_sources = len(extracted_by_section.get(section_name, []))
            depth = section_config.get('depth', 'moderate')
            threshold = max(1, base_threshold + DEPTH_MODULATION.get(depth, 0))

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
        logger.info(f"  ✓ Cohérence narrative améliorée")

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
                query, ctx, final_answer, max_iterations=1
            )
        except Exception as e:
            logger.warning(f"Enrichissement iteratif echoue, rapport initial conserve: {e}")

        # Étape 4.2: Génération bibliographie complète
        logger.info("  ✓ Bibliographie générée")

        # Étape 4.3: Génération des traces complètes
        final_answer = self._add_complete_traces(final_answer, ctx, exploration_data, plan)

        processing_time = (datetime.now() - start_time).total_seconds()

        return {
            "success": True,
            "query": query,
            "answer": final_answer,
            "execution_context": {
                "steps_executed": len(ctx.steps),
                "datasets_collected": len(ctx.datasets),
                "sources_discovered": ctx.discovered_sources,  # Liste complète, pas len()
                "tool_performance": ctx.tool_success_rate
            },
            "processing_time": processing_time
        }

    async def _analyze_and_plan(self, query: str, context: Dict) -> Dict[str, Any]:
        """
        Analyse la requête et planifie la stratégie avec les outils disponibles.
        """
        # Construire description des outils
        tools_desc = []
        for tool in self.tools.values():
            tools_desc.append({
                "name": tool.name,
                "type": tool.tool_type,
                "best_for": tool.best_for,
                "output": tool.output_format
            })

        # Détecter si on a une URL ou source précise
        has_url = bool(context.get("url") or context.get("sources_required"))

        # Extraire les sections demandées (si présentes dans le contexte)
        requested_sections = context.get("output_sections") or []

        prompt = f"""Analyse cette requête et planifie la stratégie ADAPTÉE avec les outils disponibles:

REQUÊTE: "{query}"

CONTEXTE:
{json.dumps(context, indent=2) if context else "Aucun"}
URL ou source spécifique fournie: {"OUI" if has_url else "NON"}
Sections demandées: {requested_sections if requested_sections else "À déterminer"}

OUTILS DISPONIBLES:
{json.dumps(tools_desc, indent=2)}

Ta mission: Créer un plan d'exécution optimal ADAPTÉ à la complexité et profondeur attendue.

ÉTAPE 1: ANALYSE DE LA REQUÊTE
Évalue ces critères pour déterminer la profondeur nécessaire:

1. **Complexité du sujet** (1-5):
   - 1 = Simple, concept unique (ex: "c'est quoi X?")
   - 3 = Modéré, plusieurs aspects (ex: "avantages et inconvénients de X")
   - 5 = Complexe, multidimensionnel (ex: "écosystème complet, tendances, adoption, futur de X")

2. **Spécificité demandée** (1-5):
   - 1 = Très large, vue générale
   - 3 = Focalisé sur certains aspects
   - 5 = Très précis, détails techniques

3. **Format demandé** (1-5):
   - 1 = Résumé court, définition
   - 3 = Rapport standard
   - 5 = Étude approfondie, analyse détaillée

4. **Profondeur temporelle** (1-5):
   - 1 = Point dans le temps (ex: "aujourd'hui")
   - 3 = Période définie (ex: "2024")
   - 5 = Évolution historique + projection future

5. **Interconnexions attendues** (1-5):
   - 1 = Sujet isolé
   - 3 = Relations avec contexte
   - 5 = Analyse systémique, impacts multiples

ÉTAPE 2: DÉTERMINER L'AMPLEUR

Calcule score_profondeur = moyenne des 5 critères

- Score 1.0-2.0 → **Rapport CONCIS** (1-2 sections, 500-1000 mots total)
- Score 2.1-3.0 → **Rapport STANDARD** (2-3 sections, 1000-1500 mots total)
- Score 3.1-4.0 → **Rapport DÉTAILLÉ** (3-5 sections, 1500-2500 mots total)
- Score 4.1-5.0 → **Étude APPROFONDIE** (4-7 sections, 2500-4000 mots total)

Retourne JSON:
{{
  "strategy_type": "direct|exploration|hybrid",
  "reasoning": "Explication de la stratégie",
  "complexity_analysis": {{
    "topic_complexity": 1-5,
    "specificity": 1-5,
    "format_depth": 1-5,
    "temporal_depth": 1-5,
    "interconnections": 1-5,
    "overall_score": moyenne,
    "target_length": "concis|standard|détaillé|approfondi",
    "estimated_words": nombre_mots_total,
    "justification": "pourquoi ce niveau de détail"
  }},
  "data_needed": ["liste des données nécessaires"],
  "sections": ["liste des sections du rapport"] ou null si pas de rapport,
  "section_targets": {{
    "nom_section": {{"words_target": nombre_mots, "depth": "light|moderate|deep"}}
  }},
  "steps": [
    {{
      "step": 1,
      "tool": "nom_outil",
      "action": "description de l'action",
      "input": {{"query": "...", "max_results": 10}},
      "target_section": "nom de la section cible" ou null,
      "expected_output": "ce qu'on attend"
    }}
  ],
  "expected_iterations": 1-5,
  "data_processing_needed": true|false
}}

STRATÉGIES OBLIGATOIRES:
- **Si URL/source fournie**: utiliser api_navigator ou search_site (stratégie "direct")
- **Si AUCUNE URL/source**: TOUJOURS commencer par "searxng" pour découvrir des sources (stratégie "exploration")
- **hybrid**: Combiner plusieurs outils

RÈGLES CRITIQUES:
1. Sans URL spécifique, la première étape DOIT être "searxng"
2. Après searxng, prévoir "webextractor" pour extraire le contenu des URLs trouvées
3. Les étapes doivent être enchaînées logiquement
4. Maximum 5 étapes dans le plan initial
5. ADAPTER le nombre de sources à collecter selon la profondeur (score 1-2 = 5 sources, score 4-5 = 15 sources)

EXEMPLE pour requête SIMPLE "c'est quoi Rust":
{{
  "strategy_type": "exploration",
  "complexity_analysis": {{
    "topic_complexity": 1,
    "specificity": 1,
    "format_depth": 1,
    "temporal_depth": 1,
    "interconnections": 1,
    "overall_score": 1.0,
    "target_length": "concis",
    "estimated_words": 600,
    "justification": "Requête simple demandant définition basique"
  }},
  "sections": ["Définition", "Principaux usages"],
  "section_targets": {{
    "Définition": {{"words_target": 300, "depth": "light"}},
    "Principaux usages": {{"words_target": 300, "depth": "light"}}
  }},
  "steps": [
    {{"step": 1, "tool": "searxng", "action": "Rechercher sources", "input": {{"query": "...", "max_results": 5}}}},
    {{"step": 2, "tool": "webextractor", "action": "Extraire contenu", "input": {{"urls": []}}}}
  ]
}}

EXEMPLE pour requête APPROFONDIE "écosystème Rust 2024, adoption entreprise, roadmap":
{{
  "strategy_type": "exploration",
  "complexity_analysis": {{
    "topic_complexity": 5,
    "specificity": 4,
    "format_depth": 5,
    "temporal_depth": 4,
    "interconnections": 5,
    "overall_score": 4.6,
    "target_length": "approfondi",
    "estimated_words": 3500,
    "justification": "Requête complexe nécessitant analyse multidimensionnelle avec données 2024, tendances adoption, et projection future"
  }},
  "sections": ["Vue d'ensemble", "Écosystème technique", "Adoption entreprise", "Cas d'usage", "Roadmap et futur", "Défis et perspectives"],
  "section_targets": {{
    "Vue d'ensemble": {{"words_target": 400, "depth": "moderate"}},
    "Écosystème technique": {{"words_target": 700, "depth": "deep"}},
    "Adoption entreprise": {{"words_target": 700, "depth": "deep"}},
    "Cas d'usage": {{"words_target": 600, "depth": "moderate"}},
    "Roadmap et futur": {{"words_target": 600, "depth": "deep"}},
    "Défis et perspectives": {{"words_target": 500, "depth": "moderate"}}
  }},
  "steps": [
    {{"step": 1, "tool": "searxng", "action": "Rechercher sources", "input": {{"query": "...", "max_results": 15}}}},
    {{"step": 2, "tool": "webextractor", "action": "Extraire contenu", "input": {{"urls": []}}}}
  ]
}}
"""

        response = await self.llm_client.generate(
            [{"role": "user", "content": prompt}],
            max_tokens=2000,
            temperature=0.2
        )

        # Parser JSON
        try:
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
                            return plan
        except:
            pass

        # Fallback: plan exploration avec SearXNG
        return {
            "strategy_type": "exploration",
            "reasoning": "Plan par défaut - exploration web",
            "data_needed": ["sources web"],
            "sections": requested_sections or ["Synthèse"],  # Sections par défaut
            "steps": [
                {
                    "step": 1,
                    "tool": "searxng",
                    "action": "Rechercher des sources sur le web",
                    "input": {"query": query, "max_results": 10},
                    "target_section": requested_sections[0] if requested_sections else "Synthèse",
                    "expected_output": "liste d'URLs"
                },
                {
                    "step": 2,
                    "tool": "webextractor",
                    "action": "Extraire le contenu des pages trouvées",
                    "input": {"urls": []},
                    "target_section": requested_sections[0] if requested_sections else "Synthèse",
                    "expected_output": "contenu extrait"
                }
            ],
            "expected_iterations": 2,
            "data_processing_needed": False
        }

    async def _execute_step(self, step: Dict, ctx: ExecutionContext) -> Dict[str, Any]:
        """Exécute une étape avec l'outil approprié."""
        tool_name = step.get("tool", "").lower().replace(" ", "_").replace("-", "_")
        action = step.get("action", "")
        input_data = step.get("input", {})

        logger.info(f"  🔧 Outil: {tool_name}")

        # Normaliser les noms d'outils
        if tool_name in ["searchsite", "site_search"]:
            tool_name = "search_site"
        elif tool_name in ["dataprocessor", "processor"]:
            tool_name = "data_processor"

        try:
            # Router vers le bon outil
            if tool_name in ["api_navigator", "api", "navigator"]:
                # Récupérer l'URL - soit de l'input, soit du step, soit du contexte initial
                target_url = (input_data.get("url") or
                            step.get("url", "") or
                            self.current_context.get("url", ""))

                # Si toujours pas d'URL, essayer de détecter depuis la query
                if not target_url and "geo.api.gouv.fr" in ctx.query.lower():
                    target_url = "https://geo.api.gouv.fr"

                logger.info(f"  🌐 URL cible: {target_url}")

                result = await self.adaptive_navigator.execute(
                    user_query=input_data.get("query", ctx.query),
                    target_url=target_url,
                    context=input_data.get("context", {"is_api": True} if "api" in target_url.lower() else {})
                )

                if result.get("success") and result.get("results"):
                    ctx.add_dataset(f"step_{len(ctx.steps)}_data", result["results"])
                    ctx.add_step(tool_name, action, input_data, result["results"], True)
                    return result
                else:
                    ctx.add_step(tool_name, action, input_data, None, False)
                    return {"success": False, "error": "Pas de résultats"}

            elif tool_name in ["searxng", "search_web", "web_search"]:
                # Recherche web avec SearXNG
                query = input_data.get("query", ctx.query)
                max_results = input_data.get("max_results", 10)
                categories = input_data.get("categories")
                time_range = input_data.get("time_range")

                logger.info(f"  🔍 Recherche SearXNG: {query}")

                results = await searxng_client.search(
                    query, max_results=max_results,
                    categories=categories, time_range=time_range
                )

                if results:
                    # Extraire URLs et titres
                    urls_data = []
                    for result in results[:max_results]:
                        urls_data.append({
                            "url": result.url,
                            "title": result.title,
                            "snippet": result.content
                        })
                        # Déduplication des sources
                        if result.url not in ctx.discovered_sources:
                            ctx.discovered_sources.append(result.url)

                    ctx.add_dataset(f"searxng_{len(ctx.steps)}", urls_data)
                    ctx.add_step(tool_name, action, input_data, urls_data, True)

                    logger.info(f"  ✅ {len(urls_data)} URLs trouvées")
                    return {"success": True, "data": urls_data, "urls": [d["url"] for d in urls_data]}
                else:
                    ctx.add_step(tool_name, action, input_data, None, False)
                    return {"success": False, "error": "Aucun résultat"}

            elif tool_name in ["webextractor", "extract", "extract_content"]:
                # Extraction de contenu web
                urls = input_data.get("urls", [])

                # Si pas d'URLs fournies, chercher dans les données précédentes
                if not urls:
                    all_data = ctx.get_all_data()
                    for item in all_data:
                        if isinstance(item, dict) and "url" in item:
                            urls.append(item["url"])

                if not urls:
                    return {"success": False, "error": "Aucune URL à extraire"}

                # Limiter à 5 URLs pour ne pas prendre trop de temps
                urls = urls[:5]
                logger.info(f"  📄 Extraction: {len(urls)} URLs")

                extracted_data = []
                for url in urls:
                    try:
                        extract_result = await self.extractor_manager.extract(
                            url=url,
                            llm_client=self.llm_client,
                            options=ExtractionOptions(
                                timeout=30,
                                use_agent=False,  # Pas d'agent pour aller plus vite
                                headless=True
                            )
                        )

                        if extract_result.success:
                            # Extraction données structurées AUTOMATIQUE
                            topic_context = ctx.query if hasattr(ctx, 'query') else ""
                            structured = await self.data_extractor.extract_structured_data(
                                content=extract_result.content or "",
                                source_url=url,
                                topic_context=topic_context
                            )

                            extracted_data.append({
                                "url": url,
                                "title": extract_result.title or "",
                                "content": extract_result.content or "",
                                "content_type": extract_result.content_type or "unknown",
                                "metadata": extract_result.metadata if hasattr(extract_result, 'metadata') else {},
                                "structured_data": structured.to_dict()  # DONNÉES STRUCTURÉES
                            })
                            logger.info(f"  ✅ Extraction réussie: {url[:60]}... ({len(structured.numerical)} num)")
                        else:
                            logger.warning(f"  ⚠️ Échec extraction {url}: {extract_result.error if hasattr(extract_result, 'error') else 'Unknown'}")
                    except Exception as e:
                        logger.error(f"  ❌ Erreur extraction {url}: {e}")
                        continue

                if extracted_data:
                    ctx.add_dataset(f"extracted_{len(ctx.steps)}", extracted_data)
                    ctx.add_step(tool_name, action, input_data, extracted_data, True)
                    logger.info(f"  ✅ {len(extracted_data)} pages extraites")
                    return {"success": True, "data": extracted_data}
                else:
                    ctx.add_step(tool_name, action, input_data, None, False)
                    return {"success": False, "error": "Aucune extraction réussie"}

            elif tool_name == "data_processor":
                # Traiter les données existantes
                all_data = ctx.get_all_data()
                if not all_data:
                    return {"success": False, "error": "Pas de données à traiter"}

                operation = input_data.get("operation", "")
                params = input_data.get("params", {})

                processor = DataProcessor(all_data)

                # Appliquer l'opération
                if operation == "filter_and_sort":
                    result_data = (processor
                                  .filter_by_field(**params.get("filter", {}))
                                  .sort(**params.get("sort", {}))
                                  .get())
                elif operation == "top_n":
                    result_data = DataProcessorFactory.top_n_by_field(
                        all_data,
                        params.get("field", "value"),
                        params.get("n", 10)
                    )
                elif operation == "aggregate":
                    result_data = DataProcessorFactory.aggregate_by_group(
                        all_data,
                        **params
                    )
                else:
                    result_data = all_data

                ctx.add_dataset(f"processed_{len(ctx.steps)}", result_data)
                ctx.add_step(tool_name, action, input_data, result_data, True)

                return {"success": True, "data": result_data}

            else:
                logger.warning(f"  ⚠️ Outil inconnu: {tool_name}")
                return {"success": False, "error": f"Outil inconnu: {tool_name}"}

        except Exception as e:
            logger.error(f"Erreur exécution étape: {e}", exc_info=True)
            ctx.add_step(tool_name, action, input_data, None, False)
            return {"success": False, "error": str(e)}

    async def _evaluate_result(
        self,
        query: str,
        step_result: Dict,
        ctx: ExecutionContext
    ) -> Dict[str, Any]:
        """Évalue le résultat et détermine les prochaines actions."""

        all_data = ctx.get_all_data()

        prompt = f"""Évalue le résultat de cette étape de recherche:

REQUÊTE INITIALE: "{query}"

DONNÉES COLLECTÉES: {len(all_data)} items
Échantillon: {json.dumps(all_data[:3], indent=2, ensure_ascii=False) if all_data else "Aucune"}

ÉTAPES DÉJÀ EXÉCUTÉES: {len(ctx.steps)}

Évalue:
{{
  "completeness": 0-100,
  "what_we_have": ["liste"],
  "what_missing": ["liste"],
  "data_quality": "excellent|good|partial|poor",
  "next_actions": [
    {{
      "tool": "nom_outil",
      "action": "description",
      "input": {{}},
      "priority": "high|medium|low",
      "reasoning": "pourquoi"
    }}
  ]
}}

RÈGLES:
- completeness: % de réponse possible avec données actuelles
- Si >= 90%: next_actions peut être vide
- Si < 50%: proposer 2-3 actions
- Privilégier traitement données existantes avant nouvelles recherches
"""

        response = await self.llm_client.generate(
            [{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.2
        )

        # Parser JSON
        try:
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
        except:
            pass

        # Fallback
        return {
            "completeness": 50 if all_data else 0,
            "what_we_have": [f"{len(all_data)} items"],
            "what_missing": [],
            "next_actions": [],
            "data_quality": "partial"
        }

    async def _synthesize_answer(self, query: str, ctx: ExecutionContext) -> Dict[str, Any]:
        """Synthétise la réponse finale."""
        all_data = ctx.get_all_data()

        if not all_data:
            return {
                "type": "no_data",
                "message": "Aucune donnée collectée",
                "steps_attempted": len(ctx.steps)
            }

        # Préparer les données pour la synthèse
        # NOUVEAU: Sélection intelligente au lieu de truncation arbitraire
        logger.info(f"  🎯 Préparation données: {len(all_data)} items disponibles")

        # Sélectionner les chunks les plus pertinents (max 50000 chars)
        selected_data = await self._semantic_chunk_selection(all_data, query, max_chars=50000)

        # Extraire le contenu textuel ET données structurées
        extracted_contents = []
        urls_info = []
        all_structured_data = []

        for item in selected_data:
            if isinstance(item, dict):
                if "content" in item and item.get("content"):
                    # Page web extraite avec contenu (PLUS de truncation ici, déjà fait par sélection)
                    page_data = {
                        "url": item.get("url", ""),
                        "title": item.get("title", ""),
                        "content": item.get("content", "")  # Contenu complet du chunk sélectionné
                    }

                    # Ajouter données structurées si disponibles
                    if "structured_data" in item and item["structured_data"]:
                        struct = item["structured_data"]
                        page_data["numerical_data"] = struct.get("numerical", [])
                        page_data["temporal_data"] = struct.get("temporal", [])
                        page_data["entities"] = struct.get("entities", [])
                        all_structured_data.append(struct)

                    extracted_contents.append(page_data)
                elif "url" in item:
                    # URL découverte sans contenu
                    urls_info.append({
                        "url": item.get("url", ""),
                        "title": item.get("title", ""),
                        "snippet": item.get("snippet", "")
                    })

        # Utiliser le LLM pour synthétiser
        data_for_synthesis = {
            "pages_extraites": extracted_contents,
            "urls_trouvées": urls_info[:5] if len(urls_info) > 5 else urls_info  # Limiter les URLs
        }

        # Validation croisée des données numériques
        validated_data = None
        if all_structured_data:
            from app.core.data_extractor import StructuredData
            structured_objects = []
            for sd in all_structured_data:
                # Reconstruire objets StructuredData
                try:
                    structured_objects.append(StructuredData(
                        source_url=sd.get("source_url", ""),
                        numerical=[],  # Simplifié pour validation
                        temporal=[],
                        entities=[],
                        relationships=[],
                        extraction_timestamp=sd.get("extraction_timestamp", ""),
                        overall_confidence=sd.get("overall_confidence", 0.0)
                    ))
                except:
                    pass

            if structured_objects:
                common_metrics = DataValidator.find_common_metrics(structured_objects)
                if common_metrics:
                    validated_data = DataValidator.validate_numerical_coherence(common_metrics)
                    logger.info(f"✓ Validation croisée: {len(common_metrics)} métriques communes trouvées")

        # Récupérer le format de sortie demandé
        output_structure = self.current_context.get("output_structure", "summary")
        output_sections = self.current_context.get("output_sections", [])

        # Si un rapport structuré est demandé, générer les sections
        if output_structure == "report" and output_sections:
            # Préparer résumé des données structurées validées
            structured_summary = ""
            if validated_data:
                structured_summary = "\n\n## DONNÉES NUMÉRIQUES VALIDÉES (multi-sources):\n"
                for metric in validated_data[:10]:  # Top 10 métriques
                    if metric.get('coherent'):
                        structured_summary += f"- {metric['metric']}: {metric['mean']:.2f} (validé par {len(metric['values'])} sources)\n"

            prompt = f"""Analyse les données collectées et crée un rapport structuré détaillé:

REQUÊTE: "{query}"

DONNÉES COLLECTÉES:
- {len(extracted_contents)} pages web extraites avec contenu complet
- {len(urls_info)} URLs découvertes
- {len(all_structured_data)} sources avec données structurées extraites
{structured_summary}

CONTENU DES PAGES EXTRAITES (avec données structurées):
{json.dumps(data_for_synthesis, indent=2, ensure_ascii=False)[:60000]}

SECTIONS DEMANDÉES: {output_sections}

Ta mission: Créer un rapport complet avec TOUTES les sections demandées.

INSTRUCTIONS CRITIQUES:
1. Analyse TOUT le contenu fourni (texte + données structurées)
2. Pour CHAQUE section demandée, rédige 4-8 paragraphes DÉTAILLÉS et COMPLETS
3. **PRIORITÉ AUX DONNÉES STRUCTURÉES**: Utilise numerical_data, temporal_data, entities
4. Inclus TOUS les faits précis, chiffres, dates, noms extraits automatiquement
5. Développe chaque point avec contexte, implications, détails
6. Base-toi UNIQUEMENT sur les données fournies
7. Cite les URLs sources utilisées pour chaque information
8. Pour chaque chiffre mentionné, ajoute-le aussi dans "data" structuré de la section
9. OBJECTIF: Rapport détaillé de 3-5 pages (12000-20000 caractères minimum)

Retourne JSON:
{{
  "type": "report",
  "summary": "Résumé exécutif en 2-3 phrases",
  "sections": [
    {{
      "title": "Titre section 1",
      "content": "Contenu détaillé (2-4 paragraphes)",
      "data": [
        {{"metric": "nom_métrique", "value": 123.45, "unit": "unité", "source_url": "https://..."}},
        ...chiffres clés de cette section
      ],
      "sources": [{{"url": "https://...", "title": "Titre"}}]
    }},
    ...pour chaque section de {output_sections}
  ],
  "bibliography": [
    {{
      "title": "Titre de la source",
      "url": "https://...",
      "type": "website"
    }}
  ],
  "confidence": "high|medium|low"
}}

IMPORTANT: Génère EXACTEMENT les sections: {output_sections}
"""
        else:
            # Format simple: synthèse globale
            prompt = f"""Synthétise la réponse à cette requête en te basant sur les données collectées:

REQUÊTE: "{query}"

DONNÉES COLLECTÉES:
- {len(extracted_contents)} pages web extraites avec contenu complet
- {len(urls_info)} URLs découvertes

CONTENU DES PAGES EXTRAITES:
{json.dumps(data_for_synthesis, indent=2, ensure_ascii=False)[:15000]}

Ta mission: Créer une synthèse complète et détaillée basée sur ces données.

INSTRUCTIONS:
1. Analyse TOUT le contenu fourni
2. Rédige une synthèse de 3-5 paragraphes minimum
3. Identifie les points clés, initiatives, chiffres importants
4. Structure l'information de manière logique
5. Cite les sources pertinentes

Retourne JSON:
{{
  "type": "summary",
  "summary": "Synthèse complète et détaillée (3-5 paragraphes)",
  "key_points": ["point 1", "point 2", ...],
  "sources_used": ["url1", "url2", ...],
  "confidence": "high|medium|low"
}}
"""

        response = await self.llm_client.generate(
            [{"role": "user", "content": prompt}],
            max_tokens=15000,  # Rapports détaillés 3-5 pages
            temperature=0.1
        )

        try:
            start_idx = response.find('{')
            if start_idx != -1:
                bracket_depth = 0
                for i, char in enumerate(response[start_idx:], start=start_idx):
                    if char == '{':
                        bracket_depth += 1
                    elif char == '}':
                        bracket_depth -= 1
                        if bracket_depth == 0:
                            synthesis_result = json.loads(response[start_idx:i + 1])

                            # POST-TRAITEMENT: Extraire données structurées du contenu texte si rapport avec sections
                            logger.info(f"  📋 Synthèse: {len(synthesis_result.get('sections', []))} sections, {len(all_structured_data)} sources données")
                            if 'sections' in synthesis_result and len(all_structured_data) > 0:
                                logger.info(f"  🔄 Lancement post-traitement")
                                synthesis_result = self._post_process_sections(
                                    synthesis_result,
                                    all_structured_data
                                )
                            else:
                                logger.warning(f"  ⚠️ Post-traitement ignoré: sections={' sections' in synthesis_result}, data_count={len(all_structured_data)}")

                            return synthesis_result
        except Exception as e:
            logger.error(f"  ❌ Erreur parsing JSON synthèse: {str(e)}")
            logger.debug(f"  Réponse LLM (200 premiers chars): {response[:200]}")

        # Fallback: données brutes
        return {
            "type": "list",
            "data": all_data,
            "summary": f"{len(all_data)} items collectés",
            "confidence": "medium",
            "sources_count": len(ctx.datasets)
        }

    def _score_relevance(self, content: str, query: str, structured_data: Optional[Dict] = None) -> float:
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

        score = 0.0
        content_lower = content.lower()
        query_lower = query.lower()

        # 1. Keyword matching (max 40 points)
        query_words = set(query_lower.split())
        query_words = {w for w in query_words if len(w) > 3}  # Mots > 3 chars

        if query_words:
            matched_words = sum(1 for word in query_words if word in content_lower)
            keyword_score = min(40, (matched_words / len(query_words)) * 40)
            score += keyword_score

        # 2. Structured data boost (max 30 points)
        if structured_data:
            num_count = len(structured_data.get("numerical", []))
            temp_count = len(structured_data.get("temporal", []))
            ent_count = len(structured_data.get("entities", []))

            # Plus de données structurées = plus pertinent
            struct_score = min(30, (num_count * 3) + (temp_count * 2) + (ent_count * 1))
            score += struct_score

        # 3. Content length quality (max 20 points)
        # Ni trop court (< 500 chars) ni trop long (> 5000 chars)
        content_len = len(content)
        if 500 <= content_len <= 5000:
            length_score = 20
        elif content_len < 500:
            length_score = (content_len / 500) * 20
        else:  # > 5000
            length_score = max(10, 20 - ((content_len - 5000) / 1000))
        score += length_score

        # 4. Presence of numbers/dates (max 10 points)
        import re
        numbers = re.findall(r'\d+(?:[.,]\d+)?', content)
        dates = re.findall(r'\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4}', content)

        data_score = min(10, len(numbers) + len(dates) * 2)
        score += data_score

        return min(100.0, score)

    async def _semantic_chunk_selection(
        self,
        all_data: List[Dict],
        query: str,
        max_chars: int = 50000,
        depth: str = "moderate",
        min_score_threshold: float = None
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
            max_chars: Budget maximum de caractères
            depth: "light" | "moderate" | "deep" - Profondeur d'analyse attendue
            min_score_threshold: Score minimum pour inclure un chunk (auto si None)

        Returns:
            Liste triée des chunks les plus pertinents
        """
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
        if hasattr(self.llm_client, "embed") and valid_items:
            try:
                texts_to_embed = [query] + [item["content"][:2000] for item in valid_items]
                vectors = await self.llm_client.embed(texts_to_embed)
                query_vec = vectors[0]
                for item, vec in zip(valid_items, vectors[1:]):
                    semantic_scores[id(item)] = _cosine_similarity(query_vec, vec)
                logger.info(f"  🧠 Scoring sémantique par embeddings : {len(semantic_scores)} chunks scorés")
            except Exception as e:
                logger.warning(f"Embeddings indisponibles pour la selection semantique, repli sur mots-cles seuls: {e}")

        scored_items = []

        for item in valid_items:
            content = item.get("content", "")

            # Score mots-cles + donnees structurees (existant)
            structured_data = item.get("structured_data")
            keyword_score = self._score_relevance(content, query, structured_data)

            # Score semantique (0-1 -> 0-100) vs la requete
            sem = semantic_scores.get(id(item))

            # Bonus de corroboration : un chunk confirme par des sources
            # independantes (calcule dans _cross_reference_data, cf.
            # corroboration_count) merite de remonter, meme si son score
            # de pertinence brut est un peu plus faible - c'est le meme
            # principe que le croisement de sources en fact-checking.
            corrob = min(item.get("corroboration_count", 0), 3) / 3.0  # plafonne a 3 sources

            if sem is not None:
                score = (keyword_score * 0.5) + (sem * 100 * 0.35) + (corrob * 100 * 0.15)
            else:
                score = (keyword_score * 0.85) + (corrob * 100 * 0.15)

            scored_items.append({
                "score": score,
                "item": item,
                "content_length": len(content)
            })

        # Trier par score décroissant
        scored_items.sort(key=lambda x: x["score"], reverse=True)

        # Adapter seuil et stratégie selon profondeur
        if min_score_threshold is None:
            if depth == "light":
                # Synthèse concise: seulement top sources très pertinentes
                min_score_threshold = 60.0
                max_chunks_target = 4  # Peu de sources, qualité maximale
            elif depth == "deep":
                # Analyse approfondie: exploration large
                min_score_threshold = 35.0
                max_chunks_target = 15  # Beaucoup de sources pour vision complète
            else:  # moderate
                # Équilibré - ABAISSÉ pour permettre plus de données
                min_score_threshold = 40.0  # Était 55 (trop strict), maintenant 40
                max_chunks_target = 8  # Était 6, maintenant 8
        else:
            max_chunks_target = 20  # Pas de limite si seuil custom

        # Sélectionner les meilleurs chunks
        selected = []
        total_chars = 0
        chunks_selected = 0

        for scored in scored_items:
            # Arrêter si on a atteint le nombre cible ET que le score diminue trop
            if chunks_selected >= max_chunks_target and scored["score"] < min_score_threshold:
                break

            # Filtrer les chunks avec score trop faible
            if scored["score"] < min_score_threshold:
                continue

            item = scored["item"]
            content_len = scored["content_length"]

            # Vérifier si on peut ajouter ce chunk
            if total_chars + content_len > max_chars:
                # Pour profondeur deep, essayer de prendre au moins un extrait
                if depth == "deep":
                    remaining = max_chars - total_chars
                    if remaining > 1000:  # Au moins 1000 chars disponibles
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

        logger.info(f"  📊 Sélection intelligente: {len(selected)}/{len(all_data)} chunks, {total_chars:,} chars (score moyen: {sum(s['score'] for s in scored_items[:len(selected)]) / max(len(selected), 1):.1f}/100)")

        return selected

    def _post_process_sections(
        self,
        synthesis_result: Dict[str, Any],
        all_structured_data: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Post-traite les sections pour extraire les données structurées du texte.
        """
        import re

        logger.info(f"  🔄 POST-TRAITEMENT: Démarrage avec {len(all_structured_data)} sources de données")

        # Agréger toutes les données numériques
        all_numerical = []
        for struct_data in all_structured_data:
            if isinstance(struct_data, dict) and "numerical" in struct_data:
                all_numerical.extend(struct_data.get("numerical", []))

        # Créer mapping: métrique → données
        metrics_map = {}
        for num_data in all_numerical:
            if isinstance(num_data, dict):
                metric = num_data.get("metric", "").lower()
                if metric:
                    if metric not in metrics_map:
                        metrics_map[metric] = []
                    metrics_map[metric].append(num_data)

        # Patterns pour détecter chiffres dans texte
        number_patterns = [
            r'(\d+(?:[.,]\d+)?)\s*(%|€|millions?|milliards?|K|M|Md)',
            r'(\d+(?:[.,]\d+)?)\s+([A-Za-zÀ-ÿ]+)',
        ]

        for section in synthesis_result.get('sections', []):
            existing_data = section.get('data')
            if existing_data is not None and len(existing_data) > 0:
                continue  # Déjà rempli par le LLM

            content = section.get('content', '')
            section_data = []

            for pattern in number_patterns:
                matches = re.finditer(pattern, content)
                for match in matches:
                    try:
                        value_str = match.group(1).replace(',', '.')
                        value = float(value_str)
                        unit = match.group(2) if len(match.groups()) > 1 else None

                        context_start = max(0, match.start() - 20)
                        context = content[context_start:match.start()].strip()

                        source_url = None
                        metric_name = None

                        for metric, num_list in metrics_map.items():
                            for num in num_list:
                                if abs(num.get("value", 0) - value) < 0.01:
                                    if num.get("unit") == unit or unit is None:
                                        source_url = num.get("source_url")
                                        metric_name = num.get("metric")
                                        break
                            if source_url:
                                break

                        section_data.append({
                            "metric": metric_name or "valeur",
                            "value": value,
                            "unit": unit,
                            "source_url": source_url
                        })

                    except (ValueError, IndexError):
                        continue

            if section_data:
                section['data'] = section_data[:5]
                logger.info(f"  ✅ Post-traitement: {len(section_data[:5])} données pour '{section.get('title')}'")

        return synthesis_result

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

        for iteration in range(max_iterations):
            logger.info(f"  🔄 Itération {iteration + 1}/{max_iterations}")

            # Analyser qualité de chaque section
            quality_analysis = await self._analyze_report_quality(query, report, ctx)

            logger.info(f"  📊 Qualité globale: {quality_analysis.get('overall_score', 0)}/100")

            # Si qualité suffisante, arrêter
            if quality_analysis.get('overall_score', 0) >= 85:
                logger.info("  ✅ Qualité suffisante atteinte")
                break

            # Identifier sections à enrichir (score < 75 OU longueur < 2000 chars)
            weak_sections = []
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
                if score < 75 or content_length < 2000:
                    weak_sections.append(section_analysis)
                    if content_length < 2000:
                        logger.info(f"  📏 Section '{section_title}' trop courte: {content_length} chars")

            if not weak_sections:
                logger.info("  ✅ Toutes les sections sont de qualité et longueur suffisantes")
                break

            logger.info(f"  🎯 {len(weak_sections)} section(s) à enrichir")

            # Enrichir les sections faibles (max 2 par itération)
            for section_analysis in weak_sections[:2]:
                section_title = section_analysis.get('title')
                missing = section_analysis.get('missing', [])

                logger.info(f"  🔍 Enrichissement: {section_title}")
                logger.info(f"    Manques: {', '.join(missing)}")

                # Recherche ciblée pour combler les manques
                enrichment_data = await self._targeted_search(query, section_title, missing, ctx)

                # Régénérer la section avec les nouvelles données
                if enrichment_data:
                    updated_section = await self._regenerate_section(
                        query, section_title, report, enrichment_data, ctx
                    )

                    # Remplacer la section dans le rapport
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
{chr(10).join(sections_text)[:5000]}

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
                max_tokens=2000,
                temperature=0.1
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

        # Fallback
        return {
            "overall_score": 70,
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
        search_query = f"{query} {section_title} {' '.join(missing[:3])}"

        logger.info(f"    🔎 Recherche ciblée: {search_query}")

        try:
            # Recherche SearXNG
            results = await searxng_client.search(search_query, max_results=5)

            if not results:
                return None

            # Extraire les 2 premiers résultats les plus pertinents
            urls_to_extract = [r.url for r in results[:2]]

            extracted_data = []
            for url in urls_to_extract:
                try:
                    extract_result = await self.extractor_manager.extract(
                        url=url,
                        llm_client=self.llm_client,
                        options=ExtractionOptions(
                            timeout=30,
                            use_agent=False,
                            headless=True
                        )
                    )

                    if extract_result.success and extract_result.content:
                        # Extraction données structurées (enrichissement ciblé)
                        structured = await self.data_extractor.extract_structured_data(
                            content=extract_result.content[:2000],
                            source_url=url,
                            topic_context=f"{query} - {section_title}"
                        )

                        extracted_data.append({
                            "url": url,
                            "title": extract_result.title or "",
                            "content": extract_result.content[:2000],
                            "structured_data": structured.to_dict()
                        })
                        # Ajouter aux sources découvertes (avec déduplication)
                        if url not in ctx.discovered_sources:
                            ctx.discovered_sources.append(url)
                except Exception as e:
                    logger.warning(f"    ⚠️ Échec extraction {url}: {e}")
                    continue

            return {
                "query": search_query,
                "sources": extracted_data,
                "missing": missing
            }

        except Exception as e:
            logger.error(f"Erreur recherche ciblée: {e}")
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

        # Données d'enrichissement
        enrichment_content = json.dumps(
            enrichment_data.get('sources', []),
            indent=2,
            ensure_ascii=False
        )[:3000]

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
                max_tokens=2000,
                temperature=0.1
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

        # Fallback: retourner la section actuelle
        return current_section

    async def _validate_numerical_coherence(self, report: Dict[str, Any]) -> Dict[str, Any]:
        """Valide la cohérence des chiffres mentionnés dans le rapport."""

        sections_text = []
        for section in report.get('sections', []):
            sections_text.append(f"## {section.get('title')}\n{section.get('content', '')}")

        full_text = "\n\n".join(sections_text)

        prompt = f"""Analyse la cohérence numérique de ce rapport:

{full_text[:8000]}

Ta mission:
1. Extraire TOUS les chiffres mentionnés (montants, pourcentages, dates, quantités)
2. Vérifier leur cohérence temporelle et logique
3. Identifier les incohérences potentielles
4. Proposer des corrections si nécessaire

Retourne JSON:
{{
  "coherent": true|false,
  "issues": [
    {{
      "type": "temporal_incoherence|logical_contradiction|missing_unit",
      "description": "Description du problème",
      "values_involved": ["7.1 milliards en 2024", "13 milliards total"],
      "severity": "high|medium|low",
      "suggested_fix": "Explication ou correction"
    }}
  ],
  "key_numbers": [
    {{
      "value": "7.1",
      "unit": "milliards euros",
      "context": "levées 2024",
      "temporal_marker": "2024"
    }}
  ]
}}

RÈGLES:
- Si total cumulé < montant annuel récent: incohérence temporelle
- Si pourcentages ne somment pas correctement: incohérence logique
- Si dates contradictoires: incohérence temporelle
"""

        try:
            # Sortie structuree forcee (au lieu du parsing manuel de texte) :
            # ce validateur est le premier converti comme preuve de concept -
            # schema simple et stable, contrairement au plan de recherche qui
            # a des cles dynamiques (section_targets par nom de section).
            validation_schema = {
                "type": "object",
                "properties": {
                    "coherent": {"type": "boolean"},
                    "issues": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string"},
                                "description": {"type": "string"},
                                "values_involved": {"type": "array", "items": {"type": "string"}},
                                "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                                "suggested_fix": {"type": "string"}
                            },
                            "required": ["type", "description", "values_involved", "severity", "suggested_fix"],
                            "additionalProperties": False
                        }
                    },
                    "key_numbers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "value": {"type": "string"},
                                "unit": {"type": "string"},
                                "context": {"type": "string"},
                                "temporal_marker": {"type": "string"}
                            },
                            "required": ["value", "unit", "context", "temporal_marker"],
                            "additionalProperties": False
                        }
                    }
                },
                "required": ["coherent", "issues", "key_numbers"],
                "additionalProperties": False
            }

            if hasattr(self.llm_client, "generate_structured"):
                validation = await self.llm_client.generate_structured(
                    [{"role": "user", "content": prompt}],
                    schema=validation_schema,
                    schema_name="numerical_validation",
                    max_tokens=1500,
                    temperature=0.1
                )
            else:
                # Repli pour un client LLM qui ne supporte pas la sortie
                # structuree (ex: Albert) - ancien parsing manuel conserve.
                response = await self.llm_client.generate(
                    [{"role": "user", "content": prompt}],
                    max_tokens=1500,
                    temperature=0.1
                )
                start_idx = response.find('{')
                validation = None
                if start_idx != -1:
                    bracket_depth = 0
                    for i, char in enumerate(response[start_idx:], start=start_idx):
                        if char == '{':
                            bracket_depth += 1
                        elif char == '}':
                            bracket_depth -= 1
                            if bracket_depth == 0:
                                validation = json.loads(response[start_idx:i + 1])
                                break

            if validation is not None:
                # Logger les problèmes trouvés
                if not validation.get('coherent', True):
                    high_severity_issues = [
                        issue for issue in validation.get('issues', [])
                        if issue.get('severity') == 'high'
                    ]
                    if high_severity_issues:
                        logger.warning(f"⚠️ {len(high_severity_issues)} incohérence(s) numérique(s) détectée(s)")
                        for issue in high_severity_issues:
                            logger.warning(f"  - {issue.get('description')}")
                            logger.info(f"    Correction suggérée: {issue.get('suggested_fix')}")

                    # Ajouter une note de validation au rapport
                    report['validation'] = {
                        'numerical_coherence_checked': True,
                        'issues_found': len(validation.get('issues', [])),
                        'high_severity_issues': len(high_severity_issues)
                    }
                else:
                    logger.info("✓ Cohérence numérique validée")
                    report['validation'] = {
                        'numerical_coherence_checked': True,
                        'coherent': True
                    }

                return report
        except Exception as e:
            logger.error(f"Erreur validation cohérence: {e}")

        # Fallback: retourner sans modification
        return report

    async def close(self):
        """Ferme les ressources."""
        await self.adaptive_navigator.close()
    # ===================================================================
    # NOUVELLE ARCHITECTURE OPTIMISÉE
    # Phase 3: Synthèse par section
    # Phase 4: Assemblage global et évaluation
    # Phase 5: Comblement des manques
    # ===================================================================

    async def _synthesize_sections(self, query: str, ctx: ExecutionContext) -> Dict[str, Any]:
        """
        PHASE 3: Synthèse par section.
        Pour chaque section, synthétise les données brutes collectées en contenu rédigé.
        """
        logger.info(f"📝 PHASE 3: Synthèse par section ({len(ctx.final_content['sections'])} sections)")

        for section_name, section_data in ctx.final_content['sections'].items():
            raw_data = section_data['raw_data']

            if not raw_data:
                logger.warning(f"  ⚠️ Section '{section_name}': aucune donnée")
                continue

            logger.info(f"  🔄 Synthèse section: {section_name} ({len(raw_data)} données)")

            # Déterminer la profondeur d'analyse pour cette section
            # TODO: Extraire du plan généré en Phase 1 section_targets
            # Pour l'instant, utiliser moderate par défaut
            section_depth = "moderate"

            # Sélection intelligente des données pertinentes pour cette section
            selected_data = await self._semantic_chunk_selection(raw_data, query, max_chars=20000, depth=section_depth)

            # Préparation du prompt de synthèse
            data_for_synthesis = []
            all_structured_data = []

            for item in selected_data:
                if isinstance(item, dict):
                    if "content" in item and item.get("content"):
                        page_data = {
                            "url": item.get("url", ""),
                            "title": item.get("title", ""),
                            "content": item.get("content", "")
                        }

                        # Ajouter données structurées
                        if "structured_data" in item and item["structured_data"]:
                            struct = item["structured_data"]
                            page_data["numerical_data"] = struct.get("numerical", [])
                            page_data["temporal_data"] = struct.get("temporal", [])
                            page_data["entities"] = struct.get("entities", [])
                            all_structured_data.append(struct)

                        data_for_synthesis.append(page_data)

            # Appel LLM pour synthétiser cette section
            # APPROCHE QUALITATIVE: adapter les instructions selon la profondeur, pas imposer longueur arbitraire
            if section_depth == "light":
                synthesis_instructions = """Ta mission: Synthèse CONCISE des points clés uniquement.

INSTRUCTIONS:
1. Identifie les 3-5 informations les plus importantes
2. Rédige 2-3 paragraphes courts et précis
3. Va droit à l'essentiel, sans détails secondaires
4. Cite les sources principales"""
            elif section_depth == "deep":
                synthesis_instructions = """Ta mission: Analyse APPROFONDIE explorant tous les aspects.

INSTRUCTIONS:
1. Analyse TOUTES les données fournies en profondeur
2. Explore les interconnexions et implications
3. Rédige 6-10 paragraphes détaillés
4. Développe les nuances et contextes
5. Compare les sources et perspectives
6. Cite systématiquement toutes les sources"""
            else:  # moderate
                synthesis_instructions = """Ta mission: Rédiger un contenu ÉQUILIBRÉ et informatif.

INSTRUCTIONS:
1. Analyse les données principales
2. Rédige 4-6 paragraphes structurés
3. Équilibre profondeur et clarté
4. Cite les sources importantes"""

            prompt = f"""Synthétise le contenu pour la section "{section_name}" du rapport.

REQUÊTE INITIALE: "{query}"

DONNÉES DISPONIBLES ({len(data_for_synthesis)} sources):
{json.dumps(data_for_synthesis, indent=2, ensure_ascii=False)[:25000]}

DONNÉES STRUCTURÉES EXTRAITES:
- {sum(len(s.get('numerical', [])) for s in all_structured_data)} données numériques
- {sum(len(s.get('temporal', [])) for s in all_structured_data)} données temporelles
- {sum(len(s.get('entities', [])) for s in all_structured_data)} entités

{synthesis_instructions}

RÈGLES TECHNIQUES:
- CITE LES SOURCES: Pour chaque affirmation, ajoute [SOURCE:url_exacte]
  Exemple: "Selon l'étude de 2024 [SOURCE:https://exemple.com], l'adoption a augmenté de 45%."
- Utilise les données structurées (chiffres, dates, noms) avec précision
- INTERDICTION: Ne PAS inventer de références [1], [2], [3] - utilise UNIQUEMENT [SOURCE:url]
- La longueur sera la CONSÉQUENCE NATURELLE de la qualité de l'analyse, pas un objectif arbitraire

Retourne JSON:
{{
  "content": "Contenu rédigé avec citations [SOURCE:url]...",
  "key_data": [
    {{"metric": "...", "value": ..., "unit": "...", "source": "url"}}
  ],
  "sources_used": ["url1", "url2", "url3"]
}}

RAPPEL: Chaque affirmation factuelle DOIT avoir [SOURCE:url]. Les sources_used doivent lister TOUTES les URLs citées.

Rédige maintenant pour "{section_name}":"""

            response = await self.llm_client.generate(
                [{"role": "user", "content": prompt}],
                max_tokens=5000,
                temperature=0.3
            )

            # Parser JSON
            try:
                start_idx = response.find('{')
                if start_idx != -1:
                    bracket_depth = 0
                    for i, char in enumerate(response[start_idx:], start=start_idx):
                        if char == '{':
                            bracket_depth += 1
                        elif char == '}':
                            bracket_depth -= 1
                            if bracket_depth == 0:
                                section_result = json.loads(response[start_idx:i + 1])

                                # Mettre à jour la section dans le contexte
                                ctx.final_content['sections'][section_name]['content'] = section_result.get('content', '')
                                ctx.final_content['sections'][section_name]['metadata']['key_data'] = section_result.get('key_data', [])
                                ctx.final_content['sections'][section_name]['metadata']['sources'] = section_result.get('sources_used', [])

                                logger.info(f"    ✓ Section synthétisée: {len(section_result.get('content', ''))} chars")
                                break
            except Exception as e:
                logger.error(f"  ❌ Erreur synthèse section '{section_name}': {e}")

        return ctx.final_content

    async def _assemble_and_evaluate(self, query: str, ctx: ExecutionContext) -> Dict[str, Any]:
        """
        PHASE 4: Assemblage global et évaluation.
        Assemble les sections, crée cohérence inter-sections, identifie manques.
        """
        logger.info("🔗 PHASE 4: Assemblage global et évaluation")

        # Préparer les sections pour l'assemblage
        sections_content = []
        for section_name, section_data in ctx.final_content['sections'].items():
            if section_data['content']:
                sections_content.append({
                    "title": section_name,
                    "content": section_data['content'],
                    "data": section_data['metadata'].get('key_data', [])
                })

        if not sections_content:
            return {
                "type": "error",
                "message": "Aucune section synthétisée"
            }

        # Assemblage et mise en cohérence
        prompt = f"""Assemble et finalise ce rapport.

REQUÊTE: "{query}"

SECTIONS DISPONIBLES ({len(sections_content)}):
{json.dumps(sections_content, indent=2, ensure_ascii=False)[:30000]}

Ta mission:
1. Vérifier cohérence entre sections
2. Ajouter transitions/liaisons SANS supprimer le contenu existant
3. Compléter introduction/conclusion si nécessaire
4. Vérifier cohérence numérique

RÈGLES CRITIQUES:
- PRÉSERVER TOUTES les citations [SOURCE:url] présentes dans le contenu
- NE PAS réécrire ou reformuler les sections - seulement ajouter transitions
- CONSERVER toutes les données structurées (data)
- Si tu ajoutes des transitions, utilise aussi des [SOURCE:url] si nécessaire

Retourne JSON:
{{
  "type": "report",
  "title": "Titre du rapport",
  "sections": [
    {{
      "title": "...",
      "content": "Contenu PRÉSERVÉ avec [SOURCE:url] + transitions ajoutées",
      "data": [...]  // TOUS les data de la section originale
    }}
  ],
  "metadata": {{
    "word_count": ...,
    "coherence_score": 0-100,
    "sources_count": ...
  }}
}}"""

        response = await self.llm_client.generate(
            [{"role": "user", "content": prompt}],
            max_tokens=10000,
            temperature=0.2
        )

        # Parser JSON
        try:
            start_idx = response.find('{')
            if start_idx != -1:
                bracket_depth = 0
                for i, char in enumerate(response[start_idx:], start=start_idx):
                    if char == '{':
                        bracket_depth += 1
                    elif char == '}':
                        bracket_depth -= 1
                        if bracket_depth == 0:
                            final_report = json.loads(response[start_idx:i + 1])
                            logger.info(f"  ✓ Rapport assemblé: {len(final_report.get('sections', []))} sections")
                            return final_report
        except Exception as e:
            logger.error(f"  ❌ Erreur assemblage: {e}")

        # Fallback: retourner sections brutes
        return {
            "type": "report",
            "sections": sections_content,
            "metadata": {"coherence_score": 70}
        }

    def _convert_sources_to_bibliography(self, report: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convertit les citations [SOURCE:url] en références numérotées [1], [2], etc.
        et génère une bibliographie ordonnée.

        AMÉLIORATION: Inclut aussi TOUTES les sources des données structurées,
        même si elles ne sont pas citées explicitement dans le texte.
        """
        import re

        # Collecter toutes les URLs uniques citées dans le rapport
        url_to_num = {}
        bibliography = []
        counter = 1

        # ÉTAPE 1: Collecter les URLs citées dans le contenu avec [SOURCE:url]
        for section in report.get('sections', []):
            content = section.get('content', '')

            # Trouver toutes les citations [SOURCE:url]
            pattern = r'\[SOURCE:(https?://[^\]]+)\]'
            matches = re.findall(pattern, content)

            for url in matches:
                if url not in url_to_num:
                    url_to_num[url] = counter
                    # Extraire le domaine pour le titre
                    domain = url.split('/')[2] if len(url.split('/')) > 2 else url
                    bibliography.append({
                        "id": counter,
                        "url": url,
                        "title": f"Source {counter} - {domain}",
                        "accessed": datetime.now().strftime("%Y-%m-%d")
                    })
                    counter += 1

        # ÉTAPE 2: Ajouter les sources des données structurées (métriques, etc.)
        # Ces sources ont été utilisées même si non citées explicitement dans le texte
        for section in report.get('sections', []):
            data_points = section.get('data', [])
            for data_point in data_points:
                source_url = data_point.get('source')
                if source_url and source_url not in url_to_num:
                    url_to_num[source_url] = counter
                    domain = source_url.split('/')[2] if len(source_url.split('/')) > 2 else source_url
                    bibliography.append({
                        "id": counter,
                        "url": source_url,
                        "title": f"Source {counter} - {domain}",
                        "accessed": datetime.now().strftime("%Y-%m-%d")
                    })
                    counter += 1

        # Remplacer [SOURCE:url] par [numéro] dans toutes les sections
        for section in report.get('sections', []):
            content = section.get('content', '')

            # Remplacer chaque citation par son numéro
            for url, num in url_to_num.items():
                content = content.replace(f'[SOURCE:{url}]', f'[{num}]')

            section['content'] = content

        # Ajouter la bibliographie au rapport
        report['bibliography'] = bibliography

        logger.info(f"  ✓ Bibliographie générée: {len(bibliography)} sources")

        return report

    def _identify_gaps(self, report: Dict[str, Any], ctx: ExecutionContext) -> List[Dict[str, Any]]:
        """
        Identifie les manques dans le rapport SANS appel LLM.
        Utilise des heuristiques simples.
        """
        gaps = []

        for section in report.get('sections', []):
            section_title = section.get('title', '')
            content = section.get('content', '')
            data = section.get('data', [])

            # Critère 1: Section trop courte
            if len(content) < 1500:
                gaps.append({
                    "section": section_title,
                    "type": "content_too_short",
                    "description": f"Section courte: {len(content)} chars",
                    "priority": "high" if len(content) < 800 else "medium"
                })

            # Critère 2: Pas assez de données structurées
            if len(data) < 2:
                gaps.append({
                    "section": section_title,
                    "type": "missing_data",
                    "description": f"Peu de données: {len(data)} métriques",
                    "priority": "medium"
                })

            # Critère 3: Pas de sources citées
            if "http" not in content and "www" not in content:
                gaps.append({
                    "section": section_title,
                    "type": "missing_sources",
                    "description": "Aucune source citée",
                    "priority": "low"
                })

        logger.info(f"  📊 Manques identifiés: {len(gaps)}")
        for gap in gaps:
            logger.info(f"    - {gap['section']}: {gap['description']} ({gap['priority']})")

        return gaps

    async def _fill_gaps_iteration(
        self,
        query: str,
        ctx: ExecutionContext,
        report: Dict[str, Any],
        gaps: List[Dict],
        max_iterations: int = 1
    ) -> Dict[str, Any]:
        """
        PHASE 5: Comblement des manques par itération.
        Relance recherches ciblées et re-synthèse.
        """
        if not gaps:
            return report

        logger.info(f"🔄 PHASE 5: Comblement de {len(gaps)} manques (max {max_iterations} itérations)")

        # Filtrer les gaps prioritaires
        high_priority_gaps = [g for g in gaps if g['priority'] == 'high']
        if not high_priority_gaps:
            logger.info("  ✓ Pas de manques critiques, rapport suffisant")
            return report

        for iteration in range(max_iterations):
            logger.info(f"  🔄 Itération {iteration + 1}/{max_iterations}")

            # Traiter les 2 premiers gaps prioritaires
            for gap in high_priority_gaps[:2]:
                section_name = gap['section']
                gap_type = gap['type']

                logger.info(f"    🎯 Comblement: {section_name} - {gap_type}")

                # Recherche ciblée
                search_query = f"{query} {section_name}"
                enrichment_data = await self._targeted_search(
                    query=query,
                    section_title=section_name,
                    missing_aspects=[gap_type],
                    ctx=ctx
                )

                if enrichment_data:
                    # Ajouter aux raw_data de la section
                    if section_name in ctx.final_content['sections']:
                        ctx.final_content['sections'][section_name]['raw_data'].extend(enrichment_data)

            # Re-synthèse des sections modifiées
            await self._synthesize_sections(query, ctx)

            # Re-assemblage
            report = await self._assemble_and_evaluate(query, ctx)

            # Re-évaluation des gaps
            new_gaps = self._identify_gaps(report, ctx)
            if len(new_gaps) < len(high_priority_gaps):
                logger.info(f"  ✓ Progrès: {len(high_priority_gaps)} → {len(new_gaps)} manques")
                high_priority_gaps = [g for g in new_gaps if g['priority'] == 'high']
                if not high_priority_gaps:
                    break
            else:
                logger.info("  ⚠️ Pas d'amélioration, arrêt")
                break

        return report

    # ========================================================================
    # NOUVELLES MÉTHODES POUR ARCHITECTURE REFONDÉE
    # ========================================================================

    async def _exploratory_phase(self, query: str, context: Dict) -> Dict[str, Any]:
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

        # Recherche exploratoire avec limite réduite
        search_params = {
            "query": query,
            "max_results": 8,  # Restreint pour exploration rapide
            "categories": ["general"],
            "engines": ["google", "duckduckgo"]
        }

        try:
            search_results = await searxng_client.search(
                query=search_params["query"],
                max_results=search_params["max_results"],
                categories=",".join(search_params["categories"]),
                engines=",".join(search_params["engines"])
            )

            urls_found = []
            if search_results:
                urls_found = [
                    {
                        "url": r.url,
                        "title": r.title,
                        "snippet": r.content[:200] if r.content else ""
                    }
                    for r in search_results[:search_params["max_results"]]
                ]

            # Analyser les snippets pour identifier sous-thèmes
            topics_found = self._extract_topics_from_snippets(urls_found)

            # Évaluer la richesse du champ
            field_assessment = "rich" if len(urls_found) >= 6 else ("moderate" if len(urls_found) >= 3 else "limited")

            logger.info(f"     → {len(urls_found)} sources, champ: {field_assessment}")
            logger.info(f"     → Sous-thèmes: {', '.join(topics_found[:3])}")

            return {
                "sources": urls_found,
                "field_assessment": field_assessment,
                "topics_found": topics_found,
                "data_preview": {
                    "total_sources": len(urls_found),
                    "snippets": [u["snippet"] for u in urls_found[:3]]
                }
            }

        except Exception as e:
            logger.error(f"  ❌ Erreur exploration: {e}")
            return {
                "sources": [],
                "field_assessment": "unknown",
                "topics_found": [],
                "data_preview": {}
            }

    def _extract_topics_from_snippets(self, urls_data: List[Dict]) -> List[str]:
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
        common = Counter(filtered).most_common(5)

        return [word for word, count in common if count >= 2]

    async def _create_detailed_plan(
        self,
        query: str,
        context: Dict,
        exploration_data: Dict
    ) -> Dict[str, Any]:
        """
        PHASE 1.2: Planification détaillée avec canvas complet.

        Utilise les données d'exploration pour créer un plan précis incluant:
        - Structure complète (sections, sous-sections)
        - Enchaînements narratifs
        - Objectifs par section
        - Profondeur adaptée
        """
        logger.info("  📋 Création du plan détaillé avec canvas")

        # Construire le contexte enrichi
        topics_found = exploration_data.get("topics_found", [])
        field_richness = exploration_data.get("field_assessment", "moderate")
        sources_count = len(exploration_data.get("sources", []))

        prompt = f"""Tu es un planificateur expert. Crée un plan détaillé pour répondre à cette requête.

REQUÊTE: "{query}"

DONNÉES D'EXPLORATION:
- Sources découvertes: {sources_count}
- Richesse du champ: {field_richness}
- Sous-thèmes identifiés: {', '.join(topics_found[:5])}
- Aperçu: {exploration_data.get('data_preview', {}).get('snippets', [])[0][:150] if exploration_data.get('data_preview', {}).get('snippets') else 'N/A'}

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
      "key_questions": ["question à explorer 1", "question 2"]
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

IMPORTANT: Adapte la structure aux sous-thèmes découverts: {', '.join(topics_found[:5])}
"""

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
                    "maxItems": 10,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "words_target": {"type": "integer", "minimum": 100, "maximum": 1200},
                            "depth": {"type": "string", "enum": ["light", "moderate", "deep"]},
                            "objectives": {"type": "array", "items": {"type": "string"}},
                            "key_questions": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["name", "words_target", "depth", "objectives", "key_questions"],
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
                        "search_depth": {"type": "string", "enum": ["quick", "standard", "exhaustive"]}
                    },
                    "required": ["total_sources_needed", "sources_per_section", "search_depth"],
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
                "Introduction": {"words_target": 300, "depth": "moderate", "objectives": ["Introduire le sujet"], "key_questions": []},
                "Analyse": {"words_target": 600, "depth": "moderate", "objectives": ["Analyser le sujet"], "key_questions": []},
                "Conclusion": {"words_target": 300, "depth": "light", "objectives": ["Conclure"], "key_questions": []}
            },
            "narrative_flow": [],
            "search_strategy": {"total_sources_needed": 10, "sources_per_section": 3, "search_depth": "standard"}
        }

        try:
            if hasattr(self.llm_client, "generate_structured"):
                structured = await self.llm_client.generate_structured(
                    [{"role": "user", "content": prompt}],
                    schema=plan_schema, schema_name="research_plan",
                    max_tokens=3000, temperature=0.2
                )
                # Conversion vers le format interne historique (sections=liste
                # de strings, section_targets=dict) attendu par tout le reste
                # du pipeline (Phase 2, _final_assembly, etc.) - zero
                # changement requis ailleurs dans le fichier.
                plan = {
                    "complexity_analysis": structured["complexity_analysis"],
                    "sections": [s["name"] for s in structured["sections"]],
                    "section_targets": {
                        s["name"]: {
                            "words_target": s["words_target"],
                            "depth": s["depth"],
                            "objectives": s["objectives"],
                            "key_questions": s["key_questions"]
                        } for s in structured["sections"]
                    },
                    "narrative_flow": structured["narrative_flow"],
                    "search_strategy": structured["search_strategy"]
                }
                logger.info(f"     → Plan créé (sortie structurée): {len(plan['sections'])} sections")
                return plan
            else:
                # Repli non-structure pour un client sans generate_structured (ex: Albert)
                response = await self.llm_client.generate(
                    [{"role": "user", "content": prompt}], max_tokens=3000, temperature=0.2
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

        except Exception as e:
            logger.error(f"  ❌ Erreur planification: {e}")

        logger.warning("  ⚠️  Plan de repli generique utilise (echec generation structuree)")
        return FALLBACK_PLAN

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

        # Construire requête de recherche ciblée
        key_questions = section_config.get("key_questions", [])
        objectives = section_config.get("objectives", [])

        # Requête enrichie
        search_query = f"{query} {section_name}"
        if key_questions:
            search_query += f" {' '.join(key_questions[:2])}"

        # Nombre de sources selon profondeur
        depth = section_config.get("depth", "moderate")
        max_sources = {"light": 3, "moderate": 5, "deep": 8}.get(depth, 5)

        try:
            search_results = await searxng_client.search(
                query=search_query,
                max_results=max_sources
            )

            sources = []
            if search_results:
                sources = [
                    {
                        "url": r.url,
                        "title": r.title,
                        "snippet": r.content[:300] if r.content else ""
                    }
                    for r in search_results[:max_sources]
                ]

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
            options = ExtractionOptions(
                extract_images=False,
                extract_links=False,
                clean_html=True,
                use_agent=True,
                headless=True
            )

            # Extraire chaque URL séquentiellement, en reutilisant le cache
            # partage si cette URL a deja ete extraite pour une autre section
            # (frequent : un meme article pertinent pour plusieurs sections).
            extracted_data = []
            for url in urls[:5]:  # Limiter à 5 URLs max pour éviter timeout
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
                        extracted_data.append({
                            "source": url,
                            "title": result.title or "",
                            "content": result.content[:3000],  # Limiter à 3000 chars
                            "metadata": {}
                        })

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

        CORROBORATION_MIN = 0.75
        CORROBORATION_MAX = 0.92  # au-dela : probable reprise verbatim, pas une vraie confirmation

        enriched = []

        # Embeddings des nouveaux chunks, avec cache partage (evite un
        # recalcul si _semantic_chunk_selection traite le meme contenu ensuite)
        new_embeddings = {}
        if hasattr(self.llm_client, "embed") and new_data:
            try:
                to_embed, keys = [], []
                for dp in new_data:
                    key = dp.get("source", "") + "::" + dp.get("content", "")[:100]
                    if key not in context.embedding_cache:
                        to_embed.append(dp.get("content", "")[:2000])
                        keys.append(key)
                if to_embed:
                    vectors = await self.llm_client.embed(to_embed)
                    for key, vec in zip(keys, vectors):
                        context.embedding_cache[key] = vec
                for dp in new_data:
                    key = dp.get("source", "") + "::" + dp.get("content", "")[:100]
                    new_embeddings[id(dp)] = context.embedding_cache.get(key)
            except Exception as e:
                logger.warning(f"       Embeddings indisponibles pour le croisement semantique: {e}")

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
        words_target = section_config.get("words_target", 500)
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
            words_target = min(words_target, 150)
            logger.info(f"       ⚠️  Aucune source pour '{section_name}', objectif réduit à {words_target} mots")
        elif n_sources <= 2 and words_target > 300:
            original_target = words_target
            words_target = max(250, words_target // 2)
            depth = "light"  # coherence : ne pas demander 6-10 paragraphes "deep" sur 250 mots
            logger.info(f"       ⚠️  Peu de sources ({n_sources}) pour '{section_name}', objectif ajusté {original_target}→{words_target} mots, profondeur réduite")

        # Sélection sémantique adaptée
        selected_data = await self._semantic_chunk_selection(
            all_data=enriched_data,
            query=f"{query} {section_name}",
            max_chars=words_target * 10,  # Approximation
            depth=depth
        )

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
                already_written.append(f"- \"{other_name}\": {other_content[:180].strip()}...")

        already_written_block = ""
        if already_written:
            already_written_block = f"""
SECTIONS DÉJÀ RÉDIGÉES DANS CE RAPPORT (pour éviter les répétitions, construis sur ce qui a déjà été dit plutôt que de le reformuler) :
{chr(10).join(already_written[:6])}
"""

        prompt = f"""Rédige la section "{section_name}" pour répondre à: "{query}"

OBJECTIFS DE CETTE SECTION:
{chr(10).join(f'- {obj}' for obj in objectives)}

QUESTIONS CLÉS À EXPLORER:
{chr(10).join(f'- {q}' for q in key_questions)}

INSTRUCTIONS:
{depth_instructions}
{already_written_block}
DONNÉES DISPONIBLES ({len(selected_data)} sources):
{json.dumps(selected_data[:5], indent=2, ensure_ascii=False)}

RÈGLES:
1. Cite TOUTES les sources avec format [SOURCE:url]
2. La longueur sera CONSÉQUENCE NATURELLE de la qualité, pas un objectif strict
3. Objectif approximatif: ~{words_target} mots
4. Structure: paragraphes cohérents, pas de listes à puces
5. Ton: informatif, précis, fluide
6. Ne répète pas ce qui est déjà couvert par les autres sections listées ci-dessus

Rédige uniquement le contenu (pas de titre de section, pas de métadonnées).
"""

        try:
            response = await self.llm_client.generate(
                [{"role": "user", "content": prompt}],
                max_tokens=int(words_target * 2.5),
                temperature=0.3
            )

            word_count = len(response.split())
            logger.info(f"       → {word_count} mots générés")

            return response.strip()

        except Exception as e:
            logger.error(f"       ❌ Erreur synthèse: {e}")
            return f"[Erreur lors de la génération de la section {section_name}]"

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
                "content_preview": section_data.get('content', '')[:500],
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
            if hasattr(self.llm_client, "generate_structured"):
                analysis = await self.llm_client.generate_structured(
                    [{"role": "user", "content": prompt}],
                    schema=intersections_schema, schema_name="coherence_analysis",
                    max_tokens=1500, temperature=0.2
                )
                return analysis
            else:
                response = await self.llm_client.generate(
                    [{"role": "user", "content": prompt}], max_tokens=1500, temperature=0.2
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

        return {"improvements": [], "redundancies": [], "coherence_score": 75}

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

        logger.info(f"  🔧 Application de {len(high_priority)} améliorations")

        for improvement in high_priority[:3]:  # Limiter à 3 max
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
        title = f"Analyse: {query.title()}"

        # Générer summary (résumé du premier paragraphe de la première section)
        summary = ""
        if sections_list:
            first_content = sections_list[0].get("content", "")
            # Prendre les 2 premières phrases
            sentences = first_content.split('. ')[:2]
            summary = '. '.join(sentences) + '.' if sentences else "Rapport de recherche approfondie."

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

        # Conversion sources → bibliographie
        final_report = self._convert_sources_to_bibliography(final_report)

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
