"""Etape 4.1.5 : enrichissement iteratif des sections faibles ou trop courtes."""

import json
import logging
from typing import Any, Dict

from app.research.context import ExecutionContext
from app.research.stages.services import StageServices
from app.research.stages.regenerate import regenerate_section
from app.research.stages.search import targeted_search

logger = logging.getLogger(__name__)


async def iterative_enrichment(
    svc: StageServices,
    query: str,
    ctx: ExecutionContext,
    report: Dict[str, Any],
    max_iterations: int = 2,
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
        quality_analysis = await analyze_report_quality(svc, query, report, ctx)

        overall_score = quality_analysis.get("overall_score")
        logger.info(
            f"  📊 Qualité globale: "
            f"{overall_score if overall_score is not None else 'non mesurée'}/100"
        )

        # Volume reel du rapport, mesure sans LLM. Sert de garde-fou au
        # score de qualite : un rapport quasi vide ne doit jamais pouvoir
        # sortir sur un bon score, quelle que soit la note rendue par
        # l'analyse (qui peut echouer, halluciner, ou noter le plan plutot
        # que le contenu).
        total_chars = sum(len(s.get("content", "") or "") for s in report.get("sections", []))
        n_sections = max(1, len(report.get("sections", [])))
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
        analysed_titles = {sa.get("title") for sa in quality_analysis.get("sections_analysis", [])}
        for sec in report.get("sections", []):
            if sec.get("title") in analysed_titles:
                continue  # traitée par la boucle ci-dessous
            content_length = len(sec.get("content", "") or "")
            if content_length < ctx.config.enrichment_min_section_chars:
                logger.warning(
                    f"  ⚠️  Section '{sec.get('title')}' non analysée et trop courte "
                    f"({content_length} chars) — enrichissement forcé"
                )
                weak_sections.append(
                    {
                        "title": sec.get("title"),
                        "score": 0,
                        "missing_elements": ["contenu absent ou insuffisant"],
                    }
                )

        for i, section_analysis in enumerate(quality_analysis.get("sections_analysis", [])):
            section_title = section_analysis.get("title")
            score = section_analysis.get("score", 100)

            # Trouver la section correspondante dans le rapport
            actual_section = None
            for sec in report.get("sections", []):
                if sec.get("title") == section_title:
                    actual_section = sec
                    break

            # Critères: score < 75 OU longueur < 2000 chars
            content_length = len(actual_section.get("content", "")) if actual_section else 0
            if (
                score < ctx.config.enrichment_weak_score
                or content_length < ctx.config.enrichment_min_section_chars
            ):
                weak_sections.append(section_analysis)
                if content_length < ctx.config.enrichment_min_section_chars:
                    logger.info(
                        f"  📏 Section '{section_title}' trop courte: {content_length} chars"
                    )

        if not weak_sections:
            logger.info("  ✅ Toutes les sections sont de qualité et longueur suffisantes")
            break

        logger.info(f"  🎯 {len(weak_sections)} section(s) à enrichir")
        ctx.diagnostics.note(
            "enrichissement.iteration",
            iteration=iteration + 1,
            weak_sections=len(weak_sections),
            regenerated_max=min(
                ctx.config.enrichment_max_sections_per_iteration, len(weak_sections)
            ),
            overall_score=overall_score,
        )

        # Enrichir les sections faibles (max 2 par itération)
        for section_analysis in weak_sections[: ctx.config.enrichment_max_sections_per_iteration]:
            section_title = section_analysis.get("title")
            missing = section_analysis.get("missing", [])

            logger.info(f"  🔍 Enrichissement: {section_title}")
            logger.info(f"    Manques: {', '.join(missing)}")

            # Recherche ciblée pour combler les manques
            enrichment_data = await targeted_search(svc, query, section_title, missing, ctx)

            # Régénérer la section avec les nouvelles données
            if enrichment_data:
                from app.research.enrichment_utils import count_citations

                _before = (
                    next(
                        (s for s in report.get("sections", []) if s.get("title") == section_title),
                        None,
                    )
                    or {}
                )
                updated_section = await regenerate_section(
                    svc, query, section_title, report, enrichment_data, ctx
                )
                if (
                    isinstance(updated_section, dict)
                    and updated_section is not _before
                    and updated_section.get("content") != _before.get("content")
                ):
                    updated_section = await _guarded(
                        svc, ctx, report, section_title, _before, updated_section, enrichment_data
                    )
                _after = updated_section if isinstance(updated_section, dict) else {}
                ctx.diagnostics.note(
                    "enrichissement.section_regeneree",
                    title=section_title,
                    chars_before=len(_before.get("content", "") or ""),
                    chars_after=len(_after.get("content", "") or ""),
                    citations_before=count_citations(_before.get("content")),
                    citations_after=count_citations(_after.get("content")),
                    unchanged=updated_section is _before,
                )

                # Remplacer la section dans le rapport (jamais par None :
                # une section introuvable ne doit pas vider son emplacement)
                if not isinstance(updated_section, dict):
                    continue
                for i, section in enumerate(report.get("sections", [])):
                    if section.get("title") == section_title:
                        report["sections"][i] = updated_section
                        break

    return report


async def _guarded(
    svc: StageServices,
    ctx: ExecutionContext,
    report: Dict[str, Any],
    section_title: str,
    before: Dict[str, Any],
    after: Dict[str, Any],
    enrichment_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Contrat de reecriture (app/research/guard.py) : la section regeneree
    remplace la precedente seulement si elle passe la verification."""
    from app.research.enrichment_utils import numbered_to_source
    from app.research.guard import accept_rewrite, sources_from

    text = numbered_to_source(str(after.get("content") or ""), report.get("bibliography") or [])
    sources = sources_from(before.get("data") or [], enrichment_data.get("sources") or [])
    if await accept_rewrite(svc.llm_client, ctx, f"enrichissement:{section_title}", text, sources):
        return after
    return before


async def analyze_report_quality(
    svc: StageServices, query: str, report: Dict[str, Any], ctx: ExecutionContext
) -> Dict[str, Any]:
    """Analyse la qualité de chaque section du rapport."""

    sections_text = []
    for section in report.get("sections", []):
        sections_text.append(f"## {section.get('title')}\n{section.get('content', '')}")

    prompt = f"""Analyse la qualité de ce rapport de recherche:

REQUÊTE: "{query}"

RAPPORT:
{chr(10).join(sections_text)[: ctx.config.enrichment_report_chars]}

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
        response = await svc.llm_client.generate(
            [{"role": "user", "content": prompt}],
            # 4000 et non 2000 : cette fonction REGENERE une section en
            # demandant explicitement un contenu "PLUS detaille que
            # l'original". Un plafond de 2000 jetons (~450 mots en
            # francais) coupait donc en pleine phrase les sections
            # qu'elle etait censee enrichir — meme defaut que la
            # synthese initiale, ou 30% des rapports finissaient sur un
            # mot tronque.
            max_tokens=ctx.config.enrichment_analysis_max_tokens,
            temperature=ctx.config.enrichment_analysis_temperature,
        )

        # Parser JSON
        start_idx = response.find("{")
        if start_idx != -1:
            bracket_depth = 0
            for i, char in enumerate(response[start_idx:], start=start_idx):
                if char == "{":
                    bracket_depth += 1
                elif char == "}":
                    bracket_depth -= 1
                    if bracket_depth == 0:
                        return json.loads(response[start_idx : i + 1])
    except Exception as e:
        logger.error(f"Erreur analyse qualité: {e}")
        ctx.diagnostics.note("enrichissement.analyse_echouee", error=repr(e))

    # Fallback : aucune mesure. Le score est absent (None), pas invente.
    ctx.diagnostics.note("enrichissement.score_absent")
    return {"overall_score": None, "sections_analysis": [], "gaps_to_fill": []}
