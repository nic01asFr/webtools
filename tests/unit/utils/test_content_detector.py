"""Tests de app.utils.content_detector (constat C80)."""

import pytest
from app.utils.content_detector import ContentDetector


@pytest.mark.parametrize(
    "url, attendu",
    [
        ("https://github.com/user/repo", "repository"),
        ("https://gitlab.com/a/b", "repository"),
        ("https://docs.python.org/3/", "documentation"),
        ("https://developer.mozilla.org/fr/", "documentation"),
        ("https://example.com/docs/guide/intro", "documentation"),
        ("https://example.com/api/reference", "documentation"),
        ("https://www.amazon.fr/dp/123", "product"),
        ("https://boutique.shop.example/x", "product"),
        ("https://medium.com/@a/post", "article"),
        ("https://news.example.org/a", "article"),
        ("https://exemple.org/page", "webpage"),
    ],
)
def test_types_attendus(url: str, attendu: str) -> None:
    assert ContentDetector.detect(url) == attendu


@pytest.mark.parametrize(
    "url",
    [
        "https://www.capital.fr/economie",
        "https://www.rapide.fr/a",
        "https://www.workshop-paris.fr/",
        "https://www.compost-info.fr/",
    ],
)
def test_pas_de_faux_positif_par_sous_chaine(url: str) -> None:
    assert ContentDetector.detect(url) == "webpage"


def test_entrees_degenerees() -> None:
    assert ContentDetector.detect("") == "webpage"
    assert ContentDetector.detect("pas une url") == "webpage"


def test_url_non_latine() -> None:
    assert ContentDetector.detect("https://例え.jp/ページ") == "webpage"


def test_url_de_100000_caracteres() -> None:
    assert ContentDetector.detect("https://exemple.org/" + "a" * 100_000) == "webpage"
