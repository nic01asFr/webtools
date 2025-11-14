"""
Modèles de données pour l'API WebExtract.
"""

from typing import Optional, Dict, Any, Literal, List
from pydantic import BaseModel, Field, HttpUrl


class LLMConfig(BaseModel):
    """Configuration du LLM à utiliser pour l'extraction."""

    provider: Literal["openai", "anthropic", "albert"] = Field(
        default="openai",
        description="Provider LLM à utiliser"
    )
    api_key: str = Field(
        description="Clé API pour le provider LLM"
    )
    model: str = Field(
        default="gpt-4o",
        description="Nom du modèle à utiliser"
    )
    base_url: Optional[str] = Field(
        default=None,
        description="URL de base pour l'API (optionnel)"
    )


class ExtractionOptions(BaseModel):
    """Options pour l'extraction."""

    use_agent: bool = Field(
        default=True,
        description="Utiliser l'agent IA ou extraction directe seulement"
    )
    timeout: int = Field(
        default=45,
        ge=5,
        le=300,
        description="Timeout en secondes pour l'extraction"
    )
    headless: bool = Field(
        default=True,
        description="Utiliser le navigateur en mode headless"
    )
    enable_ocr: bool = Field(
        default=False,
        description="Activer l'extraction OCR pour les images"
    )


class ExtractRequest(BaseModel):
    """Requête d'extraction de contenu web."""

    url: str = Field(
        description="URL de la page à extraire"
    )
    prompt: Optional[str] = Field(
        default=None,
        description="Prompt personnalisé pour l'extraction (optionnel)"
    )
    extraction_type: Literal["general", "article", "product", "repository", "documentation"] = Field(
        default="general",
        description="Type d'extraction à effectuer"
    )
    llm_config: Optional[LLMConfig] = Field(
        default=None,
        description="Configuration du LLM (optionnel, utilise la config par défaut si non fourni)"
    )
    options: Optional[ExtractionOptions] = Field(
        default=None,
        description="Options d'extraction (optionnel)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "url": "https://example.com/article",
                "prompt": "Extrait le contenu principal de cet article",
                "extraction_type": "article",
                "llm_config": {
                    "provider": "openai",
                    "api_key": "sk-...",
                    "model": "gpt-4o"
                },
                "options": {
                    "use_agent": True,
                    "timeout": 45,
                    "headless": True,
                    "enable_ocr": False
                }
            }
        }


class WebResult(BaseModel):
    """Résultat d'une extraction de contenu web."""

    success: bool = Field(
        description="Indique si l'extraction a réussi"
    )
    url: str = Field(
        description="URL de la page extraite"
    )
    content_type: Optional[str] = Field(
        default=None,
        description="Type de contenu détecté"
    )
    title: Optional[str] = Field(
        default=None,
        description="Titre de la page"
    )
    content: Optional[str] = Field(
        default=None,
        description="Contenu extrait"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Métadonnées additionnelles"
    )
    error: Optional[str] = Field(
        default=None,
        description="Message d'erreur si l'extraction a échoué"
    )

    @classmethod
    def from_error(cls, url: str, error_message: str) -> "WebResult":
        """Crée un résultat d'erreur."""
        return cls(
            success=False,
            url=url,
            error=error_message
        )

    @classmethod
    def from_success(
        cls,
        url: str,
        content_type: str,
        title: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> "WebResult":
        """Crée un résultat de succès."""
        return cls(
            success=True,
            url=url,
            content_type=content_type,
            title=title,
            content=content,
            metadata=metadata or {}
        )


class ExtractResponse(BaseModel):
    """Réponse de l'API d'extraction."""

    success: bool
    url: str
    content_type: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None

    @classmethod
    def from_web_result(cls, result: WebResult) -> "ExtractResponse":
        """Convertit un WebResult en ExtractResponse."""
        return cls(
            success=result.success,
            url=result.url,
            content_type=result.content_type,
            title=result.title,
            content=result.content,
            metadata=result.metadata,
            error=result.error
        )

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "url": "https://example.com/article",
                "content_type": "article",
                "title": "Titre de l'article",
                "content": "Contenu extrait de l'article...",
                "metadata": {
                    "extraction_method": "agent",
                    "extraction_duration_ms": 3500,
                    "content_length": 5000
                },
                "error": None
            }
        }


class HealthResponse(BaseModel):
    """Réponse du endpoint de santé."""

    status: str = Field(description="Statut du service")
    version: str = Field(description="Version du service")
    playwright_available: bool = Field(description="Playwright est-il disponible?")


# ============================================================================
# Modèles pour l'endpoint de recherche profonde (Deep Research)
# ============================================================================


class ResearchRequest(BaseModel):
    """Requête de recherche profonde"""

    query: str = Field(
        ...,
        description="Requête utilisateur (ex: 'Comment configurer Traefik avec Docker ?')",
        min_length=5,
        max_length=500
    )

    start_urls: Optional[List[HttpUrl]] = Field(
        default=None,
        description="URLs de départ (optionnel, sinon recherche via SearXNG)"
    )

    max_depth: int = Field(
        default=2,
        ge=1,
        le=3,
        description="Profondeur maximale de navigation (1-3)"
    )

    max_sources: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Nombre maximum de sources à analyser"
    )

    language: str = Field(
        default="fr",
        description="Langue pour la recherche et la synthèse"
    )

    extract_links: bool = Field(
        default=True,
        description="Extraire et suivre les liens pertinents"
    )


class SourceInfo(BaseModel):
    """Informations sur une source utilisée"""

    url: str = Field(..., description="URL de la source")
    title: str = Field(..., description="Titre de la page")
    excerpt: str = Field(..., description="Extrait pertinent")
    relevance_score: float = Field(..., ge=0.0, le=1.0, description="Score de pertinence")
    depth: int = Field(..., description="Profondeur de navigation")
    visited_at: str = Field(..., description="Timestamp de visite")


class NavigationStep(BaseModel):
    """Étape de navigation"""

    from_url: str
    to_url: str
    reason: str = Field(..., description="Raison de la navigation")
    links_found: int = 0


class ResearchResponse(BaseModel):
    """Réponse de recherche profonde"""

    success: bool
    query: str

    answer: Optional[str] = Field(
        None,
        description="Réponse synthétisée par le LLM"
    )

    sources: List[SourceInfo] = Field(
        default_factory=list,
        description="Liste des sources utilisées"
    )

    navigation_path: List[NavigationStep] = Field(
        default_factory=list,
        description="Chemin de navigation suivi"
    )

    total_pages_visited: int = 0
    total_links_analyzed: int = 0

    processing_time_seconds: float = 0.0

    error: Optional[str] = None

    metadata: dict = Field(
        default_factory=dict,
        description="Métadonnées additionnelles"
    )

    @classmethod
    def from_error(cls, query: str, error_message: str):
        """Crée une réponse d'erreur"""
        return cls(
            success=False,
            query=query,
            error=error_message
        )


# ============================================================================
# Modèles pour l'endpoint d'analyse d'images (Vision)
# ============================================================================


class VisionRequest(BaseModel):
    """Requête d'analyse d'image avec Albert Vision"""

    image_url: HttpUrl = Field(
        ...,
        description="URL de l'image à analyser"
    )

    prompt: str = Field(
        ...,
        description="Question ou instruction concernant l'image",
        min_length=5,
        max_length=1000
    )

    system_prompt: Optional[str] = Field(
        default=None,
        description="Prompt système optionnel pour guider l'analyse"
    )

    temperature: float = Field(
        default=0.15,
        ge=0.0,
        le=2.0,
        description="Température pour la génération (0.0 = déterministe)"
    )

    max_tokens: int = Field(
        default=500,
        ge=50,
        le=4096,
        description="Nombre maximum de tokens dans la réponse"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "image_url": "https://example.com/image.png",
                "prompt": "Décris cette image en détail",
                "system_prompt": "Tu es un assistant spécialisé en analyse d'images.",
                "temperature": 0.15,
                "max_tokens": 500
            }
        }


class VisionResponse(BaseModel):
    """Réponse d'analyse d'image"""

    success: bool
    image_url: str
    prompt: str
    analysis: Optional[str] = Field(
        None,
        description="Analyse de l'image par Albert"
    )
    model_used: str = Field(
        default="albert-large",
        description="Modèle utilisé pour l'analyse"
    )
    processing_time_seconds: float = 0.0
    error: Optional[str] = None

    @classmethod
    def from_error(cls, image_url: str, prompt: str, error_message: str):
        """Crée une réponse d'erreur"""
        return cls(
            success=False,
            image_url=image_url,
            prompt=prompt,
            error=error_message,
            model_used="albert-large"
        )


# ============================================================================
# Modèles pour l'endpoint de recherche interactive sur site (Site Search)
# ============================================================================


class SearchSiteRequest(BaseModel):
    """Requête de recherche interactive sur un site web"""

    site_url: HttpUrl = Field(
        ...,
        description="URL du site web à explorer (page d'accueil ou page de recherche)"
    )

    search_query: str = Field(
        ...,
        description="Requête de recherche à saisir dans le formulaire du site",
        min_length=2,
        max_length=200
    )

    max_results: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Nombre maximum de résultats à extraire"
    )

    timeout: int = Field(
        default=60,
        ge=10,
        le=300,
        description="Timeout en secondes pour l'opération complète"
    )

    extract_full_content: bool = Field(
        default=False,
        description="Extraire le contenu complet des résultats (plus lent)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "site_url": "https://www.legifrance.gouv.fr/",
                "search_query": "droit du travail congés payés",
                "max_results": 10,
                "timeout": 60,
                "extract_full_content": False
            }
        }


class SearchResult(BaseModel):
    """Un résultat de recherche individuel"""

    title: str = Field(..., description="Titre du résultat")
    url: str = Field(..., description="URL du résultat")
    snippet: str = Field(..., description="Extrait/description du résultat")
    full_content: Optional[str] = Field(
        None,
        description="Contenu complet si extract_full_content=True"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Métadonnées additionnelles (date, auteur, etc.)"
    )


class SearchSiteResponse(BaseModel):
    """Réponse de recherche interactive sur site"""

    success: bool
    site_url: str
    search_query: str

    results: List[SearchResult] = Field(
        default_factory=list,
        description="Liste des résultats de recherche extraits"
    )

    total_results_found: int = Field(
        default=0,
        description="Nombre total de résultats trouvés (peut être > len(results))"
    )

    search_form_detected: bool = Field(
        default=False,
        description="Indique si un formulaire de recherche a été détecté"
    )

    search_performed: bool = Field(
        default=False,
        description="Indique si la recherche a été effectuée avec succès"
    )

    processing_time_seconds: float = 0.0
    error: Optional[str] = None

    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Métadonnées sur le processus de recherche"
    )

    @classmethod
    def from_error(cls, site_url: str, search_query: str, error_message: str):
        """Crée une réponse d'erreur"""
        return cls(
            success=False,
            site_url=site_url,
            search_query=search_query,
            error=error_message
        )
