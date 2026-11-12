"""
OutputFormatter - Formatage standardisé et adaptatif des outputs

Crée des structures de réponse cohérentes mais adaptées à chaque endpoint.
"""

import logging
from typing import Dict, Any, Optional
from app.core.execution_tracer import ExecutionTracer
from app.core.source_registry import SourceRegistry

logger = logging.getLogger(__name__)


class OutputFormatter:
    """
    Formateur d'outputs standardisés

    Usage:
        formatter = OutputFormatter(tracer, registry, processing_time)
        output = formatter.format_deep_research_output(...)
    """

    def __init__(
        self,
        tracer: ExecutionTracer,
        registry: SourceRegistry,
        processing_time: float
    ):
        self.tracer = tracer
        self.registry = registry
        self.processing_time = processing_time

    def _base_output(
        self,
        success: bool,
        query: str,
        error: Optional[str] = None
    ) -> Dict[str, Any]:
        """Structure de base commune à tous les outputs"""
        output = {
            "success": success,
            "query": query,
            "processing_time": round(self.processing_time, 2)
        }

        if error:
            output["error"] = error

        return output

    def format_deep_research_output(
        self,
        topic: str,
        report: Optional[Dict[str, Any]],
        execution_context: Optional[Dict[str, Any]] = None,
        success: bool = True,
        error: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Formater output pour /api/v1/research/deep

        Returns:
            {
                success, topic (not query), processing_time,
                execution: {workflow, summary + completeness},
                sources: {apis, websites, datasets},
                result: {summary, sections, bibliography}
            }
        """
        output = {
            "success": success,
            "topic": topic,  # Note: topic, pas query
            "processing_time": round(self.processing_time, 2)
        }

        if error:
            output["error"] = error
            return output

        # Ajouter trace d'exécution
        execution_trace = self.tracer.get_trace()

        # Enrichir avec infos du contexte d'exécution si fourni
        if execution_context:
            execution_trace["summary"].update({
                "completeness_achieved": execution_context.get("completeness", 0),
                "steps_executed": execution_context.get("steps_executed", 0)
            })

            # Ajouter info sur data processing si disponible
            if execution_context.get("data_processing"):
                execution_trace["summary"]["data_processing"] = execution_context["data_processing"]

        output["execution"] = execution_trace

        # Ajouter sources
        all_sources = self.registry.get_all_sources()
        output["sources"] = all_sources

        # Ajouter statistiques dans execution summary
        stats = self.registry.get_statistics()

        # Vérifier status des sources requises
        required_status = "all_success" if self.registry.has_required_sources_succeeded() else "partial_failure"

        output["execution"]["summary"].update({
            "required_sources_status": required_status,
            "total_records_processed": stats.get("total_records_retrieved", 0)
        })

        # Ajouter résultat (le rapport)
        if report:
            output["result"] = report

        return output
