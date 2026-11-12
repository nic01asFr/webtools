"""Le rechargement du catalogue de moteurs est plafonne dans EngineCatalog.ensure_loaded."""

import asyncio

import pytest
from app.services.engine_catalog import EngineCatalog
from app.services.search_service import SearXNGClient


def _catalogue_vide(temps: list[float], appels: list[int]) -> EngineCatalog:
    cat = EngineCatalog(clock=lambda: temps[0])
    cat._base_url = "http://s"

    async def faux_load(*args, **kwargs) -> bool:
        appels.append(1)
        await asyncio.sleep(0)
        return False

    cat.load = faux_load  # type: ignore[method-assign]
    return cat


async def test_dix_appels_concurrents_une_seule_tentative() -> None:
    appels: list[int] = []
    cat = _catalogue_vide([1000.0], appels)
    await asyncio.gather(*(cat.ensure_loaded() for _ in range(10)))
    assert len(appels) == 1


async def test_second_appel_dans_l_intervalle_sans_tentative() -> None:
    appels: list[int] = []
    temps = [1000.0]
    cat = _catalogue_vide(temps, appels)
    await cat.ensure_loaded()
    temps[0] += 10
    assert await cat.ensure_loaded() is False
    assert len(appels) == 1
    temps[0] += cat.retry_interval + 1
    await cat.ensure_loaded()
    assert len(appels) == 2


async def test_catalogue_disponible_pas_de_rechargement() -> None:
    appels: list[int] = []
    cat = _catalogue_vide([1000.0], appels)
    cat._loaded = True
    assert await cat.ensure_loaded() is True
    assert appels == []


async def test_la_recherche_delegue_au_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.engine_catalog import engine_catalog

    appels: list[int] = []

    async def faux(*args, **kwargs) -> bool:
        appels.append(1)
        return False

    monkeypatch.setattr(engine_catalog, "_loaded", False)
    monkeypatch.setattr(engine_catalog, "ensure_loaded", faux)
    await SearXNGClient._retry_catalog_load()
    assert len(appels) == 1
