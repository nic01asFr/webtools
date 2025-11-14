"""
Generic Data Extraction System

Extrait automatiquement des données structurées depuis n'importe quelle source,
indépendamment du domaine (IA, démographie, économie, santé, etc.)
"""

import logging
import re
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class NumericalData:
    """Donnée numérique extraite"""
    metric: str  # Ex: "montant_investissement", "nombre_habitants"
    value: float
    unit: Optional[str] = None  # Ex: "euros", "habitants", "%"
    context: Optional[str] = None  # Contexte dans la phrase
    temporal_marker: Optional[str] = None  # Ex: "2024", "T1 2023"
    source_sentence: Optional[str] = None
    confidence: float = 1.0

    def to_dict(self):
        return asdict(self)


@dataclass
class TemporalData:
    """Donnée temporelle extraite"""
    event: str
    date: str  # Format ISO ou texte
    precision: str  # "year", "month", "day", "quarter"
    context: Optional[str] = None
    confidence: float = 1.0

    def to_dict(self):
        return asdict(self)


@dataclass
class EntityData:
    """Entité nommée extraite"""
    name: str
    type: str  # "organization", "person", "location", "product"
    role: Optional[str] = None  # Rôle dans le contexte
    context: Optional[str] = None
    confidence: float = 1.0

    def to_dict(self):
        return asdict(self)


@dataclass
class RelationshipData:
    """Relation entre entités"""
    entity1: str
    relation: str  # "investit_dans", "dirige", "situé_à"
    entity2: str
    context: Optional[str] = None
    confidence: float = 1.0

    def to_dict(self):
        return asdict(self)


@dataclass
class StructuredData:
    """Container pour toutes les données extraites d'une source"""
    source_url: str
    numerical: List[NumericalData]
    temporal: List[TemporalData]
    entities: List[EntityData]
    relationships: List[RelationshipData]
    extraction_timestamp: str
    overall_confidence: float = 0.0

    def to_dict(self):
        return {
            "source_url": self.source_url,
            "numerical": [n.to_dict() for n in self.numerical],
            "temporal": [t.to_dict() for t in self.temporal],
            "entities": [e.to_dict() for e in self.entities],
            "relationships": [r.to_dict() for r in self.relationships],
            "extraction_timestamp": self.extraction_timestamp,
            "overall_confidence": self.overall_confidence
        }


class GenericDataExtractor:
    """
    Extracteur générique de données structurées.
    S'adapte automatiquement au contenu sans configuration domaine-spécifique.
    """

    def __init__(self, llm_client):
        self.llm_client = llm_client

    async def extract_structured_data(
        self,
        content: str,
        source_url: str,
        topic_context: str = ""
    ) -> StructuredData:
        """
        Extrait toutes les données structurables du contenu.

        Args:
            content: Texte brut à analyser
            source_url: URL source
            topic_context: Contexte du sujet recherché (optionnel)

        Returns:
            StructuredData avec toutes les données extraites
        """

        # Limiter le contenu pour performance
        content_sample = content[:8000] if len(content) > 8000 else content

        prompt = f"""Analyse ce contenu et extrait TOUTES les données structurées pertinentes.

CONTENU:
{content_sample}

CONTEXTE DU SUJET (optionnel): {topic_context if topic_context else "Analyse générique"}

Ta mission: Identifier et extraire automatiquement:

1. **DONNÉES NUMÉRIQUES**: Tout chiffre avec contexte
   - Montants, quantités, pourcentages, ratios
   - Avec unité et période si mentionnée

2. **DONNÉES TEMPORELLES**: Dates, périodes, évolutions
   - Dates précises, années, trimestres
   - Événements datés

3. **ENTITÉS**: Noms propres importants
   - Organisations, personnes, lieux, produits
   - Avec leur rôle dans le contexte

4. **RELATIONS**: Connexions entre entités
   - Qui fait quoi, qui dirige quoi, situé où

Retourne JSON:
{{
  "numerical": [
    {{
      "metric": "nom_métrique_générique",
      "value": 123.45,
      "unit": "unité",
      "context": "phrase source",
      "temporal_marker": "2024",
      "confidence": 0.9
    }}
  ],
  "temporal": [
    {{
      "event": "description événement",
      "date": "2024-01-15",
      "precision": "day|month|year|quarter",
      "context": "phrase source",
      "confidence": 0.9
    }}
  ],
  "entities": [
    {{
      "name": "Nom Entité",
      "type": "organization|person|location|product|other",
      "role": "rôle dans le contexte",
      "context": "phrase source",
      "confidence": 0.8
    }}
  ],
  "relationships": [
    {{
      "entity1": "Entité A",
      "relation": "type_relation",
      "entity2": "Entité B",
      "context": "phrase source",
      "confidence": 0.7
    }}
  ]
}}

RÈGLES CRITIQUES:
- Extraire TOUT chiffre mentionné avec son contexte
- Ne pas inventer de données absentes
- Confidence élevée (>0.8) si explicite, moyenne (<0.8) si déduit
- metric/event/relation en snake_case générique
- Être exhaustif mais précis
"""

        try:
            response = await self.llm_client.generate(
                [{"role": "user", "content": prompt}],
                max_tokens=3000,
                temperature=0.1
            )

            # Parser JSON
            start_idx = response.find('{')
            if start_idx != -1:
                bracket_depth = 0
                for i, char in enumerate(response[start_idx:], start=start_idx):
                    if char == '{':
                        bracket_depth += 1
                    elif char == '}':
                        bracket_depth -= 1
                        if bracket_depth == 0:
                            import json
                            extracted = json.loads(response[start_idx:i + 1])

                            # Construire StructuredData
                            numerical = [
                                NumericalData(**n) for n in extracted.get("numerical", [])
                            ]
                            temporal = [
                                TemporalData(**t) for t in extracted.get("temporal", [])
                            ]
                            entities = [
                                EntityData(**e) for e in extracted.get("entities", [])
                            ]
                            relationships = [
                                RelationshipData(**r) for r in extracted.get("relationships", [])
                            ]

                            # Calculer confiance globale
                            all_confidences = (
                                [n.confidence for n in numerical] +
                                [t.confidence for t in temporal] +
                                [e.confidence for e in entities] +
                                [r.confidence for r in relationships]
                            )
                            overall_confidence = (
                                sum(all_confidences) / len(all_confidences)
                                if all_confidences else 0.0
                            )

                            structured_data = StructuredData(
                                source_url=source_url,
                                numerical=numerical,
                                temporal=temporal,
                                entities=entities,
                                relationships=relationships,
                                extraction_timestamp=datetime.now().isoformat(),
                                overall_confidence=overall_confidence
                            )

                            logger.info(
                                f"✓ Données extraites: {len(numerical)} num, "
                                f"{len(temporal)} temp, {len(entities)} ent, "
                                f"{len(relationships)} rel (conf: {overall_confidence:.2f})"
                            )

                            return structured_data

        except Exception as e:
            logger.error(f"Erreur extraction données structurées: {e}")

        # Fallback: retourner structure vide
        return StructuredData(
            source_url=source_url,
            numerical=[],
            temporal=[],
            entities=[],
            relationships=[],
            extraction_timestamp=datetime.now().isoformat(),
            overall_confidence=0.0
        )


class DataValidator:
    """Valide et compare les données extraites de multiples sources"""

    @staticmethod
    def find_common_metrics(data_list: List[StructuredData]) -> Dict[str, List[NumericalData]]:
        """Trouve les métriques présentes dans plusieurs sources"""

        metrics_by_name = {}

        for data in data_list:
            for num in data.numerical:
                metric_key = num.metric.lower().strip()
                if metric_key not in metrics_by_name:
                    metrics_by_name[metric_key] = []
                metrics_by_name[metric_key].append(num)

        # Garder seulement les métriques présentes dans au moins 2 sources
        common_metrics = {
            k: v for k, v in metrics_by_name.items()
            if len(v) >= 2
        }

        return common_metrics

    @staticmethod
    def validate_numerical_coherence(
        metrics: Dict[str, List[NumericalData]]
    ) -> List[Dict[str, Any]]:
        """Valide la cohérence entre valeurs similaires de différentes sources"""

        validations = []

        for metric_name, values in metrics.items():
            if len(values) < 2:
                continue

            # Calculer variance
            nums = [v.value for v in values]
            mean_val = sum(nums) / len(nums)
            variance = sum((x - mean_val) ** 2 for x in nums) / len(nums)
            std_dev = variance ** 0.5

            # Déterminer cohérence
            coherent = (std_dev / mean_val) < 0.1 if mean_val != 0 else False

            validations.append({
                "metric": metric_name,
                "values": nums,
                "sources": [v.source_sentence for v in values],
                "mean": mean_val,
                "std_dev": std_dev,
                "coherent": coherent,
                "recommended_value": mean_val if coherent else None
            })

        return validations
