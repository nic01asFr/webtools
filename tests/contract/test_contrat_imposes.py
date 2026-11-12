"""Contrat des parametres imposes : sections et sources requises.

Execute le pipeline de bout en bout avec les doublures (comme
test_pipeline_contract, qui reste inchange) et applique les invariants du
rapport : imposer des sections ou des sources ne doit pas produire un rapport
qui viole un invariant.
"""

import app.agents.intelligent_orchestrator as orchestrator_module
import pytest
from app.research.invariants import check_report

from tests.contract.test_pipeline_contract import QUERY, _FastAsyncio, nominal
from tests.unit.research.test_v4_4_imposes import REQ_URL, build, titles


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator_module, "asyncio", _FastAsyncio())


async def test_contrat_sections_imposees() -> None:
    orch, llm, _s, _e = build(rename={"Outlook": "outlook", "Overview": "OVERVIEW"})
    imposed = ["outlook", "OVERVIEW"]
    result = await orch.execute_intelligent_research(QUERY, {"output_sections": imposed})

    assert result["success"] is True
    assert titles(result) == imposed
    assert llm.unmatched == []
    assert check_report(result["answer"])["violations"] == []
    assert len(llm.calls_for(schema_name="research_plan")) == 1


async def test_contrat_source_requise_citee() -> None:
    specs = nominal()
    specs[1].cite_order = [REQ_URL]
    orch, llm, _s, extractor = build(specs)
    result = await orch.execute_intelligent_research(QUERY, {"sources_required": [REQ_URL]})
    report = result["answer"]

    assert llm.unmatched == []
    assert REQ_URL in extractor.urls_requested()
    assert REQ_URL in {b["url"] for b in report["bibliography"]}
    assert not report.get("degraded")
    assert "required_sources_uncited" not in report
    assert check_report(report)["violations"] == []
    assert titles(result) == ["Overview", "Costs", "Outlook"]


async def test_contrat_source_requise_en_echec_degrade_sans_perdre_le_rapport() -> None:
    orch, _llm, _s, _e = build(required_page=False)
    result = await orch.execute_intelligent_research(QUERY, {"sources_required": [REQ_URL]})
    report = result["answer"]

    assert result["success"] is True
    assert report["degraded"] is True
    assert [f["url"] for f in report["required_sources_failed"]] == [REQ_URL]
    assert titles(result) == ["Overview", "Costs", "Outlook"]
    assert check_report(report)["violations"] == []
