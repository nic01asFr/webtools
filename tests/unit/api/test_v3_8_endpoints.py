"""Endpoints simples : recherche rapide, vision, recherche, extraction (lot V3-8)."""

import inspect
from typing import Any

import pytest
from app.api.v1.endpoints import extract, research_quick, search, vision
from app.core.llm.base import LLMEmptyContentError
from app.utils.url_safety import assert_safe_url_async
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fakes import FakeExtractorManager, FakeLLM, FakeSearch, make_result

SECRET = "detail-interne-secret"
URL_A = "https://exemple.org/a"
URL_B = "https://exemple.org/b"
URL_C = "https://exemple.org/c"


class TrackedLLM(FakeLLM):
    """FakeLLM qui sait dire s'il a ete ferme et qui sait analyser une image."""

    supports_vision = True

    def __init__(self) -> None:
        super().__init__()
        self.closed = False
        self.vision_answer: Any = "une image"

    async def _aclose(self) -> None:
        self.closed = True

    async def generate_with_vision(
        self, text: str, image_url: str, system_prompt: Any = None, **kw: Any
    ) -> str:
        if isinstance(self.vision_answer, BaseException):
            raise self.vision_answer
        return self.vision_answer


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    for module in (extract, vision, search, research_quick):
        app.include_router(module.router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def _quick_setup(
    monkeypatch: pytest.MonkeyPatch,
    pages: dict[str, Any],
    results: Any,
    llm: FakeLLM | None = None,
) -> FakeLLM:
    llm = llm or FakeLLM().on_text("Question", "Reponse [1].")
    fake_search = FakeSearch().add("Quelle est la reponse ?", results)

    async def fake_get_llm() -> FakeLLM:
        return llm

    monkeypatch.setattr(research_quick, "searxng_client", fake_search)
    monkeypatch.setattr(research_quick, "manager", FakeExtractorManager(pages))
    monkeypatch.setattr(research_quick, "get_llm_client", fake_get_llm)
    return llm


QUICK_BODY = {"query": "Quelle est la reponse ?", "max_sources": 3}


# --- Recherche rapide (C63) -------------------------------------------------


def test_quick_reponse_vide_du_modele_est_une_erreur(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = FakeLLM().on_text("Question", LLMEmptyContentError(SECRET))
    _quick_setup(monkeypatch, {URL_A: "contenu"}, [make_result(URL_A, "A")], llm)
    r = client.post("/api/v1/research/quick", json=QUICK_BODY)
    assert r.status_code == 502
    assert SECRET not in r.text


def test_quick_reponse_none_du_modele_est_une_erreur(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = FakeLLM().on_text("Question", lambda call: None)
    _quick_setup(monkeypatch, {URL_A: "contenu"}, [make_result(URL_A, "A")], llm)
    assert client.post("/api/v1/research/quick", json=QUICK_BODY).status_code == 502


def test_quick_comptes_et_sources_ecartees(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = [make_result(URL_A, "A", score=0.8), make_result(URL_B, "B"), make_result(URL_C, "C")]
    _quick_setup(monkeypatch, {URL_A: "contenu a", URL_B: RuntimeError(SECRET)}, results)
    body = client.post("/api/v1/research/quick", json=QUICK_BODY).json()
    assert body["success"] is True
    counts = (body["sources_attempted"], body["sources_extracted"], body["sources_discarded"])
    assert counts == (3, 1, 2)
    assert {d["url"] for d in body["discarded"]} == {URL_B, URL_C}
    assert SECRET not in str(body)


def test_quick_ni_rang_ni_nombre_de_sources(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = [make_result(URL_A, "A", score=0.8), make_result(URL_B, "B", score=0.0)]
    _quick_setup(monkeypatch, {URL_A: "contenu a", URL_B: "contenu b"}, results)
    body = client.post("/api/v1/research/quick", json=QUICK_BODY).json()
    assert [s["relevance"] for s in body["sources"]] == [0.8, None]
    assert body["confidence"] is None


def test_quick_echec_de_recherche_sans_detail_brut(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quick_setup(monkeypatch, {}, RuntimeError(SECRET))
    r = client.post("/api/v1/research/quick", json=QUICK_BODY)
    assert r.status_code == 502
    assert SECRET not in r.text


def _long_page() -> str:
    filler = "Texte de remplissage sans rapport avec la question posee. " * 80
    return filler + "\n\nMARQUEUR-PASSAGE reponse exacte a la question reponse.\n\n" + filler


def _prompt_envoye(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> str:
    llm = _quick_setup(monkeypatch, {URL_A: _long_page()}, [make_result(URL_A, "A")])
    assert client.post("/api/v1/research/quick", json=QUICK_BODY).status_code == 200
    return llm.calls_for(kind="generate")[0].prompt


def test_quick_drapeau_selection_desactive_tronque_a_2000(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WEBTOOLS_PIPELINE_QUICK_USES_CHUNK_SELECTION", raising=False)
    assert "MARQUEUR-PASSAGE" not in _prompt_envoye(client, monkeypatch)


def test_quick_drapeau_selection_active_garde_le_passage_pertinent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBTOOLS_PIPELINE_QUICK_USES_CHUNK_SELECTION", "1")
    assert "MARQUEUR-PASSAGE" in _prompt_envoye(client, monkeypatch)


# --- Vision (C64) -----------------------------------------------------------


@pytest.fixture
def vision_llm(monkeypatch: pytest.MonkeyPatch) -> TrackedLLM:
    llm = TrackedLLM()
    llm.api_key = "cle"

    async def fake_get_llm() -> TrackedLLM:
        return llm

    async def no_check(url: str, resolver: Any = None) -> None:
        return None

    monkeypatch.setattr(vision, "get_llm_client", fake_get_llm)
    monkeypatch.setattr(vision, "assert_safe_url_async", no_check)
    return llm


VISION_BODY = {"image_url": "https://exemple.org/i.png", "prompt": "Decris l'image"}


def test_vision_succes(client: TestClient, vision_llm: TrackedLLM) -> None:
    body = client.post("/api/v1/vision", json=VISION_BODY).json()
    assert body["success"] is True
    assert body["analysis"] == "une image"
    assert body["model_used"] == "fake-llm"
    assert vision_llm.closed is False


@pytest.mark.parametrize("answer", [None, "", "  "])
def test_vision_analyse_vide_est_une_erreur(
    client: TestClient, vision_llm: TrackedLLM, answer: Any
) -> None:
    vision_llm.vision_answer = answer
    assert client.post("/api/v1/vision", json=VISION_BODY).status_code == 502


def test_vision_contenu_vide_du_client_est_une_erreur(
    client: TestClient, vision_llm: TrackedLLM
) -> None:
    vision_llm.vision_answer = LLMEmptyContentError(SECRET)
    r = client.post("/api/v1/vision", json=VISION_BODY)
    assert r.status_code == 502
    assert SECRET not in r.text


def test_vision_erreur_inattendue_sans_detail_brut(
    client: TestClient, vision_llm: TrackedLLM
) -> None:
    vision_llm.vision_answer = RuntimeError(SECRET)
    r = client.post("/api/v1/vision", json=VISION_BODY)
    assert r.status_code == 500
    assert SECRET not in r.text


def test_vision_sans_cle_api(client: TestClient, vision_llm: TrackedLLM) -> None:
    vision_llm.api_key = ""
    assert client.post("/api/v1/vision", json=VISION_BODY).status_code == 503


def test_vision_url_privee_refusee(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    llm = TrackedLLM()
    llm.api_key = "cle"

    async def fake_get_llm() -> TrackedLLM:
        return llm

    monkeypatch.setattr(vision, "get_llm_client", fake_get_llm)
    body = {"image_url": "http://127.0.0.1/i.png", "prompt": "Decris l'image"}
    assert client.post("/api/v1/vision", json=body).status_code == 400


def test_vision_plus_de_defaut_albert() -> None:
    assert "albert.api.etalab" not in inspect.getsource(vision)


def test_vision_sante_suit_la_configuration(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vision.settings, "default_llm_api_key", "cle")
    monkeypatch.setattr(vision.settings, "default_llm_base_url", "https://llm.test/api")
    body = client.get("/api/v1/vision/health").json()
    assert body["base_url"] == "https://llm.test/api"


# --- Extraction (C69, C50, C70) ---------------------------------------------


LLM_CONFIG = {
    "provider": "openai",
    "api_key": "cle",
    "model": "m",
    "base_url": "https://llm.exemple.org/v1",
}
EXTRACT_BODY = {"url": URL_A}


@pytest.fixture
def extract_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    shared = TrackedLLM()
    created: list[TrackedLLM] = []

    async def fake_get_llm() -> TrackedLLM:
        return shared

    def fake_create(**kwargs: Any) -> TrackedLLM:
        llm = TrackedLLM()
        created.append(llm)
        return llm

    async def no_check(url: str, resolver: Any = None) -> None:
        return None

    pages = FakeExtractorManager({URL_A: "contenu"})
    monkeypatch.setattr(extract, "get_llm_client", fake_get_llm)
    monkeypatch.setattr(extract.LLMFactory, "create", staticmethod(fake_create))
    monkeypatch.setattr(extract, "assert_safe_url_async", no_check)
    monkeypatch.setattr(extract, "manager", pages)
    return {"shared": shared, "created": created, "pages": pages}


def test_extract_base_url_privee_refusee(
    client: TestClient, extract_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extract, "assert_safe_url_async", assert_safe_url_async)
    config = {**LLM_CONFIG, "base_url": "http://127.0.0.1:9/v1"}
    r = client.post("/api/v1/extract", json={**EXTRACT_BODY, "llm_config": config})
    assert r.status_code == 400
    assert extract_env["created"] == []
    assert extract_env["pages"].calls == []


def test_extract_client_de_l_appelant_est_ferme(
    client: TestClient, extract_env: dict[str, Any]
) -> None:
    r = client.post("/api/v1/extract", json={**EXTRACT_BODY, "llm_config": LLM_CONFIG})
    assert r.status_code == 200
    (created,) = extract_env["created"]
    assert created.closed is True
    assert extract_env["pages"].calls[0].llm_client is created


def test_extract_client_de_l_appelant_ferme_meme_en_cas_d_erreur(
    client: TestClient, extract_env: dict[str, Any]
) -> None:
    extract_env["pages"].pages[URL_A] = RuntimeError(SECRET)
    r = client.post("/api/v1/extract", json={**EXTRACT_BODY, "llm_config": LLM_CONFIG})
    assert r.status_code == 500
    assert SECRET not in r.text
    assert extract_env["created"][0].closed is True


def test_extract_config_du_service_utilise_le_client_partage(
    client: TestClient, extract_env: dict[str, Any]
) -> None:
    assert client.post("/api/v1/extract", json=EXTRACT_BODY).status_code == 200
    assert extract_env["created"] == []
    assert extract_env["pages"].calls[0].llm_client is extract_env["shared"]
    assert extract_env["shared"].closed is False


def test_extract_entrees_bornees(client: TestClient, extract_env: dict[str, Any]) -> None:
    long_url = "https://exemple.org/" + "a" * 3000
    assert client.post("/api/v1/extract", json={"url": long_url}).status_code == 422
    assert client.post("/api/v1/extract", json={**EXTRACT_BODY, "prompt": "x" * 5000}).status_code == 422


# --- Recherche simple (C70) -------------------------------------------------


def test_search_erreur_sans_detail_brut(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(search, "searxng_client", FakeSearch().add("requete", RuntimeError(SECRET)))
    body = client.post("/api/v1/search", json={"query": "requete"}).json()
    assert body["success"] is False
    assert SECRET not in str(body)


def test_search_entrees_bornees(client: TestClient) -> None:
    assert client.post("/api/v1/search", json={"query": "q", "engines": "x" * 500}).status_code == 422
