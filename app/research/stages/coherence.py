"""Etape 3 : revue de coherence inter-sections et application des ameliorations."""

import json
import logging
from typing import Any, Dict

from app.research.context import ExecutionContext
from app.research.stages.services import StageServices


logger = logging.getLogger(__name__)


async def analyze_intersections(
    svc: StageServices, query: str, plan: Dict, context: ExecutionContext
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
    for section_name, section_data in context.final_content["sections"].items():
        sections_content.append(
            {
                "title": section_name,
                "content_preview": section_data.get("content", "")[
                    : context.config.coherence_preview_chars
                ],
                "word_count": len(section_data.get("content", "").split()),
                "sources_count": len(section_data.get("raw_data", [])),
            }
        )

    prompt = f"""Analyse la cohérence globale de ce rapport en cours de construction.

REQUÊTE INITIALE: "{query}"

SECTIONS GÉNÉRÉES:
{json.dumps(sections_content, indent=2, ensure_ascii=False)}

ENCHAÎNEMENTS PRÉVUS:
{json.dumps(plan.get("narrative_flow", []), indent=2)}

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
                        "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["type", "between_sections", "issue", "suggestion", "priority"],
                    "additionalProperties": False,
                },
            },
            "redundancies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sections": {"type": "array", "items": {"type": "string"}},
                        "description": {"type": "string"},
                    },
                    "required": ["sections", "description"],
                    "additionalProperties": False,
                },
            },
            "coherence_score": {"type": "integer", "minimum": 0, "maximum": 100},
        },
        "required": ["improvements", "redundancies", "coherence_score"],
        "additionalProperties": False,
    }

    try:
        if svc.llm_client.supports_structured:
            analysis = await svc.llm_client.generate_structured(
                [{"role": "user", "content": prompt}],
                schema=intersections_schema,
                schema_name="coherence_analysis",
                max_tokens=context.config.coherence_max_tokens,
                temperature=context.config.coherence_temperature,
            )
            return analysis
        else:
            response = await svc.llm_client.generate(
                [{"role": "user", "content": prompt}],
                max_tokens=context.config.coherence_max_tokens,
                temperature=context.config.coherence_temperature,
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
                            analysis = json.loads(response[start_idx : i + 1])
                            return analysis

    except Exception as e:
        logger.error(f"  ❌ Erreur analyse cohérence: {e}")
        context.diagnostics.note("coherence.analyse_echouee", error=repr(e))

    # Aucune mesure : le score est absent (None), pas invente.
    context.diagnostics.note("coherence.score_absent")
    return {"improvements": [], "redundancies": [], "coherence_score": None}


async def apply_coherence_improvements(coherence_analysis: Dict, context: ExecutionContext) -> None:
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
            "coherence.append_desactive",
            suggestions_ignorees=len(high_priority[: context.config.coherence_max_improvements]),
        )
        return

    logger.info(f"  🔧 Application de {len(high_priority)} améliorations")

    for improvement in high_priority[
        : context.config.coherence_max_improvements
    ]:  # plafond d'ameliorations
        imp_type = improvement.get("type")
        sections_involved = improvement.get("between_sections", [])
        suggestion = improvement.get("suggestion", "")

        if imp_type == "transition" and len(sections_involved) == 2:
            # Ajouter transition entre deux sections
            section_a, section_b = sections_involved
            if (
                section_a in context.final_content["sections"]
                and section_b in context.final_content["sections"]
            ):
                # Ajouter phrase de transition à la fin de section A
                current_content = context.final_content["sections"][section_a]["content"]
                transition_text = f"\n\n{suggestion}"
                context.final_content["sections"][section_a]["content"] = (
                    current_content + transition_text
                )
                context.diagnostics.note(
                    "coherence.transition_collee",
                    section=section_a,
                    suggestion_chars=len(suggestion or ""),
                    chars_before=len(current_content or ""),
                    chars_after=len(current_content or "") + len(transition_text),
                )
                logger.info(f"     → Transition ajoutée: {section_a} → {section_b}")
