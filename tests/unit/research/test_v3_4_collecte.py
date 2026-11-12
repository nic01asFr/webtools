"""Lot V3-4 : requetes, langue et plafonds de la collecte.

Chaque test lit ce que la fausse recherche a recu (texte, langue, moteurs)
ou ce que le faux extracteur a ete invite a extraire, drapeau dans chacune de
ses deux positions.
"""

from typing import Any

import app.services.engine_catalog as engine_catalog_module
import app.utils.chunking as chunking_module
import pytest
from app.agents.intelligent_orchestrator import ExecutionContext, IntelligentOrchestrator
from app.research.pipeline_config import PipelineConfig
from app.services.engine_catalog import EngineCatalog
from app.research.stages.queries import build_search_query
from app.research.stages.search import section_research_phase, targeted_search
from app.research.stages.extract import section_extraction_phase

from tests.contract.test_pipeline_contract import QUERY, build_orchestrator, nominal
from tests.fakes import FakeExtractorManager, FakeLLM, FakeSearch, make_result

PAGE = (
    "Field report on solar kits. " * 40
)  # assez long pour etre extrait sans etre decoupe


def make_orch(
    config: PipelineConfig | None = None,
    search: FakeSearch | None = None,
    pages: FakeExtractorManager | None = None,
) -> tuple[IntelligentOrchestrator, FakeSearch, FakeExtractorManager]:
    search = search or FakeSearch()
    pages = pages or FakeExtractorManager()
    orch = IntelligentOrchestrator(
        FakeLLM(), search_client=search, extractor_manager=pages, config=config or PipelineConfig()
    )
    return orch, search, pages


def catalogue(*names: str) -> EngineCatalog:
    cat = EngineCatalog()
    cat.ingest(
        {"engines": [{"name": n, "enabled": True, "categories": ["general"]} for n in names]}
    )
    return cat


@pytest.fixture
def cat(monkeypatch: pytest.MonkeyPatch) -> EngineCatalog:
    c = catalogue("wikipedia search", "wiby", "wikibooks")
    monkeypatch.setattr(engine_catalog_module, "engine_catalog", c)
    return c


SECTION = {"depth": "moderate", "key_questions": ["Q ?"], "search_terms": ["solar costs"]}


# --- Langue portee par le contexte (C32) ------------------------------------


@pytest.mark.parametrize("flag", [False, True])
async def test_langue_detectee_avant_la_premiere_recherche(flag: bool) -> None:
    orch, _llm, search, _ext = build_orchestrator(nominal())
    orch.config = PipelineConfig(search_language_from_query=flag)
    # Valeur laissee par une requete precedente : ne doit rien influencer.
    orch._search_language = "de"
    await orch.execute_intelligent_research(QUERY)

    expected = "en" if flag else "all"
    assert len(search.queries) >= 2
    assert {c.language for c in search.queries} == {expected}


async def test_langue_du_contexte() -> None:
    orch, _search, _pages = make_orch()
    ctx = ExecutionContext("q", config=orch.config)
    context: dict[str, Any] = {}
    ctx.set_search_language(context, "fr")
    assert (ctx.search_language, context["search_language"]) == ("fr", "fr")


@pytest.mark.parametrize("flag", [False, True])
async def test_langue_section_et_recherche_ciblee(flag: bool) -> None:
    config = PipelineConfig(search_language_from_query=flag)
    page = "https://ex.org/a"
    search = FakeSearch(default=[make_result(page, "t", "s")])
    orch, search, _pages = make_orch(config, search, FakeExtractorManager({page: PAGE}))
    ctx = ExecutionContext("q", config=config)
    ctx.search_language = "fr"

    await section_research_phase(orch.services(), "q", "S", SECTION, {}, ctx)
    await targeted_search(orch.services(), "q", "S", ["m"], ctx)

    expected = "fr" if flag else "all"
    assert [c.language for c in search.queries] == [expected, expected]


# --- Filtre de domaines avant la troncature (C35) ---------------------------


async def test_filtre_de_domaines_avant_la_troncature() -> None:
    bad = [make_result(f"https://bad.com/{i}", "t", "s") for i in range(5)]
    good = [make_result(f"https://good.org/{i}", "t", "s") for i in range(3)]
    search = FakeSearch(default=bad + good)
    orch, search, _pages = make_orch(search=search)
    ctx = ExecutionContext("q", config=orch.config)
    ctx.domains_exclusions = ["bad.com"]

    data = await section_research_phase(orch.services(), "q", "S", SECTION, {}, ctx)

    assert [s["url"] for s in data["sources"]] == [r.url for r in good]


async def test_sans_contrainte_la_demande_reste_inchangee() -> None:
    orch, search, _pages = make_orch()
    ctx = ExecutionContext("q", config=orch.config)
    await section_research_phase(orch.services(), "q", "S", SECTION, {}, ctx)
    assert search.queries[0].max_results == 5


# --- Cascade de moteurs (C32) ------------------------------------------------


async def test_recherche_ciblee_passe_par_la_cascade(cat: EngineCatalog) -> None:
    orch, search, _pages = make_orch()
    ctx = ExecutionContext("q", config=orch.config)
    await targeted_search(orch.services(), "solar", "Costs", ["price"], ctx)
    assert search.queries[0].engines == ",".join(cat.cascade(per_axis=3))
    assert search.queries[0].engines


async def test_recherche_ciblee_sans_catalogue_ne_restreint_pas() -> None:
    orch, search, _pages = make_orch()
    ctx = ExecutionContext("q", config=orch.config)
    await targeted_search(orch.services(), "solar", "Costs", ["price"], ctx)
    assert search.queries[0].engines is None
    assert ctx.diagnostics.count("collecte.catalogue_moteurs_indisponible") == 1


async def test_catalogue_recharge_avant_consultation(monkeypatch: pytest.MonkeyPatch) -> None:
    c = EngineCatalog()
    c._base_url = "http://searxng.invalid"

    async def fake_fetch(base_url: str, timeout: float) -> dict[str, Any]:
        return {"engines": [{"name": "wiby", "enabled": True, "categories": ["general"]}]}

    monkeypatch.setattr(c, "_fetch_config", fake_fetch)
    monkeypatch.setattr(engine_catalog_module, "engine_catalog", c)
    orch, search, _pages = make_orch()
    ctx = ExecutionContext("q", config=orch.config)

    await section_research_phase(orch.services(), "q", "S", SECTION, {}, ctx)

    assert search.queries[0].engines == "wiby"


# --- separate_search_terms (C34) ---------------------------------------------


TERMS_SECTION = {
    "depth": "moderate",
    "key_questions": [],
    "search_terms": ["solar costs", '"grid parity" filetype:pdf'],
}


@pytest.mark.parametrize("flag", [False, True])
async def test_termes_de_recherche_separes(flag: bool, cat: EngineCatalog) -> None:
    shared = make_result("https://ex.org/shared", "t", "s")
    only_a = make_result("https://ex.org/a", "t", "s")
    only_b = make_result("https://ex.org/b", "t", "s")
    search = FakeSearch()
    search.add("solar costs", [shared, only_a])
    search.add('"grid parity" filetype:pdf', [shared, only_b])
    orch, search, _pages = make_orch(PipelineConfig(separate_search_terms=flag), search)
    ctx = ExecutionContext("q", config=orch.config)

    data = await section_research_phase(orch.services(), "q", "S", TERMS_SECTION, {}, ctx)

    if flag:
        assert search.queries_text() == ["solar costs", '"grid parity" filetype:pdf']
        # La requete a operateur n'est pas restreinte aux moteurs de la cascade.
        assert search.queries[0].engines
        assert search.queries[1].engines is None
        urls = [s["url"] for s in data["sources"]]
        assert sorted(urls) == sorted(["https://ex.org/shared", "https://ex.org/a", "https://ex.org/b"])
        assert len(urls) == len(set(urls))
    else:
        # Une seule requete concatenee (puis le repli sans operateur existant).
        assert search.queries_text()[0] == 'solar costs "grid parity" filetype:pdf'
        assert 'solar costs "grid parity" filetype:pdf' not in search.queries_text()[1:]


async def test_terme_en_echec_n_annule_pas_les_autres() -> None:
    search = FakeSearch()
    search.add("solar costs", RuntimeError("moteur en panne"))
    search.add("solar prices", [make_result("https://ex.org/ok", "t", "s")])
    section = dict(TERMS_SECTION, search_terms=["solar costs", "solar prices"])
    orch, search, _pages = make_orch(PipelineConfig(separate_search_terms=True), search)
    ctx = ExecutionContext("q", config=orch.config)

    data = await section_research_phase(orch.services(), "q", "S", section, {}, ctx)

    assert [s["url"] for s in data["sources"]] == ["https://ex.org/ok"]
    assert ctx.diagnostics.count("collecte.recherche_terme_echec") == 1


# --- extraction_cap_follows_depth (C35) --------------------------------------


@pytest.mark.parametrize("flag", [False, True])
async def test_plafond_d_extraction(flag: bool) -> None:
    urls = [f"https://ex.org/{i}" for i in range(8)]
    pages = FakeExtractorManager({u: PAGE for u in urls})
    orch, _search, pages = make_orch(PipelineConfig(extraction_cap_follows_depth=flag), pages=pages)
    ctx = ExecutionContext("q", config=orch.config)
    section_data = {"sources": [{"url": u} for u in urls]}

    await section_extraction_phase(orch.services(), "S", section_data, ctx)

    assert len(pages.urls_requested()) == (8 if flag else 5)


# --- exploration_uses_plan_query (C31) ---------------------------------------


LONG_QUERY = (
    "Please produce a comprehensive report about the impact of off-grid solar energy "
    "kits on rural electricity access in sub-saharan africa and south asia"
)


@pytest.mark.parametrize("flag", [False, True])
async def test_requete_d_exploration(flag: bool, cat: EngineCatalog) -> None:
    orch, search, _pages = make_orch(PipelineConfig(exploration_uses_plan_query=flag))
    ctx = ExecutionContext(LONG_QUERY, config=orch.config)

    await orch._exploratory_phase(LONG_QUERY, {}, ctx=ctx)

    call = search.queries[0]
    if flag:
        assert call.query == build_search_query(orch.services(), query=LONG_QUERY, section_name="")
        assert len(call.query.split()) <= 12 < len(LONG_QUERY.split())
        assert call.engines == ",".join(cat.cascade(per_axis=3))
    else:
        assert call.query == LONG_QUERY
        assert call.engines == "google,duckduckgo"


# --- targeted_search_skip_structured (C36, C79) ------------------------------


@pytest.mark.parametrize("flag", [False, True])
async def test_recherche_ciblee_extraction_structuree(flag: bool) -> None:
    url = "https://ex.org/a"
    search = FakeSearch(default=[make_result(url, "t", "s")])
    orch, _search, _pages = make_orch(
        PipelineConfig(targeted_search_skip_structured=flag),
        search,
        FakeExtractorManager({url: PAGE}),
    )
    calls: list[str] = []

    class _Structured:
        def to_dict(self) -> dict[str, list[Any]]:
            return {"numerical": [], "temporal": [], "entities": []}

    async def fake_structured(**kwargs: Any) -> _Structured:
        calls.append(kwargs["source_url"])
        return _Structured()

    orch.data_extractor.extract_structured_data = fake_structured  # type: ignore[method-assign]
    ctx = ExecutionContext("q", config=orch.config)

    result = await targeted_search(orch.services(), "solar", "Costs", ["price"], ctx)

    assert result is not None and result["sources"]
    assert calls == ([] if flag else [url])
    assert all(("structured_data" in it) is (not flag) for it in result["sources"])


# --- chunking_v2 (C73) -------------------------------------------------------


@pytest.mark.parametrize("flag", [False, True])
async def test_chunking_v2_transmis(flag: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []
    original = chunking_module.document_to_chunk_items

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("v2", False))
        return original(*args, **kwargs)

    monkeypatch.setattr(chunking_module, "document_to_chunk_items", spy)
    url = "https://ex.org/a"
    search = FakeSearch(default=[make_result(url, "t", "s")])
    orch, _search, _pages = make_orch(
        PipelineConfig(chunking_v2=flag), search, FakeExtractorManager({url: PAGE})
    )
    orch.data_extractor.extract_structured_data = _no_structured  # type: ignore[method-assign]
    ctx = ExecutionContext("q", config=orch.config)

    await section_extraction_phase(orch.services(), "S", {"sources": [{"url": url}]}, ctx)
    await targeted_search(orch.services(), "solar", "Costs", ["price"], ctx)

    assert seen == [flag, flag]


async def _no_structured(**kwargs: Any) -> Any:
    class _S:
        def to_dict(self) -> dict[str, Any]:
            return {}

    return _S()


# --- Drapeaux d'extraction de la vague 1 -------------------------------------


@pytest.mark.parametrize("root_only", [False, True])
@pytest.mark.parametrize("single_pass", [False, True])
async def test_options_d_extraction_transmises(root_only: bool, single_pass: bool) -> None:
    url = "https://ex.org/a"
    config = PipelineConfig(rss_tier_root_only=root_only, single_llm_pass_enabled=single_pass)
    search = FakeSearch(default=[make_result(url, "t", "s")])
    orch, _search, pages = make_orch(config, search, FakeExtractorManager({url: PAGE}))
    orch.data_extractor.extract_structured_data = _no_structured  # type: ignore[method-assign]
    ctx = ExecutionContext("q", config=config)

    await section_extraction_phase(orch.services(), "S", {"sources": [{"url": url}]}, ctx)
    await targeted_search(orch.services(), "solar", "Costs", ["price"], ctx)

    assert len(pages.calls) == 2
    for call in pages.calls:
        assert call.options.rss_tier_root_only is root_only
        assert call.options.single_llm_pass_enabled is single_pass



# --- Repli quand une recherche ne rend rien (run citecheck du 12/11/2026) ---


class _EnginesSearch(FakeSearch):
    """Ne rend des resultats que pour les moteurs et requetes autorises."""

    def __init__(self, ok_engines: set, ok_queries: set | None, results: list) -> None:
        super().__init__()
        self.ok_engines = ok_engines
        self.ok_queries = ok_queries
        self.results = results

    async def search(self, query, max_results=10, language="all", categories=None,
                     engines=None, time_range=None, max_pages=1):
        await super().search(query, max_results, language, categories, engines, time_range, max_pages)
        engines_ok = engines is None or bool(set((engines or "").split(",")) & self.ok_engines)
        query_ok = self.ok_queries is None or query in self.ok_queries
        return list(self.results)[:max_results] if engines_ok and query_ok else []


def _news_and_general_catalog(monkeypatch: pytest.MonkeyPatch) -> EngineCatalog:
    cat = EngineCatalog()
    cat.ingest({"engines": [
        {"name": "google news", "enabled": True, "categories": ["news"]},
        {"name": "wikipedia search", "enabled": True, "categories": ["general"]},
    ]})
    monkeypatch.setattr(engine_catalog_module, "engine_catalog", cat)
    return cat


NEWS_SECTION = {"depth": "moderate", "key_questions": ["Q ?"], "search_terms": ["solar costs"],
                "source_axis": "actualite"}


async def test_axe_sans_resultat_repli_sur_la_cascade_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    _news_and_general_catalog(monkeypatch)
    page = "https://ex.org/a"
    search = _EnginesSearch({"wikipedia search"}, None, [make_result(page, "t", "s")])
    orch, search, _pages = make_orch(search=search, pages=FakeExtractorManager({page: PAGE}))
    ctx = ExecutionContext("q", config=orch.config)

    data = await section_research_phase(orch.services(), "q", "S", NEWS_SECTION, {}, ctx)

    assert [c.engines for c in search.queries] == ["google news", "google news,wikipedia search"]
    assert [s["url"] for s in data["sources"]] == [page]
    assert ctx.diagnostics.count("collecte.repli_cascade_complete") == 1


async def test_requete_trop_longue_repli_sur_la_requete_raccourcie(monkeypatch: pytest.MonkeyPatch) -> None:
    _news_and_general_catalog(monkeypatch)
    page = "https://ex.org/a"
    long_terms = "solar costs Africa rural kits 2024 households grid comparison solar"
    section = {**NEWS_SECTION, "search_terms": [long_terms]}
    short = "solar costs Africa rural kits 2024"
    search = _EnginesSearch({"wikipedia search"}, {short}, [make_result(page, "t", "s")])
    orch, search, _pages = make_orch(search=search, pages=FakeExtractorManager({page: PAGE}))
    ctx = ExecutionContext("q", config=orch.config)

    data = await section_research_phase(orch.services(), "q", "S", section, {}, ctx)

    assert search.queries[-1].query == short
    assert [s["url"] for s in data["sources"]] == [page]
    assert ctx.diagnostics.count("collecte.repli_requete_courte") == 1


async def test_repli_desactivable(monkeypatch: pytest.MonkeyPatch) -> None:
    _news_and_general_catalog(monkeypatch)
    search = _EnginesSearch({"wikipedia search"}, None, [make_result("https://ex.org/a", "t", "s")])
    orch, search, _pages = make_orch(PipelineConfig(search_zero_result_fallback=False), search)
    ctx = ExecutionContext("q", config=orch.config)

    data = await section_research_phase(orch.services(), "q", "S", NEWS_SECTION, {}, ctx)

    assert len(search.queries) == 1 and data["sources"] == []


def test_shorten_query() -> None:
    from app.research.stages.search import shorten_query

    q = "Commission européenne interdiction néonicotinoïdes 2013 lobbying agriculture Europe 2013"
    assert shorten_query(q, 6) == "Commission européenne interdiction néonicotinoïdes 2013 lobbying"
    assert shorten_query('site:eur-lex.europa.eu "règlement" néonicotinoïdes dérogations', 6) == (
        "règlement néonicotinoïdes dérogations"
    )
