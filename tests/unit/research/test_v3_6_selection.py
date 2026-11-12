"""Lot V3-6 : selection des passages et donnees du prompt de synthese."""

import asyncio
from collections import Counter
from typing import Any

import app.agents.intelligent_orchestrator as orchestrator_module
import pytest
from app.agents.intelligent_orchestrator import ExecutionContext, IntelligentOrchestrator
from app.core.llm.base import LLMEmptyContentError
from app.research.embedding_cache import content_key
from app.research.pipeline_config import PipelineConfig
from app.research.stages.crossref import cross_reference_data
from app.research.stages.select import semantic_chunk_selection
from app.research.stages.synthesize import synthesize_single_section

from tests.fakes import FakeExtractorManager, FakeLLM, FakeSearch

QUERY = "solar energy rural electricity access"
SECTION = "Overview"
SECTION_CONFIG = {
    "depth": "moderate",
    "words_target": 400,
    "objectives": ["Couvrir le sujet"],
    "key_questions": ["Question ?"],
    "search_terms": ["solar rural"],
}
SYNTH = "Rédige la section"


class _FastAsyncio:
    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)

    @staticmethod
    async def sleep(_delay: float) -> None:
        return None


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator_module, "asyncio", _FastAsyncio())


def _body(tag: str, tail: str = "") -> str:
    # Les 100 premiers caracteres sont identiques pour tous les passages de
    # meme tag : seule la fin differe.
    return (
        f"Solar energy rural electricity access report {tag}. " * 3
        + f"Households gained access in 2021, reaching 28,9 percent. {tail}" * 4
    )


def _item(source: str, tail: str = "", tag: str = "a", **extra: Any) -> dict[str, Any]:
    return {"source": source, "title": f"Titre {source}", "content": _body(tag, tail), **extra}


def _orch(llm: FakeLLM | None = None) -> IntelligentOrchestrator:
    return IntelligentOrchestrator(
        llm or FakeLLM(), search_client=FakeSearch(), extractor_manager=FakeExtractorManager()
    )


def _ctx(**flags: Any) -> ExecutionContext:
    return ExecutionContext(QUERY, config=PipelineConfig(**flags))


def _embedded_contents(llm: FakeLLM) -> list[str]:
    return [t for c in llm.calls_for(kind="embed") for t in c.texts if t.startswith("Solar")]


def _events(ctx: ExecutionContext, name: str) -> list[dict[str, Any]]:
    return [e for e in ctx.diagnostics.to_dict()["events"] if e["event"] == name]


# --- C37 : cache d'embeddings -------------------------------------------------


async def test_chaque_passage_est_embedde_une_seule_fois() -> None:
    llm = FakeLLM()
    llm.on_text(SYNTH, "Texte redige. " * 20)
    orch, ctx = _orch(llm), _ctx()
    data = [_item(f"https://s{i}.org/a", tail=f"variante {i}") for i in range(4)]

    enriched = await cross_reference_data(orch.services(), SECTION, data, ctx)
    await synthesize_single_section(orch.services(), QUERY, SECTION, SECTION_CONFIG, enriched, ctx)

    counts = Counter(_embedded_contents(llm))
    assert len(counts) == 4
    assert max(counts.values()) == 1


async def test_cache_distingue_les_passages_a_prefixe_commun() -> None:
    llm = FakeLLM()
    orch, ctx = _orch(llm), _ctx()
    a = _item("https://s.org/a", tail="premiere fin")
    b = _item("https://s.org/a", tail="seconde fin")
    assert a["content"][:100] == b["content"][:100]

    enriched = await cross_reference_data(orch.services(), SECTION, [a, b], ctx)

    assert content_key(a["content"]) != content_key(b["content"])
    assert len(ctx.embedding_cache) == 2
    assert len(_embedded_contents(llm)) == 2
    assert len(enriched) == 2


async def test_cache_partage_entre_croisement_et_selection() -> None:
    llm = FakeLLM()
    orch, ctx = _orch(llm), _ctx()
    data = [_item(f"https://s{i}.org/a", tail=f"v{i}") for i in range(3)]

    enriched = await cross_reference_data(orch.services(), SECTION, data, ctx)
    before = len(llm.calls_for(kind="embed"))
    await semantic_chunk_selection(orch.services(), enriched, QUERY, context=ctx)
    after = llm.calls_for(kind="embed")[before:]

    assert [c.texts for c in after] == [[QUERY]]


async def test_repli_mots_cles_note_si_embeddings_en_erreur() -> None:
    class _Broken(FakeLLM):
        async def embed(self, texts: list, model: str = "x") -> list:
            raise RuntimeError("service indisponible")

    orch, ctx = _orch(_Broken()), _ctx()
    selected = await semantic_chunk_selection(orch.services(), [_item("https://s.org/a")], QUERY, context=ctx)

    assert len(selected) == 1
    assert ctx.diagnostics.count("selection.repli_mots_cles") == 1


async def test_sans_capacite_embeddings_pas_d_appel_et_repli_note() -> None:
    llm = FakeLLM()
    llm.supports_embeddings = False
    orch, ctx = _orch(llm), _ctx()

    await cross_reference_data(orch.services(), SECTION, [_item("https://s.org/a")], ctx)
    await semantic_chunk_selection(orch.services(), [_item("https://s.org/a")], QUERY, context=ctx)

    assert llm.calls_for(kind="embed") == []
    assert ctx.diagnostics.count("selection.repli_mots_cles") >= 1


# --- C38 : plafond de passages par source (drapeau) ----------------------------


def _long_doc_and_others() -> list[dict[str, Any]]:
    data = [_item("https://long.org/doc", tail=f"morceau {i}") for i in range(6)]
    data += [_item(f"https://autre{i}.org/p", tail="autre", tag="b") for i in range(2)]
    return data


async def test_max_chunks_per_source_defaut_sans_plafond() -> None:
    orch, ctx = _orch(), _ctx()
    selected = await semantic_chunk_selection(orch.services(), 
        _long_doc_and_others(), QUERY, max_chars=100000, min_score_threshold=0.0, context=ctx
    )
    assert Counter(i["source"] for i in selected)["https://long.org/doc"] == 6


async def test_max_chunks_per_source_actif_plafonne_chaque_source() -> None:
    orch, ctx = _orch(), _ctx(max_chunks_per_source=2)
    selected = await semantic_chunk_selection(orch.services(), 
        _long_doc_and_others(), QUERY, max_chars=100000, min_score_threshold=0.0, context=ctx
    )
    per_source = Counter(i["source"] for i in selected)
    assert per_source["https://long.org/doc"] == 2
    assert len(per_source) == 3


# --- C39 : charge utile du prompt (drapeau) ------------------------------------


def _rich_item() -> dict[str, Any]:
    return _item(
        "https://s.org/a",
        corroboration_count=2,
        cross_references=[{"note": "MARQUEUR_XREF"}],
        metadata={"k": "MARQUEUR_META"},
        structured_data={"numerical": [1]},
    )


async def _synthese_prompt(**flags: Any) -> str:
    llm = FakeLLM()
    llm.on_text(SYNTH, "Texte redige. " * 20)
    orch, ctx = _orch(llm), _ctx(**flags)
    await synthesize_single_section(orch.services(), QUERY, SECTION, SECTION_CONFIG, [_rich_item()], ctx)
    return llm.calls_for(kind="generate")[0].prompt


async def test_prompt_payload_defaut_garde_tous_les_champs() -> None:
    prompt = await _synthese_prompt()
    assert "MARQUEUR_XREF" in prompt
    assert "MARQUEUR_META" in prompt


async def test_prompt_payload_minimal_garde_quatre_champs() -> None:
    prompt = await _synthese_prompt(prompt_payload_minimal=True)
    assert "MARQUEUR_XREF" not in prompt
    assert "MARQUEUR_META" not in prompt
    assert "structured_data" not in prompt
    assert '"source": "https://s.org/a"' in prompt
    assert '"title": "Titre https://s.org/a"' in prompt
    assert '"corroboration_count": 2' in prompt
    assert "Households gained access" in prompt


# --- Synthese : troncature, contenu vide, fidelite numerique --------------------


async def test_synthese_tronquee_est_notee() -> None:
    llm = FakeLLM()

    def _truncated(_call: Any) -> str:
        llm._record_finish_reason("length")
        return "Texte coupe en pleine phr"

    llm.on_text(SYNTH, _truncated)
    orch, ctx = _orch(llm), _ctx()
    await synthesize_single_section(orch.services(), 
        QUERY, SECTION, SECTION_CONFIG, [_item("https://s.org/a")], ctx
    )

    events = _events(ctx, "selection.synthese_tronquee")
    assert len(events) == 1
    assert events[0]["details"]["section"] == SECTION
    # une seule source : objectif ramene a 250 mots par la synthese
    assert events[0]["details"]["max_tokens"] == int(250 * 4.5)


async def test_synthese_non_tronquee_ne_note_rien() -> None:
    llm = FakeLLM()
    llm.on_text(SYNTH, "Texte complet. " * 20)
    orch, ctx = _orch(llm), _ctx()
    await synthesize_single_section(orch.services(), 
        QUERY, SECTION, SECTION_CONFIG, [_item("https://s.org/a")], ctx
    )
    assert ctx.diagnostics.count("selection.synthese_tronquee") == 0


async def test_contenu_vide_est_un_essai_rate_puis_reessaye() -> None:
    llm = FakeLLM()
    llm.on_text(SYNTH, [LLMEmptyContentError("vide"), "Texte redige. " * 20])
    orch, ctx = _orch(llm), _ctx()
    text = await synthesize_single_section(orch.services(), 
        QUERY, SECTION, SECTION_CONFIG, [_item("https://s.org/a")], ctx
    )

    assert text.startswith("Texte redige.")
    assert len(llm.calls_for(kind="generate")) == 2


async def test_contenu_vide_trois_fois_donne_le_marqueur_d_echec() -> None:
    llm = FakeLLM()
    llm.on_text(SYNTH, LLMEmptyContentError("vide"))
    orch, ctx = _orch(llm), _ctx()
    text = await synthesize_single_section(orch.services(), 
        QUERY, SECTION, SECTION_CONFIG, [_item("https://s.org/a")], ctx
    )
    assert text.startswith("[SYNTHESE_ECHOUEE]")
    assert len(llm.calls_for(kind="generate")) == 3


async def test_chiffres_absents_notes_sans_changer_le_texte() -> None:
    llm = FakeLLM()
    written = "En 2020, la proportion atteignait 28,7 percent [SOURCE:https://s.org/a]. " * 3
    llm.on_text(SYNTH, written)
    orch, ctx = _orch(llm), _ctx()
    text = await synthesize_single_section(orch.services(), 
        QUERY, SECTION, SECTION_CONFIG, [_item("https://s.org/a")], ctx
    )

    assert text == written.strip()
    events = _events(ctx, "selection.chiffres_absents")
    assert len(events) == 1
    assert events[0]["details"]["section"] == SECTION
    assert events[0]["details"]["nombre"] >= 1


# --- Repli sous le seuil visible dans la reponse (citecheck, 12/11/2026) -------


async def test_repli_sous_le_seuil_note_avec_la_section() -> None:
    orch, ctx = _orch(), _ctx()
    selected = await semantic_chunk_selection(
        orch.services(), [_item("https://s.org/a")], QUERY, min_score_threshold=1000.0,
        context=ctx, section="Recul du trait de cote",
    )
    assert len(selected) == 1
    events = _events(ctx, "selection.repli_sous_seuil")
    assert len(events) == 1
    assert events[0]["details"]["section"] == "Recul du trait de cote"
    assert events[0]["details"]["seuil"] == 1000.0



async def test_repli_desactive_section_sans_passage() -> None:
    orch, ctx = _orch(), _ctx(selection_fallback_enabled=False)
    selected = await semantic_chunk_selection(
        orch.services(), [_item("https://s.org/a")], QUERY, min_score_threshold=1000.0,
        context=ctx, section="Glossaire",
    )
    assert selected == []
    events = _events(ctx, "selection.repli_desactive")
    assert len(events) == 1 and events[0]["details"]["section"] == "Glossaire"
    assert _events(ctx, "selection.repli_sous_seuil") == []


async def test_repli_desactive_la_section_n_est_pas_redigee() -> None:
    llm = FakeLLM()
    orch = _orch(llm)
    ctx = ExecutionContext(QUERY, config=PipelineConfig(selection_fallback_enabled=False,
                                                        selection_min_score_moderate=1000.0))
    out = await synthesize_single_section(
        orch.services(), QUERY, SECTION, SECTION_CONFIG, [_item("https://s.org/a")], ctx
    )
    assert out.startswith("[SECTION_SANS_SOURCE]")
    assert llm.calls_for(kind="generate") == []
