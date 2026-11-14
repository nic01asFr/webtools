"""Lot V4-2 : un test unitaire par etape du pipeline, avec les doublures.

Chaque etape (app/research/stages/) est appelee seule, avec des services
construits sur FakeLLM, FakeSearch et FakeExtractorManager. Les derniers
tests couvrent l'etat propre a une requete et les modeles de frontiere.
"""

import dataclasses
import json
from typing import Any

import pytest
from app.agents.intelligent_orchestrator import ExecutionContext, IntelligentOrchestrator
from app.research.models import Passage, Plan, Report, SectionDraft, Source, check_boundary
from app.research.pipeline_config import PipelineConfig
from app.research.stages.assemble import final_assembly, report_title, strip_source_markers
from app.research.stages.coherence import analyze_intersections, apply_coherence_improvements
from app.research.stages.collect import collect_section_data
from app.research.stages.crossref import cross_reference_data
from app.research.stages.enrich import iterative_enrichment
from app.research.stages.explore import exploratory_phase
from app.research.stages.extract import section_extraction_phase
from app.research.stages.plan import create_detailed_plan
from app.research.stages.queries import (
    build_search_query,
    filter_by_constraints,
    search_pool_size,
)
from app.research.stages.regenerate import regenerate_section
from app.research.stages.search import section_research_phase, targeted_search
from app.research.stages.select import semantic_chunk_selection
from app.research.stages.services import StageServices
from app.research.stages.synthesize import synthesize_single_section, write_sections

from tests.contract.test_pipeline_contract import (
    QUERY,
    SectionSpec,
    _plan,
    build_orchestrator,
    nominal,
)
from tests.fakes import FakeExtractorManager, FakeLLM, FakeSearch, make_result

PAGE = "Field report on solar kits in remote districts. " * 40
SECTION_CONFIG = {
    "depth": "moderate",
    "words_target": 400,
    "objectives": ["Couvrir le sujet"],
    "key_questions": ["Question ?"],
    "search_terms": ["solar costs"],
}


@pytest.fixture(autouse=True)
def _sans_filtre_de_pertinence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ces tests portent sur la mecanique de collecte, avec des titres
    factices : le filtre de pertinence (C96) a ses propres tests."""
    import app.research.stages.relevance as relevance

    monkeypatch.setattr(relevance, "keep_relevant", lambda _q, results: (list(results or []), 0))



async def _no_sleep(_delay: float) -> None:
    return None


def make(
    llm: FakeLLM | None = None,
    search: FakeSearch | None = None,
    pages: FakeExtractorManager | None = None,
    config: PipelineConfig | None = None,
) -> tuple[StageServices, ExecutionContext]:
    orch = IntelligentOrchestrator(
        llm or FakeLLM(),
        search_client=search or FakeSearch(),
        extractor_manager=pages or FakeExtractorManager(),
        config=config or PipelineConfig(),
    )
    svc = dataclasses.replace(orch.services(), sleep=_no_sleep)
    return svc, ExecutionContext(QUERY, config=svc.config)


def events(ctx: ExecutionContext, name: str) -> list[dict[str, Any]]:
    return [e for e in ctx.diagnostics.to_dict()["events"] if e["event"] == name]


def item(url: str, content: str = PAGE) -> dict[str, Any]:
    return {"source": url, "url": url, "title": f"Titre {url}", "content": content}


# --- queries -----------------------------------------------------------------


def test_queries_requete_reduite_et_contraintes_de_domaines() -> None:
    svc, ctx = make()
    long_query = " ".join(f"mot{i}" for i in range(40))
    assert len(build_search_query(svc, query=long_query, section_name="").split()) < 40

    sources = [{"url": "https://bad.com/a"}, {"url": "https://good.org/b"}]
    assert filter_by_constraints(sources, ctx) == sources
    ctx.domains_exclusions = ["bad.com"]
    assert filter_by_constraints(sources, ctx) == [{"url": "https://good.org/b"}]
    assert search_pool_size(svc, 5, ctx) == 5 * svc.config.search_pool_multiplier


# --- explore -----------------------------------------------------------------


async def test_explore_decouvre_les_sources_et_evalue_le_champ() -> None:
    search = FakeSearch(
        default=[make_result(f"https://ex.org/{i}", "T", "solar kits") for i in range(3)]
    )
    svc, ctx = make(search=search)
    data = await exploratory_phase(svc, QUERY, {}, ctx=ctx)
    assert [s["url"] for s in data["sources"]] == [f"https://ex.org/{i}" for i in range(3)]
    assert data["field_assessment"] in {"rich", "moderate", "limited"}
    assert len(search.queries) == 1


async def test_explore_echec_de_recherche_rend_un_champ_inconnu() -> None:
    class Broken:
        async def search(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("moteur hors service")

    svc, ctx = make()
    svc = dataclasses.replace(svc, search_client=Broken())
    data = await exploratory_phase(svc, QUERY, {}, ctx=ctx)
    assert data["field_assessment"] == "unknown" and data["sources"] == []
    assert len(events(ctx, "collecte.exploration_echec")) == 1


# --- plan --------------------------------------------------------------------


async def test_plan_structure_et_repli() -> None:
    llm = FakeLLM().on_structured("research_plan", _plan([SectionSpec("Overview", "solar", [])]))
    svc, ctx = make(llm)
    plan = await create_detailed_plan(svc, QUERY, {}, {"sources": []}, ctx=ctx)
    assert plan["sections"] == ["Overview"]
    assert plan["section_targets"]["Overview"]["search_terms"] == ["solar"]
    assert not plan.get("fallback")

    svc, ctx = make(FakeLLM())  # aucun scenario : l'appel echoue
    plan = await create_detailed_plan(svc, QUERY, {}, {"sources": []}, ctx=ctx)
    assert plan["fallback"] is True
    assert events(ctx, "plan.repli")


# --- search ------------------------------------------------------------------


async def test_search_section_et_recherche_ciblee() -> None:
    url = "https://ex.org/a"
    search = FakeSearch(default=[make_result(url, "T", "s")])
    svc, ctx = make(search=search, pages=FakeExtractorManager({url: PAGE}))

    data = await section_research_phase(svc, QUERY, "S", SECTION_CONFIG, {}, ctx)
    assert [s["url"] for s in data["sources"]] == [url]

    targeted = await targeted_search(svc, QUERY, "S", ["prix"], ctx)
    assert targeted is not None and targeted["missing"] == ["prix"]
    assert all(c["url"] == url for c in targeted["sources"])
    assert url in ctx.discovered_sources


# --- extract -----------------------------------------------------------------


async def test_extract_rend_les_passages_et_remplit_le_contexte() -> None:
    url = "https://ex.org/a"
    svc, ctx = make(pages=FakeExtractorManager({url: PAGE}))
    ctx.initialize_sections(["S"])
    passages = await section_extraction_phase(
        svc, "S", {"sources": [{"url": url, "title": "T"}]}, ctx
    )
    assert passages and all(p["source"] == url for p in passages)
    assert "Field report" in passages[0]["content"]


# --- crossref ----------------------------------------------------------------


async def test_crossref_annote_les_passages() -> None:
    svc, ctx = make()
    data = [item("https://a.org/x"), item("https://b.org/y", PAGE + " autre fin")]
    enriched = await cross_reference_data(svc, "S", data, ctx)
    assert len(enriched) == 2
    assert all("corroboration_count" in e for e in enriched)


# --- select ------------------------------------------------------------------


async def test_select_retient_des_passages_du_lot_et_ignore_les_vides() -> None:
    svc, ctx = make()
    data = [item(f"https://s{i}.org/a", PAGE[: 400 + i]) for i in range(4)]
    data.append({"source": "https://vide.org", "content": ""})
    selected = await semantic_chunk_selection(svc, data, QUERY, context=ctx)
    assert selected
    assert all(any(s is d for d in data) for s in selected)
    assert all(s["content"] for s in selected)


# --- synthesize --------------------------------------------------------------


async def test_synthesize_redige_puis_marque_l_echec() -> None:
    llm = FakeLLM().on_text("Rédige la section", "Texte redige [SOURCE:https://a.org/x]. " * 20)
    svc, ctx = make(llm)
    text = await synthesize_single_section(
        svc, QUERY, "S", SECTION_CONFIG, [item("https://a.org/x")], ctx
    )
    assert "Texte redige" in text

    svc, ctx = make(FakeLLM())
    text = await synthesize_single_section(
        svc, QUERY, "S", SECTION_CONFIG, [item("https://a.org/x")], ctx
    )
    assert text.startswith("[SYNTHESE_ECHOUEE]")


async def test_write_sections_remplit_le_canvas() -> None:
    llm = FakeLLM().on_text("Rédige la section", "Texte redige [SOURCE:https://a.org/x]. " * 20)
    svc, ctx = make(llm)
    ctx.initialize_sections(["S"])
    await write_sections(svc, ctx, QUERY, {"S": SECTION_CONFIG}, {"S": [item("https://a.org/x")]})
    section = ctx.final_content["sections"]["S"]
    assert "Texte redige" in section["content"] and section["raw_data"]


# --- coherence ---------------------------------------------------------------


async def test_coherence_analyse_et_application() -> None:
    llm = FakeLLM().on_structured(
        "coherence_analysis", {"improvements": [], "redundancies": [], "coherence_score": 90}
    )
    svc, ctx = make(llm)
    ctx.initialize_sections(["A", "B"])
    analysis = await analyze_intersections(svc, QUERY, {"sections": ["A", "B"]}, ctx)
    assert analysis["improvements"] == []
    await apply_coherence_improvements(analysis, ctx)


# --- enrich / regenerate -----------------------------------------------------


def report_with(content: str) -> dict[str, Any]:
    return {"sections": [{"title": "S1", "content": content}], "bibliography": []}


async def test_enrich_desactive_et_qualite_suffisante() -> None:
    svc, ctx = make(config=PipelineConfig(enrichment_enabled=False))
    report = report_with("court")
    assert await iterative_enrichment(svc, QUERY, ctx, report, max_iterations=1) is report
    assert events(ctx, "enrichissement.desactive")

    good = json.dumps({"overall_score": 95, "sections_analysis": [], "gaps_to_fill": []})
    svc, ctx = make(FakeLLM().on_text("Analyse la qualit", good))
    report = report_with("x" * 5000)
    out = await iterative_enrichment(svc, QUERY, ctx, report, max_iterations=1)
    assert out["sections"][0]["content"] == "x" * 5000


async def test_regenerate_section_inconnue_rend_none() -> None:
    svc, ctx = make()
    assert (
        await regenerate_section(svc, QUERY, "absente", report_with("t"), {"sources": []}, ctx)
        is None
    )


# --- assemble ----------------------------------------------------------------


async def test_assemble_rapport_titre_et_nettoyage() -> None:
    svc, ctx = make()
    ctx.initialize_sections(["S"])
    ctx.final_content["sections"]["S"]["content"] = "Fait etabli [SOURCE:https://a.org/x]. " * 30
    ctx.final_content["sections"]["S"]["raw_data"] = [item("https://a.org/x")]
    plan = {"sections": ["S"], "complexity_analysis": {"overall_score": 2.0}}
    report = await final_assembly(svc, QUERY, plan, ctx)
    assert [s["title"] for s in report["sections"]] == ["S"]
    assert report["bibliography"][0]["url"] == "https://a.org/x"
    assert report["title"] == report_title(QUERY, ctx)

    dirty = {"summary": "Resume [SOURCE:https://a.org/x]", "sections": [{"content": "T [SOURCE:u"}]}
    strip_source_markers(dirty)
    assert dirty == {"summary": "Resume", "sections": [{"content": "T"}]}


# --- collect -----------------------------------------------------------------


async def test_collect_une_entree_par_section_meme_sans_donnee() -> None:
    url = "https://ex.org/a"
    search = FakeSearch().add("solar costs", [make_result(url, "T", "s")])
    svc, ctx = make(search=search, pages=FakeExtractorManager({url: PAGE}))
    ctx.initialize_sections(["Costs", "Vide"])
    plan = {
        "search_strategy": {"sources_per_section": 1},
        "section_targets": {
            "Costs": dict(SECTION_CONFIG),
            "Vide": {**SECTION_CONFIG, "search_terms": ["rien"]},
        },
    }
    by_section = await collect_section_data(svc, ctx, QUERY, plan, {"sources": []})
    assert set(by_section) == {"Costs", "Vide"}
    assert by_section["Costs"] and not by_section["Vide"]


async def test_budget_de_section_court_apres_l_attente_de_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Une seule place, deux sections de 0,6 s chacune, budget de 1 s : la
    # seconde attend 0,6 s sa place. Si l'attente comptait dans le budget,
    # elle sortirait vide (cas d08 du 13/11/2026).
    import asyncio

    from app.core.config import settings

    monkeypatch.setattr(settings, "max_concurrent_extractions", 1)
    url_a, url_b = "https://ex.org/a", "https://ex.org/b"
    search = FakeSearch().add("terme a", [make_result(url_a, "T", "s")])
    search.add("terme b", [make_result(url_b, "T", "s")])
    inner_search = search.search

    async def slow_search(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.6)
        return await inner_search(*args, **kwargs)

    monkeypatch.setattr(search, "search", slow_search)
    config = PipelineConfig(section_budget_min_seconds=1, section_budget_timeout_ratio=0.0)
    svc, ctx = make(search=search, pages=FakeExtractorManager({url_a: PAGE, url_b: PAGE}), config=config)
    ctx.initialize_sections(["A", "B"])
    plan = {
        "search_strategy": {"sources_per_section": 1},
        "section_targets": {
            "A": {**SECTION_CONFIG, "search_terms": ["terme a"]},
            "B": {**SECTION_CONFIG, "search_terms": ["terme b"]},
        },
    }
    by_section = await collect_section_data(svc, ctx, QUERY, plan, {"sources": []})
    assert by_section["A"] and by_section["B"]
    assert ctx.diagnostics.count("collecte.budget_section_depasse") == 0


async def test_budget_de_section_depasse_est_note(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    search = FakeSearch()
    calls: list[str] = []

    async def stalled_search(*_args: Any, **_kwargs: Any) -> Any:
        # Seul le premier appel (celui de la section) reste bloque ; le
        # complement pre-redaction qui suit repond tout de suite.
        calls.append("x")
        if len(calls) == 1:
            await asyncio.sleep(5)
        return []

    monkeypatch.setattr(search, "search", stalled_search)
    config = PipelineConfig(section_budget_min_seconds=1, section_budget_timeout_ratio=0.0)
    svc, ctx = make(search=search, config=config)
    ctx.initialize_sections(["Lente"])
    plan = {"search_strategy": {"sources_per_section": 1}, "section_targets": {"Lente": dict(SECTION_CONFIG)}}
    by_section = await collect_section_data(svc, ctx, QUERY, plan, {"sources": []})
    assert by_section["Lente"] == []
    assert ctx.diagnostics.count("collecte.budget_section_depasse") == 1


# --- Etat propre a une requete -----------------------------------------------


async def test_deux_requetes_successives_n_interferent_pas() -> None:
    sections = nominal()
    orch, _llm, search, _ext = build_orchestrator(sections)
    before = dict(vars(orch))

    excluded = "example.net"
    first = await orch.execute_intelligent_research(QUERY, {"sources_exclusions": [excluded]})
    assert dict(vars(orch)) == before  # aucun attribut ecrit pendant la requete
    cited = {b["url"] for b in first["answer"]["bibliography"]}
    assert cited and not any(excluded in u for u in cited)

    second = await orch.execute_intelligent_research(QUERY)
    cited = {b["url"] for b in second["answer"]["bibliography"]}
    assert any(excluded in u for u in cited)

    third = await orch.execute_intelligent_research(QUERY, {"domains_whitelist": ["example.org"]})
    cited = {b["url"] for b in third["answer"]["bibliography"]}
    assert cited and all("example.org" in u for u in cited)
    assert dict(vars(orch)) == before


async def test_frontieres_typees_sans_ecart_sur_le_scenario_nominal() -> None:
    orch, _llm, _search, _ext = build_orchestrator(nominal())
    result = await orch.execute_intelligent_research(QUERY)
    names = [e["event"] for e in result["diagnostics"]["events"]["events"]]
    assert "pipeline.frontiere_invalide" not in names


# --- Modeles -----------------------------------------------------------------


def test_modeles_round_trip_exact() -> None:
    plan = {
        "complexity_analysis": {"overall_score": 3.0},
        "sections": ["A"],
        "section_targets": {
            "A": {"words_target": 300, "depth": "light", "search_terms": ["x"], "inconnu": 1}
        },
        "narrative_flow": [],
        "search_strategy": {"sources_per_section": 2},
    }
    assert Plan.from_dict(plan).to_dict() == plan
    passage = {"source": "u", "url": "u", "title": "t", "content": "c", "corroboration_count": 2}
    assert Passage.from_dict(passage).to_dict() == passage
    source = {"url": "u", "title": "t", "snippet": "s"}
    assert Source.from_dict(source).to_dict() == source
    draft = {"raw_data": [passage], "content": "c", "metadata": {"data_count": 1}}
    assert SectionDraft.from_dict(draft).to_dict() == draft
    report = {
        "type": "report",
        "title": "T",
        "sections": [{"title": "A", "content": "c", "data": [passage]}],
        "bibliography": [{"url": "u"}],
        "degraded": True,
    }
    assert Report.from_dict(report).to_dict() == report


def test_frontiere_invalide_est_notee_sans_lever() -> None:
    ctx = ExecutionContext("q", config=PipelineConfig())
    assert check_boundary(Plan, {"sections": "pas une liste"}, ctx.diagnostics, "plan") is None
    assert events(ctx, "pipeline.frontiere_invalide")[0]["details"]["etape"] == "plan"
    with pytest.raises(Exception):
        Plan.from_dict({"sections": "pas une liste"})
