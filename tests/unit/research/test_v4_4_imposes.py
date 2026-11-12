"""Lot V4-4 : sections imposees (output_format.sections) et sources requises (sources.required).

Doublures de tests/fakes uniquement (aucun reseau, aucun LLM reel). Chaque
test echoue sur le code d'avant le lot : sections et URL requises n'etaient
que citees dans le prompt du plan.
"""

import asyncio
import re
from typing import Any

import app.agents.intelligent_orchestrator as orchestrator_module
import pytest
from app.research.pipeline_config import PipelineConfig
from app.research.stages.imposed import enforce_imposed_sections
from app.research.stages.plan_schema import build_plan_schema
from app.research.stages.services import StageServices

from tests.contract.test_pipeline_contract import (
    QUERY,
    SectionSpec,
    _section_text,
    build_orchestrator,
    nominal,
)
from tests.fakes import make_result
from tests.unit.api.test_v3_3_deep_contract import Harness

REQ_URL = "https://req.org/rapport"
REQ_TITLE = "Rapport officiel requis"
REQ_TEXT = (
    "Official requirement report on solar electricity access: subsidised kits, "
    "tariff reform and village mini-grids are the three levers named by the ministry."
)


class _FastAsyncio:
    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)

    @staticmethod
    async def sleep(_delay: float) -> None:
        return None


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator_module, "asyncio", _FastAsyncio())


def build(
    specs: list[SectionSpec] | None = None,
    extra: list[SectionSpec] | None = None,
    required_page: bool = True,
    rename: dict[str, str] | None = None,
):
    """Orchestrateur de doublures. `extra` : sections que le plan du faux LLM n'a pas
    prevues mais dont la recherche et la redaction sont scenarisees ; `rename` :
    titres imposes (casse differente) dont la redaction est scenarisee."""
    specs = specs if specs is not None else nominal()
    orch, llm, search, extractor = build_orchestrator(specs)
    for spec in extra or []:
        search.add(
            re.compile(spec.name.split()[0], re.IGNORECASE),
            [make_result(u, f"{spec.name} {i}", "Snippet") for i, u in enumerate(spec.sources)],
        )
        for url in spec.sources:
            extractor.add_page(
                url, f"Field report published at {url}. " + "Solar facts. " * 40, title=url
            )
        llm.on_text(rf'Rédige la section "{re.escape(spec.name)}"', _section_text(spec))
    for old, new in (rename or {}).items():
        spec = next(s for s in specs if s.name == old)
        llm.on_text(rf'Rédige la section "{re.escape(new)}"', _section_text(spec))
    if required_page:
        extractor.add_page(REQ_URL, REQ_TEXT, title=REQ_TITLE)
    return orch, llm, search, extractor


def titles(result: dict[str, Any]) -> list[str]:
    return [s["title"] for s in result["answer"]["sections"]]


def events(result: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [e for e in result["diagnostics"]["events"]["events"] if e["event"] == name]


# --- Sections imposees -----------------------------------------------------------


async def test_sections_imposees_titres_et_ordre_malgre_un_autre_plan() -> None:
    policy = SectionSpec("Policy drivers", "policy drivers", ["https://example.org/policy-1"])
    orch, llm, _s, _e = build(extra=[policy], rename={"Outlook": "OUTLOOK"})
    imposed = ["OUTLOOK", "Policy drivers", "overview"]
    llm.on_text(r'Rédige la section "overview"', _section_text(nominal()[0]))

    result = await orch.execute_intelligent_research(QUERY, {"output_sections": imposed})

    # Le faux LLM propose Overview, Costs, Outlook : l'ordre et les titres imposes l'emportent.
    assert result["success"] is True
    assert titles(result) == imposed
    assert llm.unmatched == []
    [event] = events(result, "plan.sections_imposees")
    details = event["details"]
    assert details["ajoutees"] == ["Policy drivers"]
    assert details["retirees"] == ["Costs"]
    assert {"from": "Outlook", "to": "OUTLOOK"} in details["renommees"]


async def test_section_ajoutee_recoit_une_cible_par_defaut() -> None:
    policy = SectionSpec("Policy drivers", "policy drivers", ["https://example.org/policy-1"])
    orch, llm, search, _e = build(extra=[policy])
    await orch.execute_intelligent_research(
        QUERY, {"output_sections": ["Overview", "Policy drivers"]}
    )

    # Requete derivee du titre et de la consigne par la fonction existante.
    assert any("policy" in q for q in search.queries_text())
    assert llm.unmatched == []


async def test_section_imposee_de_type_meta_n_est_pas_filtree() -> None:
    methodo = SectionSpec(
        "Méthodologie de recherche", "solar methodology", ["https://example.org/m-1"]
    )
    orch, _llm, _s, _e = build(
        [nominal()[0], methodo], rename={"Méthodologie de recherche": "méthodologie de RECHERCHE"}
    )
    # Le faux LLM propose la section meta ; sans l'imposition, le filtre meta la retire.
    result = await orch.execute_intelligent_research(
        QUERY, {"output_sections": ["Overview", "méthodologie  de RECHERCHE"]}
    )
    assert titles(result) == ["Overview", "méthodologie de RECHERCHE"]
    assert events(result, "plan.sections_imposees")[0]["details"]["ajoutees"] == []


async def test_le_modele_de_plan_recoit_les_sections_dans_le_prompt_et_le_schema() -> None:
    orch, llm, _s, _e = build()
    imposed = ["Overview", "Outlook"]
    await orch.execute_intelligent_research(QUERY, {"output_sections": imposed})

    [call] = llm.calls_for(schema_name="research_plan")
    assert "SECTIONS IMPOSEES PAR L'APPELANT" in call.prompt
    items = call.schema["properties"]["sections"]
    assert items["items"]["properties"]["name"]["enum"] == imposed
    assert items["minItems"] == items["maxItems"] == 2


def test_schema_inchange_sans_sections_imposees() -> None:
    cfg = PipelineConfig()
    assert build_plan_schema(cfg) == build_plan_schema(cfg, imposed_sections=[])


def test_normalisation_comparaison_insensible_casse_et_espaces() -> None:
    svc = StageServices(None, None, None, None, PipelineConfig())
    plan = {
        "sections": ["Vue  d'ensemble", "Autre"],
        "section_targets": {
            "Vue  d'ensemble": {"words_target": 500, "depth": "deep"},
            "Autre": {"words_target": 100, "depth": "light"},
        },
        "narrative_flow": [
            {"from_section": "Vue  d'ensemble", "to_section": "Autre", "transition_type": "zoom-in"}
        ],
    }
    out = enforce_imposed_sections(svc, None, QUERY, plan, ["vue d'ENSEMBLE", "Chiffres"])
    assert out["sections"] == ["vue d'ENSEMBLE", "Chiffres"]
    assert out["section_targets"]["vue d'ENSEMBLE"]["depth"] == "deep"
    chiffres = out["section_targets"]["Chiffres"]
    assert chiffres["depth"] == "moderate"
    assert chiffres["words_target"] == PipelineConfig().plan_fallback_analysis_words
    assert chiffres["search_terms"] and chiffres["search_terms"][0]
    assert out["narrative_flow"] == []  # transition vers une section retiree


# --- Sources requises -------------------------------------------------------------


async def test_url_requise_extraite_proposee_a_toutes_les_sections_et_citee() -> None:
    specs = nominal()
    specs[0].cite_order = [REQ_URL]  # la redaction de Overview cite la source requise
    orch, llm, _s, extractor = build(specs)

    result = await orch.execute_intelligent_research(QUERY, {"sources_required": [REQ_URL]})

    assert extractor.urls_requested().count(REQ_URL) == 1  # une seule extraction, mise en cache
    for name in ("Overview", "Costs", "Outlook"):
        [call] = llm.calls_for(pattern=rf'Rédige la section "{name}"')
        assert "Official requirement report" in call.prompt, name
    report = result["answer"]
    assert REQ_URL in {b["url"] for b in report["bibliography"]}
    assert not report.get("degraded")
    assert "required_sources_uncited" not in report
    assert "required_sources_failed" not in report
    # Visible du plan : titre et extrait de la page dans le prompt du plan.
    [plan_call] = llm.calls_for(schema_name="research_plan")
    assert REQ_TITLE in plan_call.prompt and "Official requirement report" in plan_call.prompt


async def test_url_requise_non_citee_est_signalee_sans_degrader() -> None:
    orch, _llm, _s, _e = build()
    result = await orch.execute_intelligent_research(QUERY, {"sources_required": [REQ_URL]})
    report = result["answer"]

    assert report["required_sources_uncited"] == [REQ_URL]
    assert not report.get("degraded")
    assert events(result, "sources.requise_non_citee")


async def test_url_requise_en_echec_rend_le_rapport_degrade() -> None:
    orch, _llm, _s, _e = build(required_page=False)
    result = await orch.execute_intelligent_research(QUERY, {"sources_required": [REQ_URL]})
    report = result["answer"]

    assert report["degraded"] is True
    [failed] = report["required_sources_failed"]
    assert failed["url"] == REQ_URL and failed["reason"]
    assert events(result, "sources.requise_echec")


@pytest.mark.parametrize(
    "constraint", [{"domains_whitelist": ["example.org"]}, {"sources_exclusions": ["req.org"]}]
)
async def test_url_requise_hors_contraintes_de_domaines_est_acceptee(
    constraint: dict[str, Any],
) -> None:
    orch, llm, _s, extractor = build()
    result = await orch.execute_intelligent_research(
        QUERY, {"sources_required": [REQ_URL], **constraint}
    )

    assert REQ_URL in extractor.urls_requested()
    [call] = llm.calls_for(pattern=r'Rédige la section "Overview"')
    assert "Official requirement report" in call.prompt
    assert "required_sources_failed" not in result["answer"]
    [event] = events(result, "sources.requise_hors_contraintes")
    assert event["details"]["url"] == REQ_URL


async def test_sans_parametre_rien_ne_change() -> None:
    orch, _llm, _s, extractor = build()
    result = await orch.execute_intelligent_research(QUERY, {})
    assert titles(result) == ["Overview", "Costs", "Outlook"]
    assert REQ_URL not in extractor.urls_requested()
    assert events(result, "plan.sections_imposees") == []
    assert "required_sources_failed" not in result["answer"]


# --- Endpoint -----------------------------------------------------------------------


async def test_endpoint_sections_imposees_et_source_requise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs = nominal()
    specs[0].cite_order = [REQ_URL]
    h = Harness(monkeypatch, specs)
    h.extractor.add_page(REQ_URL, REQ_TEXT, title=REQ_TITLE)
    h.llm.on_text(r'Rédige la section "OUTLOOK"', _section_text(specs[2]))

    body = (
        await h.post(
            {
                "topic": QUERY,
                "sources": {"required": [REQ_URL]},
                "output_format": {"sections": ["OUTLOOK", "Overview"]},
            }
        )
    ).json()

    assert body["success"] is True and body["degraded"] is False
    assert [s["title"] for s in body["result"]["sections"]] == ["OUTLOOK", "Overview"]
    assert body["execution"]["summary"]["required_sources_status"] == "all_success"
    assert "required_sources_failed" not in body


async def test_endpoint_source_requise_en_echec(monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(monkeypatch, nominal())
    body = (
        await h.post({"topic": QUERY, "sources": {"required": ["https://req.org/absente"]}})
    ).json()

    assert body["degraded"] is True and body["success"] is False
    [failed] = body["required_sources_failed"]
    assert failed["url"] == "https://req.org/absente" and failed["reason"]
    assert "https://req.org/absente" in body["error"]
    assert body["execution"]["summary"]["required_sources_status"] == "partial_failure"
    [website] = [w for w in body["sources"]["websites"] if w["url"] == "https://req.org/absente"]
    assert website["status"] == "failed" and website["reason"]
