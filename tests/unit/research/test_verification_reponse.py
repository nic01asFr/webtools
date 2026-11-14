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



# --- Recherche approfondie : verification des sections (deep_verify_sections) ---

import dataclasses
import re

from app.research.context import ExecutionContext
from app.research.stages.synthesize import synthesize_single_section
from tests.contract.test_pipeline_contract import build_orchestrator, nominal

SECTION_URL = "https://example.org/kyoto"
SECTION_DATA = [{"source": SECTION_URL, "title": "Kyoto", "content": PAGE * 20}]
DRAFT = "Le protocole est entre en vigueur le quarantieme jour suivant la ratification [SOURCE:" + SECTION_URL + "]."
CORRECTED = (
    "Le protocole est entre en vigueur le quatre-vingt-dixieme jour suivant la ratification "
    "[SOURCE:" + SECTION_URL + "]."
)


def _deep(verdict: str, verify: bool = True, after: str = "soutenue", **flags):
    """verdict : verdict de la redaction ; after : celui de la reecriture,
    reverifiee (deep_verify_rewrite)."""
    orch, llm, _search, _extractor = build_orchestrator(nominal())
    orch.config = dataclasses.replace(orch.config, deep_verify_sections=verify, **flags)
    llm._text.insert(0, (re.compile("Verification de ta reponse", re.DOTALL), CORRECTED))
    llm._text.insert(1, (re.compile("Rédige la section", re.DOTALL), DRAFT))
    llm.on_structured("verification", [
        {"phrases": [{"numero": 1, "verdict": verdict, "probleme": "90 jours", "appuis": []}]},
        {"phrases": [{"numero": 1, "verdict": after, "probleme": "", "appuis": []}]},
    ])
    return orch, llm


async def _write(orch) -> tuple[str, ExecutionContext]:
    ctx = ExecutionContext("q", config=orch.config)
    out = await synthesize_single_section(
        orch.services(), "q", "Entree en vigueur", {"depth": "moderate", "words_target": 300},
        SECTION_DATA, ctx,
    )
    return out, ctx


async def test_section_fautive_reecrite() -> None:
    orch, llm = _deep("contredite")
    out, ctx = await _write(orch)
    assert out == CORRECTED
    assert ctx.diagnostics.count("selection.section_reecrite") == 1
    check = [c for c in llm.calls if c.schema_name == "verification"][0]
    # Le verificateur voit les passages sous l'etiquette [SOURCE:url] citee par la section.
    assert f"[SOURCE:{SECTION_URL}]" in check.prompt and DRAFT in check.prompt


async def test_section_soutenue_inchangee() -> None:
    orch, _llm = _deep("soutenue")
    out, ctx = await _write(orch)
    assert out == DRAFT
    assert ctx.diagnostics.count("selection.section_reecrite") == 0
    assert ctx.diagnostics.count("selection.verification") == 1


async def test_sans_drapeau_section_non_verifiee() -> None:
    orch, llm = _deep("contredite", verify=False)
    out, _ctx = await _write(orch)
    assert out == DRAFT
    assert not [c for c in llm.calls if c.schema_name == "verification"]


def test_titres_et_regles_de_tableau_non_verifies() -> None:
    text = "### Calendrier\n| A | B |\n|---|---|\n| 2005 | entree en vigueur |\nPhrase normale ici [1]."
    assert split_sentences(text) == ["| A | B |", "| 2005 | entree en vigueur |", "Phrase normale ici [1]."]
