"""scripts/config_connexion.py : contenu et droits du fichier de connexion."""

from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("config_connexion", ROOT / "scripts" / "config_connexion.py")
config_connexion = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(config_connexion)

ENV = {
    "MCP_PUBLIC_URL": "https://mcp.example.org/",
    "WEBTOOLS_PUBLIC_API": "https://api.example.org",
    "WEBTOOLS_API_KEY": "cle-api",
    "MCP_ACCESS_TOKEN": "jeton-mcp",
}


def test_contenu_donne_url_mcp_et_cles() -> None:
    text = config_connexion.render(ENV)
    assert "`https://mcp.example.org/mcp`" in text
    assert "`jeton-mcp`" in text
    assert "X-API-Key: cle-api" in text
    assert "https://api.example.org/health" in text


def test_valeur_absente_signalee() -> None:
    text = config_connexion.render({})
    assert "(non defini : MCP_PUBLIC_URL)" in text
    assert "(non defini : MCP_ACCESS_TOKEN)" in text


def test_fichier_ecrit_et_reserve_au_proprietaire(tmp_path: Path, monkeypatch) -> None:
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    target = tmp_path / "README-webtools.md"
    assert config_connexion.main(["config_connexion.py", str(target)]) == 0
    assert "jeton-mcp" in target.read_text(encoding="utf-8")
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
