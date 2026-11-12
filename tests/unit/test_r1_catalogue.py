"""Une recherche retente le chargement du catalogue de moteurs, au plus une fois par intervalle."""

import contextlib

import pytest
from app.services.engine_catalog import engine_catalog
from app.services.search_service import SearXNGClient


@pytest.fixture(autouse=True)
def _isoler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SearXNGClient, "_catalog_last_attempt", None)
    monkeypatch.setattr(engine_catalog, "_loaded", False)
    monkeypatch.setattr(engine_catalog, "_engines", {})


def _compteur(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    appels: list[int] = []

    async def faux_ensure_loaded(*args, **kwargs) -> bool:
        appels.append(1)
        return False

    monkeypatch.setattr(engine_catalog, "ensure_loaded", faux_ensure_loaded)
    return appels


async def test_recherche_retente_le_catalogue_vide(monkeypatch: pytest.MonkeyPatch) -> None:
    appels = _compteur(monkeypatch)
    await SearXNGClient._retry_catalog_load()
    assert len(appels) == 1


async def test_au_plus_une_fois_par_intervalle(monkeypatch: pytest.MonkeyPatch) -> None:
    appels = _compteur(monkeypatch)
    temps = [1000.0]
    monkeypatch.setattr("app.services.search_service.time.monotonic", lambda: temps[0])
    for _ in range(5):
        await SearXNGClient._retry_catalog_load()
    assert len(appels) == 1
    temps[0] += SearXNGClient.CATALOG_RETRY_INTERVAL + 1
    await SearXNGClient._retry_catalog_load()
    assert len(appels) == 2


async def test_catalogue_disponible_pas_de_rechargement(monkeypatch: pytest.MonkeyPatch) -> None:
    appels = _compteur(monkeypatch)
    monkeypatch.setattr(engine_catalog, "_loaded", True)
    await SearXNGClient._retry_catalog_load()
    assert appels == []


async def test_search_declenche_le_rechargement(monkeypatch: pytest.MonkeyPatch) -> None:
    appels = _compteur(monkeypatch)
    client = SearXNGClient("http://searxng.invalide:1")

    async def sans_reseau(*args, **kwargs):
        raise RuntimeError("aucun appel reseau dans les tests")

    monkeypatch.setattr(SearXNGClient, "_respect_rate_limit", sans_reseau)
    with contextlib.suppress(Exception):  # seul le rechargement nous interesse
        await client.search("requete r1 sans reseau")
    assert len(appels) == 1
