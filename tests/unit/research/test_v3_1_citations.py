"""Citations et bibliographie (lot V3-1) : fonctions pures de app.research.citations."""

import copy
from typing import Any

import pytest
from app.agents.intelligent_orchestrator import IntelligentOrchestrator
from app.research.citations import (
    closest_source,
    construire_bibliographie,
    convert_text,
    find_citations,
    split_trailing_punctuation,
    url_key,
)
from app.research.diagnostics import Diagnostics
from app.research.invariants import check_report
from app.research.pipeline_config import PipelineConfig

DATE = "2026-01-01"


def _report(*contents: str, data: list[list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    sections = []
    for i, content in enumerate(contents):
        sections.append({"title": f"S{i}", "content": content, "data": data[i] if data else []})
    return {"title": "T", "summary": "", "sections": sections}


def _build(report: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return construire_bibliographie(report, accessed=DATE, **kwargs)


# --- Motif, ponctuation, normalisation ---------------------------------------


def test_find_citations_ferme_et_non_ferme() -> None:
    text = "a [SOURCE:https://x.org/a] b [SOURCE: https://y.org/z. fin"
    assert find_citations(text) == ["https://x.org/a", "https://y.org/z"]


@pytest.mark.parametrize(
    ("raw", "attendu"),
    [
        ("https://y.org/z.", ("https://y.org/z", ".")),
        ("https://y.org/z,", ("https://y.org/z", ",")),
        ("https://y.org/z.)", ("https://y.org/z", ".)")),
        ("https://fr.wikipedia.org/wiki/A_(b)", ("https://fr.wikipedia.org/wiki/A_(b)", "")),
        ("https://fr.wikipedia.org/wiki/A_(b).", ("https://fr.wikipedia.org/wiki/A_(b)", ".")),
    ],
)
def test_split_trailing_punctuation(raw: str, attendu: tuple[str, str]) -> None:
    assert split_trailing_punctuation(raw) == attendu


def test_url_key_normalise() -> None:
    assert url_key("HTTPS://X.org/a/#frag") == url_key("https://x.org/a")
    assert url_key("https://x.org/a?p=1") != url_key("https://x.org/a")


def test_convert_text_une_passe_prefixe_commun() -> None:
    numbers = {url_key("https://x.org/a"): 1, url_key("https://x.org/a/b"): 2}
    text = "un [SOURCE:https://x.org/a] deux [SOURCE:https://x.org/a/b]."
    assert convert_text(text, numbers) == "un [1] deux [2]."


def test_convert_text_laisse_une_citation_inconnue() -> None:
    text = "x [SOURCE:https://z.org/q]"
    assert convert_text(text, {}) == text


def test_convert_text_retire_une_citation_none() -> None:
    numbers = {url_key("https://z.org/q"): None}
    assert convert_text("Fait [SOURCE:https://z.org/q].", numbers) == "Fait."


# --- Reproductions C44 et C45 ------------------------------------------------


def test_c44_prefixe_commun() -> None:
    report = _report("A [SOURCE:https://x.org/a] puis B [SOURCE:https://x.org/a/b] fin.")
    out = _build(report)
    assert out["sections"][0]["content"] == "A [1] puis B [2] fin."
    assert [b["url"] for b in out["bibliography"]] == ["https://x.org/a", "https://x.org/a/b"]
    assert check_report(out)["violations"] == []


def test_c44_prefixe_commun_ordre_inverse() -> None:
    report = _report("B [SOURCE:https://x.org/a/b] puis A [SOURCE:https://x.org/a].")
    out = _build(report)
    assert out["sections"][0]["content"] == "B [1] puis A [2]."


def test_c45_ponctuation_finale_marqueur_non_ferme() -> None:
    report = _report("Voir [SOURCE:https://y.org/z.")
    out = _build(report)
    assert [b["url"] for b in out["bibliography"]] == ["https://y.org/z"]
    assert out["sections"][0]["content"] == "Voir [1]."


def test_resume_converti_et_source_commune_aux_sections() -> None:
    report = _report("a [SOURCE:https://x.org/a]", "b [SOURCE:https://x.org/a]")
    report["summary"] = "r [SOURCE:https://x.org/a]"
    out = _build(report)
    assert len(out["bibliography"]) == 1
    assert out["summary"] == "r [1]"
    assert out["sections"][1]["content"] == "b [1]"


# --- Idempotence --------------------------------------------------------------


def test_idempotence() -> None:
    report = _report(
        "A [SOURCE:https://x.org/a] B [SOURCE:https://x.org/a/b]",
        data=[[{"source": "https://d.org/p", "title": "Page D"}]],
    )
    once = _build(report)
    snapshot = copy.deepcopy(once)
    twice = _build(once)
    assert twice == snapshot


def test_second_passage_complete_sans_renumeroter() -> None:
    report = _report("A [SOURCE:https://x.org/a]")
    _build(report)
    report["sections"][0]["content"] += " C [SOURCE:https://n.org/new] et [1]"
    out = _build(report)
    assert out["sections"][0]["content"] == "A [1] C [2] et [1]"
    assert [b["id"] for b in out["bibliography"]] == [1, 2]


# --- Drapeaux (deux positions chacun) ---------------------------------------


def test_bibliography_cited_only_defaut_garde_les_sources_non_citees() -> None:
    data = [[{"source": "https://d.org/p", "title": "D"}]]
    out = _build(_report("A [SOURCE:https://x.org/a]", data=data))
    assert [b["url"] for b in out["bibliography"]] == ["https://x.org/a", "https://d.org/p"]


def test_bibliography_cited_only_actif() -> None:
    data = [[{"source": "https://d.org/p", "title": "D"}]]
    out = _build(_report("A [SOURCE:https://x.org/a]", data=data), cited_only=True)
    assert [b["url"] for b in out["bibliography"]] == ["https://x.org/a"]


def test_titres_par_defaut() -> None:
    data = [[{"source": "https://d.org/p", "title": "Page D"}]]
    out = _build(_report("A [SOURCE:https://d.org/p]", data=data))
    assert out["bibliography"][0]["title"] == "Source 1 - d.org"


def test_bibliography_real_titles_actif_et_repli() -> None:
    data = [[{"source": "https://d.org/p", "title": "Page D"}, {"source": "https://e.org/q"}]]
    report = _report("A [SOURCE:https://d.org/p] B [SOURCE:https://e.org/q]", data=data)
    out = _build(report, real_titles=True)
    assert [b["title"] for b in out["bibliography"]] == ["Page D", "Source 2 - e.org"]


def test_validate_cited_urls_desactive_garde_l_url_inventee() -> None:
    data = [[{"source": "https://d.org/p", "title": "D"}]]
    out = _build(_report("A [SOURCE:https://inconnu.org/x]", data=data))
    assert out["bibliography"][0]["url"] == "https://inconnu.org/x"


def test_validate_cited_urls_retire_et_note() -> None:
    diag = Diagnostics()
    data = [[{"source": "https://d.org/p", "title": "D"}]]
    out = _build(
        _report("A [SOURCE:https://inconnu.org/x] fin.", data=data),
        validate_cited_urls=True,
        diagnostics=diag,
    )
    assert out["sections"][0]["content"] == "A fin."
    assert "https://inconnu.org/x" not in [b["url"] for b in out["bibliography"]]
    assert diag.count("citations.citation_retiree") == 1


def test_validate_cited_urls_rapproche_et_note() -> None:
    diag = Diagnostics()
    data = [[{"source": "https://d.org/rapport", "title": "D"}]]
    out = _build(
        _report("A [SOURCE:https://d.org/rapport/annexe-3] fin.", data=data),
        validate_cited_urls=True,
        diagnostics=diag,
    )
    assert out["sections"][0]["content"] == "A [1] fin."
    assert out["bibliography"][0]["url"] == "https://d.org/rapport"
    assert diag.count("citations.url_rapprochee") == 1


def test_validate_cited_urls_section_sans_passage_utilise_les_sources_du_rapport() -> None:
    data = [[{"source": "https://d.org/p", "title": "D"}], []]
    out = _build(
        _report("A [SOURCE:https://d.org/p]", "B [SOURCE:https://d.org/p]", data=data),
        validate_cited_urls=True,
    )
    assert out["sections"][1]["content"] == "B [1]"


def test_validate_cited_urls_idempotent() -> None:
    data = [[{"source": "https://d.org/p", "title": "D"}]]
    once = _build(_report("A [SOURCE:https://d.org/p]", data=data), validate_cited_urls=True)
    snapshot = copy.deepcopy(once)
    assert _build(once, validate_cited_urls=True) == snapshot


# --- closest_source -------------------------------------------------------------


def test_closest_source_meme_domaine_et_prefixe() -> None:
    allowed = ["https://d.org/a", "https://d.org/a/b", "https://autre.org/a/b/c"]
    assert closest_source("https://www.d.org/a/b/c/d", allowed) == "https://d.org/a/b"
    assert closest_source("https://d.org/a", allowed) == "https://d.org/a"


def test_closest_source_aucune() -> None:
    assert closest_source("https://d.org/z", ["https://d.org/a"]) is None
    assert closest_source("https://d.org/", ["https://d.org/a"]) is None


# --- Methode de l'orchestrateur ------------------------------------------------


def _orchestrator(config: PipelineConfig) -> IntelligentOrchestrator:
    return IntelligentOrchestrator(
        object(),  # type: ignore[arg-type]
        search_client=object(),
        extractor_manager=object(),
        config=config,
    )


def test_methode_orchestrateur_delegue_et_applique_les_drapeaux() -> None:
    report = _report(
        "A [SOURCE:https://x.org/a] B [SOURCE:https://x.org/a/b]",
        data=[[{"source": "https://d.org/p", "title": "Page D"}]],
    )
    out = _orchestrator(PipelineConfig())._convert_sources_to_bibliography(copy.deepcopy(report))
    assert out["sections"][0]["content"] == "A [1] B [2]"
    assert len(out["bibliography"]) == 3

    config = PipelineConfig(bibliography_cited_only=True)
    out = _orchestrator(config)._convert_sources_to_bibliography(copy.deepcopy(report))
    assert len(out["bibliography"]) == 2


def test_drapeaux_par_defaut_desactives() -> None:
    config = PipelineConfig()
    assert not (
        config.validate_cited_urls
        or config.bibliography_cited_only
        or config.bibliography_real_titles
    )
