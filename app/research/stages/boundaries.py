"""Frontieres typees entre les etapes : validation des donnees echangees.

Chaque fonction valide la sortie d'une etape contre les modeles de
app.research.models et note l'ecart dans le journal de la requete ; les dict
continuent de circuler (aucun changement de comportement).
"""

from typing import Any, Dict, List, Optional

from app.research.context import ExecutionContext
from app.research.models import Passage, Plan, Report, SectionDraft, check_boundary


def check_plan(ctx: ExecutionContext, plan: Dict[str, Any]) -> Optional[Plan]:
    """Sortie de l'etape plan."""
    return check_boundary(Plan, plan, ctx.diagnostics, "plan")


def check_collected(ctx: ExecutionContext, extracted_by_section: Dict[str, List[Dict]]) -> None:
    """Sortie de l'etape collecte : les passages de chaque section."""
    for name, passages in extracted_by_section.items():
        for passage in passages:
            if check_boundary(Passage, passage, ctx.diagnostics, f"collecte:{name}") is None:
                break  # une note par section suffit


def check_drafts(ctx: ExecutionContext) -> None:
    """Sortie de l'etape synthese : les sections redigees du contexte."""
    for name, draft in ctx.final_content["sections"].items():
        check_boundary(SectionDraft, draft, ctx.diagnostics, f"synthese:{name}")


def check_report(ctx: ExecutionContext, report: Dict[str, Any]) -> Optional[Report]:
    """Sortie de l'etape assemblage."""
    return check_boundary(Report, report, ctx.diagnostics, "assemblage")
