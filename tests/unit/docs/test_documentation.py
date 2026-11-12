"""Lot V5-1 : la documentation n'annonce que ce que le code fait.

- tous les champs de PipelineConfig figurent dans docs/CONFIGURATION.md ;
- docs/CONFIGURATION.md est a jour (regenere en memoire puis compare) ;
- README.md ne cite aucun parametre retire de /research/deep ;
- aucun document ne cite une methode privee de l'orchestrateur disparue.
"""

import dataclasses
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

from app.agents.intelligent_orchestrator import IntelligentOrchestrator
from app.research.pipeline_config import PipelineConfig

RACINE = Path(__file__).resolve().parents[3]
CONFIGURATION = RACINE / "docs" / "CONFIGURATION.md"

# Champs retires de DeepResearchRequest (une requete qui les envoie recoit 422),
# sous la forme dont un document les cite comme parametre.
CHAMPS_RETIRES = [
    "max_steps",
    "max_results",
    "min_sources",
    "date_range",
    "include_data",
    "include_reports",
    "include_academic",
    "suggested",
    "include_charts",
    "include_timeline",
    "output_format.structure",
    "stream",
]


@pytest.fixture(scope="module")
def generateur() -> ModuleType:
    chemin = RACINE / "docs" / "generer_configuration.py"
    spec = importlib.util.spec_from_file_location("generer_configuration", chemin)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Les dataclasses du module cherchent leur module dans sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_chaque_champ_de_pipeline_config_est_documente() -> None:
    texte = CONFIGURATION.read_text(encoding="utf-8")
    absents = [
        f.name
        for f in dataclasses.fields(PipelineConfig)
        if f"`WEBTOOLS_PIPELINE_{f.name.upper()}`" not in texte or f"`{f.name}`" not in texte
    ]
    assert absents == [], f"champs absents de docs/CONFIGURATION.md : {absents}"


def test_configuration_est_a_jour(generateur: ModuleType) -> None:
    actuel = CONFIGURATION.read_text(encoding="utf-8")
    attendu = generateur.appliquer(actuel)
    assert actuel == attendu, (
        "docs/CONFIGURATION.md est perime : lancer `python docs/generer_configuration.py`"
    )


def test_chaque_drapeau_a_une_campagne(generateur: ModuleType) -> None:
    drapeaux, _requete, _budgets = generateur._separer(generateur.lire_champs())
    assert {c.nom for c in drapeaux} <= set(generateur.CAMPAGNES)
    inutiles = set(generateur.CAMPAGNES) - {c.nom for c in drapeaux}
    assert inutiles == set(), f"campagnes pour des champs qui n'existent plus : {inutiles}"


def _est_cite_comme_parametre(texte: str, champ: str) -> bool:
    """Vrai si le champ apparait entre apostrophes inverses ou entre guillemets JSON."""
    motif = rf'(`[\w.]*{re.escape(champ)}[\w.:=]*`)|("{re.escape(champ)}"\s*:)'
    return re.search(motif, texte) is not None


def _sections_recherche_approfondie(texte: str) -> str:
    """Texte des sections qui parlent de /research/deep.

    Une section en fait partie si son titre contient "deep" ou "approfondi", ou si
    son corps contient "research/deep" (exemple curl). Les memes noms de champ,
    comme max_results, restent valides sur les autres endpoints. Les lignes qui
    disent qu'un champ est retire ou refuse (422) ne sont pas des citations.
    """
    blocs: list[tuple[str, list[str]]] = [("", [])]
    for ligne in texte.splitlines():
        if ligne.startswith("#"):
            blocs.append((ligne.lower(), []))
        else:
            blocs[-1][1].append(ligne)
    gardees: list[str] = []
    for titre, lignes in blocs:
        concerne = "deep" in titre or "approfondi" in titre or any("research/deep" in x for x in lignes)
        if not concerne or "retir" in titre:
            continue
        # Paragraphe par paragraphe : une phrase qui dit "retire" ou "422" peut
        # s'etendre sur plusieurs lignes.
        paragraphe: list[str] = []
        for ligne in [*lignes, ""]:
            if ligne.strip():
                paragraphe.append(ligne)
                continue
            bloc = "\n".join(paragraphe)
            if paragraphe and "retir" not in bloc.lower() and "422" not in bloc:
                gardees.extend(paragraphe)
            paragraphe = []
    return "\n".join(gardees)


def test_readme_ne_cite_aucun_champ_retire() -> None:
    texte = (RACINE / "README.md").read_text(encoding="utf-8")
    section = _sections_recherche_approfondie(texte)
    assert section, "README.md n'a plus de section sur /research/deep"
    cites = [c for c in CHAMPS_RETIRES if _est_cite_comme_parametre(section, c)]
    assert cites == [], f"champs retires cites comme parametres dans README.md : {cites}"


def test_les_champs_retires_sont_bien_refuses() -> None:
    from app.api.v1.endpoints.research_deep import DeepResearchRequest

    for champ in ("max_steps", "max_results", "stream", "max_sources"):
        with pytest.raises(Exception):
            DeepResearchRequest(topic="sujet de test", **{champ: 1})


DOCUMENTS = [
    "README.md",
    "DEEP_RESEARCH_ARCHITECTURE.md",
    "USE_CASES.md",
    "GETTING_STARTED.md",
    "docs/CONFIGURATION.md",
    "benchmarks/PROTOCOLE.md",
]


@pytest.mark.parametrize("nom", DOCUMENTS)
def test_aucune_methode_privee_disparue_dans_les_documents(nom: str) -> None:
    """Un document qui cite `_xxx(` doit viser une methode qui existe encore."""
    texte = (RACINE / nom).read_text(encoding="utf-8")
    existantes = {n for n in dir(IntelligentOrchestrator) if n.startswith("_")}
    cites = set(re.findall(r"`(_[a-z][a-z_0-9]*)\(", texte))
    disparues = sorted(cites - existantes)
    assert disparues == [], f"{nom} cite des methodes qui n'existent plus : {disparues}"
