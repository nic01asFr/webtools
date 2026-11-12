"""Coherence des fichiers de deploiement (lot R1) : verrous, image, compose."""

import re
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parents[2]

# Paquets propres a Windows : leur presence dans un verrou compile pour Linux
# fait echouer `pip install -r` dans l'image.
PAQUETS_WINDOWS = ("pywin32", "pywin32-ctypes", "colorama", "win32-setctime")


def _paquets(verrou: str) -> dict[str, str]:
    texte = (RACINE / verrou).read_text(encoding="utf-8")
    return {
        m.group(1).lower(): m.group(2)
        for m in re.finditer(r"^([A-Za-z0-9_.\-]+)==(\S+)", texte, re.MULTILINE)
    }


def test_verrous_sans_paquet_windows() -> None:
    for verrou in ("requirements.lock", "requirements-mcp.lock"):
        presents = [p for p in PAQUETS_WINDOWS if p in _paquets(verrou)]
        assert not presents, f"{verrou} contient des paquets Windows : {presents}"


def test_verrous_sans_chemin_local() -> None:
    for verrou in ("requirements.lock", "requirements-mcp.lock"):
        texte = (RACINE / verrou).read_text(encoding="utf-8")
        assert not re.search(r"[A-Za-z]:[\/]|/Users/|/home/", texte), verrou


def test_dotenv_declare_et_verrouille() -> None:
    assert re.search(
        r'"python-dotenv[>=<~]', (RACINE / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert "python-dotenv" in _paquets("requirements.lock")


def test_dockerfile_recoit_le_commit() -> None:
    texte = (RACINE / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^ARG WEBTOOLS_COMMIT", texte, re.MULTILINE)
    assert re.search(r"^ENV WEBTOOLS_COMMIT=\$\{WEBTOOLS_COMMIT\}", texte, re.MULTILINE)


def _compose() -> dict:
    return yaml.safe_load((RACINE / "docker-compose.yml").read_text(encoding="utf-8"))


def test_compose_passe_le_commit_au_build() -> None:
    args = _compose()["services"]["webtools"]["build"]["args"]
    assert "WEBTOOLS_COMMIT" in args


def test_service_mcp_a_son_propre_healthcheck_sur_8090() -> None:
    sante = _compose()["services"]["webtools-mcp"].get("healthcheck")
    assert sante, "webtools-mcp herite du HEALTHCHECK de l'API (port 8000)"
    commande = " ".join(sante["test"])
    assert ":8090/" in commande and ":8000" not in commande


def test_searxng_lit_contact_url_dans_l_environnement() -> None:
    # settings.yml ne doit pas fixer general.contact_url : SearXNG lit alors
    # SEARXNG_CONTACT_URL (verifie sur l'image epinglee).
    reglages = yaml.safe_load(
        (RACINE / "docker" / "searxng" / "settings.yml").read_text(encoding="utf-8")
    )
    assert "contact_url" not in (reglages.get("general") or {})
    assert "SEARXNG_CONTACT_URL" in (RACINE / "docker-compose.yml").read_text(encoding="utf-8")
