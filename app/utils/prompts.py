"""
Templates de prompts pour l'extraction de contenu web.
Optimisés pour albert-code avec 128K tokens de contexte.
"""

from typing import Dict


class PromptTemplates:
    """Collection de templates de prompts pour différents types de contenu."""

    # Template pour extraction générale (optimisé albert-code)
    GENERAL = """Tu es un expert en analyse de contenu web. Analyse le HTML suivant et extrait le contenu principal.

URL: {url}

Instructions:
1. Identifie le contenu principal en ignorant:
   - Les menus de navigation (<nav>, <header>, <footer>)
   - Les publicités (<aside>, .ads, .advertisement)
   - Les éléments secondaires (commentaires, articles recommandés)

2. Extrait:
   - Le titre principal (généralement dans <h1> ou <title>)
   - Le contenu textuel principal (articles, sections, paragraphes)

3. Retourne UNIQUEMENT au format suivant (pas de markdown, pas d'explications):
TITRE: [titre exact de la page]
CONTENU: [contenu principal en texte brut, préserve les paragraphes]

Ne fournis AUCUNE explication, UNIQUEMENT le format demandé."""

    # Template pour articles (optimisé albert-code)
    ARTICLE = """Tu es un expert en extraction d'articles web. Analyse le HTML suivant.

URL: {url}

Extrait les métadonnées et le contenu de cet article:
1. Titre (cherche <h1>, <title>, ou attributs og:title)
2. Auteur (cherche <meta name="author">, .author, .byline)
3. Date (cherche <time>, <meta property="article:published_time">)
4. Corps de l'article (généralement <article>, <main>, .post-content)

Retourne UNIQUEMENT au format suivant:
TITRE: [titre]
AUTEUR: [auteur ou "Non disponible"]
DATE: [date ou "Non disponible"]
CONTENU: [texte complet de l'article]

Pas d'explication, UNIQUEMENT le format demandé."""

    # Template pour produits e-commerce
    PRODUCT = """Visite l'URL {url} et analyse-la comme une page de produit.
Extrait les informations suivantes:

1. Nom du produit
2. Prix (si disponible)
3. Description du produit
4. Caractéristiques principales
5. Disponibilité (si mentionnée)

Format de réponse attendu:
TITRE: [nom du produit]
PRIX: [prix ou "Non disponible"]
DESCRIPTION: [description du produit]
CARACTÉRISTIQUES: [liste des caractéristiques principales]
DISPONIBILITÉ: [en stock / rupture / Non disponible]
"""

    # Template pour dépôts de code
    REPOSITORY = """Visite l'URL {url} et analyse-la comme un dépôt de code.
Extrait les informations suivantes:

1. Nom du projet
2. Description du projet
3. Langage(s) de programmation utilisé(s)
4. Nombre d'étoiles / forks (si disponible)
5. Contenu du README principal
6. Technologies et frameworks utilisés

Format de réponse attendu:
TITRE: [nom du projet]
DESCRIPTION: [description courte]
LANGAGES: [langages de programmation]
STATISTIQUES: [étoiles, forks, etc.]
README: [contenu principal du README]
TECHNOLOGIES: [liste des technologies]
"""

    # Template pour documentation (optimisé albert-code - exploite ses capacités code)
    DOCUMENTATION = """Tu es albert-code, expert en analyse de documentation technique. Analyse ce HTML de documentation.

URL: {url}

Extrait:
1. Titre de la documentation
2. Contenu technique (préserve la structure: sections, sous-sections)
3. Blocs de code (cherche <code>, <pre>, .highlight)
4. Notes/warnings (cherche .note, .warning, .important)

Retourne au format:
TITRE: [titre]
CONTENU: [documentation complète avec structure hiérarchique]
EXEMPLES: [tous les blocs de code avec leur contexte]
NOTES: [avertissements et notes importantes]

Format strict, pas d'explication."""

    # Template pour Légifrance (spécifique au contexte français)
    LEGIFRANCE = """Visite l'URL {url} qui est un texte juridique sur Légifrance.

Étapes à suivre:
1. Attends que la page soit complètement chargée (environ 5 secondes)
2. Recherche le contenu principal qui contient le texte juridique
   - Généralement dans un élément avec id='content' ou class='corpsTexte'
3. Extrait d'abord le titre du texte juridique
4. Puis extrait le contenu du texte juridique (articles, sections, etc.)
5. Ignore les menus, publicités, en-têtes, pieds de page et les éléments secondaires
6. Assure-toi de capturer l'intégralité du texte juridique

Format de réponse attendu:
TITRE: [titre du texte juridique]
CONTENU: [texte juridique complet avec articles et sections]
"""

    @staticmethod
    def get_prompt(content_type: str, url: str, custom_prompt: str = None) -> str:
        """
        Récupère le prompt approprié pour le type de contenu.

        Args:
            content_type: Type de contenu ("general", "article", "product", etc.)
            url: URL à extraire
            custom_prompt: Prompt personnalisé (optionnel, prioritaire)

        Returns:
            Prompt formaté avec l'URL
        """
        # Si un prompt personnalisé est fourni, l'utiliser
        if custom_prompt:
            return custom_prompt.format(url=url) if "{url}" in custom_prompt else custom_prompt

        # Cas spécial pour Légifrance
        if "legifrance.gouv.fr" in url.lower():
            return PromptTemplates.LEGIFRANCE.format(url=url)

        # Sélectionner le template selon le type
        templates: Dict[str, str] = {
            "general": PromptTemplates.GENERAL,
            "webpage": PromptTemplates.GENERAL,
            "article": PromptTemplates.ARTICLE,
            "product": PromptTemplates.PRODUCT,
            "repository": PromptTemplates.REPOSITORY,
            "documentation": PromptTemplates.DOCUMENTATION,
        }

        template = templates.get(content_type, PromptTemplates.GENERAL)
        return template.format(url=url)
