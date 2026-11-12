"""Les defauts LLM de .env.example et de docker-compose.yml suivent config.py.

config.py est la source unique ; compose et .env.example ne peuvent pas
l'importer, ce test empeche leurs copies de deriver.
"""

import re
from pathlib import Path

from app.core.config import Settings

RACINE = Path(__file__).resolve().parents[3]

CHAMPS = {
    "DEFAULT_LLM_PROVIDER": "default_llm_provider",
    "DEFAULT_LLM_MODEL": "default_llm_model",
    "DEFAULT_LLM_BASE_URL": "default_llm_base_url",
}


def _defauts() -> Settings:
    return Settings(_env_file=None)


def test_defauts_documentes_dans_env_example() -> None:
    texte = (RACINE / ".env.example").read_text(encoding="utf-8")
    s = _defauts()
    for variable, champ in CHAMPS.items():
        m = re.search(rf"^#\s*{variable}=(\S+)\s*$", texte, re.MULTILINE)
        assert m, f"{variable} non documente dans .env.example"
        assert m.group(1) == getattr(s, champ)


def test_defauts_de_docker_compose() -> None:
    texte = (RACINE / "docker-compose.yml").read_text(encoding="utf-8")
    s = _defauts()
    for variable, champ in CHAMPS.items():
        m = re.search(rf"{variable}=\$\{{{variable}:-([^}}]+)\}}", texte)
        assert m, f"{variable} sans defaut dans docker-compose.yml"
        assert m.group(1) == getattr(s, champ)


def test_compose_epingle_searxng_par_condensat() -> None:
    texte = (RACINE / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r"image:\s*searxng/searxng:\S*@sha256:[0-9a-f]{64}", texte)
    assert not re.search(r"image:\s*searxng/searxng(:latest)?\s*$", texte, re.MULTILINE)


def test_start_all_sans_chemin_en_dur_ni_defaut_llm() -> None:
    texte = (RACINE / "start_all.sh").read_text(encoding="utf-8")
    assert "/home/onyxia" not in texte
    assert "DEFAULT_LLM_MODEL" not in texte
    assert "SEARXNG_SETTINGS_PATH" in texte
    assert "WEBTOOLS_API_PYTHON" in texte and "WEBTOOLS_MCP_PYTHON" in texte
