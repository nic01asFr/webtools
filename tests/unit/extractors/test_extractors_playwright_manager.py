"""Gestionnaire Playwright : dossier des navigateurs, echec memorise."""

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from app.core.browser import playwright_manager as pm


@pytest.fixture(autouse=True)
def _etat_propre() -> Iterator[None]:
    pm.reset_playwright_state()
    yield
    pm.reset_playwright_state()


def test_variable_d_environnement_respectee(tmp_path: Path) -> None:
    assert pm.browsers_path({"PLAYWRIGHT_BROWSERS_PATH": str(tmp_path)}, "win32") == tmp_path


def test_defaut_windows_et_linux() -> None:
    win = pm.browsers_path({"LOCALAPPDATA": "C:/Users/x/AppData/Local"}, "win32")
    assert win == Path("C:/Users/x/AppData/Local") / "ms-playwright"
    assert pm.browsers_path({}, "linux") == Path.home() / ".cache" / "ms-playwright"


async def _lancement_ok() -> None:
    return None


async def _lancement_ko() -> None:
    raise RuntimeError("Executable doesn't exist at .../chromium_headless_shell-1254")


async def test_ne_remplace_pas_la_variable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "chromium-1").mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    monkeypatch.setattr(pm, "_launch_probe", _lancement_ok)
    assert await pm.ensure_playwright_installed() is True
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path)


async def test_dossier_present_mais_navigateur_absent_n_est_pas_disponible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Cas reel : des revisions chromium-* d'une autre version de Playwright
    # sont presentes, celle attendue ne l'est pas, et l'installation echoue.
    (tmp_path / "chromium-1217").mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    monkeypatch.setattr(pm, "_launch_probe", _lancement_ko)
    monkeypatch.setattr(
        pm, "_run_install", lambda: subprocess.CompletedProcess([], 1, stdout="", stderr="echec reseau")
    )
    assert await pm.ensure_playwright_installed() is False
    assert pm.is_playwright_available() is False
    assert pm.playwright_error() == "echec reseau"


async def test_installation_reussie_mais_lancement_impossible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "chromium-1217").mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    monkeypatch.setattr(pm, "_launch_probe", _lancement_ko)
    monkeypatch.setattr(pm, "_run_install", lambda: subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    assert await pm.ensure_playwright_installed() is False
    assert "Executable doesn't exist" in (pm.playwright_error() or "")


async def test_lancement_repare_par_l_installation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "chromium-1217").mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    essais: list[int] = []

    async def lancement_apres_installation() -> None:
        essais.append(1)
        if len(essais) == 1:
            await _lancement_ko()

    monkeypatch.setattr(pm, "_launch_probe", lancement_apres_installation)
    monkeypatch.setattr(pm, "_run_install", lambda: subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    assert await pm.ensure_playwright_installed() is True
    assert len(essais) == 2


async def test_echec_d_installation_memorise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "vide"))
    appels: list[int] = []

    def faux_install() -> "subprocess.CompletedProcess[str]":
        appels.append(1)
        return subprocess.CompletedProcess([], 1, stdout="", stderr="echec")

    monkeypatch.setattr(pm, "_run_install", faux_install)
    assert await pm.ensure_playwright_installed() is False
    assert await pm.ensure_playwright_installed() is False
    assert len(appels) == 1
