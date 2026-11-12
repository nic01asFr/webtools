"""Etape 4 : assemblage final du rapport, titre, resume, bibliographie et traces."""

import logging
from datetime import datetime
from typing import Any, Dict, Optional, Sequence

from app.research.context import ExecutionContext
from app.research.diagnostics import Diagnostics
from app.research.stages.services import StageServices


logger = logging.getLogger(__name__)


def report_title(query: str, context: ExecutionContext) -> str:
    """Titre du rapport (commun a l'assemblage final et au rapport partiel)."""
    title = f"Analyse: {query.title()}"
    if context.config.clean_title:
        from app.research.report_title import short_title

        title = short_title(query)
        if not title:
            title = f"Analyse: {query.title()}"
            context.diagnostics.note("assemblage.titre_repli", raison="consigne vide")
    return title


def apply_summary(report: Dict[str, Any], context: ExecutionContext) -> None:
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
        sentences = first.split(". ")[: context.config.summary_sentences]
        summ = (
            ". ".join(sentences).rstrip(".") + "."
            if sentences and first
            else "Rapport de recherche approfondie."
        )
        summ = _re.sub(r"\s*\[SOURCE:[^\]]*\]?", "", summ).strip()
        report["summary"] = summ or "Rapport de recherche approfondie."


def convert_sources_to_bibliography(
    svc: StageServices,
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
        validate_cited_urls=svc.config.validate_cited_urls,
        cited_only=svc.config.bibliography_cited_only,
        real_titles=svc.config.bibliography_real_titles,
        diagnostics=diagnostics,
        provided_urls=provided_urls,
    )
    logger.info(f"  Bibliographie generee: {len(report['bibliography'])} sources")
    return report


async def final_assembly(
    svc: StageServices, query: str, plan: Dict, context: ExecutionContext
) -> Dict[str, Any]:
    """
    PHASE 4.1: Assemblage final du rapport complet.

    Combine toutes les sections en rapport structuré.
    """
    logger.info("  📦 Assemblage final")

    sections_list = []
    total_words = 0

    for section_name in plan.get("sections", []):
        if section_name in context.final_content["sections"]:
            section_data = context.final_content["sections"][section_name]
            content = section_data.get("content", "")
            word_count = len(content.split())
            total_words += word_count

            sections_list.append(
                {
                    "title": section_name,
                    "content": content,
                    "data": section_data.get("raw_data", []),
                    "metadata": {
                        "word_count": word_count,
                        "sources_count": len(section_data.get("raw_data", [])),
                    },
                }
            )

    # Générer titre et summary
    complexity = plan.get("complexity_analysis", {})
    title = report_title(query, context)

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
            "target_length": complexity.get("target_length", "standard"),
        },
    }

    # Conversion sources → bibliographie (journal de la requete transmis :
    # sans lui les notes citations.* sont perdues)
    final_report = convert_sources_to_bibliography(
        svc, final_report, getattr(context, "diagnostics", None)
    )

    # Comptabiliser les sections perdues par echec LLM. Sans cela, un
    # rapport dont TOUTES les sections ont echoue sortait avec
    # success=true et 88 mots (5 messages d'erreur) : l'appelant ne
    # pouvait pas distinguer un sujet pauvre d'une panne d'infra, et un
    # run de benchmark aurait mesure la disponibilite du LLM plutot que
    # la qualite du pipeline.
    failed = [s["title"] for s in sections_list if "[SYNTHESE_ECHOUEE]" in (s.get("content") or "")]
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
        s["title"] for s in sections_list if "[SECTION_SANS_SOURCE]" in (s.get("content") or "")
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
        s
        for s in sections_list
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
    apply_summary(final_report, context)

    logger.info(f"  ✓ Rapport assemblé: {total_words} mots, {len(sections_list)} sections")

    return final_report


def add_complete_traces(
    report: Dict[str, Any], context: ExecutionContext, exploration_data: Dict, plan: Dict
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
            "topics_identified": exploration_data.get("topics_found", []),
        },
        "planning_phase": {
            "complexity_analysis": plan.get("complexity_analysis", {}),
            "sections_planned": plan.get("sections", []),
            "narrative_flow": plan.get("narrative_flow", []),
            "search_strategy": plan.get("search_strategy", {}),
        },
        "construction_phase": {
            "sections_built": list(context.final_content["sections"].keys()),
            "total_sources_collected": sum(
                len(s.get("raw_data", [])) for s in context.final_content["sections"].values()
            ),
            "steps_executed": len(context.steps),
        },
        "coherence_phase": {"improvements_applied": "See coherence analysis"},
    }

    logger.info("  ✓ Traces complètes ajoutées")

    return report


def strip_source_markers(final_answer: Dict[str, Any]) -> None:
    """Retire du rapport les marqueurs [SOURCE:...] residuels (modifie en place)."""
    # Nettoyage FINAL des marqueurs residuels, apres toutes les etapes.
    # Le filet pose dans _final_assembly ne suffit pas : l'enrichissement
    # iteratif puis cette conversion s'executent APRES lui et peuvent
    # reintroduire un resume non nettoye. Ici, plus rien ne s'execute
    # ensuite — c'est le dernier point ou agir.
    import re as _re_final

    _marker = _re_final.compile(r"\s*\[SOURCE:[^\]]*\]?")
    for _k in ("summary", "introduction", "conclusion"):
        if isinstance(final_answer.get(_k), str):
            final_answer[_k] = _marker.sub("", final_answer[_k]).strip()
    for _s in final_answer.get("sections") or []:
        if isinstance(_s.get("content"), str) and "[SOURCE:" in _s["content"]:
            _s["content"] = _marker.sub("", _s["content"]).strip()
