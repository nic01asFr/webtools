"""Redaction v3 (C93) : etiquettes de source, faits etablis, citations
ajoutees par le code, contrat de reecriture, coherence sautee."""

from __future__ import annotations

import json
from typing import Any

from app.research.answer_check import SentenceVerdict, add_missing_citations
from app.research.context import ExecutionContext
from app.research.guard import accept_rewrite, sources_from
from app.research.pipeline_config import PipelineConfig
from app.research.stages.enrich import iterative_enrichment
from app.research.stages.synthesis_prompt import (
    SourceLabels,
    build_prompt_v3,
    established_facts,
    ids_to_markers,
)
from app.research.stages.synthesize import synthesize_single_section

from tests.fakes import FakeLLM, FakeSearch, make_result
from tests.unit.research.test_v3_6_selection import (
    QUERY,
    SECTION,
    SECTION_CONFIG,
    SYNTH,
    _ctx,
    _item,
    _orch,
    _rich_item,
)

URL_A = "https://s.org/a"
URL_B = "https://t.org/b"


def _labels() -> SourceLabels:
    return SourceLabels.from_passages([
        {"source": URL_A, "title": "A", "content": "passage un"},
        {"source": URL_B, "title": "B", "content": "passage deux"},
        {"source": URL_A, "title": "A", "content": "passage trois"},
    ])


# --- Etiquettes ---------------------------------------------------------------


def test_etiquettes_par_page_dans_l_ordre() -> None:
    labels = _labels()
    assert labels.urls == [URL_A, URL_B]
    assert labels.known == {"S1", "S2"}
    block = labels.block()
    assert block.startswith(f"[S1] A — {URL_A}\npassage un\n[...]\npassage trois")
    assert f"[S2] B — {URL_B}" in block


def test_etiquettes_converties_en_marqueurs() -> None:
    text, unknown = ids_to_markers("Un fait [S1]. Deux faits [S1, S2]. Faux [S9].", _labels())
    assert text == (
        f"Un fait [SOURCE:{URL_A}]. Deux faits [SOURCE:{URL_A}] [SOURCE:{URL_B}]. Faux."
    )
    assert unknown == ["S9"]


def test_etiquettes_variantes_d_ecriture() -> None:
    text, unknown = ids_to_markers("A [s2]. B [S 1; S2]. C [S1,S1].", _labels())
    assert text == (
        f"A [SOURCE:{URL_B}]. B [SOURCE:{URL_A}] [SOURCE:{URL_B}]. C [SOURCE:{URL_A}]."
    )
    assert unknown == []


# --- Faits deja etablis ---------------------------------------------------------


def test_faits_etablis_phrases_citees_seulement() -> None:
    sections = {
        "Contexte": {"content": (
            f"Transition sans source sur le sujet. La loi date de 2019 [SOURCE:{URL_A}]. "
            f"Le seuil est de 50 km [SOURCE:{URL_B}]."
        )},
        "Vide": {"content": "[SECTION_SANS_SOURCE] Aucune donnee."},
        SECTION: {"content": f"Section en cours [SOURCE:{URL_A}]."},
    }
    facts = established_facts(sections, SECTION, total_chars=3000, per_section_chars=600)
    assert facts == [
        "- (Contexte) La loi date de 2019.",
        "- (Contexte) Le seuil est de 50 km.",
    ]


def test_faits_etablis_bornes() -> None:
    long = " ".join(f"Fait numero {i} etabli par la source [SOURCE:{URL_A}]." for i in range(50))
    sections = {"S1": {"content": long}, "S2": {"content": long}}
    facts = established_facts(sections, "autre", total_chars=200, per_section_chars=120)
    assert sum(len(f.split(") ", 1)[1]) for f in facts) <= 200
    assert any(f.startswith("- (S1)") for f in facts)


# --- Prompt ------------------------------------------------------------------------


def test_prompt_v3_sans_url_a_recopier_ni_contradiction() -> None:
    prompt = build_prompt_v3(
        query=QUERY, section_name=SECTION, language="fr", depth="deep",
        objectives=["Objectif"], key_questions=["Question ?"], labels=_labels(),
        facts=["- (Contexte) La loi date de 2019."],
    )
    assert f'Rédige la section "{SECTION}"' in prompt
    assert "[S1] A" in prompt and "N'écris jamais d'URL" in prompt
    assert "[SOURCE:" not in prompt
    assert "paragraphes" not in prompt  # plus de nombre de paragraphes impose
    assert "ne documentent pas est omis" in prompt
    assert "FAITS DÉJÀ ÉTABLIS" in prompt and "La loi date de 2019." in prompt
    # Le tableau d'exemple porte une colonne Source sur chaque ligne.
    rows = [line for line in prompt.splitlines() if line.strip().startswith("| élément")]
    assert rows and all(line.rstrip().endswith("[S1] |") or line.rstrip().endswith("[S2] |") for line in rows)


async def test_synthese_v3_prompt_sans_confirmation_par_similarite() -> None:
    llm = FakeLLM()
    llm.on_text(SYNTH, "Texte redige sur le sujet [S1]. " * 20)
    llm.on_structured("verification", {"phrases": []})
    orch, ctx = _orch(llm), _ctx()
    out = await synthesize_single_section(
        orch.services(), QUERY, SECTION, SECTION_CONFIG, [_rich_item()], ctx
    )
    prompt = llm.calls_for(kind="generate")[0].prompt
    assert "MARQUEUR_XREF" not in prompt and "MARQUEUR_META" not in prompt
    assert "corroboration" not in prompt
    assert f"[S1] Titre {URL_A} — {URL_A}" in prompt
    assert f"[SOURCE:{URL_A}]" in out and "[S1]" not in out


# --- Citations ajoutees par le code ---------------------------------------------


def test_citation_ajoutee_quand_le_verificateur_designe_la_source() -> None:
    text = "La loi date de 2019. Le seuil est de 50 km [S2]. Transition."
    verdicts = [
        SentenceVerdict(1, "La loi date de 2019.", "soutenue", "", ("S1 Titre A",)),
        SentenceVerdict(2, "Le seuil est de 50 km [S2].", "soutenue", "", ("S2",)),
        SentenceVerdict(3, "Transition.", "soutenue", "", ()),
    ]
    out, added = add_missing_citations(text, verdicts, {"S1", "S2"})
    assert out == "La loi date de 2019 [S1]. Le seuil est de 50 km [S2]. Transition."
    assert added == 1


def test_citation_non_ajoutee_si_fautive_ou_inconnue() -> None:
    text = "La loi date de 2019. Le seuil est de 50 km."
    verdicts = [
        SentenceVerdict(1, "La loi date de 2019.", "partielle", "date", ("S1",)),
        SentenceVerdict(2, "Le seuil est de 50 km.", "soutenue", "", ("S7",)),
    ]
    out, added = add_missing_citations(text, verdicts, {"S1", "S2"})
    assert out == text and added == 0


async def test_synthese_v3_cite_la_phrase_soutenue_sans_etiquette() -> None:
    sentence = "Households gained access in 2021 in many rural areas."
    llm = FakeLLM()
    llm.on_text(SYNTH, f"{sentence} Solar kits are cheaper than diesel [S1].")
    llm.on_structured("verification", {"phrases": [
        {"numero": 1, "verdict": "soutenue", "probleme": "", "appuis": ["S1"]},
        {"numero": 2, "verdict": "soutenue", "probleme": "", "appuis": ["S1"]},
    ]})
    orch, ctx = _orch(llm), _ctx()
    out = await synthesize_single_section(
        orch.services(), QUERY, SECTION, SECTION_CONFIG, [_item(URL_A)], ctx
    )
    assert out == (
        f"Households gained access in 2021 in many rural areas [SOURCE:{URL_A}]. "
        f"Solar kits are cheaper than diesel [SOURCE:{URL_A}]."
    )
    assert ctx.diagnostics.count("citations.ajoutees") == 1
    verification = llm.calls_for(schema_name="verification")[0]
    assert f"[S1 Titre {URL_A}]" in verification.prompt


async def test_synthese_v3_etiquette_inconnue_retiree_et_notee() -> None:
    llm = FakeLLM()
    llm.on_text(SYNTH, "Solar kits are cheaper than diesel in rural areas [S4].")
    llm.on_structured("verification", {"phrases": []})
    orch, ctx = _orch(llm), _ctx()
    out = await synthesize_single_section(
        orch.services(), QUERY, SECTION, SECTION_CONFIG, [_item(URL_A)], ctx
    )
    assert out == "Solar kits are cheaper than diesel in rural areas."
    assert ctx.diagnostics.count("citations.etiquette_inconnue") == 1


async def test_synthese_v3_faits_des_sections_precedentes_dans_le_prompt() -> None:
    llm = FakeLLM()
    llm.on_text(SYNTH, "Texte redige sur le sujet [S1]. " * 5)
    llm.on_structured("verification", {"phrases": []})
    orch, ctx = _orch(llm), _ctx()
    ctx.final_content["sections"]["Contexte"] = {
        "content": f"Le programme a debute en 2015 [SOURCE:{URL_B}].",
    }
    await synthesize_single_section(orch.services(), QUERY, SECTION, SECTION_CONFIG, [_item(URL_A)], ctx)
    prompt = llm.calls_for(kind="generate")[0].prompt
    assert "- (Contexte) Le programme a debute en 2015." in prompt


async def test_verificateur_distinct_transmis() -> None:
    llm = FakeLLM()
    llm.on_text(SYNTH, "Solar kits are cheaper than diesel in rural areas [S1].")
    llm.on_structured("verification", {"phrases": []})
    orch, ctx = _orch(llm), _ctx(verification_model="qwen3-8-27b")
    await synthesize_single_section(orch.services(), QUERY, SECTION, SECTION_CONFIG, [_item(URL_A)], ctx)
    assert llm.calls_for(schema_name="verification")[0].kwargs["model"] == "qwen3-8-27b"


# --- Contrat de reecriture ------------------------------------------------------


def test_sources_du_contrat_par_page() -> None:
    sources = sources_from(
        [{"source": URL_A, "content": "un"}, {"source": URL_A, "content": "deux"}],
        [{"url": URL_B, "content": "trois"}, {"url": URL_B}],
    )
    assert sources == [(URL_A, "un\n[...]\ndeux"), (URL_B, "trois")]


async def test_reecriture_refusee_si_une_phrase_n_est_pas_soutenue() -> None:
    llm = FakeLLM()
    llm.on_structured("verification", {"phrases": [
        {"numero": 1, "verdict": "non_soutenue", "probleme": "absent", "appuis": []},
    ]})
    ctx = ExecutionContext("q")
    ok = await accept_rewrite(llm, ctx, "enrichissement:S1", "Un chiffre invente de 42 %.", [(URL_A, "texte")])
    assert ok is False
    assert ctx.diagnostics.count("verification.reecriture_refusee") == 1


async def test_reecriture_refusee_si_verification_impossible() -> None:
    ctx = ExecutionContext("q")
    ok = await accept_rewrite(FakeLLM(), ctx, "x", "Une phrase assez longue pour etre jugee.", [(URL_A, "t")])
    assert ok is False


async def test_contrat_inactif_sans_verification() -> None:
    ctx = ExecutionContext("q", config=PipelineConfig(deep_verify_sections=False))
    assert await accept_rewrite(FakeLLM(), ctx, "x", "Texte.", []) is True


async def test_enrichissement_garde_la_section_verifiee_si_la_regeneration_est_fautive() -> None:
    from tests.fakes import FakeExtractorManager
    from app.agents.intelligent_orchestrator import IntelligentOrchestrator

    cfg = PipelineConfig(enrichment_enabled=True, regenerate_v2=True, targeted_search_skip_structured=True)
    llm = FakeLLM()
    orch = IntelligentOrchestrator(
        llm,
        search_client=FakeSearch(default=[make_result(URL_B, "Nouvelle")]),
        extractor_manager=FakeExtractorManager({URL_B: "contenu nouveau " * 300}),
        config=cfg,
    )
    ctx = ExecutionContext("q", config=cfg)
    llm.on_text(r"Analyse la qualit", json.dumps({
        "overall_score": 40,
        "sections_analysis": [{"title": "S1", "score": 30, "missing": ["chiffres"]}],
        "gaps_to_fill": [],
    }))
    llm.on_structured(
        "section_regeneree",
        {"content": f"Fait ancien [SOURCE:{URL_A}] et chiffre invente de 42 % [SOURCE:{URL_B}]."},
    )
    llm.on_structured("verification", {"phrases": [
        {"numero": 1, "verdict": "non_soutenue", "probleme": "42 % absent", "appuis": []},
    ]})
    before = f"Fait ancien verifie [1]. " * 10
    report: dict[str, Any] = {
        "sections": [{"title": "S1", "content": before, "data": [{"source": URL_A, "content": "fait ancien"}]}],
        "bibliography": [{"id": 1, "url": URL_A, "title": "A"}],
    }
    out = await iterative_enrichment(orch.services(), "q", ctx, report, max_iterations=1)
    assert out["sections"][0]["content"] == before
    assert ctx.diagnostics.count("verification.reecriture_refusee") == 1


# --- Coherence ------------------------------------------------------------------


async def test_analyse_de_coherence_sautee_quand_rien_ne_l_utilise() -> None:
    from tests.contract.test_pipeline_contract import QUERY as CQ, build_orchestrator, nominal

    orch, llm, _s, _e = build_orchestrator(nominal())
    result = await orch.execute_intelligent_research(CQ)
    assert result["success"] is True
    assert llm.calls_for(schema_name="coherence_analysis") == []
    assert result["diagnostics"]["events"]["counts"]["coherence.analyse_sautee"] == 1
