"""Verification de la reponse rapide contre ses sources (C89, drapeau quick_verify_answer).

Mesure d'origine (benchmarks/citecheck, 12/11/2026) : "quarantieme jour" ecrit
pour "quatre-vingt-dixieme", donnee declaree absente alors que presente.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import research_quick
from app.research.answer_check import correction_request, split_sentences, SentenceVerdict
from tests.fakes import FakeExtractorManager, FakeLLM, FakeSearch, make_result

URL = "https://ex.org/kyoto"
PAGE = ("Le protocole entre en vigueur le quatre-vingt-dixieme jour suivant la ratification. " * 5)
FIRST = "Le protocole est entre en vigueur le quarantieme jour suivant la ratification [1]."
FIXED = "Le protocole est entre en vigueur le quatre-vingt-dixieme jour suivant la ratification [1]."
BODY = {"query": "Quand le protocole est-il entre en vigueur ?", "max_sources": 1}


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(research_quick.router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def _setup(monkeypatch: pytest.MonkeyPatch, llm: FakeLLM, verify: bool) -> None:
    monkeypatch.setenv("WEBTOOLS_PIPELINE_QUICK_VERIFY_ANSWER", "1" if verify else "0")
    search = FakeSearch().add(BODY["query"], [make_result(URL, "Kyoto")])

    async def get_llm() -> FakeLLM:
        return llm

    monkeypatch.setattr(research_quick, "searxng_client", search)
    monkeypatch.setattr(research_quick, "manager", FakeExtractorManager({URL: PAGE}))
    monkeypatch.setattr(research_quick, "get_llm_client", get_llm)


def _llm(verdict: str, problem: str = "") -> FakeLLM:
    # La demande de correction contient aussi "Question" : elle passe d'abord.
    return (
        FakeLLM()
        .on_text("Verification de ta reponse", FIXED)
        .on_text("Question", FIRST)
        .on_structured("verification", {"phrases": [{"numero": 1, "verdict": verdict, "probleme": problem}]})
    )


def test_sans_drapeau_aucune_verification(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _llm("contredite", "la source dit quatre-vingt-dixieme")
    _setup(monkeypatch, llm, verify=False)
    body = client.post("/api/v1/research/quick", json=BODY).json()
    assert body["answer"] == FIRST and body["verification"] is None
    assert llm.calls_for(kind="structured") == []


def test_phrase_contredite_reecrite_avec_la_critique(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _llm("contredite", "la source dit quatre-vingt-dixieme jour")
    _setup(monkeypatch, llm, verify=True)
    body = client.post("/api/v1/research/quick", json=BODY).json()
    assert body["answer"] == FIXED
    assert body["verification"]["reecrite"] is True
    assert body["verification"]["fautives"][0]["verdict"] == "contredite"
    rewrite = llm.calls_for(kind="generate")[-1]
    assert "quatre-vingt-dixieme jour" in rewrite.prompt and FIRST in rewrite.prompt
    # Le verificateur a recu la phrase et le texte transmis au modele.
    check = llm.calls_for(kind="structured")[0]
    assert FIRST in check.prompt and "quatre-vingt-dixieme" in check.prompt


def test_reponse_soutenue_gardee(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _llm("soutenue")
    _setup(monkeypatch, llm, verify=True)
    body = client.post("/api/v1/research/quick", json=BODY).json()
    assert body["answer"] == FIRST
    assert body["verification"] == {"verifiee": True, "phrases": 1, "fautives": [], "reecrite": False}
    assert len(llm.calls_for(kind="generate")) == 1


def test_verification_en_echec_garde_la_reponse(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    llm = FakeLLM().on_text("Question", FIRST)  # generate_structured non scenarise : leve
    _setup(monkeypatch, llm, verify=True)
    r = client.post("/api/v1/research/quick", json=BODY)
    assert r.status_code == 200
    assert r.json()["answer"] == FIRST and r.json()["verification"]["verifiee"] is False


def test_split_sentences_et_demande_de_correction() -> None:
    assert split_sentences("Entre en 2005 [1]. Signe en 1997 [2].\nRatifie par la Russie.") == [
        "Entre en 2005 [1].", "Signe en 1997 [2].", "Ratifie par la Russie."
    ]
    text = correction_request([SentenceVerdict(1, FIRST, "contredite", "la source dit 90 jours")])
    assert FIRST in text and "la source dit 90 jours" in text and "Reecris la reponse complete" in text
