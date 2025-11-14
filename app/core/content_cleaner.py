"""
Advanced Content Cleaning and Contextualization System

Nettoie le contenu HTML extrait et l'enrichit avec des métadonnées contextuelles
pour maximiser la qualité de l'analyse par les agents IA.
"""

import re
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
from bs4 import BeautifulSoup, NavigableString, Tag

logger = logging.getLogger(__name__)


@dataclass
class ContentMetadata:
    """Métadonnées extraites du contenu"""
    title: Optional[str] = None
    author: Optional[str] = None
    publish_date: Optional[str] = None
    last_modified: Optional[str] = None
    description: Optional[str] = None
    keywords: List[str] = None
    language: Optional[str] = None
    reading_time_minutes: Optional[int] = None

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class CleanedContent:
    """Contenu nettoyé et enrichi"""
    main_content: str  # Contenu principal nettoyé
    structured_content: str  # Contenu avec marqueurs structurels
    metadata: ContentMetadata
    sections: List[Dict[str, str]]  # Sections identifiées avec titres
    statistics: Dict[str, int]  # Stats sur le nettoyage

    def to_dict(self):
        return {
            "main_content": self.main_content,
            "structured_content": self.structured_content,
            "metadata": self.metadata.to_dict(),
            "sections": self.sections,
            "statistics": self.statistics
        }


class AdvancedContentCleaner:
    """
    Système avancé de nettoyage et contextualisation de contenu.

    Fonctionnalités:
    - Suppression HTML/CSS/JavaScript résiduel
    - Filtrage des éléments non-contenu (nav, footer, ads, etc.)
    - Préservation de la structure (titres, listes, tableaux)
    - Extraction de métadonnées
    - Marquage sémantique pour les agents
    """

    # Éléments à supprimer (navigation, widgets, etc.)
    NOISE_SELECTORS = [
        'nav', 'header', 'footer', 'aside',
        '.navigation', '.nav', '.menu', '.sidebar',
        '.footer', '.header', '.ad', '.ads', '.advertisement',
        '.social', '.share', '.comments', '.related',
        '.cookie', '.popup', '.modal', '.newsletter',
        '#comments', '#sidebar', '#footer', '#header',
        '[role="navigation"]', '[role="complementary"]',
        'script', 'style', 'noscript', 'iframe'
    ]

    # Sélecteurs de contenu principal (par priorité)
    MAIN_CONTENT_SELECTORS = [
        'article',
        'main',
        '[role="main"]',
        '.article-content',
        '.post-content',
        '.entry-content',
        '.main-content',
        '#main-content',
        '.content',
        '#content'
    ]

    # Patterns de dates
    DATE_PATTERNS = [
        r'\d{4}-\d{2}-\d{2}',  # ISO: 2024-01-15
        r'\d{2}/\d{2}/\d{4}',  # FR: 15/01/2024
        r'\d{1,2}\s+(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)\s+\d{4}',
        r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}'
    ]

    def clean_and_contextualize(self, html_content: str, url: str = "") -> CleanedContent:
        """
        Nettoie et enrichit le contenu HTML.

        Args:
            html_content: Contenu HTML brut
            url: URL source (optionnel)

        Returns:
            CleanedContent avec contenu nettoyé et métadonnées
        """
        stats = {
            "original_length": len(html_content),
            "cleaned_length": 0,
            "elements_removed": 0,
            "sections_found": 0
        }

        # Parser le HTML
        soup = BeautifulSoup(html_content, 'html.parser')

        # Extraire les métadonnées AVANT nettoyage
        metadata = self._extract_metadata(soup, url)

        # Supprimer les éléments de bruit
        stats["elements_removed"] = self._remove_noise(soup)

        # Trouver le contenu principal
        main_element = self._find_main_content(soup)

        if not main_element:
            main_element = soup.body if soup.body else soup

        # Extraire les sections structurées
        sections = self._extract_sections(main_element)
        stats["sections_found"] = len(sections)

        # Générer le contenu nettoyé simple
        main_content = self._extract_clean_text(main_element)

        # Générer le contenu structuré avec marqueurs
        structured_content = self._generate_structured_content(main_element)

        stats["cleaned_length"] = len(main_content)

        # Calculer temps de lecture estimé
        words_count = len(main_content.split())
        metadata.reading_time_minutes = max(1, words_count // 200)  # ~200 mots/min

        logger.info(
            f"Nettoyage terminé: {stats['original_length']} → {stats['cleaned_length']} chars, "
            f"{stats['elements_removed']} éléments supprimés, {stats['sections_found']} sections"
        )

        return CleanedContent(
            main_content=main_content,
            structured_content=structured_content,
            metadata=metadata,
            sections=sections,
            statistics=stats
        )

    def _extract_metadata(self, soup: BeautifulSoup, url: str) -> ContentMetadata:
        """Extrait les métadonnées du document."""
        metadata = ContentMetadata(keywords=[])

        # Titre
        title_tag = soup.find('title')
        if title_tag:
            metadata.title = title_tag.get_text(strip=True)

        # Métadonnées Open Graph / Twitter Cards
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            metadata.title = og_title['content']

        # Description
        desc_tag = soup.find('meta', attrs={'name': 'description'})
        if desc_tag and desc_tag.get('content'):
            metadata.description = desc_tag['content']

        og_desc = soup.find('meta', property='og:description')
        if og_desc and og_desc.get('content'):
            metadata.description = og_desc['content']

        # Auteur
        author_tag = soup.find('meta', attrs={'name': 'author'})
        if author_tag and author_tag.get('content'):
            metadata.author = author_tag['content']

        # Sélecteurs d'auteur courants
        author_selectors = [
            '.author', '.byline', '[rel="author"]',
            '.entry-author', '.post-author'
        ]
        for selector in author_selectors:
            author_elem = soup.select_one(selector)
            if author_elem:
                metadata.author = author_elem.get_text(strip=True)
                break

        # Date de publication
        date_tag = soup.find('meta', property='article:published_time')
        if date_tag and date_tag.get('content'):
            metadata.publish_date = date_tag['content']

        # Chercher dans le contenu
        if not metadata.publish_date:
            time_tag = soup.find('time')
            if time_tag:
                metadata.publish_date = time_tag.get('datetime') or time_tag.get_text(strip=True)

        # Date de modification
        modified_tag = soup.find('meta', property='article:modified_time')
        if modified_tag and modified_tag.get('content'):
            metadata.last_modified = modified_tag['content']

        # Mots-clés
        keywords_tag = soup.find('meta', attrs={'name': 'keywords'})
        if keywords_tag and keywords_tag.get('content'):
            metadata.keywords = [k.strip() for k in keywords_tag['content'].split(',')]

        # Langue
        html_tag = soup.find('html')
        if html_tag and html_tag.get('lang'):
            metadata.language = html_tag['lang']

        return metadata

    def _remove_noise(self, soup: BeautifulSoup) -> int:
        """Supprime les éléments de bruit."""
        removed_count = 0

        for selector in self.NOISE_SELECTORS:
            elements = soup.select(selector)
            for elem in elements:
                elem.decompose()
                removed_count += 1

        return removed_count

    def _find_main_content(self, soup: BeautifulSoup) -> Optional[Tag]:
        """Trouve l'élément de contenu principal."""
        for selector in self.MAIN_CONTENT_SELECTORS:
            element = soup.select_one(selector)
            if element:
                return element
        return None

    def _extract_sections(self, element: Tag) -> List[Dict[str, str]]:
        """Extrait les sections avec leurs titres."""
        sections = []
        current_section = None
        current_content = []

        for child in element.descendants:
            if isinstance(child, Tag):
                # Détection de titre de section
                if child.name in ['h1', 'h2', 'h3', 'h4']:
                    # Sauvegarder la section précédente
                    if current_section:
                        sections.append({
                            "title": current_section,
                            "level": current_level,
                            "content": '\n'.join(current_content).strip()
                        })

                    # Nouvelle section
                    current_section = child.get_text(strip=True)
                    current_level = child.name
                    current_content = []

                # Ajouter le contenu textuel
                elif child.name == 'p' and current_section:
                    text = child.get_text(strip=True)
                    if text:
                        current_content.append(text)

        # Dernière section
        if current_section and current_content:
            sections.append({
                "title": current_section,
                "level": current_level,
                "content": '\n'.join(current_content).strip()
            })

        return sections

    def _extract_clean_text(self, element: Tag) -> str:
        """Extrait le texte nettoyé sans marqueurs."""
        # Obtenir tout le texte
        text = element.get_text(separator='\n', strip=True)

        # Nettoyer les espaces multiples
        text = re.sub(r' {2,}', ' ', text)

        # Nettoyer les sauts de ligne multiples
        text = re.sub(r'\n{3,}', '\n\n', text)

        # Supprimer les lignes très courtes (probablement du bruit)
        lines = text.split('\n')
        cleaned_lines = []
        for line in lines:
            line = line.strip()
            # Garder si > 20 chars OU si c'est un titre (court mais important)
            if len(line) > 20 or (len(line) > 5 and line[0].isupper()):
                cleaned_lines.append(line)

        return '\n'.join(cleaned_lines)

    def _generate_structured_content(self, element: Tag) -> str:
        """Génère du contenu avec marqueurs structurels pour les agents."""
        parts = []

        for child in element.descendants:
            if not isinstance(child, Tag):
                continue

            # Titres avec marqueurs de niveau
            if child.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                level = child.name[1]
                text = child.get_text(strip=True)
                if text:
                    parts.append(f"\n{'#' * int(level)} {text}\n")

            # Listes avec marqueurs
            elif child.name == 'li':
                text = child.get_text(strip=True)
                if text and text not in ' '.join(parts):  # Éviter duplicates
                    parts.append(f"• {text}")

            # Paragraphes
            elif child.name == 'p':
                text = child.get_text(strip=True)
                if text and len(text) > 30:  # Filtrer les courts
                    parts.append(f"\n{text}\n")

            # Blockquotes avec marqueur
            elif child.name == 'blockquote':
                text = child.get_text(strip=True)
                if text:
                    parts.append(f"\n> {text}\n")

            # Tableaux - extraire comme données structurées
            elif child.name == 'table':
                table_text = self._extract_table(child)
                if table_text:
                    parts.append(f"\n[TABLE]\n{table_text}\n[/TABLE]\n")

        # Assembler et nettoyer
        content = '\n'.join(parts)
        content = re.sub(r'\n{3,}', '\n\n', content)

        return content.strip()

    def _extract_table(self, table: Tag) -> str:
        """Extrait un tableau en format texte structuré."""
        rows = []

        # Headers
        headers = table.find_all('th')
        if headers:
            row = ' | '.join(h.get_text(strip=True) for h in headers)
            rows.append(row)
            rows.append('-' * len(row))

        # Rows
        for tr in table.find_all('tr'):
            cells = tr.find_all(['td', 'th'])
            if cells:
                row = ' | '.join(c.get_text(strip=True) for c in cells)
                rows.append(row)

        return '\n'.join(rows) if rows else ""


def clean_content_for_agents(html_or_text: str, url: str = "") -> Dict[str, Any]:
    """
    Fonction utilitaire pour nettoyer du contenu.

    Args:
        html_or_text: Contenu HTML ou texte brut
        url: URL source

    Returns:
        Dict avec contenu nettoyé et métadonnées
    """
    cleaner = AdvancedContentCleaner()

    # Détecter si c'est du HTML
    is_html = bool(re.search(r'<[^>]+>', html_or_text))

    if is_html:
        result = cleaner.clean_and_contextualize(html_or_text, url)
        return result.to_dict()
    else:
        # Texte déjà nettoyé - juste normaliser
        text = re.sub(r' {2,}', ' ', html_or_text)
        text = re.sub(r'\n{3,}', '\n\n', text)

        return {
            "main_content": text,
            "structured_content": text,
            "metadata": ContentMetadata().to_dict(),
            "sections": [],
            "statistics": {
                "original_length": len(html_or_text),
                "cleaned_length": len(text),
                "elements_removed": 0,
                "sections_found": 0
            }
        }
