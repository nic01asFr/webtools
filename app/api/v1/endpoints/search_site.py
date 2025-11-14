"""
Endpoint pour la recherche interactive sur les sites web.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.api.models import (
    SearchSiteRequest,
    SearchSiteResponse,
    SearchResult
)
from app.core.llm import get_llm_client
from app.agents.site_search_agent import InteractiveSiteAgent

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/search-site", response_model=SearchSiteResponse)
async def search_site(request: SearchSiteRequest):
    """
    🔍 **Recherche Interactive sur Site Web**

    Effectue une recherche interactive sur un site web en:
    1. Détectant le formulaire de recherche
    2. Remplissant le champ avec votre requête
    3. Soumettant le formulaire
    4. Extrayant les résultats affichés

    ---

    ## 📝 Paramètres

    - **site_url** (obligatoire): URL du site à explorer
      - Exemples: `https://www.legifrance.gouv.fr/`, `https://docs.python.org/`

    - **search_query** (obligatoire): Requête de recherche à saisir
      - Longueur: 2-200 caractères
      - Exemple: `"droit du travail congés payés"`

    - **max_results** (optionnel, défaut: 10): Nombre de résultats à extraire
      - Plage: 1-50 résultats

    - **timeout** (optionnel, défaut: 60s): Durée maximale de l'opération
      - Plage: 10-300 secondes

    - **extract_full_content** (optionnel, défaut: false): Extraire le contenu complet
      - ⚠️ Plus lent, visite chaque page de résultat

    ---

    ## 💡 Cas d'Usage

    ### 1. 🏛️ **Recherche Juridique**
    ```json
    {
      "site_url": "https://www.legifrance.gouv.fr/",
      "search_query": "licenciement économique"
    }
    ```

    ### 2. 📚 **Documentation Technique**
    ```json
    {
      "site_url": "https://docs.python.org/3/",
      "search_query": "asyncio task"
    }
    ```

    ### 3. 🏥 **Bases de Données Spécialisées**
    ```json
    {
      "site_url": "https://pubmed.ncbi.nlm.nih.gov/",
      "search_query": "covid-19 vaccine efficacy",
      "max_results": 20
    }
    ```

    ### 4. 📰 **Archives de Presse**
    ```json
    {
      "site_url": "https://www.lemonde.fr/",
      "search_query": "intelligence artificielle france"
    }
    ```

    ### 5. 🛍️ **Sites E-commerce**
    ```json
    {
      "site_url": "https://www.example-shop.com/",
      "search_query": "laptop 15 inch",
      "max_results": 15
    }
    ```

    ---

    ## 📊 Exemple de Requête

    ```bash
    curl -X POST "https://webtools.colaig.fr/api/v1/search-site" \\
      -H "Content-Type: application/json" \\
      -d '{
        "site_url": "https://www.legifrance.gouv.fr/",
        "search_query": "droit du travail congés payés",
        "max_results": 10,
        "timeout": 60
      }'
    ```

    ---

    ## 📤 Exemple de Réponse

    ```json
    {
      "success": true,
      "site_url": "https://www.legifrance.gouv.fr/",
      "search_query": "droit du travail congés payés",
      "results": [
        {
          "title": "Article L3141-1 - Code du travail",
          "url": "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000033020517",
          "snippet": "Tout salarié a droit chaque année à un congé payé à la charge de l'employeur...",
          "metadata": {
            "date": "2023-08-01",
            "source": "Code du travail",
            "section": "Partie législative"
          }
        },
        {
          "title": "Congés payés : durée et calcul - Service-Public.fr",
          "url": "https://www.service-public.fr/particuliers/vosdroits/F2258",
          "snippet": "La durée des congés payés dépend du temps de travail effectué...",
          "metadata": {
            "type": "Fiche pratique"
          }
        }
      ],
      "total_results_found": 47,
      "search_form_detected": true,
      "search_performed": true,
      "processing_time_seconds": 12.5,
      "metadata": {
        "agent_steps": 8,
        "extraction_method": "browser-use + albert-code"
      }
    }
    ```

    ---

    ## ⚡ Performances

    | Opération | Temps Moyen | Notes |
    |-----------|-------------|-------|
    | Détection formulaire | 2-3s | Analyse de la page |
    | Remplissage + Soumission | 1-2s | Interaction avec le DOM |
    | Attente résultats | 3-5s | Dépend du site |
    | Extraction résultats | 5-10s | Parsing avec LLM |
    | **Total (sans full_content)** | **10-20s** | ~10 résultats |
    | **Total (avec full_content)** | **30-90s** | Visite chaque page |

    ---

    ## ✅ Garanties

    - ✅ **Extraction Réelle**: Interagit réellement avec le site (pas d'API)
    - ✅ **Formulaires Complexes**: Gère JavaScript, AJAX, redirections
    - ✅ **Sites Protégés**: Compatible Cloudflare, reCAPTCHA (certains cas)
    - ✅ **Résultats Exacts**: Extrait uniquement ce qui est affiché

    ---

    ## ⚠️ Limitations

    - ❌ **Sites avec CAPTCHA avancé**: Peut échouer (ex: Google)
    - ❌ **Authentification requise**: Ne gère pas les connexions
    - ❌ **Formulaires très complexes**: Filtres multiples, menus cascades
    - ⏱️ **Performance**: Plus lent qu'une API directe

    ---

    ## 🔧 Technologies

    - **browser-use**: Automation navigateur avec LLM
    - **albert-code**: Détection et parsing intelligent
    - **Playwright**: Interaction bas-niveau avec le DOM

    ---

    **🚀 Propulsé par Albert API 🇫🇷**
    """
    start_time = datetime.now()

    try:
        # Obtenir le client LLM (albert-code par défaut)
        llm_client = await get_llm_client()

        # Créer l'agent de recherche interactive
        agent = InteractiveSiteAgent(
            llm_client=llm_client,
            timeout=request.timeout,
            headless=True
        )

        # Effectuer la recherche
        result = await agent.search_site(
            site_url=str(request.site_url),
            search_query=request.search_query,
            max_results=request.max_results,
            extract_full_content=request.extract_full_content
        )

        # Si l'agent a retourné une erreur
        if not result["success"]:
            return SearchSiteResponse.from_error(
                site_url=str(request.site_url),
                search_query=request.search_query,
                error_message=result.get("error", "Erreur inconnue")
            )

        # Convertir les résultats en modèles Pydantic
        search_results = [
            SearchResult(
                title=r.get("title", "Sans titre"),
                url=r.get("url", ""),
                snippet=r.get("snippet", ""),
                full_content=r.get("full_content"),
                metadata=r.get("metadata", {})
            )
            for r in result.get("results", [])
        ]

        # Calculer le temps de traitement
        processing_time = (datetime.now() - start_time).total_seconds()

        return SearchSiteResponse(
            success=True,
            site_url=str(request.site_url),
            search_query=request.search_query,
            results=search_results,
            total_results_found=result.get("total_results_found", len(search_results)),
            search_form_detected=result.get("search_form_detected", True),
            search_performed=result.get("search_performed", True),
            processing_time_seconds=processing_time,
            metadata=result.get("metadata", {})
        )

    except Exception as e:
        logger.error(f"Erreur lors de la recherche interactive: {e}", exc_info=True)
        processing_time = (datetime.now() - start_time).total_seconds()

        return SearchSiteResponse.from_error(
            site_url=str(request.site_url),
            search_query=request.search_query,
            error_message=f"Erreur: {str(e)}"
        )
