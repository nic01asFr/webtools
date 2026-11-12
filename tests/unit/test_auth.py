"""Authentification par cle d'API et refus de demarrer sans cle (D6)."""

import importlib
from contextlib import asynccontextmanager

import pytest
from app.api.auth import (
    AuthConfigurationError,
    require_api_key,
    verify_auth_configuration,
)
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("WEBTOOLS_API_KEY", raising=False)
    monkeypatch.delenv("WEBTOOLS_ALLOW_ANONYMOUS", raising=False)

    app = FastAPI(dependencies=[Depends(require_api_key)])

    @app.get("/secret")
    async def secret() -> dict:
        return {"ok": True}

    return TestClient(app)


def test_cle_correcte_acceptee(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBTOOLS_API_KEY", "abc")
    assert client.get("/secret", headers={"X-API-Key": "abc"}).status_code == 200


def test_cle_fausse_refusee(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBTOOLS_API_KEY", "abc")
    assert client.get("/secret", headers={"X-API-Key": "xyz"}).status_code == 401


def test_cle_absente_de_la_requete_refusee(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBTOOLS_API_KEY", "abc")
    assert client.get("/secret").status_code == 401


def test_en_tete_non_ascii_donne_401_et_non_500(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBTOOLS_API_KEY", "abc")
    reponse = client.get("/secret", headers={"X-API-Key": "clé".encode("latin-1")})
    assert reponse.status_code == 401


def test_sans_cle_configuree_requete_refusee(client: TestClient) -> None:
    assert client.get("/secret").status_code == 401


def test_mode_anonyme_explicite_accepte(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBTOOLS_ALLOW_ANONYMOUS", "1")
    assert client.get("/secret").status_code == 200


def test_verification_au_demarrage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEBTOOLS_API_KEY", raising=False)
    monkeypatch.delenv("WEBTOOLS_ALLOW_ANONYMOUS", raising=False)
    with pytest.raises(AuthConfigurationError):
        verify_auth_configuration()

    monkeypatch.setenv("WEBTOOLS_ALLOW_ANONYMOUS", "1")
    verify_auth_configuration()

    monkeypatch.delenv("WEBTOOLS_ALLOW_ANONYMOUS")
    monkeypatch.setenv("WEBTOOLS_API_KEY", "abc")
    verify_auth_configuration()


def test_import_de_l_application_sans_cle_ne_leve_pas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEBTOOLS_API_KEY", raising=False)
    monkeypatch.delenv("WEBTOOLS_ALLOW_ANONYMOUS", raising=False)
    import app.main

    importlib.reload(app.main)  # l'import seul ne doit pas refuser


@pytest.mark.asyncio
async def test_lifespan_de_l_application_refuse_sans_cle(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import main

    @asynccontextmanager
    async def factice(_application: FastAPI):
        yield None

    monkeypatch.setattr(main, "_lifespan_applicatif", factice)
    monkeypatch.delenv("WEBTOOLS_API_KEY", raising=False)
    monkeypatch.delenv("WEBTOOLS_ALLOW_ANONYMOUS", raising=False)
    with pytest.raises(AuthConfigurationError):
        async with main.app.router.lifespan_context(main.app):
            pass

    monkeypatch.setenv("WEBTOOLS_API_KEY", "abc")
    async with main.app.router.lifespan_context(main.app):
        pass
