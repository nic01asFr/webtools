"""Etape 1.2 : plan detaille par sections (canvas, enchainements, strategie de recherche)."""

import json
import logging
from typing import Any, Dict, Optional

from app.research.context import ExecutionContext
from app.research.stages.services import StageServices
from app.research.stages.plan_prompt import build_plan_prompt_v2, is_meta_section
from app.research.stages.plan_schema import build_fallback_plan, build_plan_schema

logger = logging.getLogger(__name__)


async def create_detailed_plan(
    svc: StageServices,
    query: str,
    context: Dict,
    exploration_data: Dict,
    ctx: Optional["ExecutionContext"] = None,
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

    cfg = ctx.config if ctx is not None else svc.config

    # Construire le contexte enrichi
    topics_found = exploration_data.get("topics_found", [])
    field_richness = exploration_data.get("field_assessment", "moderate")
    sources_count = len(exploration_data.get("sources", []))

    prompt = f"""Tu es un planificateur expert. Crée un plan détaillé pour répondre à cette requête.

REQUÊTE: "{query}"

DONNÉES D'EXPLORATION:
- Sources découvertes: {sources_count}
- Richesse du champ: {field_richness}
- Sous-thèmes identifiés: {", ".join(topics_found[: cfg.plan_topics_in_prompt])}
- Aperçu: {exploration_data.get("data_preview", {}).get("snippets", [])[0][: cfg.plan_snippet_chars] if exploration_data.get("data_preview", {}).get("snippets") else "N/A"}

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

IMPORTANT: Adapte la structure aux sous-thèmes découverts: {", ".join(topics_found[: cfg.plan_topics_in_prompt])}
"""

    def _note(event: str, **details: Any) -> None:
        if ctx is not None:
            ctx.diagnostics.note(event, **details)

    plan_schema = build_plan_schema(cfg)

    FALLBACK_PLAN = build_fallback_plan(cfg)

    if svc.config.plan_prompt_v2:
        prompt = build_plan_prompt_v2(query, context, exploration_data, cfg)
        if not svc.llm_client.supports_structured:
            # Sans sortie structuree, le schema devient la seule description
            # du format attendu.
            prompt += (
                "\nReponds uniquement par un objet JSON conforme a ce schema :\n"
                + json.dumps(plan_schema, ensure_ascii=False)
            )

    fallback_reason = ""
    fallback_error = ""
    try:
        if svc.llm_client.supports_structured:
            structured = await svc.llm_client.generate_structured(
                [{"role": "user", "content": prompt}],
                schema=plan_schema,
                schema_name="research_plan",
                # 6000 et non 3000 : le plan porte desormais un champ de
                # plus par section (source_axis). A 3000, la reponse etait
                # tronquee en plein JSON ("Unterminated string at char
                # 10963"), tout le plan structure etait rejete et le
                # pipeline retombait sur le plan de secours — sans axes,
                # sans search_terms. Un depassement silencieux de ce genre
                # annule tout le travail de structuration en amont.
                max_tokens=cfg.plan_max_tokens_structured,
                temperature=cfg.plan_temperature,
            )
            # Conversion vers le format interne historique (sections=liste
            # de strings, section_targets=dict) attendu par tout le reste
            # du pipeline (Phase 2, _final_assembly, etc.) - zero
            # changement requis ailleurs dans le fichier.
            plan = {
                "complexity_analysis": structured["complexity_analysis"],
                "sections": [
                    s["name"] for s in structured["sections"] if not is_meta_section(s["name"])
                ],
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
                        "source_axis": s.get("source_axis"),
                    }
                    for s in structured["sections"]
                    if not is_meta_section(s["name"])
                },
                "narrative_flow": structured["narrative_flow"],
                "search_strategy": structured["search_strategy"],
            }
            if plan["sections"]:
                logger.info(
                    f"     → Plan créé (sortie structurée): {len(plan['sections'])} sections"
                )
                return plan
            # Toutes les sections ont ete filtrees comme meta : un plan
            # vide donnerait un rapport vide, on prefere le repli.
            fallback_reason = "sections_filtrees"
        else:
            # Repli non-structure pour un client sans generate_structured (ex: Albert)
            response = await svc.llm_client.generate(
                [{"role": "user", "content": prompt}],
                max_tokens=cfg.plan_max_tokens_unstructured,
                temperature=cfg.plan_temperature,
            )
            start_idx = response.find("{")
            if start_idx != -1:
                bracket_depth = 0
                for i, char in enumerate(response[start_idx:], start=start_idx):
                    if char == "{":
                        bracket_depth += 1
                    elif char == "}":
                        bracket_depth -= 1
                        if bracket_depth == 0:
                            plan = json.loads(response[start_idx : i + 1])
                            logger.info(
                                f"     → Plan créé (parsing manuel): {len(plan.get('sections', []))} sections"
                            )
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
