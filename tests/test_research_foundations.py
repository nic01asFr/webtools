"""Tests de PipelineConfig, Diagnostics, check_report et des doublures."""

import dataclasses
import re

import pytest
from app.agents.intelligent_orchestrator import ExecutionContext, IntelligentOrchestrator
from app.research.diagnostics import MAX_EVENTS, Diagnostics
from app.research.invariants import check_report
from app.research.pipeline_config import PipelineConfig
from app.services.search_service import SearchResult, searxng_client

from tests.fakes import FakeExtractorManager, FakeLLM, FakeLLMError, FakeSearch, make_result

# --- PipelineConfig ----------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _DemoConfig(PipelineConfig):
    flag: bool = False
    limit: int = 3
    ratio: float = 0.5
    label: str = "base"
    maybe: int | None = None


def test_config_vide_par_defaut() -> None:
    # Chaque champ vaut son defaut : aucune valeur n'est imposee sans variable.
    expected = {f.name: f.default for f in dataclasses.fields(PipelineConfig)}
    assert PipelineConfig().to_dict() == expected
    assert PipelineConfig.from_env({}) == PipelineConfig()


def test_config_sans_variable_garde_les_defauts() -> None:
    assert _DemoConfig.from_env({}) == _DemoConfig()


def test_config_conversion_selon_le_type() -> None:
    env = {
        "WEBTOOLS_PIPELINE_FLAG": "true",
        "WEBTOOLS_PIPELINE_LIMIT": "7",
        "WEBTOOLS_PIPELINE_RATIO": "0.25",
        "WEBTOOLS_PIPELINE_LABEL": "Mixte",
        "WEBTOOLS_PIPELINE_MAYBE": "12",
        "WEBTOOLS_PIPELINE_INCONNU": "ignore",
        "AUTRE": "x",
    }
    cfg = _DemoConfig.from_env(env)
    demo = {"flag": True, "limit": 7, "ratio": 0.25, "label": "Mixte", "maybe": 12}
    assert {k: v for k, v in cfg.to_dict().items() if k in demo} == demo


@pytest.mark.parametrize("raw,expected", [("1", True), ("OFF", False), ("yes", True), ("0", False)])
def test_config_booleens(raw: str, expected: bool) -> None:
    assert _DemoConfig.from_env({"WEBTOOLS_PIPELINE_FLAG": raw}).flag is expected


@pytest.mark.parametrize(
    "name,raw",
    [("FLAG", "peut-etre"), ("LIMIT", "trois"), ("RATIO", "x"), ("MAYBE", "1.5")],
)
def test_config_valeur_invalide_nomme_la_variable(name: str, raw: str) -> None:
    with pytest.raises(ValueError, match=f"WEBTOOLS_PIPELINE_{name}"):
        _DemoConfig.from_env({f"WEBTOOLS_PIPELINE_{name}": raw})


def test_config_est_figee() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        _DemoConfig().flag = True  # type: ignore[misc]


def test_config_lit_os_environ_par_defaut(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBTOOLS_PIPELINE_LIMIT", "9")
    assert _DemoConfig.from_env().limit == 9


# --- Diagnostics -------------------------------------------------------------


def test_diagnostics_compte_et_borne() -> None:
    diag = Diagnostics()
    assert diag.count("repli") == 0
    for i in range(MAX_EVENTS + 10):
        diag.note("repli", index=i)
    diag.note("autre", url="https://x.org", obj=object())

    assert diag.count("repli") == MAX_EVENTS + 10
    assert diag.count("autre") == 1
    data = diag.to_dict()
    assert data["counts"] == {"repli": MAX_EVENTS + 10, "autre": 1}
    assert len(data["events"]) == MAX_EVENTS
    assert data["events"][-1]["event"] == "autre"
    assert data["events"][-1]["details"]["url"] == "https://x.org"
    assert isinstance(data["events"][-1]["details"]["obj"], str)


def test_diagnostics_sans_etat_global() -> None:
    first, second = Diagnostics(), Diagnostics()
    first.note("repli")
    assert second.count("repli") == 0


def test_execution_context_porte_diagnostics_et_config() -> None:
    ctx = ExecutionContext("q")
    assert isinstance(ctx.diagnostics, Diagnostics)
    assert isinstance(ctx.config, PipelineConfig)
    assert ExecutionContext("q").diagnostics is not ctx.diagnostics


# --- check_report ------------------------------------------------------------


def _report(**overrides: object) -> dict:
    base = {
        "title": "Rapport",
        "summary": "Resume complet [1].",
        "sections": [{"title": "Intro", "content": "Phrase terminee [1]."}],
        "bibliography": [{"id": 1, "url": "https://x.org/a"}],
    }
    base.update(overrides)
    return base


def _codes(report: dict) -> list[str]:
    return [v["code"] for v in check_report(report)["violations"]]


def test_rapport_conforme() -> None:
    assert check_report(_report()) == {"ok": True, "violations": []}


def test_citation_orpheline() -> None:
    report = _report(sections=[{"title": "Intro", "content": "Vrai [1] et faux [2, 3]."}])
    assert _codes(report) == ["ORPHAN_CITATION", "ORPHAN_CITATION"]


def test_bibliographie_sans_url() -> None:
    report = _report(bibliography=[{"id": 1, "url": ""}, {"id": 2}])
    assert _codes(report).count("BIBLIO_WITHOUT_URL") == 2


@pytest.mark.parametrize(
    "marker", ["[SOURCE:https://x.org]", "[SECTION_SANS_SOURCE]", "[SYNTHESE_ECHOUEE]"]
)
@pytest.mark.parametrize("where", ["title", "summary"])
def test_marqueur_interne_dans_titre_et_resume(marker: str, where: str) -> None:
    assert "INTERNAL_MARKER" in _codes(_report(**{where: f"Texte {marker}."}))


def test_marqueur_interne_dans_section() -> None:
    report = _report(sections=[{"title": "Intro", "content": "Texte [SOURCE:https://x.org/a]."}])
    assert "INTERNAL_MARKER" in _codes(report)


def test_section_vide() -> None:
    assert _codes(_report(sections=[{"title": "Intro", "content": "  \n"}])) == ["EMPTY_SECTION"]


def test_section_meta() -> None:
    report = _report(sections=[{"title": "Stratégie de recherche", "content": "Fini."}])
    assert _codes(report) == ["META_SECTION"]


@pytest.mark.parametrize(
    "content", ["Phrase coupee en plein", "Phrase avec virgule,", "Phrase [1] de"]
)
def test_section_tronquee(content: str) -> None:
    assert "TRUNCATED_SECTION" in _codes(_report(sections=[{"title": "Intro", "content": content}]))


@pytest.mark.parametrize(
    "content",
    ["Fin nette.", "Fin nette [1].", "Fin nette. [1]", "Question ?", "| a | b |\n| 1 | 2 |"],
)
def test_section_terminee(content: str) -> None:
    assert "TRUNCATED_SECTION" not in _codes(
        _report(sections=[{"title": "Intro", "content": content}])
    )


def test_residu_de_citation() -> None:
    report = _report(sections=[{"title": "Intro", "content": "Texte [1]/b] fin."}])
    assert _codes(report) == ["CITATION_RESIDUE"]


def test_rapport_minimal_sans_cles() -> None:
    assert check_report({}) == {"ok": True, "violations": []}


# --- Doublures ---------------------------------------------------------------


async def test_fake_llm_structured_et_texte() -> None:
    llm = FakeLLM()
    llm.on_structured("plan", {"a": 1})
    llm.on_text(r"bonjour", ["premier", "second"])
    llm.on_text(r"panne", FakeLLMError("boom"))
    llm.on_text(r"calcul (\d+)", lambda call: f"vu {call.prompt}")

    assert await llm.generate_structured(
        [{"role": "user", "content": "x"}], schema={}, schema_name="plan"
    ) == {"a": 1}
    msg = [{"role": "user", "content": "bonjour"}]
    assert [await llm.generate(msg), await llm.generate(msg), await llm.generate(msg)] == [
        "premier",
        "second",
        "second",
    ]
    with pytest.raises(FakeLLMError):
        await llm.generate([{"role": "user", "content": "une panne"}])
    assert await llm.generate([{"role": "user", "content": "calcul 4"}]) == "vu calcul 4"

    with pytest.raises(FakeLLMError):
        await llm.generate([{"role": "user", "content": "inconnu"}])
    with pytest.raises(FakeLLMError):
        await llm.generate_structured([], schema={}, schema_name="autre")
    assert len(llm.unmatched) == 2
    assert len(llm.calls_for(kind="structured")) == 2
    assert len(llm.calls_for(schema_name="plan")) == 1
    assert len(llm.calls_for(pattern="bonjour")) == 3


async def test_fake_llm_embed_deterministe() -> None:
    llm = FakeLLM()
    one, two, again = await llm.embed(["alpha", "beta", "alpha"])
    assert one == again and one != two and len(one) == llm.embed_dim


async def test_fake_search_requetes_et_resultats() -> None:
    search = FakeSearch()
    search.add(
        "exacte", [make_result("https://x.org/1", "T1", "c1"), make_result("https://x.org/2")]
    )
    search.add(re.compile("motif"), [make_result("https://x.org/3")])
    search.add("panne", RuntimeError("boom"))

    found = await search.search("exacte", max_results=1, language="en", engines="google")
    assert [r.url for r in found] == ["https://x.org/1"]
    assert isinstance(found[0], SearchResult)
    assert [r.url for r in await search.search("un motif ici")] == ["https://x.org/3"]
    assert await search.search("rien") == []
    with pytest.raises(RuntimeError):
        await search.search("panne")

    assert search.queries_text() == ["exacte", "un motif ici", "rien", "panne"]
    first = search.queries[0]
    assert (first.max_results, first.language, first.engines, first.max_pages) == (
        1,
        "en",
        "google",
        1,
    )


async def test_fake_extractor_pages_et_echecs() -> None:
    manager = FakeExtractorManager({"https://x.org/a": "contenu"})
    manager.add_page("https://x.org/b", "autre", title="Titre B")

    ok = await manager.extract(url="https://x.org/a", llm_client=None)
    assert (ok.success, ok.content, ok.title) == (True, "contenu", "https://x.org/a")
    titled = await manager.extract("https://x.org/b")
    assert titled.title == "Titre B"
    missing = await manager.extract("https://x.org/zzz")
    assert missing.success is False and missing.error
    assert manager.urls_requested() == ["https://x.org/a", "https://x.org/b", "https://x.org/zzz"]


# --- Coutures d'injection ----------------------------------------------------


def test_orchestrateur_injecte_ses_dependances() -> None:
    search, extractor, config = FakeSearch(), FakeExtractorManager(), PipelineConfig()
    orch = IntelligentOrchestrator(
        FakeLLM(), search_client=search, extractor_manager=extractor, config=config
    )
    assert (orch.search_client, orch.extractor_manager, orch.config) == (search, extractor, config)


def test_orchestrateur_defauts_de_production() -> None:
    orch = IntelligentOrchestrator(FakeLLM())
    assert orch.search_client is searxng_client
    assert type(orch.extractor_manager).__name__ == "ExtractorManager"
    assert isinstance(orch.config, PipelineConfig)
