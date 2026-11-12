"""
Configuration pour le service WebExtract.
"""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration du service WebExtract."""

    # API Configuration
    api_title: str = "WebTools API"
    api_version: str = "3.0.0"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Default LLM Configuration : source unique des valeurs par defaut du
    # fournisseur, du modele et de l'URL (LLM compatible OpenAI du SSPCloud,
    # celui du deploiement reel). Le code, .env.example, start_all.sh et
    # docker-compose.yml reprennent ces valeurs ; ne pas les redefinir ailleurs.
    default_llm_provider: str = "openai"
    default_llm_model: str = "qwen3-6-35b-moe"
    default_llm_api_key: str = ""
    default_llm_base_url: Optional[str] = "https://llm.lab.sspcloud.fr/api"

    # SearXNG : URL de l'instance de recherche. Defaut adapte a
    # docker-compose (service "searxng" sur le reseau interne) ; start_all.sh
    # la surcharge pour une instance locale. Lire via get_searxng_base_url().
    searxng_base_url: str = "http://searxng:8080"

    # Provider-specific configurations
    # Albert
    albert_api_url: str = "https://albert.api.etalab.gouv.fr"
    albert_api_key: str = ""
    albert_model: str = "AgentPublic/llama3-instruct-8b"

    # OpenAI
    openai_api_key: str = ""

    # Anthropic
    anthropic_api_key: str = ""

    # Playwright Configuration
    playwright_headless: bool = True
    playwright_timeout: int = 45000  # milliseconds

    # Extraction Configuration
    default_extraction_timeout: int = 45  # seconds
    max_retries: int = 3
    enable_caching: bool = True
    cache_ttl_seconds: int = 3600

    # Logging
    log_level: str = "INFO"

    # Performance
    # 3 et non 10 : chaque section peut lancer jusqu'a 5 extractions, dont
    # certaines via un navigateur Chromium complet. Au-dela, la memoire du
    # pod sature. Valeur validee en conditions reelles.
    max_concurrent_extractions: int = 3

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )


# Instance globale de configuration
settings = Settings()


def get_searxng_base_url() -> str:
    """URL de base de SearXNG, sans barre finale."""
    return settings.searxng_base_url.rstrip("/")
