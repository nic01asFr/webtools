"""
Utilitaire pour détecter le type de contenu d'une URL.
"""

import re
from urllib.parse import urlparse
from typing import Literal


ContentType = Literal["article", "product", "repository", "documentation", "webpage"]


def _contains_term(haystack: str, terms: list[str]) -> bool:
    """Vrai si un terme apparait comme mot entier (pluriel en s admis).

    Une recherche par sous-chaine classait capital.fr en documentation
    ("api" dans "capital") et workshop.fr en e-commerce ("shop")."""
    for term in terms:
        pattern = r"(?<![a-z0-9])" + re.escape(term.rstrip(".")) + r"s?(?![a-z0-9])"
        if re.search(pattern, haystack):
            return True
    return False


class ContentDetector:
    """Détecte le type de contenu à partir d'une URL."""

    # Patterns de détection
    REPOSITORY_DOMAINS = ["github.com", "gitlab.com", "bitbucket.org"]

    DOCUMENTATION_PATTERNS = [
        "docs.", "documentation", "developer", "api",
        "reference", "manual", "guide"
    ]

    ECOMMERCE_DOMAINS = [
        "amazon", "ebay", "walmart", "alibaba",
        "shopping", "store", "shop"
    ]

    NEWS_DOMAINS = [
        "news", "blog", "article", "post",
        "medium.com", "substack.com"
    ]

    @staticmethod
    def detect(url: str) -> ContentType:
        """
        Détecte le type de contenu à partir de l'URL.

        Args:
            url: URL à analyser

        Returns:
            Type de contenu détecté
        """
        parsed_url = urlparse(url)
        domain = parsed_url.netloc.lower()
        path = parsed_url.path.lower()

        # Détection de dépôts de code
        if any(repo_domain in domain for repo_domain in ContentDetector.REPOSITORY_DOMAINS):
            return "repository"

        # Détection de documentation technique
        if _contains_term(domain, ContentDetector.DOCUMENTATION_PATTERNS) or _contains_term(
            path, ContentDetector.DOCUMENTATION_PATTERNS
        ):
            return "documentation"

        # Détection de sites e-commerce
        if _contains_term(domain, ContentDetector.ECOMMERCE_DOMAINS):
            return "product"

        # Détection d'articles
        if _contains_term(domain, ContentDetector.NEWS_DOMAINS):
            return "article"

        # Par défaut
        return "webpage"
