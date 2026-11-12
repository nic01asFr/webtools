"""Configuration du pipeline de recherche approfondie.

Un changement de type B (effet sur la qualite non connu d'avance) se pilote
par un champ de PipelineConfig dont la valeur par defaut reproduit le
comportement actuel. Le nom du champ dit ce qu'il active.

Chaque champ se surcharge par la variable d'environnement
WEBTOOLS_PIPELINE_<NOM_DU_CHAMP_EN_MAJUSCULES>. Une variable portant le
prefixe mais ne correspondant a aucun champ est ignoree.
"""

import dataclasses
import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Union

ENV_PREFIX = "WEBTOOLS_PIPELINE_"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "oui"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", "non"})


def _unwrap_optional(tp: Any) -> Any:
    """Retourne T pour Optional[T] ou T | None, sinon le type inchange."""
    origin = typing.get_origin(tp)
    if origin is Union or origin is types.UnionType:
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return tp


def _convert(name: str, raw: str, tp: Any) -> Any:
    """Convertit la valeur texte d'une variable selon le type du champ."""
    target = _unwrap_optional(tp)
    text = raw.strip()
    try:
        if target is bool:
            lowered = text.lower()
            if lowered in _TRUE_VALUES:
                return True
            if lowered in _FALSE_VALUES:
                return False
            raise ValueError("attendu un booleen (1/0, true/false, yes/no, on/off)")
        if target is int:
            return int(text)
        if target is float:
            return float(text)
        if target is str:
            return raw
    except ValueError as exc:
        raise ValueError(
            f"Valeur invalide pour {ENV_PREFIX}{name.upper()}={raw!r} : {exc}"
        ) from exc
    raise ValueError(
        f"Type non pris en charge pour le champ {name!r} : {tp!r} "
        f"(bool, int, float, str et leurs Optional)"
    )


@dataclass(frozen=True)
class PipelineConfig:
    """Drapeaux et parametres du pipeline."""

    # --- Lot V3-5 : enrichissement et coherence ---
    enrichment_enabled: bool = True  # faux : saute l'enrichissement iteratif (C42)
    coherence_append_enabled: bool = True  # faux : ne colle plus les suggestions de coherence (C40)
    regenerate_v2: bool = False  # vrai : regeneration de section avec langue, citations et schema (C43)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "PipelineConfig":
        """Construit la configuration depuis l'environnement.

        environ vaut os.environ quand il est omis. Une valeur impossible a
        convertir leve ValueError avec le nom de la variable en cause.
        """
        if environ is None:
            import os

            environ = os.environ

        hints = typing.get_type_hints(cls)
        values: dict[str, Any] = {}
        for field in dataclasses.fields(cls):
            raw = environ.get(ENV_PREFIX + field.name.upper())
            if raw is None:
                continue
            values[field.name] = _convert(field.name, raw, hints[field.name])
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        """Configuration effective, jointe aux reponses et au manifeste de run."""
        return dataclasses.asdict(self)
