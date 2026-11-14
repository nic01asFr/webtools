"""Lot V3-7 : plan de recherche (repli note, plan vide, prompt v2)."""

import dataclasses
import json
from typing import Any

import pytest
from app.agents.intelligent_orchestrator import ExecutionContext, IntelligentOrchestrator
from app.core.llm.base import LLMTruncatedError
from app.research.pipeline_config import PipelineConfig
from app.research.stages.plan import create_detailed_plan

from tests.fakes import FakeLLM

QUERY = "Impact of solar energy on rural electricity access"
EXPLORATION: dict[str, Any] = {"topics_found": ["solar"], "sources": [], "field_assessment": "rich"}


def _section(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "words_target": 300,
        "depth": "moderate",
        "objectives": ["o"],
        "key_questions": ["q"],
        "search_terms": ["solar access"],
        "source_axis": "generaliste",
    }


def _plan(*names: str) -> dict[str, Any]:
    return {
        "complexity_analysis": {
            "overall_score": 3.0,
            "target_length": "standard",
            "estimated_words": 1200,
            "justification": "test",
        },
        "sections": [_section(n) for n in names],
        "narrative_flow": [],
        "search_strategy": {
            "total_sources_needed": 5,
            "sources_per_section": 2,
            "search_depth": "standard",
            "language": "en",
        },
    }


def _orch(llm: FakeLLM, v2: bool = False) -> IntelligentOrchestrator:
    config = dataclasses.replace(PipelineConfig(), plan_prompt_v2=v2)
    return IntelligentOrchestrator(llm, config=config)


async def test_exception_donne_repli_note_et_marque() -> None:
    llm = FakeLLM().on_structured("research_plan", RuntimeError("boom"))
    ctx = ExecutionContext(QUERY)
    plan = await create_detailed_plan(_orch(llm).services(), QUERY, {}, EXPLORATION, ctx=ctx)
    assert plan["fallback"] is True
    assert plan["fallback_reason"] == "exception:RuntimeError"
    assert plan["sections"] == ["Introduction", "Analyse", "Conclusion"]
    assert ctx.diagnostics.count("plan.repli") == 1
    event = ctx.diagnostics.to_dict()["events"][-1]["details"]
    assert event["cause"] == "exception" and event["error_type"] == "RuntimeError"
    assert ctx.diagnostics.count("plan.sortie_tronquee") == 0


async def test_sortie_tronquee_notee() -> None:
    llm = FakeLLM().on_structured("research_plan", LLMTruncatedError("coupe"))
    ctx = ExecutionContext(QUERY)
    plan = await create_detailed_plan(_orch(llm).services(), QUERY, {}, EXPLORATION, ctx=ctx)
    assert plan["fallback"] is True
    assert ctx.diagnostics.count("plan.sortie_tronquee") == 1
    assert ctx.diagnostics.count("plan.repli") == 1


async def test_toutes_sections_meta_declenchent_le_repli() -> None:
    llm = FakeLLM().on_structured(
        "research_plan", _plan("Structure du rapport", "Stratégie de recherche")
    )
    ctx = ExecutionContext(QUERY)
    plan = await create_detailed_plan(_orch(llm).services(), QUERY, {}, EXPLORATION, ctx=ctx)
    assert plan["fallback"] is True
    assert plan["fallback_reason"] == "sections_filtrees"
    assert plan["sections"]
    assert ctx.diagnostics.count("plan.repli") == 1


async def test_repli_sans_ctx_ne_leve_pas() -> None:
    llm = FakeLLM().on_structured("research_plan", RuntimeError("boom"))
    plan = await create_detailed_plan(_orch(llm).services(), QUERY, {}, EXPLORATION)
    assert plan["fallback"] is True


@pytest.mark.parametrize("v2", [False, True])
async def test_plan_valide_sans_repli(v2: bool) -> None:
    llm = FakeLLM().on_structured("research_plan", _plan("Contexte", "Impacts", "Methodologie de recherche"))
    ctx = ExecutionContext(QUERY)
    plan = await create_detailed_plan(_orch(llm, v2).services(), QUERY, {}, EXPLORATION, ctx=ctx)
    assert "fallback" not in plan
    assert plan["sections"] == ["Contexte", "Impacts"]
    assert ctx.diagnostics.count("plan.repli") == 0
    assert llm.unmatched == []


async def test_prompt_v1_garde_ancien_format_et_contexte_entier() -> None:
    llm = FakeLLM().on_structured("research_plan", _plan("A"))
    context = {"secret_field": "VALEUR_INUTILE", "objectives": ["obj1"]}
    await create_detailed_plan(_orch(llm, False).services(), QUERY, context, EXPLORATION)
    prompt = llm.calls_for(schema_name="research_plan")[0].prompt
    assert "Retourne JSON" in prompt
    assert "VALEUR_INUTILE" in prompt


async def test_prompt_v2_sans_ancien_format_et_contexte_reduit() -> None:
    llm = FakeLLM().on_structured("research_plan", _plan("A"))
    context = {
        "secret_field": "VALEUR_INUTILE",
        "objectives": ["obj1"],
        "output_sections": ["Section imposee"],
        "domains_whitelist": ["who.int"],
    }
    await create_detailed_plan(_orch(llm, True).services(), QUERY, context, EXPLORATION)
    prompt = llm.calls_for(schema_name="research_plan")[0].prompt
    assert "Retourne JSON" not in prompt
    assert "EXEMPLES" not in prompt
    assert "VALEUR_INUTILE" not in prompt
    assert QUERY in prompt and "obj1" in prompt
    assert "Section imposee" in prompt and "who.int" in prompt
    assert "Strategie de recherche" in prompt  # regle des sections interdites conservee


async def test_schema_identique_dans_les_deux_positions() -> None:
    schemas = []
    for v2 in (False, True):
        llm = FakeLLM().on_structured("research_plan", _plan("A"))
        await create_detailed_plan(_orch(llm, v2).services(), QUERY, {}, EXPLORATION)
        schemas.append(json.dumps(llm.calls_for(schema_name="research_plan")[0].schema, sort_keys=True))
    assert schemas[0] == schemas[1]


async def test_client_sans_sortie_structuree_prompt_v2_embarque_le_schema() -> None:
    class _TextLLM(FakeLLM):
        supports_structured = False

    llm = _TextLLM().on_text(r"CONSIGNE", json.dumps(_plan("A")))
    plan = await create_detailed_plan(_orch(llm, True).services(), QUERY, {}, EXPLORATION)
    assert "schema" in llm.calls[0].prompt
    assert "fallback" not in plan


# --- Filtre des sections meta : mots entiers, retrait note -------------------


async def test_filtre_meta_sur_mots_entiers_et_retrait_note() -> None:
    llm = FakeLLM().on_structured(
        "research_plan",
        _plan("Démarches administratives pour la vignette Crit'Air", "Notre démarche", "Coûts"),
    )
    ctx = ExecutionContext(QUERY)
    plan = await create_detailed_plan(_orch(llm).services(), QUERY, {}, EXPLORATION, ctx=ctx)
    assert plan["sections"] == ["Démarches administratives pour la vignette Crit'Air", "Coûts"]
    assert list(plan["section_targets"]) == plan["sections"]
    events = [e for e in ctx.diagnostics.to_dict()["events"] if e["event"] == "plan.section_meta_retiree"]
    assert [e["details"]["titre"] for e in events] == ["Notre démarche"]


def test_invariant_meta_section_sur_mots_entiers() -> None:
    from app.research.invariants import check_report

    def codes(title: str) -> list[str]:
        report = {"sections": [{"title": title, "content": "Texte complet."}], "bibliography": []}
        return [v["code"] for v in check_report(report)["violations"]]

    assert "META_SECTION" not in codes("Démarches administratives pour la vignette Crit'Air")
    assert "META_SECTION" in codes("Notre démarche")


# --- Chemin non structure : sections et cibles reconciliees ------------------


class _TextOnlyLLM(FakeLLM):
    supports_structured = False


def _text_plan(sections: list[Any], targets: dict[str, Any]) -> str:
    return json.dumps(
        {
            "complexity_analysis": {"overall_score": 3.0},
            "sections": sections,
            "section_targets": targets,
            "narrative_flow": [],
            "search_strategy": {"search_depth": "standard"},
        }
    )


async def test_chemin_non_structure_reconcilie_sections_et_cibles() -> None:
    cible = {"words_target": 300, "depth": "deep", "objectives": ["o"], "key_questions": ["q"]}
    llm = _TextOnlyLLM().on_text(
        r"planificateur", _text_plan(["A", "B"], {"A": cible, "C": cible})
    )
    ctx = ExecutionContext(QUERY)
    plan = await create_detailed_plan(_orch(llm).services(), QUERY, {}, EXPLORATION, ctx=ctx)
    assert "fallback" not in plan
    # Une seule liste ordonnee : sections, puis la cible orpheline.
    assert plan["sections"] == ["A", "B", "C"]
    assert list(plan["section_targets"]) == ["A", "B", "C"]
    assert plan["section_targets"]["A"]["depth"] == "deep"
    assert plan["section_targets"]["B"]["depth"] == "moderate"  # cible generique
    assert plan["section_targets"]["C"] == cible
    assert ctx.diagnostics.count("plan.cible_manquante") == 1
    assert ctx.diagnostics.count("plan.cible_sans_section") == 1


async def test_chemin_non_structure_accepte_le_format_du_schema() -> None:
    llm = _TextOnlyLLM().on_text(r"CONSIGNE", json.dumps(_plan("A", "Stratégie de recherche")))
    ctx = ExecutionContext(QUERY)
    plan = await create_detailed_plan(_orch(llm, True).services(), QUERY, {}, EXPLORATION, ctx=ctx)
    assert plan["sections"] == ["A"]
    assert plan["section_targets"]["A"]["search_terms"] == ["solar access"]
    assert ctx.diagnostics.count("plan.section_meta_retiree") == 1


async def test_chemin_non_structure_toutes_sections_meta_donne_le_repli() -> None:
    llm = _TextOnlyLLM().on_text(r"planificateur", _text_plan(["Plan de travail"], {}))
    ctx = ExecutionContext(QUERY)
    plan = await create_detailed_plan(_orch(llm).services(), QUERY, {}, EXPLORATION, ctx=ctx)
    assert plan["fallback"] is True
    assert plan["fallback_reason"] == "sections_filtrees"
