"""Lot V4-3 : enregistrement et rejeu de la collecte."""

from pathlib import Path
from typing import Any

import app.agents.intelligent_orchestrator as orchestrator_module
import pytest
from app.agents.intelligent_orchestrator import IntelligentOrchestrator
from app.research.replay import (
    ReplayMissError,
    build_replay_clients,
    normalize_url,
    read_replay_env,
)

from tests.contract.test_pipeline_contract import (
    QUERY,
    SCENARIOS,
    _FastAsyncio,
    build_orchestrator,
)
from tests.fakes import FakeExtractorManager, FakeSearch, make_result


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator_module, "asyncio", _FastAsyncio())


async def test_enregistrement_puis_rejeu_identiques(tmp_path: Path) -> None:
    factory = SCENARIOS["nominal"][0]

    _orch, llm, search, extractor = build_orchestrator(factory())
    rec_search, rec_extractor = build_replay_clients("record", tmp_path, search, extractor)
    orch_rec = IntelligentOrchestrator(
        llm, search_client=rec_search, extractor_manager=rec_extractor
    )
    recorded = await orch_rec.execute_intelligent_research(QUERY)
    assert rec_search.recorded > 0 and rec_extractor.recorded > 0
    assert list((tmp_path / "search").glob("*.json"))

    # Rejeu : doublures neuves, vides de scenario de collecte.
    _orch2, llm2, _s, _e = build_orchestrator(factory())
    rep_search, rep_extractor = build_replay_clients(
        "replay", tmp_path, FakeSearch(), FakeExtractorManager()
    )
    orch_rep = IntelligentOrchestrator(
        llm2, search_client=rep_search, extractor_manager=rep_extractor
    )
    replayed = await orch_rep.execute_intelligent_research(QUERY)

    assert rep_search.missed == 0
    assert rep_extractor.missed == 0
    assert rep_search.served > 0 and rep_extractor.served > 0
    assert replayed["answer"] == recorded["answer"]


async def test_requete_inconnue_leve_et_compte(tmp_path: Path) -> None:
    search, extractor = build_replay_clients("replay", tmp_path, None, None)
    with pytest.raises(ReplayMissError) as exc:
        await search.search("requete jamais vue")
    assert "requete jamais vue" in str(exc.value)
    assert str(tmp_path) in str(exc.value)
    with pytest.raises(ReplayMissError):
        await extractor.extract("https://example.org/inconnue")
    assert search.missed == 1
    assert extractor.missed == 1


async def test_fidelite_des_modeles_et_cle_extraction(tmp_path: Path) -> None:
    fake_search = FakeSearch({"q": [make_result("https://a.org/x", "T", "C", "eng", 0.5)]})
    fake_extractor = FakeExtractorManager().add_page("https://a.org/x", "texte", title="Titre")
    rec_s, rec_e = build_replay_clients("record", tmp_path, fake_search, fake_extractor)
    res = await rec_s.search("q", max_results=3, language="en")
    page = await rec_e.extract("https://a.org/x", prompt="p1", extraction_type="general")

    rep_s, rep_e = build_replay_clients("replay", tmp_path, None, None)
    assert await rep_s.search("q", max_results=3, language="en") == res
    # Options differentes, URL equivalente apres normalisation : meme page.
    assert await rep_e.extract("HTTPS://A.org/x/#frag", prompt="autre") == page
    # Parametre de recherche different : cle differente.
    with pytest.raises(ReplayMissError):
        await rep_s.search("q", max_results=4, language="en")


def test_normalize_url() -> None:
    assert normalize_url("HTTPS://Ex.org/a/#f") == "https://ex.org/a"
    assert normalize_url("https://ex.org/a?x=1") == "https://ex.org/a?x=1"


def test_read_replay_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("WEBTOOLS_REPLAY_MODE", raising=False)
    monkeypatch.delenv("WEBTOOLS_REPLAY_DIR", raising=False)
    assert read_replay_env() == (None, None)
    monkeypatch.setenv("WEBTOOLS_REPLAY_MODE", "replay")
    with pytest.raises(ValueError):
        read_replay_env()
    monkeypatch.setenv("WEBTOOLS_REPLAY_DIR", str(tmp_path))
    assert read_replay_env() == ("replay", tmp_path)
    monkeypatch.setenv("WEBTOOLS_REPLAY_MODE", "bizarre")
    with pytest.raises(ValueError):
        read_replay_env()


def test_record_exige_un_client() -> None:
    bad: Any = "record"
    with pytest.raises(ValueError):
        build_replay_clients(bad, "x", None, None)


async def test_fill_sert_l_enregistre_et_complete_le_reste(tmp_path: Path) -> None:
    rec_s, rec_e = build_replay_clients(
        "record",
        tmp_path,
        FakeSearch({"connue": [make_result("https://a.org/x", "T", "C")]}),
        FakeExtractorManager().add_page("https://a.org/x", "texte enregistre"),
    )
    await rec_s.search("connue")
    await rec_e.extract("https://a.org/x")

    # Le client reel du fill rend autre chose pour la page connue : elle doit
    # venir du disque. La requete et la page nouvelles partent sur le reseau.
    live_search = FakeSearch({
        "connue": [make_result("https://autre.org", "Autre", "C")],
        "nouvelle": [make_result("https://b.org/y", "T", "C")],
    })
    live_pages = FakeExtractorManager().add_page("https://a.org/x", "texte du jour")
    live_pages.add_page("https://b.org/y", "page nouvelle")
    fill_s, fill_e = build_replay_clients("fill", tmp_path, live_search, live_pages)
    assert [r.url for r in await fill_s.search("connue")] == ["https://a.org/x"]
    assert (await fill_e.extract("https://a.org/x")).content == "texte enregistre"
    assert [r.url for r in await fill_s.search("nouvelle")] == ["https://b.org/y"]
    assert (await fill_e.extract("https://b.org/y")).content == "page nouvelle"
    assert (fill_s.served, fill_s.recorded, fill_s.missed) == (1, 1, 0)
    assert (fill_e.served, fill_e.recorded, fill_e.missed) == (1, 1, 0)

    # Le complement est ecrit : un rejeu strict le sert ensuite sans manque.
    rep_s, rep_e = build_replay_clients("replay", tmp_path, None, None)
    await rep_s.search("nouvelle")
    await rep_e.extract("https://b.org/y")
    assert rep_s.missed == 0 and rep_e.missed == 0


def test_fill_exige_un_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_replay_clients("fill", tmp_path, None, None)
    monkeypatch.setenv("WEBTOOLS_REPLAY_MODE", "fill")
    monkeypatch.setenv("WEBTOOLS_REPLAY_DIR", str(tmp_path))
    assert read_replay_env() == ("fill", tmp_path)
