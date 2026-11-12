"""
OutputFormatter - Formatage standardisé et adaptatif des outputs

Crée des structures de réponse cohérentes mais adaptées à chaque endpoint.
"""

import logging
from typing import Dict, Any, List, Optional
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
        error: Optional[str] = None,
        *,
        degraded: bool = False,
        timed_out: bool = False,
        failed_sections: Optional[List[str]] = None,
        unsourced_sections: Optional[List[str]] = None,
        missing_sections: Optional[List[str]] = None,
        diagnostics: Optional[Dict[str, Any]] = None,
        required_sources_failed: Optional[List[Dict[str, str]]] = None,
        required_sources_uncited: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Formater output pour /api/v1/research/deep.

        Une seule forme, succes ou echec : les memes cles sont toujours
        presentes. La trace d'execution et les sources sont jointes meme quand
        la recherche echoue (C65), puisque c'est alors qu'on en a besoin.

        Returns:
            {
                success, degraded, timed_out, topic, processing_time, error,
                result: {title, summary, sections, bibliography, ...} | None,
                failed_sections, unsourced_sections, missing_sections,
                execution: {workflow, summary}, sources: {...},
                diagnostics: {config, events, invariants}
            }
        """
        trace = self.tracer.get_trace()
        summary = trace["summary"]
        if execution_context:
            summary["steps_executed"] = execution_context.get("steps_executed", 0)
            summary["datasets_collected"] = execution_context.get("datasets_collected", 0)
            summary["sources_discovered"] = len(execution_context.get("sources_discovered") or [])
        summary["required_sources_status"] = self.registry.required_sources_status()

        body: Dict[str, Any] = {
            "success": success,
            "degraded": degraded,
            "timed_out": timed_out,
            "topic": topic,
            "processing_time": round(self.processing_time, 2),
            "error": error,
            "result": report,
            "failed_sections": list(failed_sections or []),
            "unsourced_sections": list(unsourced_sections or []),
            "missing_sections": list(missing_sections or []),
            "execution": trace,
            "sources": self.registry.get_all_sources(),
            "diagnostics": diagnostics if diagnostics is not None else {
                "config": {}, "events": {"counts": {}, "events": []}, "invariants": None
            },
        }
        # Cles presentes seulement quand non vides (sources.required de la requete).
        if required_sources_failed:
            body["required_sources_failed"] = list(required_sources_failed)
        if required_sources_uncited:
            body["required_sources_uncited"] = list(required_sources_uncited)
        return body
