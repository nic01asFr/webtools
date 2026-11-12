"""Mesure de la fiabilite des citations de webtools.

Chaque affirmation citee d'une reponse (recherche rapide ou approfondie) est
confrontee a la page citee, recuperee independamment du service : le service
ne juge jamais ses propres citations.

Modules :
- texte      : normalisation, chiffres, recherche d'un extrait dans un texte
- triplets   : affirmations citees d'une reponse (affirmation, numero, URL)
- pages      : recuperation independante des pages citees, avec cache
- preuves    : passages de la page soumis au juge
- juge       : verdict d'un LLM juge, controle par l'extrait qu'il cite
- mesures    : taux, intervalles de confiance, origine des erreurs
- cli        : generer, evaluer, etalonner, comparer

Voir benchmarks/CITATIONS.md pour la methode et ses limites.
"""
