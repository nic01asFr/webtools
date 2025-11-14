"""
SourceRegistry - Registre centralisé de toutes les sources consultées

Enregistre APIs, sites web, datasets avec statut, métadonnées et résultats.
"""

import logging
from typing import Dict, Any, List, Optional
from enum import Enum
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


class SourceType(str, Enum):
    """Types de sources"""
    API_REST = "api_rest"
    API_GRAPHQL = "api_graphql"
    WEBSITE = "website"
    DATASET = "dataset"
    SEARCH_ENGINE = "search_engine"


class SourceStatus(str, Enum):
    """Status d'utilisation d'une source"""
    SUCCESS = "success"
    FAILED = "failed"
    DISCOVERED_NOT_USED = "discovered_not_used"
    AUTHENTICATION_REQUIRED = "authentication_required"
    TIMEOUT = "timeout"
    PARTIAL = "partial"


class SourcePriority(str, Enum):
    """Priorité d'une source"""
    REQUIRED = "required"
    SUGGESTED = "suggested"
    PROVIDED = "provided"
    AUTO_DISCOVERED = "auto_discovered"
    COMPLEMENTARY = "complementary"


@dataclass
class APISource:
    """Source API"""
    url: str
    status: str  # SourceStatus
    type: str = "REST API"
    priority: Optional[str] = None  # SourcePriority
    documentation: Optional[str] = None
    authentication_required: bool = False
    endpoints_used: List[str] = field(default_factory=list)
    records_retrieved: int = 0
    data_sample: Optional[Dict[str, Any]] = None
    quality: Optional[str] = None
    relevance: Optional[float] = None
    reason: Optional[str] = None  # Si failed ou not used
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None and v != [] and v != 0}


@dataclass
class WebsiteSource:
    """Source website"""
    url: str
    status: str  # SourceStatus
    priority: Optional[str] = None  # SourcePriority
    extraction_method: Optional[str] = None  # playwright, http, agent
    content_length: int = 0
    links_extracted: int = 0
    citation: Optional[str] = None
    relevance: Optional[float] = None
    reason: Optional[str] = None  # Si failed
    fallback: Optional[str] = None  # snippet_only, etc.

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None and v != 0}


@dataclass
class DatasetSource:
    """Source dataset"""
    url: str
    status: str  # SourceStatus
    format: str  # CSV, JSON, etc.
    records: int = 0
    processing: List[str] = field(default_factory=list)
    final_records: Optional[int] = None
    columns: Optional[List[str]] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None and v != [] and v != 0}


@dataclass
class SearchEngineSource:
    """Source search engine"""
    engines: List[str]
    query: str
    results_found: int
    deduplication: Optional[Dict[str, int]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


class SourceRegistry:
    """
    Registre centralisé de toutes les sources

    Usage:
        registry = SourceRegistry()

        # Enregistrer une API
        registry.add_api(
            url="https://api.example.com",
            status=SourceStatus.SUCCESS,
            endpoints_used=["/data"],
            records_retrieved=100
        )

        # Obtenir toutes les sources
        sources = registry.get_all_sources()
    """

    def __init__(self):
        self.apis: List[APISource] = []
        self.websites: List[WebsiteSource] = []
        self.datasets: List[DatasetSource] = []
        self.search_engines: List[SearchEngineSource] = []

    def add_api(
        self,
        url: str,
        status: SourceStatus,
        priority: Optional[SourcePriority] = None,
        **kwargs
    ):
        """Ajouter une source API"""
        api_source = APISource(
            url=url,
            status=status.value if isinstance(status, SourceStatus) else status,
            priority=priority.value if isinstance(priority, SourcePriority) else priority,
            **kwargs
        )
        self.apis.append(api_source)
        logger.debug(f"[SourceRegistry] Added API: {url} ({status})")

    def add_website(
        self,
        url: str,
        status: SourceStatus,
        priority: Optional[SourcePriority] = None,
        **kwargs
    ):
        """Ajouter une source website"""
        website_source = WebsiteSource(
            url=url,
            status=status.value if isinstance(status, SourceStatus) else status,
            priority=priority.value if isinstance(priority, SourcePriority) else priority,
            **kwargs
        )
        self.websites.append(website_source)
        logger.debug(f"[SourceRegistry] Added Website: {url} ({status})")

    def add_dataset(
        self,
        url: str,
        status: SourceStatus,
        format: str,
        **kwargs
    ):
        """Ajouter une source dataset"""
        dataset_source = DatasetSource(
            url=url,
            status=status.value if isinstance(status, SourceStatus) else status,
            format=format,
            **kwargs
        )
        self.datasets.append(dataset_source)
        logger.debug(f"[SourceRegistry] Added Dataset: {url} ({status})")

    def add_search_engine(
        self,
        engines: List[str],
        query: str,
        results_found: int,
        **kwargs
    ):
        """Ajouter une source search engine"""
        search_source = SearchEngineSource(
            engines=engines,
            query=query,
            results_found=results_found,
            **kwargs
        )
        self.search_engines.append(search_source)
        logger.debug(f"[SourceRegistry] Added Search Engines: {engines}")

    def update_source_status(self, url: str, new_status: SourceStatus):
        """Mettre à jour le statut d'une source existante"""
        # Chercher dans les websites
        for website in self.websites:
            if website.url == url:
                website.status = new_status.value if isinstance(new_status, SourceStatus) else new_status
                logger.debug(f"[SourceRegistry] Updated Website status: {url} -> {new_status}")
                return True

        # Chercher dans les APIs
        for api in self.apis:
            if api.url == url:
                api.status = new_status.value if isinstance(new_status, SourceStatus) else new_status
                logger.debug(f"[SourceRegistry] Updated API status: {url} -> {new_status}")
                return True

        # Chercher dans les datasets
        for dataset in self.datasets:
            if dataset.url == url:
                dataset.status = new_status.value if isinstance(new_status, SourceStatus) else new_status
                logger.debug(f"[SourceRegistry] Updated Dataset status: {url} -> {new_status}")
                return True

        return False

    def get_all_sources(self) -> Dict[str, Any]:
        """
        Obtenir toutes les sources formatées

        Returns:
            Dict avec apis, websites, datasets, search_engines
        """
        result = {}

        if self.apis:
            result["apis"] = [api.to_dict() for api in self.apis]

        if self.websites:
            result["websites"] = [website.to_dict() for website in self.websites]

        if self.datasets:
            result["datasets"] = [dataset.to_dict() for dataset in self.datasets]

        if self.search_engines:
            result["search_engines"] = [engine.to_dict() for engine in self.search_engines]

        return result

    def get_successful_apis(self) -> List[APISource]:
        """Obtenir uniquement les APIs réussies"""
        return [api for api in self.apis if api.status == SourceStatus.SUCCESS.value]

    def get_failed_sources(self) -> Dict[str, List]:
        """Obtenir toutes les sources ayant échoué"""
        return {
            "apis": [api.to_dict() for api in self.apis if api.status == SourceStatus.FAILED.value],
            "websites": [ws.to_dict() for ws in self.websites if ws.status == SourceStatus.FAILED.value]
        }

    def get_statistics(self) -> Dict[str, Any]:
        """Obtenir des statistiques sur les sources"""
        return {
            "total_apis": len(self.apis),
            "apis_successful": len([a for a in self.apis if a.status == SourceStatus.SUCCESS.value]),
            "apis_failed": len([a for a in self.apis if a.status == SourceStatus.FAILED.value]),
            "total_websites": len(self.websites),
            "websites_successful": len([w for w in self.websites if w.status == SourceStatus.SUCCESS.value]),
            "websites_failed": len([w for w in self.websites if w.status == SourceStatus.FAILED.value]),
            "total_datasets": len(self.datasets),
            "total_records_retrieved": sum(api.records_retrieved for api in self.apis) + sum(ds.records for ds in self.datasets)
        }

    def has_required_sources_succeeded(self) -> bool:
        """Vérifier si toutes les sources requises ont réussi"""
        required_apis = [api for api in self.apis if api.priority == SourcePriority.REQUIRED.value]
        if not required_apis:
            return True
        return all(api.status == SourceStatus.SUCCESS.value for api in required_apis)
