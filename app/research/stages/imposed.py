"""Sections imposees (output_format.sections) : normalisation deterministe du plan.

Le modele de plan recoit les sections imposees (prompt et schema), mais rien
n'oblige un modele a s'y tenir : cette etape garantit le resultat. Le plan rendu
contient exactement les sections demandees, dans l'ordre demande, avec les
titres demandes.
"""

from typing import Any

from app.research.context import ExecutionContext
from app.research.stages.queries import build_search_query
from app.research.stages.services import StageServices


def normalize_title(title: Any) -> str:
    """Forme de comparaison d'un titre : insensible a la casse et aux espaces."""
    return " ".join(str(title or "").split()).casefold()


def imposed_sections_from_context(context: Any) -> list[str]:
    """Titres imposes du contexte de l'appelant, nettoyes et dedoublonnes."""
    raw = context.get("output_sections") if isinstance(context, dict) else None
    titles: list[str] = []
    seen: set = set()
    for item in raw or []:
        title = " ".join(str(item or "").split())
        key = normalize_title(title)
        if title and key not in seen:
            seen.add(key)
            titles.append(title)
    return titles


def enforce_imposed_sections(
    svc: StageServices,
    ctx: ExecutionContext | None,
    query: str,
    plan: dict[str, Any],
    imposed: list[str],
) -> dict[str, Any]:
    """Rend un plan dont les sections sont exactement `imposed`, dans cet ordre.

    Sections du modele retenues (avec leur cible) si leur nom correspond ;
    cible par defaut pour une section manquante ; sections non demandees
    retirees. Ecarts notes dans les diagnostics (plan.sections_imposees).
    """
    if not imposed:
        return plan
    cfg = ctx.config if ctx is not None else svc.config

    llm_names: list[str] = [str(n) for n in plan.get("sections", [])]
    llm_targets: dict[str, dict[str, Any]] = plan.get("section_targets", {}) or {}
    by_key = {normalize_title(n): n for n in llm_names}
    imposed_keys = {normalize_title(t) for t in imposed}

    added: list[str] = []
    renamed: list[dict[str, str]] = []
    targets: dict[str, dict[str, Any]] = {}
    for title in imposed:
        original = by_key.get(normalize_title(title))
        if original is not None:
            target = dict(llm_targets.get(original) or {})
            if original != title:
                renamed.append({"from": original, "to": title})
            targets[title] = target
            continue
        added.append(title)
        targets[title] = {
            "words_target": cfg.plan_fallback_analysis_words,
            "depth": "moderate",
            "objectives": [f"Traiter la section imposee : {title}"],
            "key_questions": [],
            "search_terms": [build_search_query(svc, query=query, section_name=title)],
            "source_axis": "generaliste",
        }
    removed = [n for n in llm_names if normalize_title(n) not in imposed_keys]

    new_names = {normalize_title(t): t for t in imposed}
    flow: list[dict[str, Any]] = []
    for step in plan.get("narrative_flow") or []:
        src = new_names.get(normalize_title(step.get("from_section")))
        dst = new_names.get(normalize_title(step.get("to_section")))
        if src and dst:
            flow.append({**step, "from_section": src, "to_section": dst})

    result = dict(plan)
    result["sections"] = list(imposed)
    result["section_targets"] = targets
    result["narrative_flow"] = flow
    if ctx is not None and (added or removed or renamed):
        ctx.diagnostics.note(
            "plan.sections_imposees", ajoutees=added, retirees=removed, renommees=renamed
        )
    return result
