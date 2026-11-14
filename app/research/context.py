"""Contexte d'execution d'UNE requete de recherche approfondie.

Tout l'etat propre a une requete vit ici (langue, contraintes de domaines,
caches, sections redigees, diagnostics) : l'orchestrateur ne porte aucun
attribut ecrit pendant une requete, deux requetes successives ou concurrentes
sur la meme instance n'interferent donc pas.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List

from app.research.diagnostics import Diagnostics
from app.research.pipeline_config import PipelineConfig

logger = logging.getLogger(__name__)


def cosine_similarity(a: list, b: list) -> float:
    """Similarite cosinus entre deux vecteurs d'embedding (0-1)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return max(0.0, dot / (norm_a * norm_b))


def extract_domain(url: str) -> str:
    """Domaine racine d'une URL, pour juger de l'independance de deux sources."""
    from urllib.parse import urlparse
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return url


class ExecutionContext:
    """Contexte d'exécution avec historique et apprentissage."""

    def __init__(self, query: str, config: PipelineConfig | None = None):
        self.query = query
        # Configuration effective et journal des degradations de CETTE requete.
        self.config: PipelineConfig = config if config is not None else PipelineConfig.from_env()
        self.diagnostics: Diagnostics = Diagnostics()
        # Echeance globale de la requete, en temps de la boucle asyncio
        # (loop.time()). Posee par execute_with_deadline ; None hors delai
        # global. La collecte la lit pour borner ses budgets.
        self.deadline_at: float | None = None
        # Langue de recherche et de redaction, decidee par le plan (langue de
        # la consigne). "all" ne restreint rien - c'est le defaut sur, alors
        # que "auto" fait tomber PubMed de 20 a 0 resultats (verifie).
        self.search_language: str = "all"
        # Contraintes de sources de la requete (domains_whitelist et
        # sources_exclusions du contexte de l'appelant), en minuscules. Posees
        # par set_domain_constraints avant l'exploration, lues par le filtre
        # de domaines et par le calcul de la taille du pool de recherche.
        self.domains_whitelist: List[str] = []
        self.domains_exclusions: List[str] = []
        # Sources requises par l'appelant (V4-4) : URL demandees, passages
        # extraits (proposes a toutes les sections), echecs {url, reason}.
        self.required_urls: List[str] = []
        self.required_chunks: List[Dict[str, Any]] = []
        self.required_extracted: List[str] = []
        self.required_failed: List[Dict[str, str]] = []
        self.steps: List[Dict] = []
        self.datasets: Dict[str, List[Dict]] = {}
        self.discovered_sources: List[str] = []
        self.tool_success_rate: Dict[str, Dict] = {}
        # Cache d'extraction partage sur toute la duree d'une requete research_deep :
        # un meme article pertinent pour plusieurs sections (frequent) n'est
        # extrait qu'une seule fois, pas une fois par section qui le reference.
        self.extraction_cache: Dict[str, Any] = {}

        # Cache d'embeddings partage entre le croisement de sources (recherche
        # de corroboration) et la selection semantique de chunks - un meme
        # contenu ne fait jamais l'objet de deux appels d'embedding distincts,
        # peu importe l'ordre d'appel des deux fonctions qui en ont besoin.
        self.embedding_cache: Dict[str, list] = {}

        # Historique de tous les chunks extraits, tous sujets confondus, pour
        # pouvoir chercher des corroborations cross-sections (pas seulement
        # au sein d'une section) : {url, domain, content, embedding}
        self.all_extracted_chunks: List[Dict[str, Any]] = []

        # NOUVEAU: Contenu final structuré avec accumulation par section
        self.final_content: Dict[str, Any] = {
            "sections": {},  # {section_name: {"raw_data": [], "content": "", "metadata": {}}}
            "global_metadata": {
                "sources_used": [],
                "extraction_timestamps": [],
                "all_structured_data": []
            }
        }

    def initialize_sections(self, section_names: List[str]):
        """Initialise les sections du rapport."""
        for section_name in section_names:
            self.final_content["sections"][section_name] = {
                "raw_data": [],
                "content": "",
                "metadata": {"data_count": 0, "sources": []}
            }
        logger.info(f"📋 Sections initialisées: {', '.join(section_names)}")

    def add_step(self, tool: str, action: str, input_data: Any, output_data: Any, success: bool):
        """Enregistre une étape."""
        preview_chars = self.config.step_preview_chars
        input_str = str(input_data)[:preview_chars]
        output_str = str(output_data)[:preview_chars] if output_data else ""

        self.steps.append({
            "step_number": len(self.steps) + 1,
            "tool": tool,
            "action": action,
            "success": success,
            "timestamp": datetime.now().isoformat(),
            "input_preview": input_str,
            "output_preview": output_str
        })

        # Mettre à jour taux de succès
        if tool not in self.tool_success_rate:
            self.tool_success_rate[tool] = {"success": 0, "failures": 0}

        if success:
            self.tool_success_rate[tool]["success"] += 1
        else:
            self.tool_success_rate[tool]["failures"] += 1

    def add_dataset(self, name: str, data: List[Dict]):
        """Ajoute un dataset."""
        self.datasets[name] = data
        logger.info(f"💾 Dataset '{name}' ajouté: {len(data)} items")

    def get_all_data(self) -> List[Dict]:
        """Récupère toutes les données."""
        all_data = []
        for dataset in self.datasets.values():
            all_data.extend(dataset)
        return all_data

    def set_search_language(self, context: Any, lang: str) -> None:
        """Pose la langue de la requete sur ce contexte.

        Le dict `context` de l'appelant la recoit aussi : l'exploration sans
        ExecutionContext la relit de la.
        """
        self.search_language = lang
        if isinstance(context, dict):
            context['search_language'] = lang

    def set_domain_constraints(self, context: Any) -> None:
        """Lit les contraintes de domaines du contexte de l'appelant.

        Elles arrivaient jusqu'ici (sources_exclusions, domains_whitelist sont
        dans le context depuis l'endpoint) mais n'etaient jamais lues : l'API
        documentait une capacite que le pipeline n'honorait pas.
        """
        _ctx = context if isinstance(context, dict) else {}
        self.domains_whitelist = [d.lower().strip() for d in (_ctx.get("domains_whitelist") or []) if d]
        self.domains_exclusions = [d.lower().strip() for d in (_ctx.get("sources_exclusions") or []) if d]
        if self.domains_whitelist:
            logger.info(f"  🔒 Whitelist active : {self.domains_whitelist}")
        if self.domains_exclusions:
            logger.info(f"  🚫 Exclusions actives : {self.domains_exclusions}")
