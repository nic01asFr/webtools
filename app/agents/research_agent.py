"""
Agent de recherche profonde utilisant albert-code pour navigation intelligente.
"""

import logging
import re
from typing import List, Optional, Set, Dict
from datetime import datetime
from bs4 import BeautifulSoup
import httpx

from app.core.llm.base import BaseLLMClient
from app.extractors.direct_extractor import DirectExtractor
from app.api.models import SourceInfo, NavigationStep, WebResult

logger = logging.getLogger(__name__)


class ResearchAgent:
    """
    Agent qui utilise albert-code pour :
    1. Analyser le contenu des pages
    2. Identifier les liens pertinents
    3. Naviguer de manière intelligente
    4. Synthétiser une réponse finale
    """

    def __init__(
        self,
        llm_client: BaseLLMClient,
        max_depth: int = 2,
        max_sources: int = 5
    ):
        self.llm_client = llm_client
        self.max_depth = max_depth
        self.max_sources = max_sources
        self.extractor = DirectExtractor()

        # Tracking
        self.visited_urls: Set[str] = set()
        self.sources: List[SourceInfo] = []
        self.navigation_path: List[NavigationStep] = []
        self.total_links_analyzed = 0

    async def research(
        self,
        query: str,
        start_urls: List[str]
    ) -> Dict:
        """
        Effectue une recherche profonde en partant des URLs initiales.

        Args:
            query: Requête utilisateur
            start_urls: URLs de départ

        Returns:
            Dictionnaire avec answer, sources, navigation_path
        """
        logger.info(f"Starting deep research for query: {query}")
        logger.info(f"Start URLs: {start_urls}")

        # Navigation récursive
        await self._navigate_recursive(
            urls=start_urls,
            query=query,
            depth=0
        )

        # Synthèse finale avec albert-code
        answer = await self._synthesize_answer(query)

        return {
            "answer": answer,
            "sources": self.sources,
            "navigation_path": self.navigation_path,
            "total_pages_visited": len(self.visited_urls),
            "total_links_analyzed": self.total_links_analyzed
        }

    async def _navigate_recursive(
        self,
        urls: List[str],
        query: str,
        depth: int
    ):
        """Navigation récursive avec extraction de liens"""

        if depth >= self.max_depth:
            logger.info(f"Max depth {self.max_depth} reached")
            return

        if len(self.sources) >= self.max_sources:
            logger.info(f"Max sources {self.max_sources} reached")
            return

        for url in urls:
            if url in self.visited_urls:
                continue

            if len(self.sources) >= self.max_sources:
                break

            # Extraire le contenu
            result = await self._extract_page(url)
            if not result or not result.success:
                continue

            self.visited_urls.add(url)

            # Analyser la pertinence avec albert-code
            relevance = await self._analyze_relevance(
                query=query,
                content=result.content[:10000],  # Limiter pour le prompt
                url=url
            )

            if relevance["is_relevant"]:
                # Ajouter aux sources
                self.sources.append(SourceInfo(
                    url=url,
                    title=result.title or url,
                    excerpt=relevance["excerpt"],
                    relevance_score=relevance["score"],
                    depth=depth,
                    visited_at=datetime.now().isoformat()
                ))

                logger.info(f"Added source: {url} (score: {relevance['score']})")

                # Extraire les liens pertinents si pas à la profondeur max
                if depth < self.max_depth - 1:
                    next_urls = await self._extract_relevant_links(
                        query=query,
                        content=result.content[:20000],
                        base_url=url
                    )

                    if next_urls:
                        logger.info(f"Found {len(next_urls)} relevant links to follow")

                        self.navigation_path.append(NavigationStep(
                            from_url=url,
                            to_url=", ".join(next_urls[:3]),
                            reason=f"Links found relevant to query",
                            links_found=len(next_urls)
                        ))

                        # Navigation récursive
                        await self._navigate_recursive(
                            urls=next_urls[:5],  # Limiter le nombre
                            query=query,
                            depth=depth + 1
                        )

    async def _extract_page(self, url: str) -> Optional[WebResult]:
        """Extrait le contenu d'une page"""
        try:
            result = await self.extractor.extract(
                url=url,
                prompt="",
                options={"timeout": 30, "headless": True}
            )
            return result
        except Exception as e:
            logger.error(f"Error extracting {url}: {e}")
            return None

    async def _analyze_relevance(
        self,
        query: str,
        content: str,
        url: str
    ) -> Dict:
        """
        Utilise albert-code pour analyser si le contenu est pertinent.

        Returns:
            Dict avec is_relevant, score, excerpt
        """
        prompt = f"""Analyse ce contenu web pour la requête utilisateur.

Requête: "{query}"
URL: {url}
Contenu (extrait): {content[:3000]}

Réponds au format JSON STRICT (pas de markdown):
{{
  "is_relevant": true/false,
  "score": 0.0-1.0,
  "excerpt": "extrait pertinent de 200 caractères max"
}}

IMPORTANT: Réponds UNIQUEMENT le JSON, rien d'autre."""

        try:
            messages = [{"role": "user", "content": prompt}]
            response = await self.llm_client.generate(messages, max_tokens=500)

            # Parser la réponse JSON
            response_clean = response.strip()
            if response_clean.startswith("```"):
                response_clean = re.sub(r'```json\n?|\n?```', '', response_clean)

            import json
            analysis = json.loads(response_clean)

            return {
                "is_relevant": analysis.get("is_relevant", False),
                "score": float(analysis.get("score", 0.0)),
                "excerpt": analysis.get("excerpt", content[:200])
            }

        except Exception as e:
            logger.error(f"Error analyzing relevance with LLM: {e}")
            # Fallback: pertinence basique par mots-clés
            query_words = set(query.lower().split())
            content_words = set(content.lower().split())
            overlap = len(query_words & content_words)
            score = min(overlap / len(query_words), 1.0) if query_words else 0.0

            return {
                "is_relevant": score > 0.3,
                "score": score,
                "excerpt": content[:200]
            }

    async def _extract_relevant_links(
        self,
        query: str,
        content: str,
        base_url: str
    ) -> List[str]:
        """
        Utilise albert-code pour identifier les liens pertinents à suivre.
        """
        # Extraire les liens du HTML
        soup = BeautifulSoup(content, 'html.parser')
        links = []

        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True)

            # Normaliser l'URL
            if href.startswith('http'):
                full_url = href
            elif href.startswith('/'):
                from urllib.parse import urlparse, urljoin
                full_url = urljoin(base_url, href)
            else:
                continue

            # Filtrer les liens non pertinents
            if any(x in full_url for x in ['#', 'javascript:', 'mailto:', '.pdf', '.zip']):
                continue

            links.append({
                "url": full_url,
                "text": text[:100]
            })

        if not links:
            return []

        self.total_links_analyzed += len(links)

        # Limiter pour le prompt
        links_sample = links[:20]

        # Demander à albert-code de sélectionner
        prompt = f"""Identifie les liens les PLUS pertinents pour répondre à cette requête.

Requête: "{query}"

Liens disponibles:
{chr(10).join([f"{i+1}. {l['text']} -> {l['url']}" for i, l in enumerate(links_sample)])}

Réponds au format JSON STRICT (pas de markdown):
{{
  "selected_urls": ["url1", "url2", ...],
  "reason": "explication courte"
}}

Sélectionne maximum 5 liens les plus pertinents. UNIQUEMENT le JSON."""

        try:
            messages = [{"role": "user", "content": prompt}]
            response = await self.llm_client.generate(messages, max_tokens=1000)

            response_clean = response.strip()
            if response_clean.startswith("```"):
                response_clean = re.sub(r'```json\n?|\n?```', '', response_clean)

            import json
            selection = json.loads(response_clean)

            selected_urls = selection.get("selected_urls", [])
            logger.info(f"Albert-code selected {len(selected_urls)} links: {selection.get('reason', '')}")

            return selected_urls[:5]

        except Exception as e:
            logger.error(f"Error selecting links with LLM: {e}")
            # Fallback: prendre les premiers liens
            return [l["url"] for l in links[:3]]

    async def _synthesize_answer(self, query: str) -> str:
        """
        Utilise albert-code pour synthétiser une réponse à partir des sources.
        """
        if not self.sources:
            return "Aucune information pertinente trouvée."

        # Préparer le contexte avec les sources
        context = f"Requête utilisateur: {query}\n\nSources trouvées:\n\n"

        for i, source in enumerate(self.sources, 1):
            context += f"Source {i} ({source.url}):\n"
            context += f"{source.excerpt}\n\n"

        prompt = f"""{context}

En te basant UNIQUEMENT sur les sources ci-dessus, réponds à la requête de l'utilisateur de manière complète et structurée.

Inclus:
- Une réponse claire et directe
- Les points clés
- Les sources citées

Ta réponse (pas de markdown, texte brut):"""

        try:
            messages = [{"role": "user", "content": prompt}]
            answer = await self.llm_client.generate(
                messages,
                max_tokens=2000
            )
            return answer.strip()

        except Exception as e:
            logger.error(f"Error synthesizing answer: {e}")
            return f"Erreur lors de la synthèse: {str(e)}"

    def get_path(self) -> List[NavigationStep]:
        """Retourne le chemin de navigation"""
        return self.navigation_path
