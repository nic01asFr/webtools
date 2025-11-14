"""
OutputFormatter - Formatage standardisé et adaptatif des outputs

Crée des structures de réponse cohérentes mais adaptées à chaque endpoint.
"""

import logging
from typing import Dict, Any, Optional, List
from app.core.execution_tracer import ExecutionTracer
from app.core.source_registry import SourceRegistry

logger = logging.getLogger(__name__)


class OutputFormatter:
    """
    Formateur d'outputs standardisés

    Usage:
        formatter = OutputFormatter(tracer, registry)
        output = formatter.format_search_output(
            query="...",
            results=[...],
            enriched_query="...",
            dorking_applied=[...]
        )
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

    def format_search_output(
        self,
        query: str,
        results: list,
        enriched_query: Optional[str] = None,
        dorking_applied: Optional[list] = None,
        success: bool = True,
        error: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Formater output pour /api/v1/search

        Returns:
            {
                success, query, processing_time,
                enriched_query, dorking_applied,
                execution: {workflow, summary},
                sources: {search_engines_used, ...},
                result: {results}
            }
        """
        output = self._base_output(success, query, error)

        # Ajouter enrichment et dorking
        if enriched_query:
            output["enriched_query"] = enriched_query
        if dorking_applied:
            output["dorking_applied"] = dorking_applied

        # Ajouter trace d'exécution
        output["execution"] = self.tracer.get_trace()

        # Ajouter sources
        output["sources"] = self.registry.get_all_sources()

        # Ajouter résultats
        output["result"] = {
            "results": results,
            "total_results": len(results)
        }

        return output

    def format_quick_research_output(
        self,
        query: str,
        answer: Optional[str],
        confidence: Optional[str],
        citations: Optional[list] = None,
        reasoning: Optional[str] = None,
        success: bool = True,
        error: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Formater output pour /api/v1/research/quick

        Returns:
            {
                success, query, processing_time,
                execution: {workflow, summary},
                sources: {apis, websites},
                result: {answer, confidence, citations, reasoning}
            }
        """
        output = self._base_output(success, query, error)

        # Ajouter trace d'exécution
        output["execution"] = self.tracer.get_trace()

        # Ajouter sources
        all_sources = self.registry.get_all_sources()
        output["sources"] = all_sources

        # Ajouter statistiques sources dans execution summary
        stats = self.registry.get_statistics()
        output["execution"]["summary"].update({
            "total_sources_consulted": stats["total_apis"] + stats["total_websites"],
            "apis_used": stats["apis_successful"],
            "websites_used": stats["websites_successful"],
            "total_records_retrieved": stats["total_records_retrieved"]
        })

        # Ajouter résultat
        result_data = {}
        if answer:
            result_data["answer"] = answer
        if confidence:
            result_data["confidence"] = confidence
        if reasoning:
            result_data["reasoning"] = reasoning
        if citations:
            result_data["citations"] = citations

        output["result"] = result_data

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

    def add_api_discovery_info(
        self,
        discovered_apis: List[str],
        successfully_used_apis: List[str],
        failed_apis: List[str]
    ):
        """Ajouter info sur découverte d'APIs dans metadata du tracer"""
        self.tracer.add_metadata("apis_discovered", len(discovered_apis))
        self.tracer.add_metadata("apis_successfully_used", len(successfully_used_apis))
        self.tracer.add_metadata("apis_failed", len(failed_apis))

    def add_strategy_info(self, strategy: str):
        """Ajouter info sur la stratégie appliquée"""
        self.tracer.add_metadata("strategy_applied", strategy)
