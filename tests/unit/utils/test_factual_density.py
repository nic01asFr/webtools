"""Tests de app.utils.factual_density."""

from app.utils.factual_density import factual_density

PROSE = (
    "Cette introduction presente le sujet en termes generaux. Le theme est vaste "
    "et concerne de nombreux acteurs du territoire dans leur quotidien."
)
DONNEES = (
    "En 2021, la part des 65 ans et plus atteint 28,9 % contre 26,1 % en 2015. "
    "La population compte 36 200 000 habitants, soit 12 km2 par commune en moyenne."
)


def test_vide_et_court() -> None:
    attendu = {"score": 0.0, "markers": {}, "density_per_1k": 0.0}
    assert factual_density("") == attendu
    assert factual_density(None) == attendu  # type: ignore[arg-type]
    assert factual_density("12 %") == attendu


def test_prose_sans_donnees() -> None:
    res = factual_density(PROSE)
    assert res["score"] == 0.0
    assert res["markers"] == {}


def test_donnees_denses() -> None:
    res = factual_density(DONNEES)
    assert res["score"] > 0.5
    assert {"percent", "year", "large_number"} <= set(res["markers"])


def test_bonus_annee_et_chiffre() -> None:
    avec = "x " * 400 + "En 2021, 3 % de hausse observee sur la zone etudiee ici."
    sans = "x " * 400 + "Une hausse de 3 % observee sur la zone etudiee ici ok ok."
    assert factual_density(avec)["score"] > factual_density(sans)["score"]


def test_saturation_a_un() -> None:
    table = " ".join(f"{1000 + i} 12,5 % 2020" for i in range(200))
    assert factual_density(table)["score"] == 1.0


def test_score_borne() -> None:
    for t in (PROSE, DONNEES, "1 " * 200):
        assert 0.0 <= factual_density(t)["score"] <= 1.0


def test_hedging_sans_donnees_vaut_zero() -> None:
    texte = (
        "Generalement, souvent et parfois, certains acteurs reagissent de facon "
        "variable selon le contexte, sans qu'une regle claire ne se degage nettement."
    )
    assert factual_density(texte)["score"] == 0.0


def test_comparatif_quantifie_et_argent() -> None:
    res = factual_density(
        "Les ventes ont augmente de 12 points pour atteindre $1.5 trillion, "
        "alors que les couts ont diminue de 3 points, soit 45 M€ au total ici."
    )
    assert "comparative" in res["markers"]
    assert "money" in res["markers"]


def test_texte_non_latin() -> None:
    texte = "这是一个很长的中文句子，没有任何数字。" * 20
    assert factual_density(texte)["score"] == 0.0


def test_texte_de_100000_caracteres() -> None:
    texte = (DONNEES + " ") * 700
    assert len(texte) > 100_000
    res = factual_density(texte)
    assert 0.0 < res["score"] <= 1.0
