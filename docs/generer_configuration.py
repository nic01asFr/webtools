"""Genere les tableaux de docs/CONFIGURATION.md depuis PipelineConfig.

Source unique : la classe PipelineConfig (app/research/pipeline_config.py) et
les commentaires qui suivent chaque champ. Le fichier docs/CONFIGURATION.md
garde ses parties redigees a la main ; seul le bloc entre les deux reperes
ci-dessous est produit par ce script.

Usage, depuis la racine du depot :

    python docs/generer_configuration.py            # reecrit le bloc genere
    python docs/generer_configuration.py --verifier # code 1 si le fichier est perime

Le test tests/unit/docs/test_documentation.py appelle les memes fonctions.
"""

from __future__ import annotations

import dataclasses
import inspect
import re
import sys
import typing
from dataclasses import dataclass
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

from app.research.pipeline_config import ENV_PREFIX, PipelineConfig

FICHIER = RACINE / "docs" / "CONFIGURATION.md"
DEBUT = "<!-- DEBUT-GENERE : ne pas modifier a la main, voir docs/generer_configuration.py -->"
FIN = "<!-- FIN-GENERE -->"

# Premier champ des budgets et seuils : tout ce qui precede, hors parametre de
# requete, est un drapeau de type B (PLAN_FIABILISATION.md, section 4.2).
REPERE_BUDGETS = "Lot V4-1"
PARAMETRES_DE_REQUETE = frozenset({"max_sources_per_section"})

# Campagne de mesure de chaque drapeau et valeur essayee (PLAN_FIABILISATION.md,
# section 8). Un drapeau absent de ce tableau fait echouer la generation : un
# nouveau drapeau doit etre rattache a une campagne ou declare hors campagne.
HORS_CAMPAGNE = "hors campagne"
CAMPAGNES: dict[str, tuple[str, str]] = {
    "rss_tier_root_only": ("M2", "true"),
    "single_llm_pass_enabled": ("M2", "false"),
    "extraction_use_agent": ("M5", "false"),
    "search_zero_result_fallback": ("M5", "false"),
    "search_relevance_gate": ("citecheck (benchmarks/CITATIONS.md)", "false"),
    "quick_verify_answer": ("citecheck (benchmarks/CITATIONS.md)", "true"),
    "deep_verify_sections": ("citecheck (benchmarks/CITATIONS.md)", "false"),
    "selection_fallback_enabled": ("citecheck (benchmarks/CITATIONS.md)", "false"),
    "synthesis_v3": ("citecheck sur rejeu (benchmarks/CITATIONS.md)", "false"),
    "deep_verify_rewrite": ("citecheck sur rejeu (benchmarks/CITATIONS.md)", "false"),
    "clean_title": ("M3", "true"),
    "summary_mode": ("M3", "none"),
    "coherence_append_enabled": ("M3", "false"),
    "enrichment_enabled": ("M3", "true"),
    "regenerate_v2": ("M3 (essai isole si M3 reste dans le bruit)", "true"),
    "validate_cited_urls": ("M4", "true"),
    "bibliography_cited_only": ("M4", "true"),
    "bibliography_real_titles": ("M4", "true"),
    "prompt_payload_minimal": ("M4", "true"),
    "separate_search_terms": ("M5", "true"),
    "extraction_cap_follows_depth": ("M5", "true"),
    "exploration_uses_plan_query": ("M5", "true"),
    "search_language_from_query": ("M5", "true"),
    "max_chunks_per_source": ("M5", "valeur a fixer"),
    "chunking_v2": ("M5", "true"),
    "plan_prompt_v2": (HORS_CAMPAGNE, "true"),
    "targeted_search_skip_structured": (HORS_CAMPAGNE, "false"),
    "quick_uses_chunk_selection": (HORS_CAMPAGNE, "true"),
}


@dataclass
class Champ:
    nom: str
    type: str
    defaut: object
    effet: str
    groupe: str
    budget: bool = False


def _nom_type(annotation: object) -> str:
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation).replace("typing.", "").replace("NoneType", "None")


def lire_champs() -> list[Champ]:
    """Champs de PipelineConfig dans l'ordre de declaration, avec leur effet."""
    source = inspect.getsource(PipelineConfig)
    hints = typing.get_type_hints(PipelineConfig)
    champs = {f.name: f for f in dataclasses.fields(PipelineConfig)}

    ligne_champ = re.compile(r"^    (\w+): [^=]+= (.*)$")
    resultat: list[Champ] = []
    groupe = ""
    budget = False
    commentaire_precedent: list[str] = []
    for ligne in source.splitlines():
        brut = ligne.strip()
        if brut.startswith("@classmethod"):
            break
        if brut.startswith("# ---"):
            groupe = brut.strip("# -").strip()
            budget = budget or REPERE_BUDGETS in groupe
            commentaire_precedent = []
            continue
        if re.match(r"^# Etape ", brut):
            groupe = brut[2:].strip()
            commentaire_precedent = []
            continue
        if not brut:
            commentaire_precedent = []
            continue
        if brut.startswith("#"):
            commentaire_precedent.append(brut[1:].strip())
            continue
        correspondance = ligne_champ.match(ligne)
        if not correspondance:
            continue
        nom = correspondance.group(1)
        if nom not in champs:
            continue
        reste = correspondance.group(2)
        inline = reste.split("  # ", 1)[1].strip() if "  # " in reste else ""
        effet = inline or " ".join(commentaire_precedent)
        commentaire_precedent = []
        resultat.append(
            Champ(
                nom=nom,
                type=_nom_type(hints[nom]),
                defaut=champs[nom].default,
                effet=effet,
                groupe=groupe,
                budget=budget,
            )
        )
    manquants = set(champs) - {c.nom for c in resultat}
    if manquants:
        raise RuntimeError(f"champs non lus dans le code source : {sorted(manquants)}")
    return resultat


ETAPES = {
    "exploration": "Étape exploration",
    "plan": "Étape plan",
    "recherche": "Étape recherche",
    "extraction": "Étape extraction",
    "selection": "Étape sélection",
    "synthese": "Étape synthèse",
    "coherence": "Étape cohérence",
    "assemblage": "Étape assemblage",
    "enrichissement": "Étape enrichissement",
}


def _defaut(valeur: object) -> str:
    if isinstance(valeur, bool):
        return f"`{str(valeur).lower()}`"
    if isinstance(valeur, str):
        return f'`"{valeur}"`'
    return f"`{valeur}`"


def _titre_etape(groupe: str) -> str:
    cle = groupe.removeprefix("Etape ").strip()
    return ETAPES.get(cle, groupe)


def _cellule(texte: str) -> str:
    return texte.replace("|", "\\|")


def _variable(nom: str) -> str:
    return f"`{ENV_PREFIX}{nom.upper()}`"


def _separer(champs: list[Champ]) -> tuple[list[Champ], list[Champ], list[Champ]]:
    """Rend (drapeaux B, parametres de requete, budgets et seuils)."""
    drapeaux: list[Champ] = []
    requete: list[Champ] = []
    budgets: list[Champ] = []
    for champ in champs:
        if champ.nom in PARAMETRES_DE_REQUETE:
            requete.append(champ)
        elif champ.budget:
            budgets.append(champ)
        else:
            drapeaux.append(champ)
    return drapeaux, requete, budgets


def _table_drapeaux(drapeaux: list[Champ]) -> list[str]:
    inconnus = [c.nom for c in drapeaux if c.nom not in CAMPAGNES]
    if inconnus:
        raise RuntimeError(
            f"drapeaux sans campagne dans docs/generer_configuration.py : {inconnus}"
        )
    lignes = [
        "| Nom | Type | Défaut | Variable d'environnement | Effet | Campagne | Valeur essayée |",
        "|---|---|---|---|---|---|---|",
    ]
    ordre = sorted(drapeaux, key=lambda c: (CAMPAGNES[c.nom][0] == HORS_CAMPAGNE, CAMPAGNES[c.nom][0]))
    for c in ordre:
        campagne, essai = CAMPAGNES[c.nom]
        essai_texte = f"`{essai}`" if essai in ("true", "false", "none") else essai
        lignes.append(
            f"| `{c.nom}` | {_cellule(c.type)} | {_defaut(c.defaut)} | {_variable(c.nom)} "
            f"| {_cellule(c.effet)} | {campagne} | {essai_texte} |"
        )
    return lignes


def _table_simple(champs: list[Champ]) -> list[str]:
    lignes = [
        "| Nom | Type | Défaut | Variable d'environnement | Effet |",
        "|---|---|---|---|---|",
    ]
    for c in champs:
        lignes.append(
            f"| `{c.nom}` | {_cellule(c.type)} | {_defaut(c.defaut)} | {_variable(c.nom)} "
            f"| {_cellule(c.effet)} |"
        )
    return lignes


def rendre_tableaux() -> str:
    """Bloc Markdown genere : drapeaux, parametre de requete, budgets et seuils."""
    champs = lire_champs()
    drapeaux, requete, budgets = _separer(champs)
    sortie: list[str] = []

    sortie += [f"### Drapeaux à mesurer (type B) : {len(drapeaux)} champs", ""]
    sortie += _table_drapeaux(drapeaux)
    sortie += [""]

    sortie += [f"### Paramètre aussi porté par la requête : {len(requete)} champ", ""]
    sortie += _table_simple(requete)
    sortie += [""]

    sortie += [f"### Budgets et seuils : {len(budgets)} champs", ""]
    groupe = None
    bloc: list[Champ] = []

    def vider() -> None:
        if bloc:
            sortie.extend([f"#### {_titre_etape(groupe)}", ""])
            sortie.extend(_table_simple(bloc))
            sortie.append("")

    for c in budgets:
        if c.groupe != groupe:
            vider()
            groupe = c.groupe
            bloc = []
        bloc.append(c)
    vider()

    total = len(drapeaux) + len(requete) + len(budgets)
    sortie += [f"Total : {total} champs, soit tous ceux de `PipelineConfig`."]
    return "\n".join(sortie).rstrip() + "\n"


def appliquer(texte: str) -> str:
    """Remplace le bloc entre les deux reperes par les tableaux a jour."""
    debut = texte.index(DEBUT) + len(DEBUT)
    fin = texte.index(FIN)
    return texte[:debut] + "\n\n" + rendre_tableaux() + "\n" + texte[fin:]


def main(argv: list[str]) -> int:
    actuel = FICHIER.read_text(encoding="utf-8")
    nouveau = appliquer(actuel)
    if "--verifier" in argv:
        if nouveau != actuel:
            print("docs/CONFIGURATION.md est perime : lancer docs/generer_configuration.py")
            return 1
        return 0
    if nouveau != actuel:
        FICHIER.write_text(nouveau, encoding="utf-8", newline="\n")
        print("docs/CONFIGURATION.md mis a jour")
    else:
        print("docs/CONFIGURATION.md deja a jour")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
