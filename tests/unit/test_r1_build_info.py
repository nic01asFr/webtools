"""build_info et le harnais comptent l'arbre modifie de la meme facon."""

import pytest
from app.core import build_info


def test_les_fichiers_non_suivis_ne_comptent_pas(monkeypatch: pytest.MonkeyPatch) -> None:
    appels: list[tuple[str, ...]] = []

    def faux_git(*args: str) -> str:
        appels.append(args)
        return "deadbeef" if args[0] == "rev-parse" else ""

    monkeypatch.setattr(build_info, "_git", faux_git)
    build_info.read_build_info()
    statut = next(a for a in appels if a[0] == "status")
    assert "--untracked-files=no" in statut


def test_meme_critere_que_le_harnais(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from benchmarks.generate import repo_state

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "suivi.txt").write_text("a", encoding="utf-8")
    git("add", "suivi.txt")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    (tmp_path / "non_suivi.txt").write_text("b", encoding="utf-8")

    monkeypatch.setattr(build_info, "_RACINE", tmp_path)
    assert build_info.read_build_info()[1] is False
    assert repo_state(tmp_path)[1] is False
