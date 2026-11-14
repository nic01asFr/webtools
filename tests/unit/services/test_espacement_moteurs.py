"""Espacement par moteur de SearXNGClient : creneau reserve, sommeil hors verrou."""

import asyncio
import time

import pytest
from app.services.search_service import SearXNGClient


@pytest.fixture(autouse=True)
def _espacement_isole(monkeypatch: pytest.MonkeyPatch) -> None:
    # Etat de classe propre a chaque test : intervalles courts, pas
    # d'espacement global, verrou neuf (lie a la boucle du test).
    monkeypatch.setattr(SearXNGClient, "_min_interval", 0.0)
    monkeypatch.setattr(SearXNGClient, "_engine_last_at", {})
    monkeypatch.setattr(SearXNGClient, "_engine_lock", asyncio.Lock())
    monkeypatch.setattr(SearXNGClient, "ENGINE_INTERVALS", {"google": 0.4})
    monkeypatch.setattr(SearXNGClient, "DEFAULT_ENGINE_INTERVAL", 0.0)


async def _timed(engines: str, start: float) -> float:
    await SearXNGClient._respect_rate_limit(engines)
    return time.monotonic() - start


async def test_un_moteur_rapide_n_attend_pas_le_sommeil_d_un_moteur_lent() -> None:
    await SearXNGClient._respect_rate_limit("google")
    start = time.monotonic()
    google = asyncio.create_task(_timed("google", start))
    await asyncio.sleep(0.01)  # la recherche google tient son creneau
    arxiv = asyncio.create_task(_timed("arxiv", start))
    google_s, arxiv_s = await asyncio.gather(google, arxiv)
    assert google_s >= 0.35
    assert arxiv_s < 0.2


async def test_l_espacement_par_moteur_reste_garanti() -> None:
    start = time.monotonic()
    ends = sorted(await asyncio.gather(*[_timed("google", start) for _ in range(3)]))
    assert ends[0] < 0.1
    assert ends[1] - ends[0] >= 0.35
    assert ends[2] - ends[1] >= 0.35
