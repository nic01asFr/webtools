"""/health : identite du code, dependances reelles, sonde LLM en cache."""

import itertools

import pytest
from app import main
from app.core import build_info
from fastapi.testclient import TestClient


class _Searxng:
    def __init__(self, ok: bool) -> None:
        self.ok = ok

    async def health_check(self) -> bool:
        return self.ok


@pytest.fixture(autouse=True)
def _isoler(monkeypatch: pytest.MonkeyPatch) -> None:
    main._llm_probe_cache = None
    monkeypatch.setattr(main, "is_playwright_available", lambda: True)


def _brancher(monkeypatch: pytest.MonkeyPatch, searxng_ok: bool, llm_ok: bool) -> list[int]:
    appels: list[int] = []

    async def sonde() -> dict:
        appels.append(1)
        return {"configured": True, "ok": llm_ok, "detail": "ok" if llm_ok else "panne"}

    import app.services.search_service as ss

    monkeypatch.setattr(ss, "searxng_client", _Searxng(searxng_ok))
    monkeypatch.setattr(main, "_probe_llm", sonde)
    return appels


@pytest.mark.parametrize(
    "searxng_ok,llm_ok",
    list(itertools.product([True, False], repeat=2)),
)
def test_statut_selon_les_pannes(monkeypatch: pytest.MonkeyPatch, searxng_ok: bool, llm_ok: bool) -> None:
    _brancher(monkeypatch, searxng_ok, llm_ok)
    corps = TestClient(main.app).get("/health").json()
    assert corps["status"] == ("healthy" if searxng_ok and llm_ok else "degraded")
    assert corps["dependencies"]["searxng"]["ok"] is searxng_ok
    assert corps["dependencies"]["llm"]["ok"] is llm_ok
    assert corps["searxng_available"] is searxng_ok


def test_playwright_absent_degrade(monkeypatch: pytest.MonkeyPatch) -> None:
    _brancher(monkeypatch, True, True)
    monkeypatch.setattr(main, "is_playwright_available", lambda: False)
    assert TestClient(main.app).get("/health").json()["status"] == "degraded"


def test_identite_du_code(monkeypatch: pytest.MonkeyPatch) -> None:
    _brancher(monkeypatch, True, True)
    corps = TestClient(main.app).get("/health").json()
    assert corps["commit"] == build_info.COMMIT
    assert isinstance(corps["commit_dirty"], bool)
    assert corps["started_at"] == build_info.STARTED_AT
    assert corps["started_at"].endswith("+00:00")


def test_catalogue_de_moteurs_dans_la_reponse(monkeypatch: pytest.MonkeyPatch) -> None:
    _brancher(monkeypatch, True, True)
    from app.services.engine_catalog import engine_catalog

    monkeypatch.setattr(engine_catalog, "_engines", {
        "arxiv": {"categories": ["science"]},
        "github": {"categories": ["it"]},
        "pubmed": {"categories": ["science"]},
    })
    monkeypatch.setattr(engine_catalog, "_loaded", True)
    cat = TestClient(main.app).get("/health").json()["dependencies"]["engine_catalog"]
    assert cat["ok"] is True
    assert cat["engines_total"] == 3
    assert cat["engines_per_axis"] == {"academique": 2, "technique": 1}


def test_sonde_llm_mise_en_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    appels = _brancher(monkeypatch, True, True)
    client = TestClient(main.app)
    client.get("/health")
    client.get("/health")
    assert len(appels) == 1

    # Expiration du cache : une nouvelle sonde est lancee.
    horodatage, resultat = main._llm_probe_cache
    main._llm_probe_cache = (horodatage - main.LLM_PROBE_TTL_SECONDS - 1, resultat)
    client.get("/health")
    assert len(appels) == 2


@pytest.mark.asyncio
async def test_sonde_sans_cle_ne_fait_aucun_appel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEFAULT_LLM_API_KEY", raising=False)
    resultat = await main._probe_llm()
    assert resultat["configured"] is False and resultat["ok"] is False


@pytest.mark.asyncio
async def test_sonde_avec_client_factice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_LLM_API_KEY", "x")

    class Faux:
        def __init__(self, panne: bool) -> None:
            self.panne = panne

        async def generate(self, messages, **kwargs) -> str:
            assert kwargs.get("max_tokens") == 1
            if self.panne:
                raise RuntimeError("quota")
            return "pong"

    from app.core import llm

    for panne in (False, True):
        async def fabrique(panne: bool = panne) -> Faux:
            return Faux(panne)

        monkeypatch.setattr(llm, "get_llm_client", fabrique)
        assert (await main._probe_llm())["ok"] is (not panne)


def test_build_info_repli_sur_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_info, "_git", lambda *a: None)
    monkeypatch.setenv("WEBTOOLS_COMMIT", "abc123")
    assert build_info.read_build_info() == ("abc123", False)
    monkeypatch.delenv("WEBTOOLS_COMMIT")
    assert build_info.read_build_info() == ("unknown", False)


def test_build_info_depuis_git(monkeypatch: pytest.MonkeyPatch) -> None:
    reponses = {"rev-parse": "deadbeef", "status": " M fichier.py"}
    monkeypatch.setattr(build_info, "_git", lambda *a: reponses[a[0]])
    assert build_info.read_build_info() == ("deadbeef", True)
    reponses["status"] = ""
    assert build_info.read_build_info() == ("deadbeef", False)
