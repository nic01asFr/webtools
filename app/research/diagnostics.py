"""Journal des degradations d'une requete.

Un repli, une exception avalee ou une donnee ignoree s'y declare par
note(evenement, **details). La reponse dit ainsi ce qui s'est degrade au lieu
de le taire. Un objet par requete (porte par ExecutionContext) : aucun etat
global.
"""

from collections import deque
from typing import Any

MAX_EVENTS = 50
_MAX_DETAIL_CHARS = 300


def _jsonable(value: Any) -> Any:
    """Ramene une valeur de detail a un type serialisable en JSON."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_DETAIL_CHARS else value[:_MAX_DETAIL_CHARS] + "..."
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return _jsonable(repr(value))


class Diagnostics:
    """Compteurs par evenement et liste bornee des derniers evenements."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)

    def note(self, event: str, **details: Any) -> None:
        """Enregistre une occurrence de l'evenement avec ses details."""
        self._counts[event] = self._counts.get(event, 0) + 1
        self._events.append(
            {"event": event, "details": {k: _jsonable(v) for k, v in details.items()}}
        )

    def count(self, event: str) -> int:
        """Nombre d'occurrences de l'evenement (0 s'il n'a jamais eu lieu)."""
        return self._counts.get(event, 0)

    def to_dict(self) -> dict[str, Any]:
        """Forme serialisable : {"counts": {...}, "events": [les 50 derniers]}."""
        return {"counts": dict(self._counts), "events": list(self._events)}
