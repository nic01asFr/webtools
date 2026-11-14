"""Prompt du plan detaille et filtre des sections qui decrivent le travail plutot que le sujet."""

import logging
import re

from app.research.pipeline_config import PipelineConfig


logger = logging.getLogger(__name__)


# Titres de sections qui decrivent le TRAVAIL et non le sujet. Observe
# sur la tache 55 du benchmark : le modele avait ajoute "Structure Finale
# du Rapport", "Strategie de Recherche Detaillee" et "Criteres de
# Validation" comme sections a part entiere - 3 sections sur 11 de notes
# de travail livrees au lecteur. C'est la tache qui a obtenu le plus
# mauvais score (0.306) malgre le PLUS de mots (4905) et de references
# (29) : ni le volume ni le sourcage ne compensent du hors-sujet.
META_SECTION_PATTERNS = (
    "structure du rapport",
    "structure finale",
    "plan du rapport",
    "plan de travail",
    "strategie de recherche",
    "stratégie de recherche",
    "methodologie de recherche",
    "méthodologie de recherche",
    "criteres de validation",
    "critères de validation",
    "sources consultees",
    "sources consultées",
    "demarche",
    "démarche",
)

# Mots ou expressions entiers : une sous-chaine retirait "Demarches
# administratives pour la vignette Crit'Air" (le mot "demarches" contient
# "demarche") sans que rien ne le signale.
_META_SECTION_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(p) for p in META_SECTION_PATTERNS) + r")(?!\w)"
)


def is_meta_section(name: str) -> bool:
    """
    Filet de securite derriere la regle du prompt : une consigne ne
    suffit pas toujours (verifie sur la langue, ou le modele ignorait
    une instruction explicite avec exemples). Ici le cout d'un faux
    positif est faible - un titre legitime rejete - alors qu'un faux
    negatif livre des notes de travail dans un rapport publie.
    """
    return bool(_META_SECTION_RE.search((name or "").lower()))


def imposed_sections_block(imposed: list) -> str:
    """Consigne des sections imposees, ajoutee au prompt du plan quand l'appelant en fixe."""
    listing = "\n".join(f"{i}. {t}" for i, t in enumerate(imposed, start=1))
    return (
        "\n\n"
        "SECTIONS IMPOSEES PAR L'APPELANT : le plan contient EXACTEMENT les "
        "sections suivantes, avec ces titres, dans cet ordre, sans en ajouter ni "
        "en retirer ni en reformuler le titre. Construis pour chacune sa cible "
        "(profondeur, objectifs, questions, requetes).\n"
        + listing
        + "\n"
    )


def required_sources_block(exploration_data: dict) -> str:
    """Titres et extraits des sources requises deja lues, pour que le plan les voie."""
    items = [s for s in (exploration_data.get("sources") or []) if s.get("required")]
    if not items:
        return ""
    lines = [
        "SOURCES OBLIGATOIRES DEJA LUES (le plan doit en tenir compte) :",
        *(f"- {s.get('title') or s.get('url')} ({s.get('url')}) : {s.get('snippet', '')}" for s in items),
    ]
    return "\n" + "\n".join(lines) + "\n"


def build_plan_prompt_v2(
    query: str,
    context: dict,
    exploration_data: dict,
    config: PipelineConfig | None = None,
) -> str:
    """
    Prompt de planification v2 (drapeau plan_prompt_v2).

    Les contraintes de forme (couverture des aspects demandes, operateurs,
    source_axis, nombre de sections) vivent dans le schema de sortie et
    n'y sont pas repetees. Le prompt ne porte ni format JSON ni exemples.
    Il ne recoit que les champs utiles du contexte, pas le dictionnaire
    entier.
    """
    cfg = config if config is not None else PipelineConfig()
    ctx_in = context if isinstance(context, dict) else {}
    topics = exploration_data.get("topics_found", [])
    snippets = (exploration_data.get("data_preview") or {}).get("snippets") or []
    lines = [
        (
            "Tu es un planificateur de rapports de recherche. Construis le plan "
            "du rapport qui repond a la consigne ci-dessous."
        ),
        "",
        f'CONSIGNE: "{query}"',
    ]
    objectives = ctx_in.get("objectives")
    if objectives:
        lines.append("OBJECTIFS: " + "; ".join(str(o) for o in objectives))
    imposed = ctx_in.get("output_sections")
    if imposed:
        lines.append(imposed_sections_block([str(x) for x in imposed]).strip())
    for key, label in (
        ("sources_required", "SOURCES OBLIGATOIRES"),
        ("sources_suggested", "SOURCES SUGGEREES"),
        ("sources_exclusions", "DOMAINES EXCLUS"),
        ("domains_whitelist", "DOMAINES AUTORISES UNIQUEMENT"),
    ):
        value = ctx_in.get(key)
        if value:
            lines.append(f"{label}: " + ", ".join(str(v) for v in value))
    lines += [
        "",
        "EXPLORATION:",
        f"- Sources decouvertes: {len(exploration_data.get('sources', []))}",
        f"- Richesse du champ: {exploration_data.get('field_assessment', 'moderate')}",
        f"- Sous-themes identifies: {', '.join(str(t) for t in topics[: cfg.plan_topics_in_prompt]) or 'N/A'}",
    ]
    if snippets:
        lines.append(f"- Apercu: {str(snippets[0])[: cfg.plan_snippet_chars]}")
    lines += [
        "",
        "Regles:",
        (
            "- Les sections decrivent le SUJET, jamais ton travail : pas de "
            "section du type Structure du rapport, Strategie de recherche, "
            "Methodologie, Criteres de validation ou Plan de travail. Chaque "
            "titre doit pouvoir figurer dans la table des matieres d'un "
            "rapport publie."
        ),
        (
            "- Adapte l'ampleur du plan a la complexite de la consigne et aux "
            "sous-themes decouverts."
        ),
        "- Definis l'enchainement logique entre sections consecutives.",
    ]
    return "\n".join(lines)
