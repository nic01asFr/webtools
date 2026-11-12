"""
Identite du code charge : commit git, arbre modifie, heure de demarrage.

Les valeurs sont calculees une seule fois, a l'import : elles decrivent le
processus en cours, pas l'etat du disque a l'instant de la requete.
"""

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

UNKNOWN_COMMIT = "unknown"

_RACINE = Path(__file__).resolve().parents[2]


def _git(*args: str) -> str | None:
    """Execute une commande git dans le depot ; None si elle echoue."""
    try:
        resultat = subprocess.run(
            ["git", *args],
            cwd=_RACINE,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return resultat.stdout.strip()


def read_build_info() -> tuple[str, bool]:
    """
    Retourne (commit, arbre_modifie).

    Ordre : commande git, puis variable WEBTOOLS_COMMIT (image sans depot
    git), sinon "unknown". L'etat de l'arbre n'est connu que via git.
    """
    commit = _git("rev-parse", "HEAD")
    if commit:
        # Meme critere que benchmarks/generate.py (repo_state) : les fichiers
        # non suivis ne modifient pas le code charge.
        statut = _git("status", "--porcelain", "--untracked-files=no")
        return commit, bool(statut)
    return os.environ.get("WEBTOOLS_COMMIT", "").strip() or UNKNOWN_COMMIT, False


COMMIT, COMMIT_DIRTY = read_build_info()
STARTED_AT: str = datetime.now(UTC).isoformat()
