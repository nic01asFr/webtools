"""Modeles des donnees echangees entre les etapes du pipeline.

Les etapes manipulent encore les dict historiques en interne (changer leur
forme changerait le comportement : prompts, ordre des cles, valeurs
absentes). Ces modeles decrivent la forme de ces dict a la FRONTIERE de
chaque etape :

    Plan            sortie de l'etape plan (stages.plan)
    SectionTarget   cible d'une section dans le plan
    Source          resultat de recherche decouvert (exploration, recherche)
    Passage         fragment de contenu extrait d'une source (stages.extract,
                    stages.search, stages.crossref, stages.select)
    SectionDraft    section redigee, telle que stockee par le contexte
    Report          rapport assemble (stages.assemble) et ses sections

Tous acceptent des champs supplementaires (extra="allow") et n'imposent que
ce que les etapes lisent. Conversion :

    Plan.from_dict(d)       valide un dict ; leve ValidationError si la
                            forme est incompatible
    modele.to_dict()        rend le dict d'origine, champs supplementaires
                            compris, sans ajouter de champ non pose

Le round-trip dict -> modele -> dict est exact (test_v4_2_models).
"""

from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

M = TypeVar("M", bound="_Model")


class _Model(BaseModel):
    """Base : champs supplementaires conserves, aucune valeur par defaut ajoutee en sortie."""

    model_config = ConfigDict(extra="allow")

    @classmethod
    def from_dict(cls: Type[M], data: Dict[str, Any]) -> M:
        return cls.model_validate(data)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class SectionTarget(_Model):
    """Cible d'une section : volume, profondeur, objectifs, questions, termes de recherche."""

    words_target: Optional[float] = None
    depth: Optional[str] = None
    objectives: List[str] = []
    key_questions: List[str] = []
    search_terms: List[str] = []
    source_axis: Optional[str] = None


class Plan(_Model):
    """Plan detaille : sections ordonnees, cibles par section, enchainements, strategie."""

    complexity_analysis: Dict[str, Any] = {}
    sections: List[str] = []
    section_targets: Dict[str, SectionTarget] = {}
    narrative_flow: List[Dict[str, Any]] = []
    search_strategy: Dict[str, Any] = {}
    # Poses par le plan generique de repli : le rapport est alors degraded.
    fallback: bool = False
    fallback_reason: Optional[str] = None


class Source(_Model):
    """Resultat de recherche : URL, titre et extrait."""

    url: str
    title: str = ""
    snippet: str = ""


class Passage(_Model):
    """Fragment de contenu extrait d'une source (un chunk)."""

    url: Optional[str] = None
    source: Optional[str] = None
    title: str = ""
    content: str = ""
    metadata: Dict[str, Any] = {}
    structured_data: Optional[Dict[str, Any]] = None
    corroboration_count: int = 0


class SectionDraft(_Model):
    """Section redigee telle que stockee dans ExecutionContext.final_content["sections"]."""

    raw_data: List[Passage] = []
    content: str = ""
    metadata: Dict[str, Any] = {}


class ReportSection(_Model):
    """Section d'un rapport assemble."""

    title: str
    content: str = ""
    data: List[Passage] = []
    metadata: Dict[str, Any] = {}


class Report(_Model):
    """Rapport assemble : titre, resume, sections, bibliographie, metadonnees."""

    type: str = "report"
    title: str = ""
    summary: str = ""
    sections: List[ReportSection] = []
    bibliography: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {}
    degraded: bool = False


def check_boundary(model: Type[M], data: Any, diagnostics: Any, stage: str) -> Optional[M]:
    """Valide `data` a la frontiere d'une etape, sans jamais interrompre le pipeline.

    Rend le modele, ou None si la forme est incompatible : l'ecart est alors
    note dans le journal de la requete (pipeline.frontiere_invalide) et les
    dict continuent de circuler tels quels.
    """
    try:
        return model.from_dict(data)
    except (ValidationError, TypeError) as exc:
        diagnostics.note(
            "pipeline.frontiere_invalide",
            etape=stage,
            modele=model.__name__,
            erreur=str(exc)[:300],
        )
        return None
