"""Lot R2 : correctifs de la relecture de la vague 3 (orchestrateur, citations)."""

import json
from typing import Any

from app.agents.intelligent_orchestrator import ExecutionContext, IntelligentOrchestrator
from app.research.pipeline_config import PipelineConfig

from tests.fakes import FakeExtractorManager, FakeLLM, FakeSearch, make_result

URL_OLD = "https://old.example.org/a"
URL_NEW = "https://new.example.org/b"


def _report() -> dict[str, Any]:
    return {
        "title": "T",
        "summary": "",
        "sections": [
            {
                "title": "S1",
                "content": f"Texte court [SOURCE:{URL_OLD}] fin.",
                "data": [{"source": URL_OLD}],
                "metadata": {"word_count": 4, "sources_count": 1},
            }
        ],
    }


async def _enrichi(provided: bool) -> tuple[dict[str, Any], ExecutionContext]:
    cfg = PipelineConfig(
        validate_cited_urls=True,
        regenerate_v2=True,
        targeted_search_skip_structured=True,
    )
    llm = FakeLLM()
    search = FakeSearch(default=[make_result(URL_NEW, "Nouvelle")])
    pages = FakeExtractorManager({URL_NEW: "contenu " * 400})
    orch = IntelligentOrchestrator(
        llm, search_client=search, extractor_manager=pages, config=cfg
    )
    ctx = ExecutionContext("requete", config=cfg)
    llm.on_text(
        r"Analyse la qualit",
        json.dumps({
            "overall_score": 40,
            "sections_analysis": [{"title": "S1", "score": 30, "missing": ["chiffres"]}],
            "gaps_to_fill": [],
        }),
    )
    llm.on_structured(
        "section_regeneree",
        {"content": f"Fait ancien [SOURCE:{URL_OLD}] et fait nouveau [SOURCE:{URL_NEW}]."},
    )
    report = await orch._iterative_enrichment("requete", ctx, _report(), max_iterations=1)
    assert URL_NEW in ctx.discovered_sources
    provided_urls = ctx.discovered_sources if provided else ()
    report = orch._convert_sources_to_bibliography(
        report, ctx.diagnostics, provided_urls=provided_urls
    )
    return report, ctx


async def test_c4_url_de_la_recherche_ciblee_conservee_apres_enrichissement() -> None:
    report, ctx = await _enrichi(provided=True)
    urls = {b["url"] for b in report["bibliography"]}
    assert URL_NEW in urls
    assert "[SOURCE:" not in report["sections"][0]["content"]
    assert ctx.diagnostics.count("citations.citation_retiree") == 0
    assert ctx.diagnostics.count("citations.url_rapprochee") == 0


async def test_c4_sans_urls_fournies_la_citation_est_ecartee() -> None:
    # Reproduit le defaut : sans l'ensemble fourni, URL_NEW est retiree.
    report, _ = await _enrichi(provided=False)
    assert URL_NEW not in {b["url"] for b in report["bibliography"]}


# --- Rapport partiel aligne sur l'assemblage final (R2-7) --------------------


def _ctx_partiel(cfg: PipelineConfig) -> tuple[IntelligentOrchestrator, ExecutionContext]:
    orch = IntelligentOrchestrator(
        FakeLLM(),
        search_client=FakeSearch(),
        extractor_manager=FakeExtractorManager(),
        config=cfg,
    )
    ctx = ExecutionContext("Question principale sur le solaire. Details en plus.", config=cfg)
    ctx.final_content["sections"] = {
        "Bonne": {"content": f"Premiere phrase. Seconde phrase. Troisieme [SOURCE:{URL_OLD}].",
                  "raw_data": [{"source": URL_OLD}]},
        "Echec": {"content": "[SYNTHESE_ECHOUEE] panne", "raw_data": []},
        "SansSource": {"content": "[SECTION_SANS_SOURCE] rien", "raw_data": []},
        "Vide": {"content": "", "raw_data": []},
    }
    return orch, ctx


def test_partiel_titre_resume_et_listes_comme_l_assemblage() -> None:
    orch, ctx = _ctx_partiel(PipelineConfig(clean_title=True))
    report = orch._partial_report(ctx.query, ctx)
    assert report["title"] == "Question principale sur le solaire"
    assert report["summary"] == "Premiere phrase. Seconde phrase."
    assert report["failed_sections"] == ["Echec"]
    assert report["unsourced_sections"] == ["SansSource"]
    assert report["missing_sections"] == ["Vide"]
    assert report["degraded"] is True
    assert report["metadata"]["failed_sections_count"] == 1


def test_partiel_resume_vide_si_summary_mode_none() -> None:
    orch, ctx = _ctx_partiel(PipelineConfig(summary_mode="none"))
    report = orch._partial_report(ctx.query, ctx)
    assert report["summary"] == ""
    assert report["title"].startswith("Analyse: ")


# --- supports_structured plutot que hasattr (R2-10) --------------------------


async def test_intersections_sans_sortie_structuree_utilise_generate() -> None:
    orch, _ = _ctx_partiel(PipelineConfig())
    llm = orch.llm_client
    llm.supports_structured = False
    llm.on_text(r".", json.dumps({"improvements": [], "redundancies": [], "coherence_score": 80}))
    ctx = ExecutionContext("q", config=PipelineConfig())
    ctx.final_content["sections"] = {"A": {"content": "x"}}
    await orch._analyze_intersections("q", {"sections": ["A"]}, ctx)
    assert llm.calls_for(kind="structured") == []
    assert llm.calls_for(kind="generate")


# --- Rejeu : cle sans moteurs, bilan dans les diagnostics (R2-5) -------------


async def test_rejeu_cle_independante_des_moteurs(tmp_path: Any) -> None:
    from app.research.replay import build_replay_clients

    fake = FakeSearch({"q": [make_result("https://a.org/x", "T")]})
    rec, _ = build_replay_clients("record", tmp_path, fake, FakeExtractorManager())
    res = await rec.search("q", engines="google,bing")
    rep, _ = build_replay_clients("replay", tmp_path, None, None)
    assert await rep.search("q", engines="wikipedia") == res
    assert await rep.search("q") == res
    assert rep.missed == 0


def test_replay_summary_compte_les_manques(tmp_path: Any) -> None:
    from app.research.replay import build_replay_clients, replay_summary

    assert replay_summary(FakeSearch(), FakeExtractorManager()) is None
    search, extractor = build_replay_clients("replay", tmp_path, None, None)
    search.missed = 2
    search.served = 3
    assert replay_summary(search, extractor) == {
        "mode": "replay", "served": 3, "missed": 2, "recorded": 0,
    }


def test_plan_temperature_est_un_champ_de_configuration() -> None:
    # Nom documente dans benchmarks/replay.md (WEBTOOLS_PIPELINE_PLAN_TEMPERATURE).
    assert "plan_temperature" in PipelineConfig().to_dict()


# --- Sources requises : seule la bibliographie compte (R2-8) -----------------


def test_source_requise_citee_hors_bibliographie_n_est_pas_utilisee() -> None:
    from app.api.v1.endpoints.research_deep import SourcesConstraints, _register_sources
    from app.core.source_registry import SourceRegistry

    url = "https://req.org/x"
    answer: dict[str, Any] = {
        "sections": [{"data": [{"source": url}]}],
        "research_traces": {"x": url},
        "bibliography": [{"id": 1, "url": "https://autre.org/y"}],
    }
    registry = SourceRegistry()
    _register_sources(registry, SourcesConstraints(required=[url]), {}, answer)
    assert registry.required_sources_status() == "partial_failure"
    answer["bibliography"].append({"id": 2, "url": url})
    registry2 = SourceRegistry()
    _register_sources(registry2, SourcesConstraints(required=[url]), {}, answer)
    assert registry2.required_sources_status() == "all_success"
