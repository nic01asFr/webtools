"""
ExecutionTracer - Traçage complet de l'exécution des workflows

Enregistre chaque étape de l'orchestration pour transparence totale.
"""

import time
import logging
from typing import Dict, Any, List, Optional
from enum import Enum
from dataclasses import dataclass, field, asdict
from datetime import datetime

logger = logging.getLogger(__name__)


class StepStatus(str, Enum):
    """Status d'une étape"""
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    PARTIAL = "partial"


class ActionType(str, Enum):
    """Types d'actions possibles"""
    DISCOVERY = "discovery"
    QUERY_ENRICHMENT = "query_enrichment"
    DORKING_CONSTRUCTION = "dorking_construction"
    WEB_SEARCH = "web_search"
    API_DETECTION = "api_detection"
    API_NAVIGATION = "api_navigation"
    SITE_SEARCH = "site_search"
    CONTENT_EXTRACTION = "content_extraction"
    DATA_PROCESSING = "data_processing"
    SYNTHESIS = "synthesis"
    PLANNING = "planning"
    ENRICHMENT_CHECK = "enrichment_check"
    RELEVANCE_SCORING = "relevance_scoring"


@dataclass
class ExecutionStep:
    """Représentation d'une étape d'exécution"""
    step: int
    action: str  # ActionType
    tool: str
    input: Dict[str, Any]
    output: Dict[str, Any] = field(default_factory=dict)
    duration: float = 0.0
    status: str = StepStatus.SUCCESS  # StepStatus
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization"""
        return {k: v for k, v in asdict(self).items() if v is not None}


class ExecutionTracer:
    """
    Tracer pour enregistrer toutes les étapes d'exécution

    Usage:
        tracer = ExecutionTracer()

        with tracer.step("query_enrichment", "query_enricher") as step:
            result = await enrich_query(...)
            step.set_input({"query": "..."})
            step.set_output({"enriched_query": "..."})

        trace = tracer.get_trace()
    """

    def __init__(self):
        self.steps: List[ExecutionStep] = []
        self.start_time = time.time()
        self.metadata: Dict[str, Any] = {}

    def add_metadata(self, key: str, value: Any):
        """Ajouter des métadonnées globales"""
        self.metadata[key] = value

    def step(self, action: str, tool: str) -> 'StepContext':
        """
        Context manager pour tracer une étape

        Args:
            action: Type d'action (ActionType)
            tool: Nom de l'outil utilisé

        Returns:
            StepContext pour gérer l'étape
        """
        return StepContext(self, action, tool)

    def add_step(self, step: ExecutionStep):
        """Ajouter une étape manuellement"""
        self.steps.append(step)
        logger.debug(f"[ExecutionTracer] Step {step.step}: {step.action} ({step.status})")

    def get_trace(self) -> Dict[str, Any]:
        """
        Obtenir la trace complète de l'exécution

        Returns:
            Dict avec workflow et summary
        """
        total_duration = time.time() - self.start_time

        # Analyser les outils utilisés
        tools_used = list(set(step.tool for step in self.steps))

        # Compter les types d'actions
        action_counts = {}
        for step in self.steps:
            action_counts[step.action] = action_counts.get(step.action, 0) + 1

        # Compter les status
        status_counts = {
            "success": sum(1 for s in self.steps if s.status == StepStatus.SUCCESS),
            "failed": sum(1 for s in self.steps if s.status == StepStatus.FAILED),
            "partial": sum(1 for s in self.steps if s.status == StepStatus.PARTIAL),
            "skipped": sum(1 for s in self.steps if s.status == StepStatus.SKIPPED),
        }

        return {
            "workflow": [step.to_dict() for step in self.steps],
            "summary": {
                "total_steps": len(self.steps),
                "total_duration": round(total_duration, 2),
                "tools_used": tools_used,
                "action_counts": action_counts,
                "status_counts": status_counts,
                **self.metadata
            }
        }

    def get_steps_by_action(self, action: str) -> List[ExecutionStep]:
        """Récupérer toutes les étapes d'un certain type"""
        return [step for step in self.steps if step.action == action]

    def get_steps_by_tool(self, tool: str) -> List[ExecutionStep]:
        """Récupérer toutes les étapes utilisant un outil"""
        return [step for step in self.steps if step.tool == tool]

    def has_failures(self) -> bool:
        """Vérifier s'il y a eu des échecs"""
        return any(step.status == StepStatus.FAILED for step in self.steps)


class StepContext:
    """Context manager pour une étape d'exécution"""

    def __init__(self, tracer: ExecutionTracer, action: str, tool: str):
        self.tracer = tracer
        self.action = action
        self.tool = tool
        self.step_number = len(tracer.steps) + 1
        self.start_time = time.time()
        self.input_data: Dict[str, Any] = {}
        self.output_data: Dict[str, Any] = {}
        self.status = StepStatus.SUCCESS
        self.error: Optional[str] = None

    def __enter__(self) -> 'StepContext':
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time

        # Si exception, marquer comme failed
        if exc_type is not None:
            self.status = StepStatus.FAILED
            self.error = str(exc_val)

        # Créer et ajouter l'étape
        step = ExecutionStep(
            step=self.step_number,
            action=self.action,
            tool=self.tool,
            input=self.input_data,
            output=self.output_data,
            duration=round(duration, 2),
            status=self.status,
            error=self.error
        )

        self.tracer.add_step(step)

        # Ne pas supprimer l'exception
        return False

    def set_input(self, data: Dict[str, Any]):
        """Définir les données d'entrée"""
        self.input_data = data

    def set_output(self, data: Dict[str, Any]):
        """Définir les données de sortie"""
        self.output_data = data

    def set_status(self, status: StepStatus, error: Optional[str] = None):
        """Définir le status de l'étape"""
        self.status = status
        if error:
            self.error = error

    def mark_partial(self, reason: str):
        """Marquer comme partiellement réussi"""
        self.status = StepStatus.PARTIAL
        self.error = reason

    def mark_failed(self, reason: str):
        """Marquer comme échoué"""
        self.status = StepStatus.FAILED
        self.error = reason
