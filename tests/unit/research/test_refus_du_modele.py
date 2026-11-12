"""Refus du modele de redaction : detecte, jamais publie comme contenu.

Cas reel du 12/11/2026 (consigne sur les neonicotinoides) : deux sections
et le resume du rapport etaient le texte de refus du modele.
"""

from __future__ import annotations

import pytest

from app.research.invariants import check_report
from app.research.refusal import is_refusal

REFUS_REEL = (
    "Il est impossible de rédiger la section demandée en respectant les consignes de "
    "citation et de fidélité aux sources, car les données fournies ne contiennent aucune "
    "information pertinente sur les néonicotinoïdes, les abeilles ou les réglementations "
    "européennes."
)


@pytest.mark.parametrize(
    "text",
    [
        REFUS_REEL,
        "Je ne peux pas rédiger cette section : les sources fournies ne permettent pas de répondre.",
        "I cannot write this section because the provided sources do not contain relevant data.",
        "The provided data does not contain any information about bees.",
    ],
)
def test_refus_detecte(text: str) -> None:
    assert is_refusal(text)


@pytest.mark.parametrize(
    "text",
    [
        "Les néonicotinoïdes agissent sur les récepteurs nicotiniques de l'acétylcholine [1].",
        # Phrase analogue loin dans une section argumentee : pas un refus.
        "Les restrictions de 2013 ont ete suivies d'une interdiction en 2018 [2]. " * 12
        + "Il est impossible de rédiger une conclusion definitive sur leur efficacite.",
        "",
        None,
    ],
)
def test_contenu_normal_non_detecte(text) -> None:
    assert not is_refusal(text)


def test_invariant_signale_un_refus_publie() -> None:
    report = {
        "title": "T",
        "summary": REFUS_REEL,
        "sections": [{"title": "Mecanismes", "content": REFUS_REEL}],
        "bibliography": [],
    }
    codes = [v["code"] for v in check_report(report)["violations"]]
    assert codes.count("REFUSAL_TEXT") == 2


async def test_synthese_d_un_refus_devient_section_sans_source() -> None:
    from app.research.context import ExecutionContext
    from app.research.stages.synthesize import synthesize_single_section
    from tests.contract.test_pipeline_contract import build_orchestrator, nominal

    orch, llm, _search, _extractor = build_orchestrator(nominal())
    llm._text.insert(0, (__import__("re").compile("Rédige la section", __import__("re").DOTALL), REFUS_REEL))
    ctx = ExecutionContext("q")
    data = [{"source": "https://example.org/a", "title": "A", "content": "x " * 400}]
    out = await synthesize_single_section(
        orch.services(), "q", "Mecanismes", {"depth": "moderate", "words_target": 300}, data, ctx
    )
    assert out.startswith("[SECTION_SANS_SOURCE]")
    assert ctx.diagnostics.count("selection.refus_du_modele") == 1
