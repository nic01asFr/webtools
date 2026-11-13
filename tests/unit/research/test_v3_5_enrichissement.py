"""Lot V3-5 : drapeaux d'enrichissement et de coherence, regeneration v2."""

import json
from typing import Any

import pytest
from app.agents.intelligent_orchestrator import ExecutionContext, IntelligentOrchestrator
from app.core.llm.base import LLMEmptyContentError
from app.research.pipeline_config import PipelineConfig
from app.research.stages.coherence import apply_coherence_improvements
from app.research.stages.regenerate import regenerate_section
from app.research.stages.enrich import analyze_report_quality, iterative_enrichment

from tests.fakes import FakeExtractorManager, FakeLLM, FakeSearch

URL_OLD = "https://old.example.org/a"
URL_NEW = "https://new.example.org/b"


def make(config: PipelineConfig | None = None) -> tuple[IntelligentOrchestrator, FakeLLM]:
    llm = FakeLLM()
    orch = IntelligentOrchestrator(
        llm,
        search_client=FakeSearch(),
        extractor_manager=FakeExtractorManager(),
        config=config or PipelineConfig(),
    )
    return orch, llm


def make_ctx(config: PipelineConfig | None = None) -> ExecutionContext:
    # Enrichissement coupe par defaut depuis le 13/11/2026 : ces tests le testent.
    return ExecutionContext("requete", config=config or PipelineConfig(enrichment_enabled=True))


def make_report() -> dict[str, Any]:
    return {
        "sections": [
            {
                "title": "S1",
                "content": "Texte court [1] fin.",
                "data": [{"source": URL_OLD}],
                "metadata": {"word_count": 4, "sources_count": 1},
            }
        ],
        "bibliography": [{"id": 1, "url": URL_OLD, "title": "t"}],
    }


# --- enrichment_enabled ------------------------------------------------------


@pytest.mark.parametrize("enabled", [True, False])
async def test_enrichment_enabled(enabled: bool) -> None:
    cfg = PipelineConfig(enrichment_enabled=enabled)
    orch, llm = make(cfg)
    ctx = make_ctx(cfg)
    llm.on_text(
        r"Analyse la qualit",
        json.dumps({"overall_score": 95, "sections_analysis": [], "gaps_to_fill": []}),
    )
    await iterative_enrichment(orch.services(), "requete", ctx, make_report(), max_iterations=1)
    assert bool(llm.calls_for(pattern=r"Analyse la qualit")) is enabled
    assert (ctx.diagnostics.count("enrichissement.desactive") == 1) is (not enabled)


# --- coherence_append_enabled ------------------------------------------------


@pytest.mark.parametrize("enabled", [True, False])
async def test_coherence_append_enabled(enabled: bool) -> None:
    cfg = PipelineConfig(coherence_append_enabled=enabled)
    orch, _ = make(cfg)
    ctx = make_ctx(cfg)
    ctx.final_content["sections"] = {"A": {"content": "debut"}, "B": {"content": "suite"}}
    analysis = {
        "improvements": [
            {
                "type": "transition",
                "between_sections": ["A", "B"],
                "issue": "x",
                "suggestion": "AJOUT",
                "priority": "high",
            }
        ]
    }
    await apply_coherence_improvements(analysis, ctx)
    assert ("AJOUT" in ctx.final_content["sections"]["A"]["content"]) is enabled
    assert ctx.diagnostics.count("coherence.transition_collee") == (1 if enabled else 0)
    assert ctx.diagnostics.count("coherence.append_desactive") == (0 if enabled else 1)


# --- regenerate_v2 -----------------------------------------------------------


def _enrichment() -> dict[str, Any]:
    return {"sources": [{"url": URL_NEW, "content": "x" * 5000}], "missing": ["chiffres"]}


async def _regen(cfg: PipelineConfig, structured: Any) -> tuple[Any, FakeLLM, ExecutionContext]:
    orch, llm = make(cfg)
    ctx = make_ctx(cfg)
    llm.on_structured("section_regeneree", structured)
    llm.on_text(r"Am.liore cette section", json.dumps({"title": "S1", "content": "legacy"}))
    result = await regenerate_section(orch.services(), "requete", "S1", make_report(), _enrichment(), ctx)
    return result, llm, ctx


async def test_regenerate_v1_par_defaut() -> None:
    result, llm, _ = await _regen(PipelineConfig(), {"content": "v2"})
    assert result["content"] == "legacy"
    assert "data" not in result  # defaut connu de v1 (C43)
    assert not llm.calls_for(kind="structured")


async def test_regenerate_v2_conserve_citations_data_metadata_et_langue() -> None:
    content = f"Texte enrichi [SOURCE:{URL_OLD}] et chiffres [SOURCE:{URL_NEW}]."
    result, llm, ctx = await _regen(PipelineConfig(regenerate_v2=True), {"content": content})
    assert f"[SOURCE:{URL_OLD}]" in result["content"]
    assert f"[SOURCE:{URL_NEW}]" in result["content"]
    assert result["data"] == [{"source": URL_OLD}]
    assert result["metadata"]["sources_count"] == 1
    assert result["metadata"]["word_count"] == len(result["content"].split())
    call = llm.calls_for(kind="structured")[0]
    # citation d'origine [1] presentee au modele sous forme d'URL
    assert f"[SOURCE:{URL_OLD}]" in call.prompt and "Redige" in call.prompt
    # donnees non coupees a 3000 caracteres
    assert "x" * 4000 in call.prompt
    assert ctx.diagnostics.count("enrichissement.regeneration_v2") == 1


async def test_regenerate_v2_langue_de_la_requete() -> None:
    cfg = PipelineConfig(regenerate_v2=True)
    orch, llm = make(cfg)
    ctx = make_ctx(cfg)
    ctx.search_language = "fr"
    llm.on_structured("section_regeneree", {"content": f"ok [SOURCE:{URL_OLD}]"})
    await regenerate_section(orch.services(), "requete", "S1", make_report(), _enrichment(), ctx)
    assert "langue 'fr'" in llm.calls_for(kind="structured")[0].prompt


async def test_regenerate_v2_retire_les_citations_inventees() -> None:
    content = f"A [SOURCE:{URL_OLD}] B [SOURCE:https://invente.example/z]"
    result, _, ctx = await _regen(PipelineConfig(regenerate_v2=True), {"content": content})
    assert "invente.example" not in result["content"]
    assert ctx.diagnostics.count("enrichissement.citations_inventees_retirees") == 1


@pytest.mark.parametrize(
    "structured,raison",
    [
        ({"content": "Plus aucune citation."}, "citations_perdues"),
        ({"content": "  "}, "contenu_vide"),
        (LLMEmptyContentError("vide"), "erreur_llm"),
    ],
)
async def test_regenerate_v2_repli_sur_la_section_d_origine(structured: Any, raison: str) -> None:
    original = make_report()["sections"][0]
    result, _, ctx = await _regen(PipelineConfig(regenerate_v2=True), structured)
    assert result == original
    event = ctx.diagnostics.to_dict()["events"][-1]
    assert event["event"] == "enrichissement.regeneration_v2_repli"
    assert event["details"]["raison"] == raison


# --- scores absents ----------------------------------------------------------


async def test_analyse_qualite_en_echec_rend_un_score_absent() -> None:
    orch, llm = make()
    ctx = make_ctx()
    llm.on_text(r"Analyse la qualit", LLMEmptyContentError("vide"))
    analysis = await analyze_report_quality(orch.services(), "q", make_report(), ctx)
    assert analysis["overall_score"] is None
    assert ctx.diagnostics.count("enrichissement.score_absent") == 1


async def test_analyse_coherence_en_echec_rend_un_score_absent() -> None:
    orch, llm = make()
    ctx = make_ctx()
    llm.on_structured("coherence_analysis", LLMEmptyContentError("vide"))
    analysis = await orch._analyze_intersections("q", {}, ctx)
    assert analysis["coherence_score"] is None
    assert ctx.diagnostics.count("coherence.score_absent") == 1


async def test_enrichissement_sans_score_ne_plante_pas_et_note() -> None:
    orch, llm = make()
    ctx = make_ctx()
    llm.on_text(r"Analyse la qualit", LLMEmptyContentError("vide"))
    out = await iterative_enrichment(orch.services(), "q", ctx, make_report(), max_iterations=1)
    assert out["sections"][0]["title"] == "S1"
    assert ctx.diagnostics.count("enrichissement.iteration") == 1
