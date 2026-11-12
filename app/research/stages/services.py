"""Services injectes dans les etapes du pipeline."""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.research.pipeline_config import PipelineConfig


@dataclass(frozen=True)
class StageServices:
    """Ce dont une etape a besoin pour travailler, sans acces a l'orchestrateur.

    Construit par IntelligentOrchestrator.services() a chaque appel : les
    attributs de l'orchestrateur (configuration, client de recherche) peuvent
    etre remplaces entre deux requetes. `config` est la configuration par
    defaut de l'orchestrateur ; une etape qui recoit un ExecutionContext lit
    celle de la requete (ctx.config).
    """

    llm_client: Any
    search_client: Any
    extractor_manager: Any
    data_extractor: Any
    config: PipelineConfig
    timeout: int = 300
    # Attente entre deux essais de synthese. Injectee pour que les tests
    # puissent la rendre instantanee sans toucher au module asyncio.
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)
