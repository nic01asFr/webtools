"""Sonde de sante : reponse vide, verrou, modele, catalogue vide."""

import asyncio

import pytest
from app import main
from app.core.llm.base import LLMClientError, LLMEmptyContentError
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isoler(monkeypatch: pytest.MonkeyPatch) -> None:
    main._llm_probe_cache = None
    monkeypatch.setattr(main.settings, "default_llm_api_key", "cle")
    monkeypatch.setattr(main.settings, "openai_api_key", "")


def _client_qui_leve(erreur: Exception):
    class Faux:
        async def generate(self, messages, **kwargs) -> str:
            raise erreur

    async def fabrique() -> Faux:
        return Faux()

    return fabrique


async def test_reponse_vide_prouve_que_le_llm_repond(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import llm

    monkeypatch.setattr(llm, "get_llm_client", _client_qui_leve(LLMEmptyContentError("vide")))
    resultat = await main._probe_llm()
    assert resultat["ok"] is True and resultat["configured"] is True


async def test_autre_erreur_reste_une_panne(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import llm

    monkeypatch.setattr(llm, "get_llm_client", _client_qui_leve(LLMClientError("401")))
    assert (await main._probe_llm())["ok"] is False


async def test_sondes_simultanees_une_seule_part(monkeypatch: pytest.MonkeyPatch) -> None:
    appels: list[int] = []

    async def sonde() -> dict:
        appels.append(1)
        await asyncio.sleep(0.05)
        return {"configured": True, "ok": True, "detail": "ok"}

    monkeypatch.setattr(main, "_probe_llm", sonde)
    resultats = await asyncio.gather(*(main._llm_status() for _ in range(8)))
    assert len(appels) == 1
    assert all(r["ok"] for r in resultats)


def _brancher(monkeypatch: pytest.MonkeyPatch, catalogue_charge: bool) -> None:
    import app.services.search_service as ss
    from app.services.engine_catalog import engine_catalog

    class Searxng:
        async def health_check(self) -> bool:
            return True

    async def sonde() -> dict:
        return {"configured": True, "ok": True, "detail": "ok"}

    monkeypatch.setattr(main, "is_playwright_available", lambda: True)
    monkeypatch.setattr(ss, "searxng_client", Searxng())
    monkeypatch.setattr(main, "_probe_llm", sonde)
    monkeypatch.setattr(
        engine_catalog,
        "_engines",
        {"arxiv": {"categories": ["science"]}} if catalogue_charge else {},
    )
    monkeypatch.setattr(engine_catalog, "_loaded", catalogue_charge)


def test_catalogue_vide_degrade_la_sante(monkeypatch: pytest.MonkeyPatch) -> None:
    _brancher(monkeypatch, catalogue_charge=False)
    assert TestClient(main.app).get("/health").json()["status"] == "degraded"
    main._llm_probe_cache = None
    _brancher(monkeypatch, catalogue_charge=True)
    assert TestClient(main.app).get("/health").json()["status"] == "healthy"


def test_la_reponse_porte_le_modele(monkeypatch: pytest.MonkeyPatch) -> None:
    _brancher(monkeypatch, catalogue_charge=True)
    monkeypatch.setattr(main.settings, "default_llm_model", "modele-de-test")
    assert TestClient(main.app).get("/health").json()["llm_model"] == "modele-de-test"
