"""Lot V3-2 : assemblage du rapport (resume, titre, summary_mode)."""

import pytest
from app.agents.intelligent_orchestrator import ExecutionContext, IntelligentOrchestrator
from app.research.pipeline_config import PipelineConfig
from app.research.stages.assemble import final_assembly
from app.research.report_title import short_title

from tests.fakes import FakeExtractorManager, FakeLLM, FakeSearch

QUERY = "Impact of solar energy on rural electricity access"
GOOD = "Solar kits changed daily life in many districts. Financing stays the main barrier."
MARKED = "[SECTION_SANS_SOURCE] Aucune donnee exploitable pour cette section."


def _orchestrator(config: PipelineConfig) -> IntelligentOrchestrator:
    return IntelligentOrchestrator(
        FakeLLM(),
        search_client=FakeSearch(),
        extractor_manager=FakeExtractorManager(),
        config=config,
    )


def _context(query: str, contents: dict[str, str], config: PipelineConfig) -> ExecutionContext:
    ctx = ExecutionContext(query, config=config)
    ctx.final_content = {
        "sections": {n: {"content": c, "raw_data": []} for n, c in contents.items()}
    }
    return ctx


async def _assemble(
    contents: dict[str, str], query: str = QUERY, **flags: object
) -> tuple[dict, ExecutionContext]:
    config = PipelineConfig(**flags)
    ctx = _context(query, contents, config)
    plan = {"sections": list(contents)}
    report = await final_assembly(_orchestrator(config).services(), query, plan, ctx)
    return report, ctx


async def test_resume_ignore_la_section_marquee() -> None:
    report, _ = await _assemble({"A": MARKED, "B": GOOD})
    assert "SECTION_SANS_SOURCE" not in report["summary"]
    assert report["summary"].startswith("Solar kits changed")
    assert report["unsourced_sections"] == ["A"]
    assert [s["title"] for s in report["sections"]] == ["B"]


async def test_degraded_et_champs_inchanges() -> None:
    report, _ = await _assemble({"A": MARKED, "B": MARKED, "C": GOOD})
    assert report["degraded"] is True
    assert report["unsourced_sections"] == ["A", "B"]
    report, _ = await _assemble({"A": MARKED, "B": GOOD, "C": GOOD})
    assert "degraded" not in report
    report, _ = await _assemble({"A": "[SYNTHESE_ECHOUEE] x", "B": GOOD})
    assert report["degraded"] is True
    assert report["failed_sections"] == ["A"]


async def test_titre_par_defaut_inchange() -> None:
    report, _ = await _assemble({"A": GOOD})
    assert report["title"] == f"Analyse: {QUERY.title()}"


async def test_clean_title_actif() -> None:
    long_query = (
        "Quel est l'impact de l'energie solaire sur l'acces a l'electricite en zone rurale ? " * 3
    )
    report, _ = await _assemble({"A": GOOD}, query=long_query, clean_title=True)
    assert (
        report["title"]
        == "Quel est l'impact de l'energie solaire sur l'acces a l'electricite en zone…"
    )
    assert not report["title"].startswith("Analyse")


async def test_clean_title_consigne_vide_repli_note() -> None:
    report, ctx = await _assemble({"A": GOOD}, query="  ", clean_title=True)
    assert report["title"].startswith("Analyse")
    assert ctx.diagnostics.count("assemblage.titre_repli") == 1


def test_short_title_coupe_au_mot() -> None:
    title = short_title("word " * 60)
    assert len(title) <= 80
    # Un titre coupe le signale par des points de suspension.
    assert title.endswith("word…")
    assert short_title("") == ""


async def test_summary_mode_extract_defaut() -> None:
    report, _ = await _assemble({"A": GOOD})
    assert report["summary"]


async def test_summary_mode_none() -> None:
    report, _ = await _assemble({"A": GOOD}, summary_mode="none")
    assert report["summary"] == ""


def test_summary_mode_invalide() -> None:
    with pytest.raises(ValueError):
        PipelineConfig(summary_mode="bref")
    assert (
        PipelineConfig.from_env({"WEBTOOLS_PIPELINE_SUMMARY_MODE": "none"}).summary_mode == "none"
    )
    with pytest.raises(ValueError):
        PipelineConfig.from_env({"WEBTOOLS_PIPELINE_SUMMARY_MODE": "x"})


# --- Resume, nombre de sources, bibliographie apres retrait (audit structure) --


async def test_resume_ne_coupe_pas_sur_une_abreviation_ni_ne_reprend_un_titre() -> None:
    contenu = (
        "### Cadre juridique\n"
        "Selon M. Dupont, l'art. 3 du code fixe le seuil a 7,3 %. "
        "Le texte est entre en vigueur en 2025. Une troisieme phrase suit."
    )
    report, _ = await _assemble({"A": contenu})
    assert report["summary"] == (
        "Selon M. Dupont, l'art. 3 du code fixe le seuil a 7,3 %. "
        "Le texte est entre en vigueur en 2025."
    )


def _ctx_with_data(sections: dict[str, tuple[str, list[dict]]], config: PipelineConfig) -> ExecutionContext:
    ctx = ExecutionContext(QUERY, config=config)
    ctx.final_content = {
        "sections": {n: {"content": c, "raw_data": d} for n, (c, d) in sections.items()}
    }
    return ctx


async def test_sources_count_compte_les_sources_distinctes() -> None:
    url_a, url_b = "https://a.org/p", "https://b.org/q"
    passages = [{"source": url_a}, {"source": url_a + "/"}, {"source": url_a}, {"source": url_b}]
    config = PipelineConfig()
    ctx = _ctx_with_data({"A": (f"Texte [SOURCE:{url_a}].", passages)}, config)
    report = await final_assembly(_orchestrator(config).services(), QUERY, {"sections": ["A"]}, ctx)
    assert report["sections"][0]["metadata"]["sources_count"] == 2


async def test_bibliographie_construite_apres_le_retrait_des_sections_marquees() -> None:
    bonne, echec = "https://bonne.org/p", "https://echec.org/q"
    config = PipelineConfig()  # bibliography_cited_only=False
    ctx = _ctx_with_data(
        {
            "Echec": ("[SYNTHESE_ECHOUEE] Section 'Echec' non generee (panne).", [{"source": echec}]),
            "Bonne": (f"Fait etabli [SOURCE:{bonne}].", [{"source": bonne}]),
        },
        config,
    )
    plan = {"sections": ["Echec", "Bonne"]}
    report = await final_assembly(_orchestrator(config).services(), QUERY, plan, ctx)
    assert [b["url"] for b in report["bibliography"]] == [bonne]
    assert report["sections"][0]["content"] == "Fait etabli [1]."


async def test_bibliographie_cited_only_inchangee() -> None:
    citee, non_citee = "https://a.org/p", "https://b.org/q"
    config = PipelineConfig(bibliography_cited_only=True)
    ctx = _ctx_with_data(
        {"A": (f"Fait [SOURCE:{citee}].", [{"source": citee}, {"source": non_citee}])}, config
    )
    report = await final_assembly(_orchestrator(config).services(), QUERY, {"sections": ["A"]}, ctx)
    assert [b["url"] for b in report["bibliography"]] == [citee]



async def test_section_planifiee_non_redigee_comptee_manquante() -> None:
    config = PipelineConfig()
    ctx = _context(QUERY, {"A": GOOD, "B": ""}, config)
    plan = {"sections": ["A", "B", "C"]}  # C n'a jamais ete initialisee
    report = await final_assembly(_orchestrator(config).services(), QUERY, plan, ctx)
    assert [s["title"] for s in report["sections"]] == ["A"]
    assert report["missing_sections"] == ["B", "C"]
    assert report["metadata"]["missing_sections_count"] == 2
    assert report["degraded"] is True
    assert ctx.diagnostics.count("assemblage.section_non_redigee") == 2
