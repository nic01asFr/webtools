"""Drapeau extraction_use_agent : il atteint les options d'extraction.

Observe en production sur un modele lent : l'agent navigateur, lance pour
chaque page difficile, faisait depasser le delai global du rapport.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.research.pipeline_config import PipelineConfig
from tests.contract.test_pipeline_contract import QUERY, build_orchestrator, nominal


@pytest.mark.parametrize("enabled", [True, False])
async def test_drapeau_transmis_aux_extractions(enabled: bool) -> None:
    orch, _llm, _search, extractor = build_orchestrator(nominal())
    orch.config = dataclasses.replace(orch.config, extraction_use_agent=enabled)
    await orch.execute_intelligent_research(QUERY)
    assert extractor.calls, "aucune extraction"
    assert {call.options.use_agent for call in extractor.calls} == {enabled}


def test_options_d_extraction_suivent_le_deploiement(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.models import ExtractionOptions

    monkeypatch.setenv("WEBTOOLS_PIPELINE_EXTRACTION_USE_AGENT", "0")
    assert ExtractionOptions().use_agent is False
    monkeypatch.delenv("WEBTOOLS_PIPELINE_EXTRACTION_USE_AGENT")
    assert ExtractionOptions().use_agent is PipelineConfig().extraction_use_agent is True
