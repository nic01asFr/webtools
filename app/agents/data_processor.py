"""
DataProcessor - utilitaires de traitement de datasets (listes de dicts).

Reconstruit a partir de l'usage reel observe dans intelligent_orchestrator.py
(fichier source manquant dans le depot d'origine).
"""

import logging
from typing import Any, Dict, List, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


class DataProcessor:
    """Traitement chainable d'une liste de dicts (filtre, tri)."""

    def __init__(self, data: List[Dict[str, Any]]):
        self._data = list(data or [])

    def filter_by_field(self, **kwargs) -> "DataProcessor":
        """
        Filtre les enregistrements dont les champs correspondent aux kwargs.
        Supporte des suffixes simples : field__gt, field__lt, field__contains.
        """
        if not kwargs:
            return self

        def matches(item: Dict[str, Any]) -> bool:
            for key, expected in kwargs.items():
                if key.endswith("__gt"):
                    field = key[:-4]
                    if not (item.get(field) is not None and item[field] > expected):
                        return False
                elif key.endswith("__lt"):
                    field = key[:-4]
                    if not (item.get(field) is not None and item[field] < expected):
                        return False
                elif key.endswith("__contains"):
                    field = key[:-10]
                    val = item.get(field)
                    if val is None or expected not in val:
                        return False
                else:
                    if item.get(key) != expected:
                        return False
            return True

        self._data = [d for d in self._data if matches(d)]
        return self

    def sort(self, field: str = None, reverse: bool = False, **kwargs) -> "DataProcessor":
        """Trie par champ. Accepte field= ou by= pour flexibilite d'appel."""
        key_field = field or kwargs.get("by")
        if not key_field:
            return self
        self._data = sorted(
            self._data,
            key=lambda d: (d.get(key_field) is None, d.get(key_field)),
            reverse=reverse
        )
        return self

    def get(self) -> List[Dict[str, Any]]:
        return self._data


class DataProcessorFactory:
    """Operations pretes a l'emploi, en fonctions statiques."""

    @staticmethod
    def top_n_by_field(data: List[Dict[str, Any]], field: str, n: int = 10) -> List[Dict[str, Any]]:
        sortable = [d for d in (data or []) if d.get(field) is not None]
        sortable.sort(key=lambda d: d[field], reverse=True)
        return sortable[:n]

    @staticmethod
    def aggregate_by_group(
        data: List[Dict[str, Any]],
        group_by: Optional[str] = None,
        agg_field: Optional[str] = None,
        agg_func: str = "count",
        **kwargs
    ) -> List[Dict[str, Any]]:
        """Agrege par groupe. agg_func : count | sum | avg."""
        if not group_by:
            return data or []

        groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
        for item in (data or []):
            groups[item.get(group_by)].append(item)

        results = []
        for key, items in groups.items():
            entry = {group_by: key, "count": len(items)}
            if agg_field and agg_func in ("sum", "avg"):
                values = [i[agg_field] for i in items if i.get(agg_field) is not None]
                if values:
                    if agg_func == "sum":
                        entry[f"{agg_field}_sum"] = sum(values)
                    elif agg_func == "avg":
                        entry[f"{agg_field}_avg"] = sum(values) / len(values)
            results.append(entry)

        return results
