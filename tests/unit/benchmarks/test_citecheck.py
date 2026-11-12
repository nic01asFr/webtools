"""Outil de mesure des citations : fonctions deterministes (aucun reseau).

Ces tests verifient l'instrument lui-meme : un instrument faux produirait
des chiffres faux sur le service.
"""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from citecheck.juge import SCHEMA, Judge, normalize_verdict, settle
from citecheck.llm import Endpoint, parse_json_object, strip_reasoning
from citecheck.mesures import answer_correct, origin, ratio_ci, summarize
from citecheck.preuves import PassageIndex, select_evidence, split_passages
from citecheck.texte import fold, interpretations, missing_numbers, numbers, quote_found
from citecheck.triplets import parse_deep, parse_quick, parse_refs, pipeline_texts, strip_markers

# --- texte ----------------------------------------------------------------------


def test_fold_unifie_typographie_et_accents() -> None:
    assert fold("L’Union  européenne « interdit »") == "l'union europeenne \" interdit \""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2018", {Decimal(2018)}),
        ("28,7", {Decimal("28.7")}),
        ("28.7", {Decimal("28.7")}),
        ("1,500", {Decimal(1500), Decimal("1.5")}),
        ("299,792,458", {Decimal(299792458)}),
        ("1.234,5", {Decimal("1234.5")}),
        ("1 200 000", {Decimal(1200000)}),
        ("4 807,81", {Decimal("4807.81")}),
    ],
)
def test_interpretations(raw: str, expected: set[Decimal]) -> None:
    assert interpretations(raw) == {v.normalize() for v in expected}


def test_numbers_ignore_petits_entiers_et_applique_multiplicateur() -> None:
    found = dict(numbers("3 pays, 12 sites et 1,2 million de ruches"))
    assert "3" not in found
    assert Decimal(12) in found["12"]
    assert Decimal(1200000) in found["1,2"]


def test_numbers_ne_fusionne_pas_deux_annees() -> None:
    assert [raw for raw, _ in numbers("entre 2013, 2018 et 2020 150 pays")] == ["2013", "2018", "2020", "150"]


@pytest.mark.parametrize(
    ("claim", "page", "missing"),
    [
        ("interdits en exterieur en 2013", "interdiction totale en exterieur en 2018", ["2013"]),
        ("une hausse de 28,7 %", "an increase of 28.7% was observed", []),
        ("1,2 million de ruches", "1 200 000 hives", []),
        ("3 neonicotinoides", "trois substances", []),
    ],
)
def test_missing_numbers(claim: str, page: str, missing: list[str]) -> None:
    assert missing_numbers(claim, page) == missing


def test_quote_found_typographie_et_coupure() -> None:
    page = "L’interdiction s’applique depuis le 19 décembre 2018 à toutes les cultures en plein champ."
    assert quote_found("l'interdiction s'applique depuis le 19 decembre 2018", page)
    # Coupure en fin d'extrait : tolere.
    assert quote_found("L'interdiction s'applique depuis le 19 decembre 2018 a toutes les cultures en serre", page)


def test_quote_found_exposant_decoupe() -> None:
    # Cas reel de l'etalonnage : <sup>14</sup>C devient "14\nC" dans la page.
    page = "14\nC is unstable, with half-life 5700 ± 30 years, decaying into nitrogen-14"
    assert quote_found("14C is unstable, with half-life 5700 ± 30 years", page)


def test_quote_found_refuse_une_invention() -> None:
    page = "L'interdiction s'applique depuis 2018 a toutes les cultures en plein champ."
    assert not quote_found("L'interdiction est entree en vigueur en 2013 pour les serres et vergers", page)
    assert not quote_found("en 2013", page)
    assert not quote_found("", page)


# --- triplets -------------------------------------------------------------------

DEEP = {
    "result": {
        "title": "T",
        "summary": "Resume qui cite une source [1].",
        "sections": [
            {
                "title": "Cadre",
                "content": (
                    "## Sous-titre\n"
                    "La Commission a restreint trois substances en 2013 [1]. Elle les a interdites "
                    "en plein champ en 2018. [2] Une phrase sans citation qui compte six mots.\n\n"
                    "| Substance | Statut |\n|---|---|\n| Imidaclopride | interdite [2, 3] |\n\n"
                    "- Un element de liste avec une source [4]\n"
                    "Un appel en plage [2-3]."
                ),
                "data": [
                    {"source": "https://a.example/1", "content": "texte extrait A"},
                    {"source": "https://b.example/2", "content": "texte extrait B"},
                ],
            },
            {"title": "Vide", "content": "[SECTION_SANS_SOURCE] Aucune donnee."},
            {"title": "Repetee", "content": "Rien.", "data": [
                {"source": "https://a.example/1", "content": "texte extrait A"}]},
        ],
        "bibliography": [
            {"id": 1, "url": "https://a.example/1", "title": "A"},
            {"id": 2, "url": "https://b.example/2", "title": "B"},
            {"id": 3, "url": "https://c.example/3", "title": "C"},
        ],
    }
}


def test_parse_refs() -> None:
    assert parse_refs("1, 3") == [1, 3]
    assert parse_refs("2-4") == [2, 3, 4]
    assert parse_refs("Source 5") == [5]


def test_strip_markers() -> None:
    assert strip_markers("Texte [1] et suite [2, 3].") == "Texte et suite."


def test_parse_deep_triplets() -> None:
    parsed = parse_deep("t1", DEEP)
    pairs = [(c.location.split(" :")[0], c.ref, c.claim) for c in parsed.citations]
    assert ("resume", 1, "Resume qui cite une source.") in pairs
    assert ("section 1", 1, "La Commission a restreint trois substances en 2013.") in pairs
    # Appel place apres le point : rattache a la phrase precedente.
    assert ("section 1", 2, "Elle les a interdites en plein champ en 2018.") in pairs
    # Ligne de tableau : une affirmation par numero, en-tete en contexte.
    table = [c for c in parsed.citations if c.claim.startswith("Imidaclopride")]
    assert {c.ref for c in table} == {2, 3}
    assert table[0].context == "En-tete du tableau : Substance | Statut"
    # Element de liste et plage de numeros.
    assert any(c.ref == 4 and c.claim == "Un element de liste avec une source" for c in parsed.citations)
    assert {c.ref for c in parsed.citations if c.claim == "Un appel en plage."} == {2, 3}
    # Numero 4 absent de la bibliographie : orphelin.
    assert [c.ref for c in parsed.orphans] == [4]
    assert parsed.skipped_sections == 1
    # Contexte : phrase precedente, sans ses appels.
    second = next(c for c in parsed.citations if c.claim.startswith("Elle les"))
    assert second.context == "La Commission a restreint trois substances en 2013."
    assert parsed.units_total > parsed.units_cited


def test_parse_quick_numerotation_des_sources() -> None:
    payload = {
        "answer": "L'interdiction date de 2018 [2]. Les premieres restrictions remontent a 2013 [1][3].",
        "sources": [{"url": "https://a/1", "title": "A"}, {"url": "https://b/2", "title": "B"}],
    }
    parsed = parse_quick("q1", payload)
    assert [(c.ref, c.url) for c in parsed.citations] == [
        (2, "https://b/2"), (1, "https://a/1"), (3, None)
    ]


def test_pipeline_texts() -> None:
    texts = pipeline_texts("deep", DEEP)
    assert texts["https://a.example/1"] == "texte extrait A"
    quick = {"sources": [{"url": "https://a/1", "content": "vu par le modele"}, {"url": "https://b/2"}]}
    assert pipeline_texts("quick", quick) == {"https://a/1": "vu par le modele"}


# --- preuves --------------------------------------------------------------------

LONG_PAGE = "\n".join(
    [f"Paragraphe de remplissage numero {i} sur un autre sujet agricole sans rapport." for i in range(400)]
    + ["Le 27 avril 2018, les Etats membres ont vote l'interdiction en plein champ des trois neonicotinoides."]
    + [f"Autre paragraphe {i} sans rapport avec la question posee ici." for i in range(400)]
)


def test_split_passages_couvre_tout_le_texte() -> None:
    passages = split_passages(LONG_PAGE, size=900)
    assert all(len(p) <= 900 for p in passages)
    assert fold("".join(passages)).count("neonicotinoides") == 1


async def test_page_courte_donnee_entiere() -> None:
    evidence = await select_evidence("x", "page courte")
    assert evidence.method == "page_entiere" and evidence.text == "page courte"


async def test_page_longue_garde_le_passage_pertinent() -> None:
    evidence = await select_evidence(
        "L'interdiction en plein champ des neonicotinoides a ete votee en 2018.", LONG_PAGE, budget=3000
    )
    assert evidence.method == "lexicale"
    assert "27 avril 2018" in evidence.text
    assert len(evidence.text) <= 3000 + 10 * len("\n[...]\n")


async def test_plongements_utilises_et_repli_si_panne() -> None:
    calls: list[int] = []

    async def embed(texts: list[str]) -> list[list[float]]:
        calls.append(len(texts))
        return [[1.0, 0.0] if "neonicotinoides" in t or "Query" in t else [0.0, 1.0] for t in texts]

    index = PassageIndex(LONG_PAGE)
    evidence = await select_evidence("interdiction", LONG_PAGE, index=index, embedder=embed, budget=2000)
    assert evidence.method == "plongements" and "27 avril 2018" in evidence.text
    # Les passages de la page ne sont plonges qu'une fois.
    await select_evidence("autre affirmation", LONG_PAGE, index=index, embedder=embed, budget=2000)
    assert calls.count(len(index.passages)) == 1

    async def broken(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("panne")

    evidence = await select_evidence("neonicotinoides 2018", LONG_PAGE, embedder=broken, budget=2000)
    assert evidence.method == "lexicale"


# --- llm --------------------------------------------------------------------------


def test_parse_json_object_tolerant() -> None:
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('<think>hmm {"faux": 0}</think>```json\n{"a": "}"}\n```') == {"a": "}"}
    assert parse_json_object('Voici : {"a": {"b": 2}} fin') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        parse_json_object("pas de json")
    assert strip_reasoning("a</think> b") == "a b"


# --- juge -----------------------------------------------------------------------

PAGE = "Depuis le 19 decembre 2018, l'usage en plein champ de l'imidaclopride est interdit dans l'UE."


def test_normalize_verdict() -> None:
    assert normalize_verdict("Non soutenue") == "non_soutenue"
    assert normalize_verdict("SOUTENUE") == "soutenue"
    assert normalize_verdict("peut-etre") is None


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        ({"verdict": "soutenue", "extrait": "l'usage en plein champ de l'imidaclopride est interdit"}, "soutenue"),
        ({"verdict": "soutenue", "extrait": "interdit depuis 2013 dans toute l'Union europeenne"}, "non_etaye"),
        ({"verdict": "soutenue", "extrait": ""}, "non_etaye"),
        ({"verdict": "contredite", "extrait": "Depuis le 19 decembre 2018"}, "contredite"),
        ({"verdict": "non_soutenue", "extrait": ""}, "non_soutenue"),
        ({"verdict": "inconnu", "extrait": ""}, "erreur_juge"),
    ],
)
def test_settle(raw: dict, status: str) -> None:
    assert settle(raw, "L'imidaclopride est interdit en plein champ depuis 2013.", PAGE).status == status


def test_settle_signale_les_chiffres_absents() -> None:
    j = settle({"verdict": "contredite", "extrait": "Depuis le 19 decembre 2018"}, "interdit en 2013", PAGE)
    assert j.missing_numbers == ["2013"]


async def test_judge_envoie_schema_et_temperature_nulle() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        content = json.dumps({"extrait": "Depuis le 19 decembre 2018", "analyse": "date differente",
                              "verdict": "contredite"})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    endpoint = Endpoint("https://llm.test/api", "k", transport=httpx.MockTransport(handler), attempts=1)
    judge = Judge(endpoint, "juge-test")
    j = await judge.judge("Interdit depuis 2013.", "", PAGE, "https://a/1")
    await endpoint.close()
    assert j.status == "contredite" and j.evidence_method == "page_entiere"
    assert seen[0]["temperature"] == 0
    assert seen[0]["response_format"]["json_schema"]["schema"] == SCHEMA
    assert "Interdit depuis 2013." in seen[0]["messages"][1]["content"]
    assert PAGE in seen[0]["messages"][1]["content"]


async def test_judge_json_invalide_redemande_puis_erreur() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "je pense que oui"}}]})

    endpoint = Endpoint("https://llm.test/api", "k", transport=httpx.MockTransport(handler), attempts=2)
    j = await Judge(endpoint, "m").judge("a", "", PAGE, "u")
    await endpoint.close()
    assert j.status == "erreur_juge" and calls["n"] == 2


# --- mesures --------------------------------------------------------------------


def _rec(task: str, status: str, **extra) -> dict:
    return {"task_id": task, "status": status, **extra}


def test_ratio_ci_sommes_par_tache_et_deterministe() -> None:
    records = [_rec("a", "soutenue")] * 3 + [_rec("a", "contredite")] + [_rec("b", "soutenue")] * 4
    res = ratio_ci(records, lambda r: r["status"] == "soutenue", lambda r: True)
    assert (res["n"], res["sur"], res["taux"]) == (7, 8, 0.875)
    assert res["ic95"][0] <= 0.875 <= res["ic95"][1]
    assert ratio_ci(records, lambda r: r["status"] == "soutenue", lambda r: True) == res


def test_ratio_ci_une_seule_tache_sans_intervalle() -> None:
    assert ratio_ci([_rec("a", "soutenue")], lambda r: True, lambda r: True)["ic95"] is None


def test_origin() -> None:
    assert origin({"pipeline_status": "soutenue"}) == "extraction"
    assert origin({"pipeline_status": "non_soutenue"}) == "redaction"
    assert origin({"pipeline_status": "contredite"}) == "redaction"
    assert origin({}) == "inconnue"


def test_summarize_denominateurs() -> None:
    records = [
        _rec("a", "soutenue"), _rec("a", "partielle"), _rec("a", "inverifiable"),
        _rec("a", "orpheline"), _rec("b", "contredite", pipeline_status="soutenue"),
        _rec("b", "soutenue", missing_numbers=["12"]),
    ]
    s = summarize(records)
    # Jugees : soutenue, partielle, contredite, soutenue = 4.
    assert (s["exactes"]["n"], s["exactes"]["sur"]) == (2, 4)
    assert (s["exactes_ou_partielles"]["n"], s["exactes_ou_partielles"]["sur"]) == (3, 4)
    # Inverifiables : sur les citations non orphelines (5).
    assert (s["inverifiables"]["n"], s["inverifiables"]["sur"]) == (1, 5)
    assert (s["orphelines"]["n"], s["orphelines"]["sur"]) == (1, 6)
    assert s["soutenues_avec_chiffre_absent"] == 1
    assert s["origine_des_fautes"] == {"inconnue": 1, "extraction": 1}


def test_answer_correct() -> None:
    assert answer_correct("Interdits depuis 2018 [1].", [r"\b2018\b"])
    assert not answer_correct("Interdits depuis 2013 [1].", [r"\b2018\b"])
    assert not answer_correct(None, [r"2018"])
