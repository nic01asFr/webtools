"""Etat OAuth du serveur MCP : il survit a une relance du processus.

Observe en production : chaque redemarrage du serveur MCP deconnectait le
connecteur claude.ai. Ces tests rejouent le flux reel du fournisseur
(enregistrement, autorisation, echange du code) puis rechargent l'etat dans
une nouvelle instance.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("fastmcp")

os.environ.setdefault("MCP_ACCESS_TOKEN", "jeton-de-test")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server.auth.provider import AuthorizationParams  # noqa: E402
from mcp.shared.auth import OAuthClientInformationFull  # noqa: E402

import mcp_server  # noqa: E402

REDIRECT = "https://claude.ai/api/mcp/auth_callback"


def _provider(path: Path) -> mcp_server.PersistentOAuthProvider:
    return mcp_server.PersistentOAuthProvider(
        base_url="https://mcp.example.org",
        client_registration_options=mcp_server.ClientRegistrationOptions(enabled=True),
        state_path=str(path),
    )


async def _connect(provider: mcp_server.PersistentOAuthProvider) -> tuple[str, str]:
    client = OAuthClientInformationFull(
        client_id="client-test",
        redirect_uris=[REDIRECT],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )
    await provider.register_client(client)
    redirect = await provider.authorize(
        client,
        AuthorizationParams(
            state="s",
            scopes=[],
            code_challenge="x" * 43,
            redirect_uri=REDIRECT,
            redirect_uri_provided_explicitly=True,
        ),
    )
    code = parse_qs(urlparse(redirect).query)["code"][0]
    auth_code = await provider.load_authorization_code(client, code)
    token = await provider.exchange_authorization_code(client, auth_code)
    return token.access_token, token.refresh_token


async def test_client_et_jetons_survivent_a_une_relance(tmp_path: Path) -> None:
    path = tmp_path / "oauth_state.json"
    access, refresh = await _connect(_provider(path))

    relance = _provider(path)
    assert await relance.get_client("client-test") is not None
    assert await relance.load_access_token(access) is not None
    client = await relance.get_client("client-test")
    assert await relance.load_refresh_token(client, refresh) is not None


async def test_fichier_reserve_au_proprietaire(tmp_path: Path) -> None:
    path = tmp_path / "oauth_state.json"
    await _connect(_provider(path))
    assert path.exists()
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


async def test_revocation_persistee(tmp_path: Path) -> None:
    path = tmp_path / "oauth_state.json"
    provider = _provider(path)
    access, _refresh = await _connect(provider)
    token = await provider.load_access_token(access)
    await provider.revoke_token(token)
    assert await _provider(path).load_access_token(access) is None


def test_etat_illisible_n_empeche_pas_le_demarrage(tmp_path: Path) -> None:
    path = tmp_path / "oauth_state.json"
    path.write_text("{pas du json", encoding="utf-8")
    provider = _provider(path)
    assert provider.clients == {}
