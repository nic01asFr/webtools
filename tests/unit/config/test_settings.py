"""Valeurs par defaut et surcharge par l'environnement de Settings."""

import pytest
from app.core import config
from app.core.config import Settings

CLES_ENV = (
    "DEFAULT_LLM_PROVIDER",
    "DEFAULT_LLM_MODEL",
    "DEFAULT_LLM_BASE_URL",
    "SEARXNG_BASE_URL",
)


@pytest.fixture(autouse=True)
def _env_propre(monkeypatch: pytest.MonkeyPatch) -> None:
    for cle in CLES_ENV:
        monkeypatch.delenv(cle, raising=False)


def _settings() -> Settings:
    # _env_file=None : un .env local ne doit pas fausser le test des defauts
    return Settings(_env_file=None)


def test_defauts_llm_uniques() -> None:
    s = _settings()
    assert s.default_llm_provider == "openai"
    assert s.default_llm_model == "qwen3-6-35b-moe"
    assert s.default_llm_base_url == "https://llm.lab.sspcloud.fr/api"


def test_defaut_searxng_pour_docker() -> None:
    assert _settings().searxng_base_url == "http://searxng:8080"


def test_surcharge_par_environnement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_LLM_MODEL", "autre-modele")
    monkeypatch.setenv("DEFAULT_LLM_BASE_URL", "http://llm.local/v1")
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://localhost:8081")
    s = _settings()
    assert s.default_llm_model == "autre-modele"
    assert s.default_llm_base_url == "http://llm.local/v1"
    assert s.searxng_base_url == "http://localhost:8081"


def test_accesseur_searxng_retire_la_barre_finale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config, "settings", Settings(searxng_base_url="http://x:8080/", _env_file=None)
    )
    assert config.get_searxng_base_url() == "http://x:8080"
