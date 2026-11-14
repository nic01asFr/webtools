"""Audit de la collecte (14/11/2026) : un test par defaut corrige.

Cache des echecs, plafond d'extraction, pages gardees a l'expiration du
budget de section, echeance globale, complement (decompte, requete, URL deja
presentes, notes), embeddings par lots et echec memorise, sources requises
comparees par url_key. Doublures de tests/fakes uniquement.
"""

import asyncio
from typing import Any

import pytest
from app.agents.intelligent_orchestrator import IntelligentOrchestrator
from app.api.models import WebResult
from app.research.context import ExecutionContext
from app.research.pipeline_config import PipelineConfig
from app.research.stages.collect import collect_section_data
from app.research.stages.crossref import cross_reference_data
from app.research.stages.extract import section_extraction_phase
from app.research.stages.queries import build_search_query, targeted_search_query
from app.research.stages.required import apply_required_sources, extract_required_sources
from app.research.stages.search import targeted_search
from app.research.stages.select import semantic_chunk_selection
from app.research.stages.services import StageServices

from tests.fakes import FakeExtractorManager, FakeLLM, FakeSearch, make_result

QUERY = "solar energy rural electricity access"
PAGE = "Field report on solar kits in remote districts. " * 40
LONG_PAGE = "Household solar kit prices fell sharply in remote districts. " * 110
SECTION_CONFIG = {
    "depth": "moderate",
    "words_target": 400,
    "objectives": ["Couvrir le sujet"],
    "key_questions": ["What is the price of household kits?"],
    "search_terms": ["solar costs"],
}


@pytest.fixture(autouse=True)
def _sans_filtre_de_pertinence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Titres factices : le filtre de pertinence (C96) a ses propres tests."""
    import app.research.stages.relevance as relevance

    monkeypatch.setattr(relevance, "keep_relevant", lambda _q, results: (list(results or []), 0))


def make(
    llm: FakeLLM | None = None,
    search: FakeSearch | None = None,
    pages: Any = None,
    config: PipelineConfig | None = None,
) -> tuple[StageServices, ExecutionContext]:
    orch = IntelligentOrchestrator(
        llm or FakeLLM(),
        search_client=search or FakeSearch(),
        extractor_manager=pages or FakeExtractorManager(),
        config=config or PipelineConfig(),
    )
    svc = orch.services()
    return svc, ExecutionContext(QUERY, config=svc.config)


def events(ctx: ExecutionContext, name: str) -> list[dict[str, Any]]:
    return [e["details"] for e in ctx.diagnostics.to_dict()["events"] if e["event"] == name]


class FlakyPages(FakeExtractorManager):
    """Premiere extraction de chaque URL en echec, les suivantes reussissent."""

    async def extract(self, url: str, *args: Any, **kwargs: Any) -> WebResult:
        first = url not in self.urls_requested()
        result = await super().extract(url, *args, **kwargs)
        if first:
            return WebResult.from_error(url=url, error_message="Erreur HTTP 503 transitoire")
        return result


class SlowPages(FakeExtractorManager):
    """Les URL de `slow` restent bloquees ; les autres repondent tout de suite."""

    def __init__(self, pages: dict[str, Any], slow: set[str]) -> None:
        super().__init__(pages)
        self.slow = slow

    async def extract(self, url: str, *args: Any, **kwargs: Any) -> WebResult:
        if url in self.slow:
            await asyncio.sleep(30)
        return await super().extract(url, *args, **kwargs)


# --- 3 : echecs d'extraction ni caches ni silencieux --------------------------


async def test_echec_d_extraction_non_cache_et_note() -> None:
    url = "https://ex.org/a"
    pages = FlakyPages({url: PAGE})
    svc, ctx = make(pages=pages)
    first = await section_extraction_phase(svc, "A", {"sources": [{"url": url}]}, ctx)
    assert first == []
    assert url not in ctx.extraction_cache
    [note] = events(ctx, "collecte.extraction_url_echec")
    assert note == {"section": "A", "url": url, "error": "Erreur HTTP 503 transitoire"}

    # Une autre section retrouve la meme URL : nouvelle tentative, reussie.
    second = await section_extraction_phase(svc, "B", {"sources": [{"url": url}]}, ctx)
    assert second and pages.urls_requested() == [url, url]
    assert url in ctx.extraction_cache


async def test_source_requise_en_echec_non_cachee() -> None:
    url = "https://req.org/rapport"
    pages = FlakyPages({url: PAGE})
    svc, ctx = make(pages=pages)
    await extract_required_sources(svc, ctx, {"sources_required": [url]}, {"sources": []})
    assert ctx.required_failed and url not in ctx.extraction_cache
    assert await section_extraction_phase(svc, "A", {"sources": [{"url": url}]}, ctx)


async def test_echec_de_la_recherche_ciblee_note() -> None:
    url = "https://ex.org/a"
    svc, ctx = make(search=FakeSearch(default=[make_result(url, "T", "s")]))
    assert not (await targeted_search(svc, QUERY, "Costs", ["price"], ctx))["sources"]
    [note] = events(ctx, "collecte.extraction_url_echec")
    assert note["section"] == "Costs" and note["url"] == url and note["error"]


# --- 4 : le plafond compte les extractions reussies --------------------------


async def test_plafond_d_extraction_compte_les_succes() -> None:
    urls = [f"https://ex.org/{i}" for i in range(8)]
    pages = FakeExtractorManager({u: PAGE for u in urls[3:]})  # 0, 1, 2 en echec
    svc, ctx = make(pages=pages)
    items = await section_extraction_phase(svc, "A", {"sources": [{"url": u} for u in urls]}, ctx)
    assert pages.urls_requested() == urls
    assert {i["source"] for i in items} == set(urls[3:])


async def test_plafond_atteint_n_extrait_pas_au_dela() -> None:
    urls = [f"https://ex.org/{i}" for i in range(8)]
    pages = FakeExtractorManager({u: PAGE for u in urls})
    svc, ctx = make(pages=pages)
    await section_extraction_phase(svc, "A", {"sources": [{"url": u} for u in urls]}, ctx)
    assert pages.urls_requested() == urls[:5]


# --- 5 : pages gardees a l'expiration du budget de section -------------------


async def test_budget_de_section_depasse_garde_les_pages_extraites() -> None:
    url_a, url_b = "https://ex.org/a", "https://ex.org/b"
    search = FakeSearch().add("solar costs", [make_result(url_a, "T", "s"), make_result(url_b, "T", "s")])
    pages = SlowPages({url_a: PAGE, url_b: PAGE}, slow={url_b})
    config = PipelineConfig(section_budget_min_seconds=1, section_budget_timeout_ratio=0.0)
    svc, ctx = make(search=search, pages=pages, config=config)
    plan = {"search_strategy": {"sources_per_section": 1}, "section_targets": {"A": dict(SECTION_CONFIG)}}

    by_section = await collect_section_data(svc, ctx, QUERY, plan, {"sources": []})

    assert by_section["A"] and {i["source"] for i in by_section["A"]} == {url_a}
    [note] = events(ctx, "collecte.budget_section_depasse")
    assert note["passages_gardes"] == len(by_section["A"]) > 0
    assert note["sources_gardees"] == 1


# --- 6 : echeance globale ---------------------------------------------------


async def test_echeance_globale_reduit_le_budget_et_saute_le_complement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search = FakeSearch()
    calls: list[str] = []

    async def stalled_search(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("x")
        await asyncio.sleep(30)
        return []

    monkeypatch.setattr(search, "search", stalled_search)
    svc, ctx = make(search=search)  # budget de section : 60 s au moins
    loop = asyncio.get_running_loop()
    ctx.deadline_at = loop.time() + 2.0  # collecte bornee a 1 s (reserve 0,5)
    plan = {"search_strategy": {"sources_per_section": 1}, "section_targets": {"A": dict(SECTION_CONFIG)}}

    start = loop.time()
    by_section = await collect_section_data(svc, ctx, QUERY, plan, {"sources": []})

    assert loop.time() - start < 1.8  # la moitie du temps restant reste a la redaction
    assert by_section["A"] == []
    [reduced] = events(ctx, "collecte.budget_reduit_echeance")
    assert reduced["budget_s"] <= 1.0 and reduced["budget_initial_s"] >= 60
    assert ctx.diagnostics.count("collecte.complement_saute_echeance") == 1
    assert calls == ["x"]  # pas de recherche de complement


async def test_echeance_globale_interrompt_un_complement_trop_long(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search = FakeSearch()
    calls: list[str] = []

    async def search_then_stall(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("x")
        if len(calls) > 1:  # le complement reste bloque
            await asyncio.sleep(30)
        return []

    monkeypatch.setattr(search, "search", search_then_stall)
    svc, ctx = make(search=search)
    loop = asyncio.get_running_loop()
    ctx.deadline_at = loop.time() + 1.0
    plan = {"search_strategy": {"sources_per_section": 1}, "section_targets": {"A": dict(SECTION_CONFIG)}}

    start = loop.time()
    await collect_section_data(svc, ctx, QUERY, plan, {"sources": []})

    assert loop.time() - start < 1.0
    [skipped] = events(ctx, "collecte.complement_saute_echeance")
    assert skipped["interrompu"] is True
    [complement] = events(ctx, "collecte.complement")
    assert complement["cause"] == "echeance globale atteinte"


async def test_sans_echeance_le_budget_de_section_est_inchange() -> None:
    svc, ctx = make()
    assert getattr(ctx, "deadline_at", None) is None
    plan = {"search_strategy": {"sources_per_section": 1}, "section_targets": {"A": dict(SECTION_CONFIG)}}
    await collect_section_data(svc, ctx, QUERY, plan, {"sources": []})
    assert ctx.diagnostics.count("collecte.budget_reduit_echeance") == 0
    assert ctx.diagnostics.count("collecte.complement_saute_echeance") == 0


def test_reserve_de_redaction_validee() -> None:
    with pytest.raises(ValueError):
        PipelineConfig(collection_writing_reserve_ratio=1.0)


# --- 7 : pas d'extraction structuree par defaut -------------------------------


async def test_recherche_ciblee_sans_extraction_structuree_par_defaut() -> None:
    url = "https://ex.org/a"
    svc, ctx = make(
        search=FakeSearch(default=[make_result(url, "T", "s")]),
        pages=FakeExtractorManager({url: PAGE}),
    )
    calls: list[str] = []

    async def fake_structured(**kwargs: Any) -> Any:
        calls.append(kwargs["source_url"])
        raise AssertionError("appel LLM non attendu")

    svc.data_extractor.extract_structured_data = fake_structured  # type: ignore[method-assign]
    result = await targeted_search(svc, QUERY, "Costs", ["price"], ctx)
    assert result is not None and result["sources"]
    assert calls == []


# --- 8 : le complement compte les sources, pas les passages ------------------


async def test_complement_declenche_pour_une_seule_page_longue() -> None:
    url, extra = "https://ex.org/long", "https://ex.org/autre"
    search = FakeSearch(default=[make_result(extra, "T", "s")])
    search.add("solar costs", [make_result(url, "T", "s")])
    pages = FakeExtractorManager({url: LONG_PAGE, extra: PAGE})
    svc, ctx = make(search=search, pages=pages)
    plan = {"search_strategy": {"sources_per_section": 2}, "section_targets": {"A": dict(SECTION_CONFIG)}}

    by_section = await collect_section_data(svc, ctx, QUERY, plan, {"sources": []})

    assert len(search.queries) == 2  # recherche de section puis complement
    [note] = events(ctx, "collecte.complement")
    assert note["sources_avant"] == 1 and note["sources_apres"] == 2
    assert "cause" not in note
    assert {i["source"] for i in by_section["A"]} == {url, extra}


# --- 9 : requete ciblee, URL deja presentes, notes ---------------------------


def test_requete_ciblee_porte_les_questions_et_reste_bornee() -> None:
    svc, _ctx = make()
    questions = ["What is the price of household kits?", "Which subsidies apply in 2024?"]
    q = targeted_search_query(svc, QUERY, "Costs", questions)
    words = q.split()
    assert {"price", "household", "kits"} <= set(words)
    assert len(words) <= svc.config.search_query_max_words
    assert "solar" in words  # le sujet ancre la requete
    # La requete de section (et de score de la selection) ne change pas.
    assert "price" not in build_search_query(svc, QUERY, "Costs", key_questions=questions).split()


async def test_recherche_ciblee_utilise_les_questions() -> None:
    search = FakeSearch()
    svc, ctx = make(search=search)
    await targeted_search(svc, QUERY, "Costs", ["What is the price of household kits?"], ctx)
    assert "price" in search.queries[0].query.split()


async def test_recherche_ciblee_ecarte_les_urls_deja_presentes() -> None:
    known, new = "https://ex.org/a/", "https://ex.org/b"
    search = FakeSearch(default=[make_result("https://ex.org/a", "T", "s"), make_result(new, "T", "s")])
    pages = FakeExtractorManager({"https://ex.org/a": PAGE, new: PAGE})
    svc, ctx = make(search=search, pages=pages)
    result = await targeted_search(svc, QUERY, "Costs", ["price"], ctx, known_urls=[known])
    assert pages.urls_requested() == [new]
    assert {i["url"] for i in result["sources"]} == {new}


async def test_recherche_ciblee_vide_est_notee() -> None:
    svc, ctx = make()
    assert await targeted_search(svc, QUERY, "Costs", ["price"], ctx) is None
    [note] = events(ctx, "collecte.recherche_ciblee_vide")
    assert note["section"] == "Costs" and note["cause"] == "aucun resultat"


async def test_complement_sans_resultat_note_sa_cause() -> None:
    svc, ctx = make()
    plan = {"search_strategy": {"sources_per_section": 1}, "section_targets": {"A": dict(SECTION_CONFIG)}}
    await collect_section_data(svc, ctx, QUERY, plan, {"sources": []})
    [note] = events(ctx, "collecte.complement")
    assert note == {
        "section": "A",
        "sources_avant": 0,
        "sources_apres": 0,
        "seuil": 1,
        "cause": "aucun resultat",
    }


# --- 11 : embeddings par lots, echec memorise --------------------------------


def _passages(n: int) -> list[dict[str, Any]]:
    return [
        {"source": f"https://s{i}.org/a", "title": "T", "content": f"Solar passage {i}. " * 30}
        for i in range(n)
    ]


async def test_embeddings_envoyes_par_lots() -> None:
    llm = FakeLLM()
    svc, ctx = make(llm=llm)
    await cross_reference_data(svc, "A", _passages(150), ctx)
    sizes = [len(c.texts) for c in llm.calls_for(kind="embed")]
    assert sizes == [64, 64, 22]
    assert len(ctx.embedding_cache) == 150


async def test_echec_des_embeddings_memorise_pour_la_requete() -> None:
    class Broken(FakeLLM):
        calls_embed = 0

        async def embed(self, texts: list, model: str = "x") -> list:
            Broken.calls_embed += 1
            raise RuntimeError("service lent")

    svc, ctx = make(llm=Broken())
    data = _passages(3)
    enriched = await cross_reference_data(svc, "A", data, ctx)
    selected = await semantic_chunk_selection(svc, enriched, QUERY, context=ctx, section="A")

    assert selected
    assert Broken.calls_embed == 1  # la selection ne repaie pas l'echec
    assert ctx.diagnostics.count("selection.embeddings_echec") == 1
    assert ctx.diagnostics.count("selection.embeddings_echec_memorise") == 1
    assert ctx.diagnostics.count("selection.repli_mots_cles") == 2


# --- 12 : source requise comparee par url_key --------------------------------


def test_source_requise_citee_sous_une_autre_ecriture() -> None:
    _svc, ctx = make()
    ctx.required_urls = ["https://Req.org/page/"]
    ctx.required_extracted = ["https://Req.org/page/"]
    report = {
        "sections": [{"content": "Fait etabli [1]."}],
        "bibliography": [{"id": 1, "url": "https://req.org/page"}],
    }
    apply_required_sources(report, ctx)
    assert "required_sources_uncited" not in report


def test_source_requise_non_citee_reste_signalee() -> None:
    _svc, ctx = make()
    ctx.required_urls = ["https://req.org/page/"]
    ctx.required_extracted = ["https://req.org/page/"]
    report = {
        "sections": [{"content": "Fait etabli [2]."}],
        "bibliography": [{"id": 1, "url": "https://req.org/page"}, {"id": 2, "url": "https://b.org"}],
    }
    apply_required_sources(report, ctx)
    assert report["required_sources_uncited"] == ["https://req.org/page/"]
