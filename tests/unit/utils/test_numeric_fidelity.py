"""Tests de app.utils.numeric_fidelity (constat C72)."""

from app.utils.numeric_fidelity import check_numeric_fidelity, extract_numbers


def test_extract_numbers_ne_coupe_pas_les_entiers_longs() -> None:
    texte = "En 2021, 1500 cas et 36200000 habitants, 45 000 euros"
    assert extract_numbers(texte) == ["2021", "1500", "36200000", "45000"]


def test_extract_numbers_espaces_de_milliers() -> None:
    assert extract_numbers("45 000 euros") == ["45000"]
    assert extract_numbers("45 000 euros") == ["45000"]
    assert extract_numbers("1 234 567 habitants") == ["1234567"]


def test_extract_numbers_annee_suivie_d_un_entier_reste_separee() -> None:
    assert extract_numbers("en 2021 150 cas") == ["2021", "150"]


def test_extract_numbers_virgule_decimale() -> None:
    assert extract_numbers("soit 28,7 % de 65 ans") == ["28.7", "65"]
    assert extract_numbers("ratio de 13,14159 environ") == ["13.14159"]


def test_extract_numbers_virgule_de_milliers() -> None:
    assert extract_numbers("1,500 cases and 12,345,678 people") == ["1500", "12345678"]


def test_extract_numbers_formes_equivalentes() -> None:
    assert extract_numbers("28,70 %") == extract_numbers("28.7%") == ["28.7"]


def test_extract_numbers_ignore_les_petits_nombres() -> None:
    assert extract_numbers("3 chats et 9 chiens") == []
    assert extract_numbers("3 chats", min_value=1.0) == ["3"]


def test_extract_numbers_vide_et_non_latin() -> None:
    assert extract_numbers("") == []
    assert extract_numbers(None) == []  # type: ignore[arg-type]
    assert extract_numbers("これは日本語です") == []
    assert extract_numbers("население 1500 человек") == ["1500"]


def test_extract_numbers_texte_de_100000_caracteres() -> None:
    texte = "valeur 1234 et " * 7000
    assert len(texte) > 100_000
    assert extract_numbers(texte)[:2] == ["1234", "1234"]


def test_derive_numerique_est_detectee() -> None:
    res = check_numeric_fidelity("on compte 1500 cas en 2020", "on compte 1509 cas en 2021")
    assert res["fidelity_rate"] < 1.0
    assert res["unmatched"] == ["1500"]


def test_chiffres_fideles() -> None:
    res = check_numeric_fidelity("on compte 1 500 cas", "on compte 1500 cas")
    assert res["fidelity_rate"] == 1.0
    assert res["unmatched_count"] == 0


def test_formatage_different_meme_valeur() -> None:
    res = check_numeric_fidelity("28,70 % des 65 ans", "28.7% des 65 ans")
    assert res["fidelity_rate"] == 1.0


def test_aucun_nombre_ecrit() -> None:
    res = check_numeric_fidelity("aucune donnee", "1500 cas")
    assert res == {"numbers_written": 0, "unmatched": [], "fidelity_rate": 1.0}


def test_annees_courantes_non_controlees() -> None:
    res = check_numeric_fidelity("en 2020", "en 2021")
    assert res["fidelity_rate"] == 1.0


def test_unmatched_limite_a_dix() -> None:
    ecrit = " ".join(str(1000 + 7 * i) for i in range(15))
    res = check_numeric_fidelity(ecrit, "rien")
    assert len(res["unmatched"]) == 10
    assert res["unmatched_count"] == 15
    assert res["fidelity_rate"] == 0.0


def test_sources_vides() -> None:
    res = check_numeric_fidelity("12345 cas", "")
    assert res["fidelity_rate"] == 0.0
