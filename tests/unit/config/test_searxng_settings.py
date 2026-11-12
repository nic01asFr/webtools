"""Garde-fou : docker/searxng/settings.yml active tous les moteurs que le code attend.

Sans lui, la configuration SearXNG derive du code sans que rien ne le dise
(la configuration reelle du pod a ete perdue une fois).
"""

from pathlib import Path
from typing import Dict, Set

import pytest
import yaml

from app.services.engine_catalog import (
    AXIS_PREFERENCE,
    CATEGORY_TO_AXIS,
    DEFAULT_AXIS,
    EXCLUDED_CATEGORIES,
    EXCLUDED_ENGINES,
    FALLBACK_PROFILES,
)
from app.services.search_service import SearXNGClient

SETTINGS_PATH = Path(__file__).resolve().parents[3] / "docker" / "searxng" / "settings.yml"

# Noms de profils de repli qui ne sont pas des moteurs SearXNG : ignores sans
# erreur par EngineCatalog.validate, donc tolerees ici (a corriger dans le code).
FALLBACK_SANS_MOTEUR = {"stackexchange"}


@pytest.fixture(scope="module")
def reglages() -> dict:
    return yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def moteurs(reglages: dict) -> Dict[str, dict]:
    return {e["name"]: e for e in reglages["engines"]}


def _attendus_par_axe() -> Set[str]:
    return {n for noms in AXIS_PREFERENCE.values() for n in noms}


def test_format_json_active(reglages: dict) -> None:
    assert "json" in reglages["search"]["formats"]


def test_aucun_secret_versionne(reglages: dict) -> None:
    # La vraie cle vient de SEARXNG_SECRET ; la valeur versionnee est le
    # marqueur amont que SearXNG exige de remplacer.
    assert reglages["server"]["secret_key"] == "ultrasecretkey"


def test_moteurs_de_axis_preference_actives(moteurs: Dict[str, dict]) -> None:
    absents = sorted(n for n in _attendus_par_axe() if n not in moteurs)
    assert not absents, f"moteurs de AXIS_PREFERENCE absents de settings.yml : {absents}"
    inactifs = sorted(
        n for n in _attendus_par_axe()
        if moteurs[n].get("disabled", True) or moteurs[n].get("inactive", True)
    )
    assert not inactifs, f"moteurs de AXIS_PREFERENCE non actifs : {inactifs}"


def test_moteurs_a_intervalle_actives(moteurs: Dict[str, dict]) -> None:
    manquants = sorted(
        n for n in SearXNGClient.ENGINE_INTERVALS
        if n not in moteurs or moteurs[n].get("disabled", True) or moteurs[n].get("inactive", True)
    )
    assert not manquants, f"moteurs de ENGINE_INTERVALS non actifs : {manquants}"


def test_moteurs_des_profils_de_repli_actives(moteurs: Dict[str, dict]) -> None:
    attendus = {n for noms in FALLBACK_PROFILES.values() for n in noms} - FALLBACK_SANS_MOTEUR
    manquants = sorted(
        n for n in attendus
        if n not in moteurs or moteurs[n].get("disabled", True) or moteurs[n].get("inactive", True)
    )
    assert not manquants, f"moteurs de FALLBACK_PROFILES non actifs : {manquants}"


def test_categories_coherentes_avec_les_axes(moteurs: Dict[str, dict]) -> None:
    # Reproduit EngineCatalog.axis_of : categories triees, premiere connue.
    # Un moteur doit tomber sur un axe dont la liste de preference le cite.
    for axe, noms in AXIS_PREFERENCE.items():
        for nom in noms:
            cats = sorted(moteurs[nom]["categories"])
            assert not set(cats) & EXCLUDED_CATEGORIES, f"{nom} : categorie exclue {cats}"
            assert nom not in EXCLUDED_ENGINES
            axe_reel = next((CATEGORY_TO_AXIS[c] for c in cats if c in CATEGORY_TO_AXIS), DEFAULT_AXIS)
            assert nom in AXIS_PREFERENCE[axe_reel], (
                f"{nom} (axe attendu {axe}) tombe sur l'axe {axe_reel} avec {cats}"
            )
