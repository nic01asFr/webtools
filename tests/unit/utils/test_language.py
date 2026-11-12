"""Tests de app.utils.language (constat C80)."""

import pytest
from app.utils.language import detect_language


@pytest.mark.parametrize(
    "texte, attendu",
    [
        ("Quelles sont les principales causes de la hausse des prix dans le monde", "fr"),
        ("What are the main advantages of the Rust programming language for systems", "en"),
        ("Cuales son los principales problemas de la economia en el mundo actual", "es"),
        ("Welche Vorteile hat die Nutzung von Wasserstoff und wie ist der Stand", "de"),
        ("Quali sono i principali vantaggi per le aziende che usano questo metodo", "it"),
        ("Quais sao os principais problemas com o uso deste metodo para as empresas", "pt"),
    ],
)
def test_langues_courantes(texte: str, attendu: str) -> None:
    assert detect_language(texte) == attendu


def test_elisions_francaises() -> None:
    assert detect_language("l'impact de l'IA sur l'emploi") == "fr"
    assert detect_language("Qu'est-ce que l'inflation et d'ou vient-elle") == "fr"


def test_apostrophe_typographique() -> None:
    assert detect_language("l’impact de l’IA sur l’emploi") == "fr"


def test_mots_accentues_allemand_italien_portugais() -> None:
    assert detect_language("Warum ist der Preis für Strom über den Durchschnitt gestiegen") == "de"
    assert detect_language("perché sono questo e quello tra noi") == "it"
    assert detect_language("eles são os que sem nada para dizer") == "pt"


def test_elision_anglaise_non_francaise() -> None:
    assert detect_language("it's what they don't know about the market") == "en"


def test_vide_trop_court_et_indetermine() -> None:
    assert detect_language("") == "all"
    assert detect_language(None) == "all"  # type: ignore[arg-type]
    assert detect_language("Rust language") == "all"
    assert detect_language("xyzzy plugh qwerty zxcvb foo") == "all"


def test_texte_non_latin() -> None:
    assert detect_language("これは日本語の文章であり、言語を判定できません") == "all"
    assert detect_language("Какие основные преимущества имеет этот метод для бизнеса") == "all"


def test_ambiguite_rend_all() -> None:
    assert detect_language("the le the le") == "all"


def test_texte_de_100000_caracteres() -> None:
    texte = "the impact of the market on the economy is large " * 2200
    assert len(texte) > 100_000
    assert detect_language(texte) == "en"
