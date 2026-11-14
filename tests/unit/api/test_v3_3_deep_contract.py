"""Lot V3-3 : contrat de l'API /research/deep.

Chaque champ de requete a un test qui prouve son effet, avec les doublures de
tests/fakes (aucun reseau, aucun LLM reel). L'endpoint est appele par ASGI ; la
fabrique d'orchestrateur est remplacee par une fabrique qui injecte les
doublures et respecte la configuration calculee par l'endpoint.
"""

import asyncio
from typing import Any

import app.agents.intelligent_orchestrator as orchestrator_module
import httpx
import pytest
from app.agents.intelligent_orchestrator import IntelligentOrchestrator
from app.api.v1.endpoints import research_deep
from app.research.pipeline_config import PipelineConfig
from fastapi import FastAPI

from tests.contract.test_pipeline_contract import (
    QUERY,
    SectionSpec,
    build_orchestrator,
    nominal,
    sourceless_first_section,
    synthesis_failure,
)

RESPONSE_KEYS = {
    "success", "degraded", "timed_out", "topic", "processing_time", "error", "result",
    "failed_sections", "unsourced_sections", "missing_sections", "execution", "sources",
    "diagnostics",
}


class _FastAsyncio:
    """Proxy du module asyncio dont sleep est instantane (reessais de synthese)."""

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)

    @staticmethod
    async def sleep(_delay: float) -> None:
        return None


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator_module, "asyncio", _FastAsyncio())


class Harness:
    """Orchestrateur de doublures branche sur l'endpoint, et client ASGI."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, sections: list[SectionSpec]) -> None:
        self.orch: IntelligentOrchestrator
        self.orch, self.llm, self.search, self.extractor = build_orchestrator(sections)
        self.factory_calls: list[tuple[PipelineConfig, int]] = []
        self.deadlines: list[float] = []
        self.real_deadline: float | None = None  # remplace le delai transmis
        harness = self

        async def create(config: PipelineConfig, section_budget_timeout: int):
            harness.factory_calls.append((config, section_budget_timeout))
            harness.orch.config = config
            real = IntelligentOrchestrator.execute_with_deadline.__get__(harness.orch)

            async def spy(query: str, context: Any = None, deadline_seconds: float = 600.0):
                harness.deadlines.append(deadline_seconds)
                return await real(query, context, harness.real_deadline or deadline_seconds)

            harness.orch.execute_with_deadline = spy  # type: ignore[method-assign]
            return harness.orch

        monkeypatch.setattr(research_deep, "create_orchestrator", create)
        app = FastAPI()
        app.include_router(research_deep.router, prefix="/api/v1")
        self.app = app

    async def post(self, body: dict[str, Any]) -> httpx.Response:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.post("/api/v1/research/deep", json=body)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(monkeypatch, nominal())


# --- Forme de la reponse --------------------------------------------------------


async def test_forme_en_succes(harness: Harness) -> None:
    resp = await harness.post({"topic": QUERY})
    body = resp.json()

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert set(body) == RESPONSE_KEYS
    assert body["success"] is True and body["degraded"] is False and body["error"] is None
    result = body["result"]
    assert result["title"]
    assert result["metadata"]["sections_count"] == 3
    assert result["bibliography"] and all(isinstance(b["id"], int) for b in result["bibliography"])
    assert [s["title"] for s in result["sections"]] == ["Overview", "Costs", "Outlook"]
    diag = body["diagnostics"]
    assert set(diag) == {"config", "events", "invariants"}
    assert diag["invariants"]["ok"] is True
    assert "counts" in diag["events"]
    summary = body["execution"]["summary"]
    assert "completeness_achieved" not in summary
    assert "total_records_processed" not in summary
    assert summary["required_sources_status"] == "none_required"
    assert body["sources"]["websites"]


async def test_forme_en_degrade(monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(monkeypatch, synthesis_failure())
    body = (await h.post({"topic": QUERY})).json()

    assert set(body) == RESPONSE_KEYS
    assert body["success"] is False and body["degraded"] is True
    assert body["failed_sections"] == ["Costs"]
    assert "echec repete" in body["error"]
    assert body["result"]["sections"]
    assert body["sources"] and body["execution"]["workflow"]


async def test_forme_en_echec_de_l_orchestrateur(harness: Harness) -> None:
    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("exploration impossible")

    harness.orch._exploratory_phase = boom  # type: ignore[method-assign]
    resp = await harness.post({"topic": QUERY, "sources": {"required": ["https://req.org/x"]}})
    body = resp.json()

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert set(body) == RESPONSE_KEYS
    assert body["success"] is False and body["result"] is None
    assert "exploration impossible" in body["error"]
    assert body["diagnostics"]["events"]["counts"]["api.orchestrator_exception"] == 1
    assert body["diagnostics"]["config"] == PipelineConfig().to_dict()
    # trace et sources conservees en echec (C65)
    assert body["execution"]["workflow"][0]["status"] == "failed"
    assert body["sources"]["websites"][0]["url"] == "https://req.org/x"
    assert body["execution"]["summary"]["required_sources_status"] == "partial_failure"


async def test_forme_en_echec_avant_l_orchestrateur(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("client LLM indisponible")

    monkeypatch.setattr(research_deep, "create_orchestrator", boom)
    body = (await harness.post({"topic": QUERY})).json()

    assert set(body) == RESPONSE_KEYS
    assert body["success"] is False
    assert "client LLM indisponible" in body["error"]
    assert body["diagnostics"]["config"] == PipelineConfig().to_dict()
    assert body["execution"]["workflow"]


# --- Un test par champ de requete ------------------------------------------------


async def test_include_section_data(monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(monkeypatch, nominal())
    without = (await h.post({"topic": QUERY})).json()
    assert all("data" not in s for s in without["result"]["sections"])

    h2 = Harness(monkeypatch, nominal())
    with_data = (
        await h2.post({"topic": QUERY, "output_format": {"include_section_data": True}})
    ).json()
    assert all("data" in s for s in with_data["result"]["sections"])
    assert any(s["data"] for s in with_data["result"]["sections"])


async def test_max_sources_per_section(monkeypatch: pytest.MonkeyPatch) -> None:
    def overview_urls(h: Harness) -> list[str]:
        return [u for u in h.extractor.urls_requested() if "overview" in u]

    def overview_caps(h: Harness) -> list[int]:
        return [c.max_results for c in h.search.queries if c.query == "solar overview"]

    default = Harness(monkeypatch, nominal())
    body_default = (await default.post({"topic": QUERY})).json()
    assert len(overview_urls(default)) == 2
    assert overview_caps(default) == [5]
    assert body_default["diagnostics"]["config"]["max_sources_per_section"] is None

    limited = Harness(monkeypatch, nominal())
    body = (await limited.post({"topic": QUERY, "max_sources_per_section": 1})).json()
    assert len(overview_urls(limited)) == 1
    assert overview_caps(limited) == [1]
    assert body["diagnostics"]["config"]["max_sources_per_section"] == 1
    assert limited.factory_calls[0][0].max_sources_per_section == 1


async def test_timeout_est_le_delai_global(harness: Harness) -> None:
    await harness.post({"topic": QUERY})
    await harness.post({"topic": QUERY, "timeout": 90})
    assert harness.deadlines == [1200.0, 90.0]
    assert harness.factory_calls[1][1] == 54  # budget par section : 0,6 x timeout


async def test_delai_depasse_rend_le_rapport_partiel(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def stall(*_a: Any, **_k: Any) -> Any:
        await asyncio.sleep(30)

    # Blocage apres la redaction des sections : l'assemblage ne finit pas.
    monkeypatch.setattr(orchestrator_module, "final_assembly", stall)
    harness.real_deadline = 0.5
    body = (await harness.post({"topic": QUERY, "timeout": 60})).json()

    assert set(body) == RESPONSE_KEYS
    assert body["timed_out"] is True and body["degraded"] is True and body["success"] is False
    assert "delai global de 60 s" in body["error"]
    sections = body["result"]["sections"]
    assert [s["title"] for s in sections] == ["Overview", "Costs", "Outlook"]
    assert body["result"]["bibliography"]
    assert "[SOURCE:" not in str(body["result"])
    assert body["diagnostics"]["events"]["counts"]["api.deadline_exceeded"] == 1
    assert body["sources"]["websites"]


async def test_delai_depasse_avant_toute_section(harness: Harness) -> None:
    async def stall(*_a: Any, **_k: Any) -> Any:
        await asyncio.sleep(30)

    harness.orch._exploratory_phase = stall  # type: ignore[method-assign]
    harness.real_deadline = 0.2
    body = (await harness.post({"topic": QUERY})).json()

    assert body["timed_out"] is True and body["success"] is False
    assert body["result"]["sections"] == []


async def test_nettoyage_bloque_n_empeche_pas_le_rapport_partiel(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = asyncio.Event()

    async def stall_then_block_cleanup(*_a: Any, **_k: Any) -> Any:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            # Nettoyage qui ne rend pas la main (fermeture de navigateur bloquee).
            await release.wait()
            raise

    monkeypatch.setattr(orchestrator_module, "final_assembly", stall_then_block_cleanup)
    monkeypatch.setenv("WEBTOOLS_PIPELINE_DEADLINE_CANCEL_GRACE_SECONDS", "0.2")
    harness.real_deadline = 0.5
    try:
        body = (await asyncio.wait_for(harness.post({"topic": QUERY, "timeout": 60}), 5)).json()
    finally:
        release.set()
        await asyncio.sleep(0.05)

    assert body["timed_out"] is True and body["degraded"] is True
    assert [s["title"] for s in body["result"]["sections"]] == ["Overview", "Costs", "Outlook"]
    counts = body["diagnostics"]["events"]["counts"]
    assert counts["api.annulation_non_terminee"] == 1
    assert counts["api.deadline_exceeded"] == 1


async def test_exception_apres_redaction_rend_le_rapport_partiel(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("assemblage casse")

    monkeypatch.setattr(orchestrator_module, "final_assembly", broken)
    body = (await harness.post({"topic": QUERY})).json()

    assert set(body) == RESPONSE_KEYS
    assert body["success"] is False and body["degraded"] is True
    assert body["timed_out"] is False
    assert "assemblage casse" in body["error"]
    assert [s["title"] for s in body["result"]["sections"]] == ["Overview", "Costs", "Outlook"]
    assert body["result"]["bibliography"]
    assert "[SOURCE:" not in str(body["result"])
    counts = body["diagnostics"]["events"]["counts"]
    assert counts["api.orchestrator_exception"] == 1
    assert counts["api.rapport_partiel_apres_exception"] == 1
    assert body["execution"]["workflow"][0]["status"] == "failed"


async def test_echeance_posee_sur_le_contexte(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.research.context import ExecutionContext

    assert ExecutionContext("q").deadline_at is None
    seen: list[tuple[float | None, float]] = []
    real_collect = orchestrator_module.collect_section_data

    async def spy(svc: Any, ctx: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append((ctx.deadline_at, asyncio.get_running_loop().time()))
        return await real_collect(svc, ctx, *args, **kwargs)

    monkeypatch.setattr(orchestrator_module, "collect_section_data", spy)
    await harness.post({"topic": QUERY, "timeout": 90})

    deadline_at, now = seen[0]
    assert deadline_at is not None
    assert now < deadline_at <= now + 90


async def test_objectives_language_et_sections_atteignent_le_plan(harness: Harness) -> None:
    await harness.post({
        "topic": QUERY,
        "objectives": ["objectif-temoin-42"],
        "context": {"language": "de"},
        "output_format": {"sections": ["section-temoin-7"]},
    })
    prompt = harness.llm.calls_for(schema_name="research_plan")[0].prompt
    assert "objectif-temoin-42" in prompt
    assert '"language": "de"' in prompt
    assert "section-temoin-7" in prompt


async def test_output_sections_raccourci(harness: Harness) -> None:
    await harness.post({"topic": QUERY, "output_sections": ["raccourci-9"]})
    assert "raccourci-9" in harness.llm.calls_for(schema_name="research_plan")[0].prompt


async def test_sources_required(monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(monkeypatch, nominal())
    used = (
        await h.post({"topic": QUERY, "sources": {"required": ["https://example.net/costs-1"]}})
    ).json()
    assert used["execution"]["summary"]["required_sources_status"] == "all_success"
    assert "https://example.net/costs-1" in h.llm.calls_for(schema_name="research_plan")[0].prompt

    h2 = Harness(monkeypatch, nominal())
    unused = (
        await h2.post({"topic": QUERY, "sources": {"required": ["https://jamais.org/vu"]}})
    ).json()
    assert unused["execution"]["summary"]["required_sources_status"] == "partial_failure"
    required = [w for w in unused["sources"]["websites"] if w.get("priority") == "required"]
    assert [w["url"] for w in required] == ["https://jamais.org/vu"]


async def test_sources_exclusions(monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(monkeypatch, nominal())
    await h.post({"topic": QUERY, "sources": {"exclusions": ["example.net"]}})
    requested = h.extractor.urls_requested()
    assert requested and not any("example.net" in u for u in requested)

    h2 = Harness(monkeypatch, nominal())
    await h2.post({"topic": QUERY})
    assert any("example.net" in u for u in h2.extractor.urls_requested())


async def test_sources_domains_whitelist(monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(monkeypatch, nominal())
    await h.post({"topic": QUERY, "sources": {"domains_whitelist": ["example.org"]}})
    requested = h.extractor.urls_requested()
    assert requested and all("example.org" in u for u in requested)


# --- Cause d'une section sans source : incident de collecte ou sujet pauvre ------


def _two_sourceless_sections() -> list[SectionSpec]:
    # Deux sections sur trois sans source : le rapport est degrade (ratio 0,5).
    specs = sourceless_first_section()
    specs[1] = SectionSpec("Costs", "solar costs")
    return specs


async def test_section_sans_source_sur_sujet_pauvre(monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(monkeypatch, _two_sourceless_sections())
    body = (await h.post({"topic": QUERY})).json()

    assert body["degraded"] is True
    assert body["unsourced_sections"] == ["Overview", "Costs"]
    assert "la collecte n'a rien remonte sur ce sujet" in body["error"]
    assert "incident de collecte" not in body["error"]


async def test_section_sans_source_apres_panne_de_recherche(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.search_service import SearchUnavailableError

    h = Harness(monkeypatch, nominal())
    h.search.add("solar overview", SearchUnavailableError("SearXNG ne repond pas"))
    h.search.add("solar costs", SearchUnavailableError("SearXNG ne repond pas"))
    body = (await h.post({"topic": QUERY})).json()

    assert body["degraded"] is True
    assert body["unsourced_sections"] == ["Overview", "Costs"]
    assert "incident de collecte" in body["error"] and "a reessayer" in body["error"]
    assert "collecte.recherche_section_echec" in body["error"]
    assert "la collecte n'a rien remonte" not in body["error"]


def test_message_distingue_incident_et_sujet_pauvre() -> None:
    from app.api.v1.endpoints.research_deep import _degradation_error

    diagnostics = {
        "events": {
            "counts": {"collecte.budget_section_depasse": 1, "collecte.resultats_hors_sujet": 4},
            "events": [
                {"event": "collecte.budget_section_depasse", "details": {"section": "A"}},
            ],
            "events_dropped": 0,
        }
    }
    message = _degradation_error([], ["A", "B"], [], False, 600, diagnostics=diagnostics)
    assert message is not None
    assert "1 section(s) sans source exploitable apres un incident de collecte" in message
    assert "collecte.budget_section_depasse x1" in message
    assert "1 section(s) sans source exploitable : la collecte n'a rien remonte" in message

    # Exploration en panne : incident global, toutes les sections lui sont attribuees.
    diagnostics["events"]["counts"] = {"collecte.exploration_echec": 1}
    diagnostics["events"]["events"] = []
    message = _degradation_error([], ["A", "B"], [], False, 600, diagnostics=diagnostics)
    assert message is not None and "2 section(s) sans source exploitable apres un incident" in message
    assert "rien remonte" not in message

    # Sans diagnostics : constat sur le sujet, comme avant.
    plain = _degradation_error([], ["A"], [], False, 600)
    assert plain is not None and "la collecte n'a rien remonte sur ce sujet" in plain


# --- Schema -----------------------------------------------------------------------

REMOVED_FIELDS = [
    {"max_steps": 10},
    {"max_results": 10},
    {"context": {"min_sources": 5}},
    {"context": {"date_range": "week"}},
    {"context": {"include_data": True}},
    {"context": {"include_reports": True}},
    {"context": {"include_academic": True}},
    {"sources": {"suggested": ["https://a.org"]}},
    {"output_format": {"include_charts": True}},
    {"output_format": {"include_timeline": True}},
    {"output_format": {"structure": "report"}},
    {"champ_inconnu": 1},
]


@pytest.mark.parametrize("extra", REMOVED_FIELDS, ids=lambda e: str(e))
async def test_champ_retire_est_refuse(harness: Harness, extra: dict[str, Any]) -> None:
    resp = await harness.post({"topic": QUERY, **extra})
    assert resp.status_code == 422
    assert harness.factory_calls == []


def test_schema_sans_champ_fantome() -> None:
    schema = research_deep.DeepResearchRequest.model_json_schema()
    assert set(schema["properties"]) == {
        "topic", "objectives", "context", "sources", "output_format", "timeout",
        "max_sources_per_section", "output_sections",
    }
    defs = schema["$defs"]
    assert set(defs["DeepResearchContext"]["properties"]) == {"language"}
    assert set(defs["SourcesConstraints"]["properties"]) == {
        "required", "exclusions", "domains_whitelist"
    }
    assert set(defs["OutputFormat"]["properties"]) == {"sections", "include_section_data"}
    for model in (schema, *defs.values()):
        assert model["additionalProperties"] is False


async def test_diagnostics_replay_expose_les_manques(
    harness: Harness, tmp_path: Any
) -> None:
    from app.research.replay import build_replay_clients

    search, extractor = build_replay_clients("replay", tmp_path, None, None)
    harness.orch.search_client = search
    harness.orch.extractor_manager = extractor
    body = (await harness.post({"topic": QUERY})).json()
    replay = body["diagnostics"]["replay"]
    assert replay["mode"] == "replay"
    assert replay["missed"] > 0
    assert set(replay) == {"mode", "served", "missed", "recorded"}
