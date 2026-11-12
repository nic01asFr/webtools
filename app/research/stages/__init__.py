"""Etapes du pipeline de recherche approfondie, une par module.

Chaque etape est un ensemble de fonctions qui recoivent explicitement ce
dont elles ont besoin : les services injectes (StageServices : client LLM,
client de recherche, gestionnaire d'extraction, extracteur de donnees,
configuration par defaut, delai) et le contexte de la requete
(ExecutionContext). Aucune n'ecrit sur l'orchestrateur.

Ordre du pipeline : explore, plan, search, extract, select, synthesize,
coherence, enrich, assemble. Les modeles echanges entre etapes sont dans
app.research.models.
"""
