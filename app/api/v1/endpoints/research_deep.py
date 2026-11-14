"""
/api/v1/research/deep - Recherche approfondie

Produit un rapport complet, dossier d'etude ou synthese exhaustive.
Utilise Intelligent Orchestrator pour autonomie maximale.

Contrat de la requete : chaque champ accepte a un effet reel sur le pipeline,
et le modele refuse tout champ inconnu (422). Les champs qui n'avaient aucun
effet (max_steps, max_results, min_sources, date_range, include_data,
include_reports, include_academic, sources.suggested, include_charts,
include_timeline, output_format.structure) ont ete retires du schema : une
requete qui les envoie recoit 422 au lieu d'etre acceptee en silence.
"""

import dataclasses
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agents.intelligent_orchestrator import IntelligentOrchestrator
from app.core.execution_tracer import ExecutionTracer, StepStatus
from app.core.llm import get_llm_client
from app.core.output_formatter import OutputFormatter
from app.core.source_registry import SourcePriority, SourceRegistry, SourceStatus
from app.research.pipeline_config import PipelineConfig

logger = logging.getLogger(__name__)
router = APIRouter()


# === MODELES DE REQUETE ===

class _StrictModel(BaseModel):
    """Refuse les champs inconnus : un champ ignore en silence est un mensonge."""

    model_config = ConfigDict(extra="forbid")


class DeepResearchContext(_StrictModel):
    language: str = Field(
        default="fr",
        description="Langue preferee, transmise au modele qui planifie le rapport",
    )


class SourcesConstraints(_StrictModel):
    required: List[str] = Field(
        default=[],
        description=(
            "URLs OBLIGATOIRES. Chacune est extraite avant la redaction (meme hors de "
            "domains_whitelist ou dans exclusions : la demande explicite prime), decoupee en "
            "passages et proposee a toutes les sections ; la pertinence decide ou elle sert. "
            "Une URL non extraite rend le rapport degraded et est listee dans "
            "required_sources_failed (url, reason). Une URL extraite mais absente de la "
            "bibliographie est listee dans required_sources_uncited, sans degrader le rapport. "
            "Bilan global : execution.summary.required_sources_status (none_required, "
            "all_success, partial_failure)."
        ),
    )
    exclusions: List[str] = Field(default=[], description="Domaines a EXCLURE (wildcards OK)")
    domains_whitelist: List[str] = Field(default=[], description="Si fourni, UNIQUEMENT ces domaines")


class OutputFormat(_StrictModel):
    sections: List[str] = Field(
        default=[],
        description=(
            "Sections IMPOSEES : le rapport contient exactement ces sections, avec ces titres "
            "et dans cet ordre (comparaison des titres insensible a la casse et aux espaces). "
            "Une section sans matiere exploitable est retiree et signalee dans "
            "unsourced_sections, comme toute section. Vide = sections choisies par le modele."
        ),
    )
    include_section_data: bool = Field(
        default=False,
        description="Joindre a chaque section les passages extraits (champ data, texte integral)",
    )


class DeepResearchRequest(_StrictModel):
    # 500 caracteres etait une limite arbitraire : une vraie consigne de
    # recherche est souvent detaillee et multi-dimensionnelle (les taches de
    # DeepResearch Bench vont jusqu'a ~1000 caracteres). Le pipeline les
    # traite correctement depuis que les requetes de moteur sont formulees
    # par le LLM plutot que par concatenation de la consigne. 4000 laisse de
    # la marge tout en bornant l'entree.
    topic: str = Field(..., description="Sujet de la recherche approfondie", min_length=5, max_length=4000)
    objectives: List[str] = Field(
        default=[],
        description="Liste d'objectifs pour guider la recherche"
    )
    context: Optional[DeepResearchContext] = Field(default=None, description="Contexte de recherche")
    sources: Optional[SourcesConstraints] = Field(default=None, description="Contraintes sur les sources")
    output_format: Optional[OutputFormat] = Field(default=None, description="Format de sortie")
    # 1 200 s par defaut depuis le 13/11/2026 : avec la verification des
    # sections, des recherches approfondies mesurees depassent 1 000 s.
    timeout: int = Field(
        default=1200, ge=60, le=1200,
        description="Delai global en secondes : passe ce delai, la reponse porte les sections deja redigees avec degraded=true",
    )
    max_sources_per_section: Optional[int] = Field(
        default=None, ge=1, le=20,
        description="Plafond de sources par section (recherche et extraction) ; absent = selon la profondeur de la section",
    )

    # Raccourci pour retrocompatibilite
    output_sections: Optional[List[str]] = Field(default=None, description="Raccourci pour output_format.sections")


# === CONSTRUCTION DE LA REPONSE ===

def build_report(answer: Dict[str, Any], include_section_data: bool) -> Dict[str, Any]:
    """Rapport de la reponse : ce que l'orchestrateur a produit, sans valeur inventee.

    Le champ data des sections (passages extraits, texte integral) n'est
    joint que sur demande (C24).
    """
    sections: List[Dict[str, Any]] = []
    for sec in answer.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        item: Dict[str, Any] = {
            "title": sec.get("title", ""),
            "content": sec.get("content", ""),
        }
        if sec.get("metadata") is not None:
            item["metadata"] = sec["metadata"]
        if include_section_data:
            item["data"] = sec.get("data") or []
        sections.append(item)

    report: Dict[str, Any] = {
        "title": answer.get("title", ""),
        "summary": answer.get("summary", ""),
        "sections": sections,
        "bibliography": [b for b in (answer.get("bibliography") or []) if isinstance(b, dict)],
        "metadata": answer.get("metadata") or {},
    }
    if answer.get("research_traces") is not None:
        report["research_traces"] = answer["research_traces"]
    return report


def _degradation_error(
    failed: List[str],
    unsourced: List[str],
    missing: List[str],
    timed_out: bool,
    deadline: int,
    plan_fallback_reason: Optional[str] = None,
    required_failed: Optional[List[Dict[str, str]]] = None,
    sections_imposed: bool = False,
    pipeline_error: Optional[str] = None,
) -> Optional[str]:
    """Message de l'erreur d'un rapport degrade, une cause par message.

    Un echec LLM est un incident technique (a reessayer), une absence de
    source est un constat sur le sujet (inutile de reessayer a l'identique),
    un delai depasse se regle par le parametre timeout : les confondre ferait
    chercher une panne la ou il n'y en a pas.
    """
    parts: List[str] = []
    if pipeline_error:
        parts.append(
            f"pipeline interrompu par une erreur ({pipeline_error}) : rapport partiel, "
            f"{len(missing)} section(s) non redigee(s)"
        )
    if timed_out:
        parts.append(
            f"delai global de {deadline} s depasse : rapport partiel, "
            f"{len(missing)} section(s) non redigee(s)"
        )
    if failed:
        parts.append(f"{len(failed)} section(s) non generee(s) : echec repete de l'appel LLM de synthese")
    if unsourced:
        parts.append(
            f"{len(unsourced)} section(s) sans source exploitable : la collecte n'a rien remonte sur ce sujet"
        )
    if plan_fallback_reason:
        # Avec des sections imposees, le repli les conserve (V4-4) : seules
        # leurs cibles (objectifs, requetes) sont generiques.
        consequence = (
            "les sections imposees sont conservees mais leurs objectifs et requetes sont generiques"
            if sections_imposed
            else "la structure du rapport ne suit pas la consigne"
        )
        parts.append(f"plan generique de repli utilise ({plan_fallback_reason}) : {consequence}")
    if required_failed:
        listing = ", ".join(f"{f.get('url')} ({f.get('reason')})" for f in required_failed)
        parts.append(f"{len(required_failed)} source(s) requise(s) non extraite(s) : {listing}")
    return " ; ".join(parts) if parts else None


def _json_response(body: Dict[str, Any], processing_time: float) -> JSONResponse:
    """Reponse JSON unique, pour le succes comme pour l'echec (C66).

    JSONResponse plutot que StreamingResponse : le corps est entierement
    construit avant l'envoi, le decouper en morceaux n'apportait rien et
    donnait deux types de reponse selon le chemin. Le code HTTP reste 200 :
    l'echec metier se lit dans success/error, avec la meme forme de corps.
    """
    return JSONResponse(
        content=body,
        headers={
            "X-Processing-Time": f"{processing_time:.2f}s",
            "Cache-Control": "no-cache",
        },
    )


async def create_orchestrator(config: PipelineConfig, section_budget_timeout: int) -> IntelligentOrchestrator:
    """Construit l'orchestrateur de production (point d'injection des tests)."""
    llm_client = await get_llm_client()
    # Rejeu de la collecte (V4-3) : actif seulement si WEBTOOLS_REPLAY_MODE est
    # pose ; la recherche et l'extraction sont alors enregistrees ou rejouees.
    from app.manager import ExtractorManager
    from app.research.replay import ReplayPlanLLM, build_replay_clients, read_replay_env
    from app.services.search_service import searxng_client

    mode, directory = read_replay_env()
    if mode:
        search_client, extractor_manager = build_replay_clients(
            mode, directory, searxng_client, ExtractorManager()
        )
        return IntelligentOrchestrator(
            ReplayPlanLLM(mode, directory, llm_client),
            timeout=section_budget_timeout,
            config=config,
            search_client=search_client,
            extractor_manager=extractor_manager,
        )
    return IntelligentOrchestrator(llm_client, timeout=section_budget_timeout, config=config)


def _register_sources(
    registry: SourceRegistry,
    sources: SourcesConstraints,
    exec_ctx: Dict[str, Any],
    answer: Optional[Dict[str, Any]],
) -> Dict[str, int]:
    """Alimente le registre : sources requises, puis sources decouvertes.

    Une source est utilisee (SUCCESS) quand son numero [n] figure dans le
    texte final du rapport. La bibliographie seule ne suffit pas : avec
    bibliography_cited_only=False elle garde toutes les sources des passages,
    citees ou non. Une source requise suit la meme regle : sa simple presence
    dans le texte du rapport (data, research_traces) ne vaut pas utilisation.
    """
    from app.research.citations import cited_bibliography_urls

    cited_urls = cited_bibliography_urls(answer or {})

    failed_reasons = {
        f.get("url"): f.get("reason")
        for f in ((answer or {}).get("required_sources_failed") or [])
        if isinstance(f, dict)
    }
    uncited = set((answer or {}).get("required_sources_uncited") or [])
    for url in sources.required:
        if url in failed_reasons:
            registry.add_website(
                url=url,
                status=SourceStatus.FAILED,
                priority=SourcePriority.REQUIRED,
                reason=failed_reasons[url],
            )
            continue
        used = url in cited_urls and url not in uncited
        registry.add_website(
            url=url,
            status=SourceStatus.SUCCESS if used else SourceStatus.DISCOVERED_NOT_USED,
            priority=SourcePriority.REQUIRED,
        )

    discovered = exec_ctx.get("sources_discovered") or []
    used_count = 0
    for url in discovered:
        if registry.has_source(url):
            continue
        used = url in cited_urls
        used_count += 1 if used else 0
        registry.add_website(
            url=url,
            status=SourceStatus.SUCCESS if used else SourceStatus.DISCOVERED_NOT_USED,
            priority=SourcePriority.AUTO_DISCOVERED,
        )
    return {"discovered": len(discovered), "used": used_count, "required": len(sources.required)}


# === ENDPOINT ===

@router.post("/research/deep")
async def research_deep(request: DeepResearchRequest):
    """
    Recherche approfondie - Rapport complet

    Workflow:
    1. EXPLORATION et plan detaille par sections
    2. RECHERCHE et EXTRACTION par section (plafond: max_sources_per_section)
    3. SYNTHESE, coherence, enrichissement et bibliographie

    La reponse a toujours la meme forme (success, degraded, result,
    execution, sources, diagnostics), y compris en cas d'echec ou de delai
    depasse (timeout) : la trace et les sources sont alors conservees.
    """
    start_time = time.time()
    logger.info(f"/research/deep: '{request.topic}'")

    tracer = ExecutionTracer()
    registry = SourceRegistry()
    sources = request.sources or SourcesConstraints()
    context = request.context or DeepResearchContext()
    output_format = request.output_format or OutputFormat()
    sections = request.output_sections or output_format.sections

    formatter_kwargs: Dict[str, Any] = {}
    exec_ctx: Dict[str, Any] = {}
    diagnostics: Optional[Dict[str, Any]] = None
    answer: Optional[Dict[str, Any]] = None
    timed_out = False
    error: Optional[str] = None
    # Erreur d'un pipeline interrompu qui rend tout de meme un rapport partiel.
    pipeline_error: Optional[str] = None

    try:
        config = PipelineConfig.from_env()
        if request.max_sources_per_section is not None:
            config = dataclasses.replace(
                config, max_sources_per_section=request.max_sources_per_section
            )

        diagnostics = {
            "config": config.to_dict(),
            "events": {"counts": {}, "events": [], "events_dropped": 0},
            "invariants": None,
        }

        orchestrator_context = {
            "topic": request.topic,
            "objectives": request.objectives,
            "language": context.language,
            "sources_required": sources.required,
            "sources_exclusions": sources.exclusions,
            "domains_whitelist": sources.domains_whitelist,
            "output_sections": sections,
        }

        with tracer.step("intelligent_orchestration", "intelligent_orchestrator") as step:
            step.set_input({
                "topic": request.topic,
                "objectives": request.objectives,
                "timeout": request.timeout,
                "max_sources_per_section": request.max_sources_per_section,
                "sources_required": sources.required,
            })
            # Le plafond par section ne depasse pas le comportement historique
            # (240 s) et reste sous le delai global.
            orchestrator = await create_orchestrator(config, min(300, int(request.timeout * 0.6)))
            result = await orchestrator.execute_with_deadline(
                request.topic, orchestrator_context, float(request.timeout)
            )
            exec_ctx = result.get("execution_context") or {}
            diagnostics = result.get("diagnostics") or diagnostics
            from app.research.replay import replay_summary

            replay = replay_summary(
                getattr(orchestrator, "search_client", None),
                getattr(orchestrator, "extractor_manager", None),
                getattr(orchestrator, "llm_client", None),
            )
            if replay is not None and isinstance(diagnostics, dict):
                diagnostics["replay"] = replay
            answer = result.get("answer")
            timed_out = bool(result.get("timed_out"))
            error = result.get("error")
            step.set_output({
                "success": result.get("success"),
                "timed_out": timed_out,
                "steps_executed": exec_ctx.get("steps_executed", 0),
            })
            if not result.get("success") and isinstance(answer, dict):
                pipeline_error = error
            if not result.get("success") or not isinstance(answer, dict):
                step.status = StepStatus.FAILED
                step.error = error or "reponse de l'orchestrateur inexploitable"
                error = error or "Research failed"
    except Exception as e:  # noqa: BLE001 - l'echec est rendu dans la reponse, avec trace et sources
        logger.error(f"/research/deep error: {e}", exc_info=True)
        error = f"{type(e).__name__}: {e}"

    with tracer.step("source_extraction", "context_parser") as step:
        counts = _register_sources(registry, sources, exec_ctx, answer if isinstance(answer, dict) else None)
        step.set_output(counts)

    processing_time = time.time() - start_time

    if not isinstance(answer, dict):
        formatter = OutputFormatter(tracer, registry, processing_time)
        body = formatter.format_deep_research_output(
            topic=request.topic,
            report=None,
            execution_context=exec_ctx,
            success=False,
            error=error or "Research failed",
            diagnostics=diagnostics,
        )
        return _json_response(body, processing_time)

    report = build_report(answer, output_format.include_section_data)
    failed = list(answer.get("failed_sections") or [])
    unsourced = list(answer.get("unsourced_sections") or [])
    missing = list(answer.get("missing_sections") or [])
    required_failed = [
        f for f in (answer.get("required_sources_failed") or []) if isinstance(f, dict)
    ]
    required_uncited = list(answer.get("required_sources_uncited") or [])
    degraded = bool(answer.get("degraded")) or timed_out or bool(pipeline_error)
    if degraded:
        formatter_kwargs["error"] = _degradation_error(
            failed, unsourced, missing, timed_out, request.timeout,
            plan_fallback_reason=answer.get("plan_fallback_reason"),
            required_failed=required_failed,
            sections_imposed=bool(sections),
            pipeline_error=pipeline_error,
        )
        logger.error(
            f"Rapport DEGRADE : {len(failed)} echec(s) LLM, {len(unsourced)} section(s) sans source, "
            f"{len(missing)} manquante(s), delai depasse: {timed_out}"
        )

    formatter = OutputFormatter(tracer, registry, processing_time)
    body = formatter.format_deep_research_output(
        topic=request.topic,
        report=report,
        execution_context=exec_ctx,
        success=not degraded,
        degraded=degraded,
        timed_out=timed_out,
        failed_sections=failed,
        unsourced_sections=unsourced,
        missing_sections=missing,
        diagnostics=diagnostics,
        required_sources_failed=required_failed,
        required_sources_uncited=required_uncited,
        **formatter_kwargs,
    )
    logger.info(f"/research/deep termine en {processing_time:.2f}s, degraded={degraded}")
    return _json_response(body, processing_time)
