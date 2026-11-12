"""Raccords entre lots de la vague 3, poses par l'integrateur.

- Le plan generique de repli (V3-7) rend le rapport degrade, avec sa cause
  dans la reponse et dans le message d'erreur de l'API.
- Le journal de diagnostics atteint la conversion des citations (V3-1).
- Le rejeu de la collecte (V4-3) est branche a la construction de
  l'orchestrateur de production.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.research.replay import ReplayExtractor, ReplaySearch
from tests.contract.test_pipeline_contract import QUERY, build_orchestrator, nominal


async def _run_with_plan_failure() -> dict[str, Any]:
    orch, llm, _search, _extractor = build_orchestrator(nominal())
    # Sans scenario "research_plan", le faux LLM leve : le plan de repli est pris.
    llm._structured.pop("research_plan")
    return await orch.execute_intelligent_research(QUERY)


async def test_plan_de_repli_rend_le_rapport_degrade() -> None:
    result = await _run_with_plan_failure()
    answer = result["answer"]
    assert answer["degraded"] is True
    assert answer["plan_fallback_reason"].startswith("exception:")
    counts = result["diagnostics"]["events"]["counts"]
    assert counts.get("plan.repli") == 1


def test_message_d_erreur_cite_le_plan_de_repli() -> None:
    from app.api.v1.endpoints.research_deep import _degradation_error

    message = _degradation_error([], [], [], False, 600, plan_fallback_reason="exception:FakeLLMError")
    assert message is not None and "plan generique de repli" in message
    assert _degradation_error([], [], [], False, 600) is None
    imposed = _degradation_error(
        [], [], [], False, 600, plan_fallback_reason="exception:X", sections_imposed=True
    )
    assert imposed is not None and "sections imposees sont conservees" in imposed
    assert "ne suit pas la consigne" not in imposed


async def test_conversion_des_citations_recoit_le_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    orch, _llm, _search, _extractor = build_orchestrator(nominal())
    received: list[Any] = []
    original = orch._convert_sources_to_bibliography

    def spy(report: dict[str, Any], diagnostics: Any = None, **kwargs: Any) -> dict[str, Any]:
        received.append(diagnostics)
        return original(report, diagnostics, **kwargs)

    monkeypatch.setattr(orch, "_convert_sources_to_bibliography", spy)
    await orch.execute_intelligent_research(QUERY)
    assert received, "la conversion des citations n'a pas ete appelee"
    assert all(d is not None for d in received)


async def test_rejeu_branche_dans_l_orchestrateur_de_production(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from app.api.v1.endpoints import research_deep
    from app.research.pipeline_config import PipelineConfig
    from tests.fakes import FakeLLM

    async def fake_client() -> FakeLLM:
        return FakeLLM()

    monkeypatch.setattr(research_deep, "get_llm_client", fake_client)
    monkeypatch.setenv("WEBTOOLS_REPLAY_MODE", "record")
    monkeypatch.setenv("WEBTOOLS_REPLAY_DIR", str(tmp_path))
    orch = await research_deep.create_orchestrator(PipelineConfig(), 120)
    assert isinstance(orch.search_client, ReplaySearch)
    assert isinstance(orch.extractor_manager, ReplayExtractor)

    monkeypatch.delenv("WEBTOOLS_REPLAY_MODE")
    orch = await research_deep.create_orchestrator(PipelineConfig(), 120)
    assert not isinstance(orch.search_client, ReplaySearch)
