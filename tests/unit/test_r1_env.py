"""Chargement de .env dans os.environ au demarrage."""

import os
from contextlib import asynccontextmanager

import pytest
from app import main


def test_load_environment_charge_sans_ecraser(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text(
        "R1_NOUVELLE=depuis_env\nR1_EXISTANTE=depuis_env\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("R1_NOUVELLE", raising=False)
    monkeypatch.setenv("R1_EXISTANTE", "explicite")
    try:
        assert main.load_environment() is True
        assert os.environ["R1_NOUVELLE"] == "depuis_env"
        assert os.environ["R1_EXISTANTE"] == "explicite"
    finally:
        os.environ.pop("R1_NOUVELLE", None)


def test_load_environment_sans_fichier(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, "find_dotenv", lambda usecwd=True: "")
    assert main.load_environment() is False


async def test_la_cle_de_l_api_vient_du_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Sans chargement, verify_auth_configuration refuse de demarrer.
    (tmp_path / ".env").write_text("WEBTOOLS_API_KEY=cle-du-env\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WEBTOOLS_API_KEY", raising=False)
    monkeypatch.delenv("WEBTOOLS_ALLOW_ANONYMOUS", raising=False)

    @asynccontextmanager
    async def lifespan_vide(application):
        yield None

    monkeypatch.setattr(main, "_lifespan_applicatif", lifespan_vide)
    try:
        async with main._lifespan_avec_controle_auth(main.app):
            assert os.environ["WEBTOOLS_API_KEY"] == "cle-du-env"
    finally:
        os.environ.pop("WEBTOOLS_API_KEY", None)
