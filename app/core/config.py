"""
Configuration pour le service WebExtract.
"""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration du service WebExtract."""

    # API Configuration
    api_title: str = "WebTools API"
    api_version: str = "2.0.0"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Default LLM Configuration
    default_llm_provider: str = "openai"
    default_llm_model: str = "gpt-4o"
    default_llm_api_key: str = ""
    default_llm_base_url: Optional[str] = None

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
