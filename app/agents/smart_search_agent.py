"""
Smart Search Agent - Analyse la structure d'un site et s'adapte automatiquement.
Utilise Vision AI pour identifier où et comment rechercher.
"""

import logging
from typing import Dict, Any, List
from datetime import datetime

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from app.core.llm.base import BaseLLMClient

logger = logging.getLogger(__name__)


class SmartSearchAgent:
    """
    Agent intelligent qui analyse visuellement un site web pour comprendre
    comment effectuer une recherche, puis l'exécute.
    """

    def __init__(self, llm_client: BaseLLMClient, timeout: int = 120):
        self.llm_client = llm_client
        self.timeout = timeout

    async def search(
        self,
        site_url: str,
        search_query: str,
        max_results: int = 10
    ) -> Dict[str, Any]:
        """
        Effectue une recherche intelligente en analysant d'abord la structure du site.

        Args:
            site_url: URL du site
            search_query: Requête de recherche
            max_results: Nombre de résultats souhaités

        Returns:
            Résultats de recherche avec métadonnées
        """
        logger.info(f"🔍 Recherche intelligente sur {site_url} pour '{search_query}'")
        start_time = datetime.now()

        try:
            # Étape 1 : Analyse visuelle du site
            logger.info("📸 Étape 1/3 : Capture et analyse visuelle du site...")
            search_strategy = await self._analyze_site_structure(site_url)

            if not search_strategy["can_search"]:
                return {
                    "success": False,
                    "error": f"Impossible de trouver un moyen de rechercher sur ce site: {search_strategy['reason']}",
                    "results": []
                }

            # Étape 2 : Exécution de la recherche selon la stratégie
            logger.info(f"🎯 Étape 2/3 : Exécution de la recherche (stratégie: {search_strategy['method']})...")
            results = await self._execute_search(
                site_url,
                search_query,
                search_strategy,
                max_results
            )

            # Étape 3 : Extraction des résultats
            logger.info("📊 Étape 3/3 : Extraction des résultats...")
            processing_time = (datetime.now() - start_time).total_seconds()

            return {
                "success": True,
                "results": results,
                "search_strategy": search_strategy,
                "processing_time": processing_time,
                "metadata": {
                    "method": search_strategy["method"],
                    "steps_executed": len(search_strategy.get("steps", []))
                }
            }

        except Exception as e:
            logger.error(f"Erreur lors de la recherche intelligente: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "results": [],
                "processing_time": (datetime.now() - start_time).total_seconds()
            }

    async def _analyze_site_structure(self, site_url: str) -> Dict[str, Any]:
        """
        Analyse la structure du site avec Vision AI pour comprendre comment rechercher.

        Returns:
            Stratégie de recherche avec étapes à suivre
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled']
            )

            context = await browser.new_context(
                user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
                viewport={'width': 1920, 'height': 1080}
            )

            page = await context.new_page()
            await page.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')

            # Naviguer et attendre le chargement
            await page.goto(site_url, timeout=60000)
            await page.wait_for_timeout(10000)  # Cloudflare + JS

            # Prendre un screenshot ET récupérer le HTML
            screenshot_bytes = await page.screenshot(full_page=False)
            html_content = await page.content()

            await browser.close()

        # Analyser avec Vision AI
        logger.info("🤖 Analyse de la page avec Vision AI...")

        # Convertir screenshot en base64 data URL
        import base64
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
        image_data_url = f"data:image/png;base64,{screenshot_b64}"

        analysis_prompt = f"""Analyse cette capture d'écran d'un site web ({site_url}).

Ta mission : Identifier COMMENT effectuer une recherche sur ce site.

Réponds au format JSON UNIQUEMENT :
{{
  "can_search": true/false,
  "reason": "Explication si can_search=false",
  "method": "direct_input" ou "click_then_input" ou "menu_navigation",
  "steps": [
    {{"action": "click", "target": "icône loupe", "css_selectors": ["button.search-icon", "[aria-label='Search']", ".header-search-button"]}},
    {{"action": "fill", "target": "champ de recherche", "css_selectors": ["input[type='search']", "#searchbox", "input.search-input"]}},
    {{"action": "submit", "target": "Enter ou bouton Go", "css_selectors": ["button.search-submit", "Enter"]}}
  ],
  "confidence": 0.95
}}

IMPORTANT pour css_selectors:
- Fournis 3-5 sélecteurs CSS possibles par ordre de probabilité
- Utilise des sélecteurs spécifiques (classes, IDs, attributs aria)
- "Enter" signifie appuyer sur la touche Entrée
- Si champ directement visible, method="direct_input" avec seulement fill+submit

Exemples de méthodes:
- **direct_input**: Champ de recherche directement visible (ex: Google, Wikipedia)
- **click_then_input**: Faut cliquer sur une icône avant (ex: menu hamburger, icône loupe)
- **menu_navigation**: Recherche dans un menu déroulant

Si aucun moyen de rechercher n'est visible, retourne can_search: false."""

        # Appeler Vision AI (meme provider/modele que le client principal -
        # fichier non exercé dans le chemin actif du service actuellement,
        # corrige par cohérence)
        from app.core.llm import LLMFactory
        vision_client = LLMFactory.create(
            provider="openai",
            model=self.llm_client.model,
            api_key=self.llm_client.api_key,
            base_url=self.llm_client.base_url
        )

        analysis = await vision_client.generate_with_vision(
            text=analysis_prompt,
            image_url=image_data_url,
            temperature=0.1,
            max_tokens=1000
        )

        logger.info(f"Vision AI réponse: {analysis[:300]}...")

        # Parser le JSON de Vision AI
        import json
        strategy = None
        try:
            start_idx = analysis.find('{')
            end_idx = analysis.rfind('}')
            if start_idx != -1 and end_idx != -1:
                json_str = analysis[start_idx:end_idx + 1]
                strategy = json.loads(json_str)
                logger.info(f"✓ Stratégie Vision AI: {strategy['method']}")
        except Exception as e:
            logger.error(f"Erreur parsing stratégie Vision AI: {e}")

        if not strategy or not strategy.get("can_search"):
            return {
                "can_search": False,
                "reason": "Impossible d'analyser la structure du site",
                "method": "unknown"
            }

        # HYBRIDE : Enrichir la stratégie avec les vrais sélecteurs du HTML
        logger.info("🔍 Enrichissement avec analyse HTML...")
        strategy = await self._enrich_strategy_with_html(strategy, html_content)

        return strategy

    async def _enrich_strategy_with_html(
        self,
        strategy: Dict[str, Any],
        html_content: str
    ) -> Dict[str, Any]:
        """
        Enrichit la stratégie Vision AI avec les vrais sélecteurs CSS trouvés dans le HTML.
        """
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_content, 'html.parser')

        # Pour chaque étape, trouver les vrais sélecteurs
        for step in strategy.get("steps", []):
            action = step["action"]

            if action == "click":
                # Chercher boutons/liens de recherche
                real_selectors = []

                # Boutons avec aria-label search
                for btn in soup.find_all(['button', 'a'], attrs={'aria-label': True}):
                    if 'search' in btn['aria-label'].lower():
                        if btn.get('id'):
                            real_selectors.append(f"#{btn['id']}")
                        elif btn.get('class'):
                            classes = ' '.join(btn['class'])
                            real_selectors.append(f"button.{btn['class'][0]}")

                # Éléments avec class search
                for elem in soup.find_all(class_=lambda x: x and 'search' in x.lower()):
                    if elem.name in ['button', 'a', 'div']:
                        if elem.get('id'):
                            real_selectors.append(f"#{elem['id']}")
                        elif elem.get('class'):
                            real_selectors.append(f".{elem['class'][0]}")

                if real_selectors:
                    step["css_selectors"] = real_selectors[:5]  # Top 5
                    logger.info(f"  → CLICK: {len(real_selectors)} sélecteurs trouvés")

            elif action == "fill":
                # Chercher inputs de recherche
                real_selectors = []

                # Inputs type search
                for inp in soup.find_all('input', type='search'):
                    if inp.get('id'):
                        real_selectors.append(f"#{inp['id']}")
                    elif inp.get('name'):
                        real_selectors.append(f"input[name='{inp['name']}']")

                # Inputs avec name/id/class contenant search/query
                for inp in soup.find_all('input'):
                    if inp.get('id') and any(k in inp['id'].lower() for k in ['search', 'query', 'q']):
                        real_selectors.append(f"#{inp['id']}")
                    elif inp.get('name') and any(k in inp['name'].lower() for k in ['search', 'query', 'q']):
                        real_selectors.append(f"input[name='{inp['name']}']")
                    elif inp.get('class') and any(k in ' '.join(inp['class']).lower() for k in ['search', 'query']):
                        real_selectors.append(f".{inp['class'][0]}")

                if real_selectors:
                    step["css_selectors"] = real_selectors[:5]
                    logger.info(f"  → FILL: {len(real_selectors)} sélecteurs trouvés")

            elif action == "submit":
                # Chercher boutons submit
                real_selectors = ["Enter"]  # Toujours inclure Enter

                for btn in soup.find_all(['button', 'input'], type='submit'):
                    if btn.get('id'):
                        real_selectors.append(f"#{btn['id']}")
                    elif btn.get('class'):
                        real_selectors.append(f".{btn['class'][0]}")

                step["css_selectors"] = real_selectors[:5]
                logger.info(f"  → SUBMIT: Enter + {len(real_selectors)-1} boutons")

        return strategy

    async def _execute_search(
        self,
        site_url: str,
        search_query: str,
        strategy: Dict[str, Any],
        max_results: int
    ) -> List[Dict[str, Any]]:
        """
        Exécute la recherche selon la stratégie identifiée avec Playwright.
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled']
            )
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
                viewport={'width': 1920, 'height': 1080}
            )
            page = await context.new_page()
            await page.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')

            # Navigation
            await page.goto(site_url, timeout=60000)
            await page.wait_for_timeout(10000)  # Cloudflare + JS

            # Exécuter les étapes selon la stratégie
            for step in strategy.get("steps", []):
                action = step["action"]
                target_desc = step["target"]

                logger.info(f"  → {action.upper()}: {target_desc}")

                if action == "click":
                    # Essayer de trouver l'élément à cliquer avec les sélecteurs Vision AI
                    clicked = await self._smart_click(page, target_desc, step.get("css_selectors", []))
                    if clicked:
                        await page.wait_for_timeout(2000)  # Attendre après le clic

                elif action == "fill":
                    # Trouver et remplir le champ avec les sélecteurs Vision AI
                    filled = await self._smart_fill(page, search_query, target_desc, step.get("css_selectors", []))
                    if filled:
                        await page.wait_for_timeout(500)

                elif action == "submit":
                    # Soumettre (Enter ou clic bouton) avec les sélecteurs Vision AI
                    await self._smart_submit(page, step.get("css_selectors", []))
                    await page.wait_for_timeout(12000)  # Attendre résultats + Cloudflare

            # Extraire les résultats avec le LLM
            html_content = await page.content()
            logger.info(f"📄 HTML récupéré: {len(html_content)} caractères")

            await browser.close()

            # Parser avec LLM
            results = await self._parse_results_with_llm(html_content, search_query, max_results)
            return results

    async def _smart_click(self, page, target_description: str, css_selectors: List[str]) -> bool:
        """Trouve et clique sur un élément basé sur les sélecteurs Vision AI."""
        # Essayer les sélecteurs fournis par Vision AI en premier
        for selector in css_selectors:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    await element.click()
                    logger.info(f"    ✓ Cliqué (Vision AI): {selector}")
                    return True
            except:
                continue

        # Fallback: sélecteurs génériques
        fallback_selectors = [
            'button[aria-label*="search" i]',
            '[class*="search"][role="button"]',
            '.search-toggle',
            '#search-toggle'
        ]

        for selector in fallback_selectors:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    await element.click()
                    logger.info(f"    ✓ Cliqué (fallback): {selector}")
                    return True
            except:
                continue

        logger.warning(f"    ✗ Impossible de cliquer sur: {target_description}")
        return False

    async def _smart_fill(self, page, text: str, target_description: str, css_selectors: List[str]) -> bool:
        """Trouve et remplit un champ de recherche."""
        # Essayer les sélecteurs fournis par Vision AI
        for selector in css_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    # Forcer la visibilité si nécessaire (champ peut être masqué initialement)
                    await element.fill(text, force=True)
                    logger.info(f"    ✓ Rempli (Vision AI): {selector}")
                    return True
            except Exception as e:
                logger.debug(f"Sélecteur {selector} échoué: {e}")
                continue

        # Fallback: sélecteurs génériques
        fallback_selectors = [
            'input[type="search"]',
            '#search',
            '#query',
            '#q',
            'input[name="q"]'
        ]

        for selector in fallback_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    await element.fill(text, force=True)
                    logger.info(f"    ✓ Rempli (fallback): {selector}")
                    return True
            except:
                continue

        logger.warning(f"    ✗ Impossible de remplir: {target_description}")
        return False

    async def _smart_submit(self, page, css_selectors: List[str]):
        """Soumet le formulaire (Enter ou clic bouton)."""
        # Si "Enter" dans les sélecteurs, utiliser Enter
        if "Enter" in css_selectors or "enter" in [s.lower() for s in css_selectors]:
            try:
                await page.keyboard.press("Enter")
                logger.info("    ✓ Soumis via Enter")
                return
            except:
                pass

        # Essayer les sélecteurs fournis par Vision AI
        for selector in css_selectors:
            if selector.lower() == "enter":
                continue
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    await element.click()
                    logger.info(f"    ✓ Cliqué (Vision AI): {selector}")
                    return
            except:
                continue

        # Fallback: Enter ou boutons génériques
        try:
            await page.keyboard.press("Enter")
            logger.info("    ✓ Soumis via Enter (fallback)")
            return
        except:
            pass

        fallback_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Go")',
            'button:has-text("Search")'
        ]

        for selector in fallback_selectors:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    await element.click()
                    logger.info(f"    ✓ Cliqué (fallback): {selector}")
                    return
            except:
                continue

    async def _parse_results_with_llm(
        self,
        html_content: str,
        search_query: str,
        max_results: int
    ) -> List[Dict[str, Any]]:
        """Parse les résultats HTML avec le LLM."""
        from app.core.llm import get_llm_client
        import json

        prompt = f"""Extrais les {max_results} premiers résultats de recherche de ce HTML pour la requête "{search_query}".

Retourne UNIQUEMENT un JSON valide:
{{
  "results": [
    {{"title": "...", "url": "...", "snippet": "..."}}
  ]
}}

Si aucun résultat, retourne {{"results": []}}

HTML (15000 premiers caractères):
{html_content[:15000]}
"""

        fresh_llm = await get_llm_client()
        response = await fresh_llm.generate(
            [{"role": "user", "content": prompt}],
            max_tokens=2000,
            temperature=0.1
        )

        try:
            start_idx = response.find('{')
            end_idx = response.rfind('}')
            if start_idx != -1 and end_idx != -1:
                json_str = response[start_idx:end_idx + 1]
                parsed = json.loads(json_str)
                results = parsed.get("results", [])
                logger.info(f"    ✓ {len(results)} résultats extraits")
                return results
        except Exception as e:
            logger.error(f"Erreur parsing résultats: {e}")

        return []

    def _build_task_from_strategy(
        self,
        site_url: str,
        search_query: str,
        strategy: Dict[str, Any],
        max_results: int
    ) -> str:
        """Construit la tâche pour browser-use basée sur la stratégie Vision."""

        steps_text = "\n".join([
            f"- {i+1}. {step['action'].upper()}: {step['target']} ({step.get('selector_hint', '')})"
            for i, step in enumerate(strategy.get("steps", []))
        ])

        task = f"""Visite {site_url} et effectue une recherche pour "{search_query}".

STRATÉGIE IDENTIFIÉE (par Vision AI):
Méthode: {strategy['method']}

ÉTAPES À SUIVRE:
{steps_text}

Après avoir obtenu les résultats, extrais les {max_results} premiers.

Retourne un JSON:
{{
  "results": [
    {{"title": "...", "url": "...", "snippet": "..."}}
  ]
}}
"""
        return task
