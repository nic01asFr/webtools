"""Test de contrat du pipeline de recherche approfondie.

Execute execute_intelligent_research de bout en bout avec des doublures
(aucun reseau, aucun LLM reel) et applique les invariants de
app.research.invariants au rapport produit. Ce test doit rester vert, sans
modification, a travers tout deplacement de code (lots de type N).

Les defauts connus sont des xfail(strict=True) nommes par leur constat :
le lot qui corrige le defaut fait passer le test en XPASS (strict : echec),
ce qui oblige a retirer le marqueur.
- C44 : citations a prefixe commun, "[1]/b]" a la place de "[2]"
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

import app.agents.intelligent_orchestrator as orchestrator_module
import pytest
from app.agents.intelligent_orchestrator import IntelligentOrchestrator
from app.research.invariants import check_report

from tests.fakes import (
    FakeExtractorManager,
    FakeLLM,
    FakeLLMError,
    FakeSearch,
    make_result,
)

QUERY = "Impact of solar energy on rural electricity access"


@dataclass
class SectionSpec:
    """Une section du plan et la matiere que les doublures lui fournissent."""

    name: str
    terms: str
    sources: list[str] = field(default_factory=list)  # URLs rendues par la recherche
    cite_order: list[str] = field(default_factory=list)  # URLs citees par la synthese
    synthesis_fails: bool = False


def _page_text(url: str) -> str:
    return (
        f"Field report published at {url}. Off-grid solar kits reached households in remote "
        f"districts, cutting kerosene use and extending evening study hours. Surveys show "
        f"that financing remains the main barrier for low-income families, while maintenance "
        f"networks decide whether installations last beyond the first years of operation."
    )


def _section_text(spec: SectionSpec) -> str:
    """Texte de synthese de plus de 2000 caracteres apres conversion des citations.

    La longueur evite le declenchement de l'enrichissement iteratif (section
    faible), qui sortirait du perimetre du contrat.
    """
    urls = spec.cite_order or spec.sources
    if not urls:
        return f"Texte jamais utilise : la section {spec.name} n'a aucune source."
    sentences = [
        f"Point {i} of the {spec.name} analysis shows how solar power changes daily life "
        f"in rural districts [SOURCE:{urls[i % len(urls)]}]."
        for i in range(45)
    ]
    return " ".join(sentences)


def _plan(sections: list[SectionSpec]) -> dict[str, Any]:
    return {
        "complexity_analysis": {
            "overall_score": 3.0,
            "target_length": "standard",
            "estimated_words": 1200,
            "justification": "scenario de test",
        },
        "sections": [
            {
                "name": s.name,
                "words_target": 400,
                "depth": "moderate",
                "objectives": [f"Couvrir {s.name}"],
                "key_questions": [f"Question sur {s.name} ?"],
                "search_terms": [s.terms],
                "source_axis": "generaliste",
            }
            for s in sections
        ],
        "narrative_flow": [],
        "search_strategy": {
            "total_sources_needed": 6,
            "sources_per_section": 1,
            "search_depth": "quick",
            "language": "en",
        },
    }


def build_orchestrator(
    sections: list[SectionSpec],
) -> tuple[IntelligentOrchestrator, FakeLLM, FakeSearch, FakeExtractorManager]:
    llm = FakeLLM()
    search = FakeSearch()
    extractor = FakeExtractorManager()

    llm.on_structured("research_plan", _plan(sections))
    llm.on_structured(
        "coherence_analysis",
        {"improvements": [], "redundancies": [], "coherence_score": 90},
    )
    llm.on_text(
        r"Analyse la qualit",
        json.dumps({"overall_score": 90, "sections_analysis": [], "gaps_to_fill": []}),
    )

    # Exploration : la requete de l'utilisateur, telle quelle.
    exploration_urls = [u for s in sections for u in s.sources][:3]
    search.add(
        QUERY,
        [
            make_result(u, f"Result {i}", f"Snippet about solar energy {i}")
            for i, u in enumerate(exploration_urls)
        ],
    )

    for spec in sections:
        search.add(
            spec.terms,
            [
                make_result(u, f"{spec.name} {i}", f"Snippet {spec.name} {i}")
                for i, u in enumerate(spec.sources)
            ],
        )
        for url in spec.sources:
            extractor.add_page(url, _page_text(url), title=f"Page {url}")
        if spec.synthesis_fails:
            response: Any = FakeLLMError("Connection error")
        else:
            response = _section_text(spec)
        llm.on_text(rf'Rédige la section "{re.escape(spec.name)}"', response)

    orch = IntelligentOrchestrator(llm, search_client=search, extractor_manager=extractor)
    return orch, llm, search, extractor


class _FastAsyncio:
    """Proxy du module asyncio dont sleep est instantane (reessais de synthese)."""

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)

    @staticmethod
    async def sleep(_delay: float) -> None:
        return None


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator_module, "asyncio", _FastAsyncio())


# --- Scenarios ---------------------------------------------------------------


def nominal() -> list[SectionSpec]:
    return [
        SectionSpec(
            "Overview",
            "solar overview",
            ["https://example.org/overview-1", "https://example.org/overview-2"],
        ),
        SectionSpec("Costs", "solar costs", ["https://example.net/costs-1"]),
        SectionSpec("Outlook", "solar outlook", ["https://example.com/outlook-1"]),
    ]


def sourceless_first_section() -> list[SectionSpec]:
    return [
        SectionSpec("Overview", "solar overview"),
        SectionSpec("Costs", "solar costs", ["https://example.net/costs-1"]),
        SectionSpec("Outlook", "solar outlook", ["https://example.com/outlook-1"]),
    ]


def synthesis_failure() -> list[SectionSpec]:
    return [
        SectionSpec("Overview", "solar overview", ["https://example.org/overview-1"]),
        SectionSpec("Costs", "solar costs", ["https://example.net/costs-1"], synthesis_fails=True),
        SectionSpec("Outlook", "solar outlook", ["https://example.com/outlook-1"]),
    ]


def common_prefix_citations() -> list[SectionSpec]:
    a, ab = "https://x.org/a", "https://x.org/a/b"
    return [
        SectionSpec("Overview", "solar overview", [a, ab], cite_order=[a, ab]),
        SectionSpec("Costs", "solar costs", ["https://example.net/costs-1"]),
    ]


# Codes d'invariants couverts par un xfail ci-dessous, par scenario : le test
# generique les ecarte pour ne pas masquer d'autres violations.
SCENARIOS: dict[str, tuple[Any, set[str]]] = {
    "nominal": (nominal, set()),
    "section_sans_source": (sourceless_first_section, set()),
    "echec_synthese": (synthesis_failure, set()),
    "prefixe_commun": (common_prefix_citations, {"CITATION_RESIDUE"}),  # C44
}


async def run_scenario(
    name: str,
) -> tuple[dict[str, Any], FakeLLM, FakeSearch, FakeExtractorManager]:
    factory, _ = SCENARIOS[name]
    orch, llm, search, extractor = build_orchestrator(factory())
    result = await orch.execute_intelligent_research(QUERY)
    return result, llm, search, extractor


# --- Contrat ----------------------------------------------------------------


@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_invariants_du_rapport(name: str) -> None:
    result, llm, _search, _extractor = await run_scenario(name)

    assert result["success"] is True
    assert llm.unmatched == [], [c.prompt[:80] for c in llm.unmatched]
    violations = check_report(result["answer"])["violations"]
    known = SCENARIOS[name][1]
    unexpected = [v for v in violations if v["code"] not in known]
    assert unexpected == []


async def test_nominal_rapport_complet() -> None:
    result, llm, search, extractor = await run_scenario("nominal")
    report = result["answer"]

    assert [s["title"] for s in report["sections"]] == ["Overview", "Costs", "Outlook"]
    assert check_report(report)["ok"] is True
    assert not report.get("degraded")
    assert {b["url"] for b in report["bibliography"]} == set(extractor.urls_requested())
    assert len(llm.calls_for(schema_name="research_plan")) == 1
    assert search.queries[0].query == QUERY


async def test_section_sans_source_est_retiree_et_signalee() -> None:
    result, _llm, _search, _extractor = await run_scenario("section_sans_source")
    report = result["answer"]

    assert [s["title"] for s in report["sections"]] == ["Costs", "Outlook"]
    assert report["unsourced_sections"] == ["Overview"]


async def test_echec_de_synthese_est_signale() -> None:
    result, llm, _search, _extractor = await run_scenario("echec_synthese")
    report = result["answer"]

    assert report["degraded"] is True
    assert report["failed_sections"] == ["Costs"]
    assert [s["title"] for s in report["sections"]] == ["Overview", "Outlook"]
    assert len(llm.calls_for(pattern=r'Rédige la section "Costs"')) == 3


async def test_c41_resume_sans_marqueur_interne() -> None:
    result, _llm, _search, _extractor = await run_scenario("section_sans_source")
    codes = [v["code"] for v in check_report(result["answer"])["violations"]]
    assert "INTERNAL_MARKER" not in codes


@pytest.mark.xfail(
    strict=True, reason="C44 : citations a prefixe commun, [SOURCE:.../a/b] devient [1]/b]"
)
async def test_c44_citations_a_prefixe_commun() -> None:
    result, _llm, _search, _extractor = await run_scenario("prefixe_commun")
    codes = [v["code"] for v in check_report(result["answer"])["violations"]]
    assert "CITATION_RESIDUE" not in codes
