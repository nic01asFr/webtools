"""
/api/v1/research/deep - Recherche approfondie

Produit un rapport complet, dossier d'étude ou synthèse exhaustive.
Utilise Intelligent Orchestrator pour autonomie maximale.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import logging
import time
import json

from app.core.llm import get_llm_client
from app.agents.intelligent_orchestrator import IntelligentOrchestrator
from app.core.execution_tracer import ExecutionTracer, ActionType
from app.core.source_registry import SourceRegistry, SourceStatus, SourcePriority
from app.core.output_formatter import OutputFormatter

logger = logging.getLogger(__name__)
router = APIRouter()


# === REQUEST / RESPONSE MODELS ===

class DeepResearchContext(BaseModel):
    language: str = Field(default="fr", description="Langue préférée")
    date_range: Optional[str] = Field(default=None, description="week, month, year")
    min_sources: int = Field(default=10, ge=1, le=100, description="Nombre minimum de sources")
    include_data: bool = Field(default=True, description="Chercher datasets")
    include_reports: bool = Field(default=True, description="Chercher rapports officiels")
    include_academic: bool = Field(default=True, description="Chercher articles académiques")


class SourcesConstraints(BaseModel):
    required: List[str] = Field(default=[], description="URLs OBLIGATOIRES (bloquant si échec)")
    suggested: List[str] = Field(default=[], description="URLs à prioriser")
    exclusions: List[str] = Field(default=[], description="Domaines à EXCLURE (wildcards OK)")
    domains_whitelist: List[str] = Field(default=[], description="Si fourni, UNIQUEMENT ces domaines")


class OutputFormat(BaseModel):
    structure: str = Field(
        default="report",
        description="report, summary, qa, data_analysis"
    )
    sections: List[str] = Field(
        default=[],
        description="Sections du rapport (auto si vide)"
    )
    include_charts: bool = Field(default=False, description="Générer graphiques si données")
    include_timeline: bool = Field(default=False, description="Timeline des événements")


class DeepResearchRequest(BaseModel):
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
    max_steps: int = Field(default=15, ge=1, le=30, description="Budget d'exploration")
    timeout: int = Field(default=600, ge=60, le=1200, description="Timeout total en secondes")

    # Raccourcis pour rétrocompatibilité
    output_sections: Optional[List[str]] = Field(default=None, description="Raccourci pour output_format.sections")
    max_results: Optional[int] = Field(default=None, description="Raccourci pour max_steps")


class ReportSection(BaseModel):
    title: str
    content: str
    data: Optional[List[Dict[str, Any]]] = None
    sources: Optional[List[Dict[str, Any]]] = None


class BibliographyItem(BaseModel):
    title: str
    url: str
    type: str  # official_report, academic, api, website
    date: Optional[str] = None


class DatasetUsed(BaseModel):
    name: str
    url: str
    records: int
    processing: Optional[str] = None


class Report(BaseModel):
    summary: str
    sections: List[ReportSection]
    bibliography: List[BibliographyItem]
    datasets_used: Optional[List[DatasetUsed]] = None


class ExecutionSummary(BaseModel):
    steps_executed: int
    sources_consulted: int
    apis_used: Optional[List[str]] = None
    data_processing: Optional[Dict[str, Any]] = None
    completeness: Optional[int] = None  # Percentage
    confidence: str  # high, medium, low


class DeepResearchResponse(BaseModel):
    success: bool
    topic: str
    report: Optional[Report] = None
    execution_summary: Optional[ExecutionSummary] = None
    processing_time: float
    error: Optional[str] = None


# === ENDPOINT ===

@router.post("/research/deep")
async def research_deep(request: DeepResearchRequest):
    """
    Recherche approfondie - Rapport complet

    Workflow:
    1. ANALYSIS: Décompose topic en sous-questions
    2. DISCOVERY: Multi-stratégies parallèles (web + APIs + datasets)
    3. COLLECTION: Routing automatique (API/Site/Page)
    4. PROCESSING: DataProcessor (filtres, agrégation, jointures)
    5. ENRICHMENT: Boucle adaptative (complétude < 90%)
    6. SYNTHESIS: Génération rapport structuré
    """
    start_time = time.time()

    logger.info(f"📚 /research/deep: '{request.topic}'")
    logger.info(f"   Objectives: {len(request.objectives)} provided")
    if request.sources:
        logger.info(f"   Sources constraints: required={len(request.sources.required)}, suggested={len(request.sources.suggested)}")

    # Initialiser tracer et registry
    tracer = ExecutionTracer()
    registry = SourceRegistry()

    try:
        llm_client = await get_llm_client()

        context = request.context or DeepResearchContext()
        sources = request.sources or SourcesConstraints()
        output_format = request.output_format or OutputFormat()

        # Appliquer raccourcis de rétrocompatibilité
        if request.output_sections:
            output_format.sections = request.output_sections
        if request.max_results:
            request.max_steps = request.max_results

        # Préparer context pour Intelligent Orchestrator
        orchestrator_context = {
            "topic": request.topic,
            "objectives": request.objectives,
            "language": context.language,
            "date_range": context.date_range,
            "min_sources": context.min_sources,
            "include_data": context.include_data,
            "include_reports": context.include_reports,
            "include_academic": context.include_academic,
            "sources_required": sources.required,
            "sources_suggested": sources.suggested,
            "sources_exclusions": sources.exclusions,
            "domains_whitelist": sources.domains_whitelist,
            "output_structure": output_format.structure,
            "output_sections": output_format.sections,
            "include_charts": output_format.include_charts,
            "include_timeline": output_format.include_timeline
        }

        # Utiliser Intelligent Orchestrator
        logger.info("🤖 Launching Intelligent Orchestrator")

        with tracer.step("intelligent_orchestration", "intelligent_orchestrator") as step:
            step.set_input({
                "topic": request.topic,
                "objectives": request.objectives,
                "max_steps": request.max_steps,
                "sources_required": sources.required,
                "sources_suggested": sources.suggested
            })

            orchestrator = IntelligentOrchestrator(llm_client)

            result = await orchestrator.execute_intelligent_research(
                query=request.topic,
                context=orchestrator_context,
                max_steps=request.max_steps
            )

            step.set_output({
                "success": result.get("success"),
                "steps_executed": result.get("execution_context", {}).get("steps_executed", 0)
            })

            if not result.get("success"):
                processing_time = time.time() - start_time
                formatter = OutputFormatter(tracer, registry, processing_time)
                return formatter.format_deep_research_output(
                    topic=request.topic,
                    report=None,
                    execution_context=None,
                    success=False,
                    error=result.get("error", "Research failed")
                )

            # Extraire réponse de l'orchestrator
            answer = result.get("answer", {})
            exec_ctx = result.get("execution_context", {})

        # === EXTRACTION DES SOURCES DEPUIS ORCHESTRATOR ===
        # L'orchestrator a déjà collecté les sources, on les extrait pour le registry
        logger.info("📋 Extracting sources from execution context")

        with tracer.step("source_extraction", "context_parser") as step:
            # Extraire APIs
            apis_used = exec_ctx.get("apis_used", [])
            for api_url in apis_used:
                registry.add_api(
                    url=api_url,
                    status=SourceStatus.SUCCESS,
                    priority=SourcePriority.SUGGESTED
                )

            # Extraire datasets depuis execution context
            datasets_count = exec_ctx.get("datasets_collected", 0)

            # Extraire sources découvertes par l'orchestrateur (SearXNG)
            sources_discovered = exec_ctx.get("sources_discovered", [])
            logger.info(f"📊 Sources découvertes: {len(sources_discovered)}")

            for url in sources_discovered:
                registry.add_website(
                    url=url,
                    status=SourceStatus.DISCOVERED_NOT_USED,
                    priority=SourcePriority.AUTO_DISCOVERED
                )

            # Extraire les données collectées pour identifier les sources réellement utilisées
            # Parser answer pour trouver les URLs qui ont été utilisées dans la synthèse
            answer_text = str(answer)
            sources_used_count = 0

            for url in sources_discovered:
                # Si l'URL ou son domaine est mentionné dans la réponse, marquer comme SUCCESS
                domain = url.split('/')[2] if len(url.split('/')) > 2 else url
                if domain in answer_text or url in answer_text:
                    registry.update_source_status(url, SourceStatus.SUCCESS)
                    sources_used_count += 1

            # Extraire sources requises/suggérées
            for url in sources.required:
                if url in answer_text:
                    registry.add_website(
                        url=url,
                        status=SourceStatus.SUCCESS,
                        priority=SourcePriority.REQUIRED
                    )
                else:
                    registry.add_website(
                        url=url,
                        status=SourceStatus.DISCOVERED_NOT_USED,
                        priority=SourcePriority.REQUIRED
                    )

            for url in sources.suggested:
                if url in answer_text:
                    registry.add_website(
                        url=url,
                        status=SourceStatus.SUCCESS,
                        priority=SourcePriority.SUGGESTED
                    )
                else:
                    registry.add_website(
                        url=url,
                        status=SourceStatus.DISCOVERED_NOT_USED,
                        priority=SourcePriority.SUGGESTED
                    )

            logger.info(f"✅ Sources trackées: {len(sources_discovered)} découvertes, {sources_used_count} utilisées")

            step.set_output({
                "apis_extracted": len(apis_used),
                "datasets_count": datasets_count,
                "sources_discovered": len(sources_discovered),
                "sources_used": sources_used_count,
                "required_sources": len(sources.required),
                "suggested_sources": len(sources.suggested)
            })

        # === PHASE FINALE: FORMAT REPORT ===
        logger.info("📄 Formatting report")

        # Si answer est déjà structuré, l'utiliser
        if answer.get("type") == "report" and "sections" in answer:
            # Report déjà structuré
            sections = [
                ReportSection(
                    title=sec.get("title", ""),
                    content=sec.get("content", ""),
                    data=sec.get("data"),
                    sources=sec.get("sources")
                )
                for sec in answer.get("sections", [])
            ]

            bibliography = [
                BibliographyItem(
                    title=bib.get("title", ""),
                    url=bib.get("url", ""),
                    type=bib.get("type", "website"),
                    date=bib.get("date")
                )
                for bib in answer.get("bibliography", [])
            ]

            datasets_used = None
            if answer.get("datasets_used"):
                datasets_used = [
                    DatasetUsed(
                        name=ds.get("name", ""),
                        url=ds.get("url", ""),
                        records=ds.get("records", 0),
                        processing=ds.get("processing")
                    )
                    for ds in answer["datasets_used"]
                ]

            report = Report(
                summary=answer.get("summary", ""),
                sections=sections,
                bibliography=bibliography,
                datasets_used=datasets_used
            )

        else:
            # Générer report depuis summary/data
            summary = answer.get("summary", str(answer.get("data", "")))
            data = answer.get("data", [])

            # Créer sections automatiquement
            sections = []

            # Section: Introduction
            if request.objectives:
                intro_content = f"Cette recherche vise à {', '.join(request.objectives).lower()}."
                sections.append(ReportSection(
                    title="Introduction",
                    content=intro_content
                ))

            # Section: Résultats principaux
            if isinstance(data, list) and len(data) > 0:
                sections.append(ReportSection(
                    title="Résultats principaux",
                    content=summary,
                    data=data[:100]  # Limiter
                ))
            else:
                sections.append(ReportSection(
                    title="Synthèse",
                    content=summary
                ))

            # Section: Conclusion
            sources_count = len(registry.websites) + len(registry.apis) + len(registry.datasets)
            sources_success = len([w for w in registry.websites if w.status == SourceStatus.SUCCESS.value])
            sections.append(ReportSection(
                title="Conclusion",
                content=f"Cette recherche approfondie sur '{request.topic}' a permis de consulter {sources_count} sources, dont {sources_success} ont été exploitées avec succès pour générer ce rapport."
            ))

            # Bibliographie depuis SourceRegistry
            bibliography = []
            seen_urls = set()  # Déduplication

            for website in registry.websites:
                if website.status == SourceStatus.SUCCESS.value and website.url not in seen_urls:
                    # Extraire domaine propre (sans www)
                    try:
                        from urllib.parse import urlparse
                        parsed = urlparse(website.url)
                        domain = parsed.netloc.replace('www.', '')
                        title = domain if domain else website.url.split('/')[2]
                    except:
                        title = website.url.split('/')[2] if len(website.url.split('/')) > 2 else website.url

                    bibliography.append(BibliographyItem(
                        title=title,
                        url=website.url,
                        type="website",
                        date=None
                    ))
                    seen_urls.add(website.url)

            for api in registry.apis:
                if api.status == SourceStatus.SUCCESS.value and api.url not in seen_urls:
                    try:
                        from urllib.parse import urlparse
                        parsed = urlparse(api.url)
                        domain = parsed.netloc.replace('www.', '')
                        title = f"API {domain}" if domain else api.url
                    except:
                        title = f"API {api.url.split('/')[2]}"

                    bibliography.append(BibliographyItem(
                        title=title,
                        url=api.url,
                        type="api",
                        date=None
                    ))
                    seen_urls.add(api.url)

            report = Report(
                summary=summary,
                sections=sections,
                bibliography=bibliography
            )

        # Execution summary
        execution_summary = ExecutionSummary(
            steps_executed=exec_ctx.get("steps_executed", 0),
            sources_consulted=exec_ctx.get("sources_consulted", 0),
            apis_used=exec_ctx.get("apis_used", []),
            data_processing=exec_ctx.get("data_processing"),
            completeness=exec_ctx.get("completeness", 85),
            confidence=answer.get("confidence", "medium")
        )

        processing_time = time.time() - start_time
        logger.info(f"✅ /research/deep completed in {processing_time:.2f}s")
        logger.info(f"   Steps: {execution_summary.steps_executed}, Sources: {execution_summary.sources_consulted}")
        logger.info(f"   Confidence: {execution_summary.confidence}")

        # Un rapport dont des sections n'ont pas pu etre redigees (echec LLM
        # repete) est DEGRADE : l'annoncer explicitement plutot que de rendre
        # success=true sur un rapport quasi vide. L'appelant doit pouvoir
        # distinguer "sujet pauvre en sources" de "l'infra LLM n'a pas
        # repondu" - ce sont deux situations sans rapport.
        answer_dict = answer if isinstance(answer, dict) else {}
        failed_sections = answer_dict.get("failed_sections", [])
        unsourced_sections = answer_dict.get("unsourced_sections", [])
        is_degraded = bool(answer_dict.get("degraded"))
        if is_degraded:
            logger.error(
                f"⚠️  Rapport DEGRADE : {len(failed_sections)} échec(s) LLM, "
                f"{len(unsourced_sections)} section(s) sans source"
            )

        # Utiliser OutputFormatter
        formatter = OutputFormatter(tracer, registry, processing_time)
        result_dict = formatter.format_deep_research_output(
            topic=request.topic,
            report=report.dict(),
            execution_context=exec_ctx,  # Passer le contexte original de l'orchestrator
            success=not is_degraded
        )
        if is_degraded:
            result_dict["degraded"] = True
            result_dict["failed_sections"] = failed_sections
            if unsourced_sections:
                result_dict["unsourced_sections"] = unsourced_sections
            # Deux causes distinctes, deux messages distincts : un echec LLM
            # est un incident technique (a reessayer), une absence de source
            # est un constat sur le sujet (inutile de reessayer a l'identique).
            # Les confondre ferait chercher une panne la ou il n'y en a pas.
            if failed_sections and unsourced_sections:
                result_dict["error"] = (
                    f"{len(failed_sections)} section(s) non générée(s) par échec LLM et "
                    f"{len(unsourced_sections)} sans source exploitable"
                )
            elif failed_sections:
                result_dict["error"] = (
                    f"{len(failed_sections)} section(s) non générée(s) : échec répété "
                    f"de l'appel LLM de synthèse"
                )
            else:
                result_dict["error"] = (
                    f"{len(unsourced_sections)} section(s) sans source exploitable : "
                    f"la collecte n'a rien remonté sur ce sujet"
                )

        # Convertir en JSON et streamer pour éviter buffering
        json_str = json.dumps(result_dict, ensure_ascii=False, indent=2)

        async def generate():
            # Envoyer le JSON par chunks pour éviter le buffering
            chunk_size = 8192  # 8KB par chunk
            for i in range(0, len(json_str), chunk_size):
                yield json_str[i:i+chunk_size].encode('utf-8')

        return StreamingResponse(
            generate(),
            media_type="application/json",
            headers={
                "X-Processing-Time": f"{processing_time:.2f}s",
                "Cache-Control": "no-cache"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ /research/deep error: {e}", exc_info=True)
        processing_time = time.time() - start_time
        formatter = OutputFormatter(tracer, registry, processing_time)
        result_dict = formatter.format_deep_research_output(
            topic=request.topic,
            report=None,
            execution_context=None,
            success=False,
            error=str(e)
        )

        # Streamer même en cas d'erreur
        json_str = json.dumps(result_dict, ensure_ascii=False, indent=2)

        async def generate():
            yield json_str.encode('utf-8')

        return StreamingResponse(
            generate(),
            media_type="application/json",
            headers={"X-Processing-Time": f"{processing_time:.2f}s"}
        )
