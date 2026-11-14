"""Audit de la redaction (14/11/2026) : sortie coupee, reecriture reverifiee,
brouillon ecrit avant la verification, citations mal formees, refus traduits,
verification partielle, sources distinctes, passage coupe a la phrase, faits
des sections les plus proches."""

from __future__ import annotations

import asyncio
from typing import Any

from app.research.answer_check import SentenceVerdict, add_missing_citations, check_answer
from app.research.context import ExecutionContext
from app.research.pipeline_config import PipelineConfig
from app.research.refusal import is_refusal
from app.research.stages.select import semantic_chunk_selection
from app.research.stages.synthesis_prompt import SourceLabels, convert_labels, established_facts
from app.research.stages.synthesize import (
    cut_at_last_sentence,
    synthesize_single_section,
    write_sections,
)

from tests.fakes import FakeLLM, FakeLLMError
from tests.unit.research.test_v3_6_selection import QUERY, SECTION, SECTION_CONFIG, SYNTH, _item, _orch

URL_A = "https://s.org/a"
URL_B = "https://t.org/b"
URL_C = "https://u.org/c"
REWRITE = "Verification de ta reponse"


def _ctx(**flags: Any) -> ExecutionContext:
    return ExecutionContext(QUERY, config=PipelineConfig(**flags))


def _events(ctx: ExecutionContext, name: str) -> list[dict[str, Any]]:
    return [e["details"] for e in ctx.diagnostics.to_dict()["events"] if e["event"] == name]


def _labels(*urls: str) -> SourceLabels:
    return SourceLabels.from_passages(
        [{"source": u, "title": f"T{i}", "content": f"passage {i}"} for i, u in enumerate(urls)]
    )


def _finishing(llm: FakeLLM, text: str, reason: str | None) -> Any:
    """Reponse scenarisee qui pose la raison d'arret du client, comme le client reel."""

    def respond(_call: Any) -> str:
        llm._record_finish_reason(reason)
        return text

    return respond


def _verdict(*items: tuple[int, str, list[str]]) -> dict[str, Any]:
    return {"phrases": [
        {"numero": n, "verdict": v, "probleme": "" if v == "soutenue" else "absent", "appuis": a}
        for n, v, a in items
    ]}


# --- 1. Plancher de max_tokens, sortie coupee ------------------------------------


async def test_plancher_de_max_tokens_pour_un_objectif_court() -> None:
    llm = FakeLLM()
    llm.on_text(SYNTH, "Solar kits are cheaper than diesel in rural areas [S1].")
    llm.on_structured("verification", {"phrases": []})
    config = dict(SECTION_CONFIG, words_target=100)
    await synthesize_single_section(_orch(llm).services(), QUERY, SECTION, config, [_item(URL_A)], _ctx())
    assert llm.calls_for(kind="generate")[0].kwargs["max_tokens"] == 3000


async def test_sortie_coupee_relancee_avec_plafond_double() -> None:
    llm = FakeLLM()
    llm.on_text(SYNTH, [
        _finishing(llm, "Solar kits are cheaper than diesel [S1]. Rural households gai", "length"),
        _finishing(llm, "Solar kits are cheaper than diesel [S1]. Rural households gained access [S1].", "stop"),
    ])
    llm.on_structured("verification", {"phrases": []})
    ctx = _ctx()
    out = await synthesize_single_section(_orch(llm).services(), QUERY, SECTION, SECTION_CONFIG, [_item(URL_A)], ctx)
    calls = llm.calls_for(kind="generate")
    assert [c.kwargs["max_tokens"] for c in calls] == [3000, 6000]
    assert out.endswith(f"Rural households gained access [SOURCE:{URL_A}].")
    assert len(_events(ctx, "selection.synthese_tronquee")) == 1
    assert _events(ctx, "selection.synthese_coupee_a_la_phrase") == []


async def test_sortie_encore_coupee_coupee_a_la_derniere_phrase() -> None:
    llm = FakeLLM()
    llm.on_text(SYNTH, _finishing(
        llm, "Solar kits are cheaper than diesel [S1]. Rural households gained acc", "length"
    ))
    llm.on_structured("verification", {"phrases": []})
    ctx = _ctx()
    out = await synthesize_single_section(_orch(llm).services(), QUERY, SECTION, SECTION_CONFIG, [_item(URL_A)], ctx)
    assert out == f"Solar kits are cheaper than diesel [SOURCE:{URL_A}]."
    events = _events(ctx, "selection.synthese_coupee_a_la_phrase")
    assert len(events) == 1 and events[0]["max_tokens"] == 6000


def test_coupe_a_la_derniere_phrase_ou_rangee_complete() -> None:
    table = "Intro du tableau [S1].\n\n| A | B | Source |\n|---|---|---|\n| x | 1 | [S1] |\n| y | 12,"
    assert cut_at_last_sentence(table) == "Intro du tableau [S1].\n\n| A | B | Source |\n|---|---|---|\n| x | 1 | [S1] |"
    assert cut_at_last_sentence("Le montant atteint 12.5 millions. Puis 12") == "Le montant atteint 12.5 millions."
    assert cut_at_last_sentence("Fait etabli [S1].\n\n### Suite\nDebut coup") == "Fait etabli [S1]."
    assert cut_at_last_sentence("Rien de complet ici") == ""


# --- 2. Reecriture reverifiee -------------------------------------------------------

DRAFT = "Solar kits are cheaper than diesel [S1]. Rural households doubled their income in 2020."
CORRECTED = "Solar kits are cheaper than diesel [S1]. Rural households gained access in 2021."


def _rewrite_llm(second: Any) -> FakeLLM:
    llm = FakeLLM()
    llm.on_text(REWRITE, CORRECTED)
    llm.on_text(SYNTH, DRAFT)
    llm.on_structured("verification", [
        _verdict((1, "soutenue", ["S1"]), (2, "non_soutenue", [])),
        second,
    ])
    return llm


async def test_reecriture_gardee_et_citee_avec_ses_propres_verdicts() -> None:
    llm = _rewrite_llm(_verdict((1, "soutenue", ["S1"]), (2, "soutenue", ["S1"])))
    ctx = _ctx()
    out = await synthesize_single_section(_orch(llm).services(), QUERY, SECTION, SECTION_CONFIG, [_item(URL_A)], ctx)
    # La phrase nouvelle recoit l'etiquette que la reverification designe.
    assert out == (
        f"Solar kits are cheaper than diesel [SOURCE:{URL_A}]. "
        f"Rural households gained access in 2021 [SOURCE:{URL_A}]."
    )
    assert len(llm.calls_for(schema_name="verification")) == 2
    assert llm.calls_for(pattern=REWRITE)[0].kwargs["max_tokens"] == 3000
    assert _events(ctx, "selection.section_reecrite")[0]["fautives_apres"] == 0


async def test_reecriture_rejetee_si_pas_moins_de_phrases_fautives() -> None:
    llm = _rewrite_llm(_verdict((1, "soutenue", ["S1"]), (2, "contredite", [])))
    ctx = _ctx()
    out = await synthesize_single_section(_orch(llm).services(), QUERY, SECTION, SECTION_CONFIG, [_item(URL_A)], ctx)
    assert out == (
        f"Solar kits are cheaper than diesel [SOURCE:{URL_A}]. "
        "Rural households doubled their income in 2020."
    )
    assert _events(ctx, "selection.reecriture_rejetee") == [
        {"section": SECTION, "fautives_origine": 1, "fautives_reecriture": 1}
    ]
    assert _events(ctx, "selection.section_reecrite") == []


async def test_reverification_en_echec_garde_la_reecriture() -> None:
    llm = _rewrite_llm(FakeLLMError("verificateur indisponible"))
    ctx = _ctx()
    out = await synthesize_single_section(_orch(llm).services(), QUERY, SECTION, SECTION_CONFIG, [_item(URL_A)], ctx)
    assert "gained access in 2021" in out
    assert len(_events(ctx, "selection.reverification_impossible")) == 1


async def test_sans_drapeau_reecriture_non_reverifiee() -> None:
    llm = _rewrite_llm(_verdict((1, "soutenue", ["S1"]), (2, "contredite", [])))
    ctx = _ctx(deep_verify_rewrite=False)
    out = await synthesize_single_section(_orch(llm).services(), QUERY, SECTION, SECTION_CONFIG, [_item(URL_A)], ctx)
    assert "gained access in 2021." in out
    assert len(llm.calls_for(schema_name="verification")) == 1


# --- 3. Brouillon ecrit avant la verification ---------------------------------------


class _HangingVerifier(FakeLLM):
    """Verification qui ne rend jamais la main : echeance globale simulee."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def generate_structured(self, messages: list, schema: dict, schema_name: str = "response", **kwargs: Any) -> dict:
        if schema_name == "verification":
            self.started.set()
            await asyncio.Event().wait()
        return await super().generate_structured(messages, schema, schema_name, **kwargs)


async def test_brouillon_dans_le_rapport_si_l_echeance_tombe_pendant_la_verification() -> None:
    llm = _HangingVerifier()
    llm.on_text(SYNTH, "Solar kits are cheaper than diesel in rural areas [S1].")
    orch, ctx = _orch(llm), _ctx()
    ctx.initialize_sections([SECTION])
    task = asyncio.ensure_future(write_sections(
        orch.services(), ctx, QUERY, {SECTION: SECTION_CONFIG}, {SECTION: [_item(URL_A)]}
    ))
    await asyncio.wait_for(llm.started.wait(), timeout=5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    entry = ctx.final_content["sections"][SECTION]
    assert entry["content"] == f"Solar kits are cheaper than diesel in rural areas [SOURCE:{URL_A}]."
    assert entry["raw_data"]
    report = orch._partial_report(QUERY, ctx)
    assert [s["title"] for s in report["sections"]] == [SECTION]
    assert report["missing_sections"] == []


async def test_brouillon_remplace_par_la_version_finale() -> None:
    llm = _rewrite_llm(_verdict((1, "soutenue", ["S1"]), (2, "soutenue", ["S1"])))
    orch, ctx = _orch(llm), _ctx()
    ctx.initialize_sections([SECTION])
    await write_sections(orch.services(), ctx, QUERY, {SECTION: SECTION_CONFIG}, {SECTION: [_item(URL_A)]})
    assert "gained access in 2021" in ctx.final_content["sections"][SECTION]["content"]


async def test_pas_de_brouillon_pour_une_section_refusee() -> None:
    llm = FakeLLM()
    llm.on_text(SYNTH, "Les sources fournies ne traitent pas de cette section.")
    ctx = _ctx()
    out = await synthesize_single_section(_orch(llm).services(), QUERY, SECTION, SECTION_CONFIG, [_item(URL_A)], ctx)
    assert out.startswith("[SECTION_SANS_SOURCE]")
    assert SECTION not in ctx.final_content["sections"]


# --- 4. Citations mal formees ---------------------------------------------------------


def test_formes_multiples_converties_et_plages_developpees() -> None:
    labels = _labels(URL_A, URL_B, URL_C)
    a, b, c = (f"[SOURCE:{u}]" for u in (URL_A, URL_B, URL_C))
    text = (
        "Un [S1-S3]. Deux [S1 et S2]. Trois [S2 and S3]. Quatre [Source S3]. "
        "Cinq [S1; S3]. Six [Sources : S1, S2]. Sept [S1–2]."
    )
    conversion = convert_labels(text, labels)
    assert conversion.text == (
        f"Un {a} {b} {c}. Deux {a} {b}. Trois {b} {c}. Quatre {c}. "
        f"Cinq {a} {c}. Six {a} {b}. Sept {a} {b}."
    )
    assert conversion.unknown == conversion.bare_numbers == conversion.malformed == []


def test_numero_nu_et_etiquette_illisible_retires_sans_deviner() -> None:
    conversion = convert_labels(
        "Fait un [1]. Fait deux [2, 3] et suite. Fait trois [S1, p. 4]. En [2021] et [1](http://x.org).",
        _labels(URL_A),
    )
    assert conversion.text == "Fait un. Fait deux et suite. Fait trois. En [2021] et [1](http://x.org)."
    assert conversion.bare_numbers == ["[1]", "[2, 3]"]
    assert conversion.malformed == ["[S1, p. 4]"]


async def test_numero_nu_note_a_la_redaction() -> None:
    llm = FakeLLM()
    llm.on_text(SYNTH, "Solar kits are cheaper than diesel [1]. Rural households gained access [S1, tableau 2].")
    llm.on_structured("verification", {"phrases": []})
    ctx = _ctx()
    out = await synthesize_single_section(_orch(llm).services(), QUERY, SECTION, SECTION_CONFIG, [_item(URL_A)], ctx)
    assert out == "Solar kits are cheaper than diesel. Rural households gained access."
    assert _events(ctx, "citations.numero_nu")[0]["appels"] == ["[1]"]
    assert _events(ctx, "citations.etiquette_mal_formee")[0]["etiquettes"] == ["[S1, tableau 2]"]


def test_forme_multiple_reconnue_comme_deja_citee() -> None:
    text = "La loi date de 2019 [S1 et S2]. Le seuil est fixe [Source S2]."
    verdicts = [
        SentenceVerdict(1, "La loi date de 2019 [S1 et S2].", "soutenue", "", ("S1",)),
        SentenceVerdict(2, "Le seuil est fixe [Source S2].", "soutenue", "", ("S2",)),
    ]
    assert add_missing_citations(text, verdicts, {"S1", "S2"}) == (text, 0)


def test_appui_source_sn_reconnu() -> None:
    verdicts = [SentenceVerdict(1, "La loi date de 2019.", "soutenue", "", ("Source S2",))]
    out, added = add_missing_citations("La loi date de 2019.", verdicts, {"S1", "S2"})
    assert (out, added) == ("La loi date de 2019 [S2].", 1)


# --- 5. Refus traduit ---------------------------------------------------------------------


def test_refus_de_la_regle_6_reconnu_dans_les_langues_de_redaction() -> None:
    for text in [
        "The provided sources do not address this section.",
        "The sources provided do not cover this section.",
        "The supplied sources do not deal with the topic of this section.",
        "The provided sources contain no information about this section.",
        "Les sources ne traitent pas de cette section.",
        "Las fuentes proporcionadas no tratan esta sección.",
        "Die bereitgestellten Quellen behandeln diesen Abschnitt nicht.",
        "Le fonti fornite non trattano questa sezione.",
        "As fontes fornecidas não tratam desta seção.",
    ]:
        assert is_refusal(text), text


def test_phrase_de_contenu_anglaise_non_prise_pour_un_refus() -> None:
    for text in [
        "The provided sources do not cover the maintenance costs, but they document the 2019 schedule [S1].",
        "The sources do not address this issue directly; the 2021 report gives the only figure [S2].",
        "This section covers the sources of funding: the provided budget does not include grants [S1].",
    ]:
        assert not is_refusal(text), text


# --- 6. Verification partielle ---------------------------------------------------------


async def test_verification_partielle_comptee_et_notee() -> None:
    sentences = " ".join(f"Phrase numero {i} assez longue pour etre jugee [S1]." for i in range(1, 6))
    llm = FakeLLM()
    llm.on_text(SYNTH, sentences)
    llm.on_structured("verification", _verdict((1, "soutenue", ["S1"]), (2, "soutenue", ["S1"])))
    verdicts = await check_answer(llm, sentences, [("S1", "texte")], label_style="id")
    assert (verdicts.submitted, verdicts.judged, verdicts.partial) == (5, 2, True)

    ctx = _ctx()
    await synthesize_single_section(_orch(llm).services(), QUERY, SECTION, SECTION_CONFIG, [_item(URL_A)], ctx)
    assert _events(ctx, "selection.verification_partielle") == [
        {"section": SECTION, "etape": "redaction", "soumises": 5, "jugees": 2}
    ]


# --- 7. Sources distinctes ----------------------------------------------------------------


async def test_peu_de_sources_compte_les_pages_et_non_les_passages() -> None:
    llm = FakeLLM()
    llm.on_text(SYNTH, "Solar kits are cheaper than diesel in rural areas [S1].")
    llm.on_structured("verification", {"phrases": []})
    # Six passages de deux pages : "peu de sources" (synthesis_few_sources_max = 2).
    data = [_item(url, tail=f"variante {i}") for i in range(3) for url in (URL_A, URL_B)]
    await synthesize_single_section(_orch(llm).services(), QUERY, SECTION, SECTION_CONFIG, data, _ctx())
    prompt = llm.calls_for(kind="generate")[0].prompt
    assert "Va a l'essentiel" in prompt


# --- 9. Passage coupe a la phrase, passages suivants essayes ----------------------------------


def _keyword_only_llm() -> FakeLLM:
    llm = FakeLLM()
    llm.supports_embeddings = False
    return llm


def _passage(source: str, sentences: int) -> dict[str, Any]:
    body = " ".join(
        f"Solar rural electricity access reached 12,5 millions of households in 20{i:02d}."
        for i in range(sentences)
    )
    return {"source": source, "title": source, "content": body}


async def test_passage_deep_coupe_a_la_derniere_phrase() -> None:
    llm = _keyword_only_llm()
    ctx = _ctx()
    first, second = _passage(URL_A, 40), _passage(URL_B, 40)
    selected = await semantic_chunk_selection(
        _orch(llm).services(), [first, second], QUERY, max_chars=len(first["content"]) + 1500,
        depth="deep", min_score_threshold=0.0, context=ctx, section=SECTION,
    )
    assert len(selected) == 2
    cut = selected[1]["content"]
    assert cut.endswith(".") and len(cut) <= 1500
    assert second["content"].startswith(cut)
    assert _events(ctx, "selection.passage_coupe")[0]["gardes"] == len(cut)


async def test_passage_trop_long_saute_les_suivants_essayes() -> None:
    llm = _keyword_only_llm()
    big_a, big_b = _passage(URL_A, 40), _passage(URL_B, 40)
    small = {"source": URL_C, "title": "C", "content": "Solar rural note of 2021."}
    selected = await semantic_chunk_selection(
        _orch(llm).services(), [big_a, big_b, small], QUERY, max_chars=len(big_a["content"]) + 200,
        depth="moderate", min_score_threshold=0.0, context=_ctx(), section=SECTION,
    )
    assert [s["source"] for s in selected] == [URL_A, URL_C]


# --- 10. Faits des sections les plus proches ----------------------------------------------------


def test_faits_des_sections_precedentes_immediates_d_abord() -> None:
    sections = {
        f"P{i}": {"content": f"Fait de la partie {i} etabli [SOURCE:{URL_A}]."} for i in range(1, 9)
    }
    sections["Courante"] = {"content": ""}
    fact_len = len("Fait de la partie 1 etabli.")
    facts = established_facts(sections, "Courante", total_chars=2 * fact_len, per_section_chars=600)
    assert facts == ["- (P7) Fait de la partie 7 etabli.", "- (P8) Fait de la partie 8 etabli."]
