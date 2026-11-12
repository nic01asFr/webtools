"""
Modèles de données pour l'API WebExtract.
"""

from typing import Optional, Dict, Any, Literal, List
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


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

    model_config = ConfigDict(extra="forbid")

    use_agent: bool = Field(
        default=True,
        description="Utiliser l'agent IA ou extraction directe seulement"
    )
    timeout: int = Field(
        default=45,
        ge=5,
        le=300,
        description="Budget global en secondes pour l'extraction, tous paliers confondus"
    )
    try_rss_first: bool = Field(
        default=True,
        description="Tenter le palier RSS/Atom avant le navigateur"
    )
    rss_tier_root_only: bool = Field(
        default=False,
        description="Ne tenter le palier RSS que pour la racine du site ou un flux"
    )
    single_llm_pass_enabled: bool = Field(
        default=True,
        description="Reecrire par un appel LLM unique un contenu direct de 100 a 500 caracteres; "
                    "faux : rendre le texte extrait tel quel"
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

    @field_validator("url")
    @classmethod
    def validate_url_format(cls, v: str) -> str:
        """
        Rejette en amont (422 immediat) toute URL syntaxiquement invalide,
        avant de declencher le pipeline d'extraction (RSS -> direct ->
        LLM -> agent). Sans ce garde-fou, une URL vide ou malformee finit
        par engager l'agent IA complet en pure perte - observe en test :
        cout et temps gaspilles, voire comportement errant de l'agent qui
        tente d'interpreter une chaine non-URL comme une intention de
        recherche.
        """
        v = (v or "").strip()
        if not v:
            raise ValueError("L'URL ne peut pas être vide")
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("L'URL doit commencer par http:// ou https://")
        if len(v) < 12:  # http://a.bc minimum plausible
            raise ValueError("URL trop courte pour être valide")
        return v
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
    searxng_available: Optional[bool] = Field(default=None, description="SearXNG est-il accessible?")
    llm_configured: Optional[bool] = Field(default=None, description="Une cle LLM est-elle configuree?")
    llm_model: Optional[str] = Field(default=None, description="Modele du LLM par defaut configure")
    commit: str = Field(default="unknown", description="Commit git du code charge")
    commit_dirty: bool = Field(default=False, description="Arbre de travail modifie au chargement")
    started_at: str = Field(default="", description="Heure de demarrage du processus (ISO 8601 UTC)")
    dependencies: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Etat detaille de chaque dependance (playwright, searxng, llm, engine_catalog)",
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
        default="unknown",
        description="Modèle utilisé pour l'analyse"
    )
    processing_time_seconds: float = 0.0
    error: Optional[str] = None

    @classmethod
    def from_error(cls, image_url: str, prompt: str, error_message: str, model_used: str = "unknown"):
        """Crée une réponse d'erreur"""
        return cls(
            success=False,
            image_url=image_url,
            prompt=prompt,
            error=error_message,
            model_used=model_used
        )


# ============================================================================
# Modèles pour l'endpoint /search (recherche web simple via SearXNG)
# ============================================================================

class SearchRequest(BaseModel):
    """Requête de recherche web simple."""
    query: str = Field(..., description="Requête de recherche", max_length=500)
    max_results: int = Field(default=10, ge=1, le=50)
    language: str = Field(default="fr")
    categories: Optional[str] = Field(default=None, description="Categories SearXNG (general, images, news...)")
    engines: Optional[str] = Field(default=None, description="Moteurs specifiques (google, bing...)")


class SimpleSearchResult(BaseModel):
    """Un resultat de recherche simple."""
    title: str
    url: str
    snippet: str = ""


class SearchResponse(BaseModel):
    """Reponse de recherche web simple."""
    query: str
    results: List[SimpleSearchResult] = Field(default_factory=list)
    total: int = 0
    success: bool = True
    error: Optional[str] = None

    @classmethod
    def from_error(cls, query: str, error_message: str):
        return cls(query=query, results=[], total=0, success=False, error=error_message)


# ============================================================================
# Modèles pour l'endpoint /research/quick (reponse rapide documentee)
# ============================================================================

class QuickResearchRequest(BaseModel):
    """Requete de recherche rapide documentee."""
    query: str = Field(..., description="Question factuelle", min_length=5, max_length=2000)
    max_sources: int = Field(default=5, ge=1, le=15)


class QuickResearchSource(BaseModel):
    """Une source citee dans la reponse rapide."""
    title: str
    url: str
    relevance: float = 0.0


class QuickResearchResponse(BaseModel):
    """Reponse de recherche rapide documentee."""
    query: str
    answer: Optional[str] = None
    sources: List[QuickResearchSource] = Field(default_factory=list)
    confidence: str = Field(default="medium", description="high | medium | low")
    success: bool = True
    error: Optional[str] = None

    @classmethod
    def from_error(cls, query: str, error_message: str):
        return cls(query=query, answer=None, sources=[], confidence="low", success=False, error=error_message)
