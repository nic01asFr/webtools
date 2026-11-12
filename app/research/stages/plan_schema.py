"""Schema JSON du plan et plan generique de repli (configures par PipelineConfig)."""

from typing import Any, Dict, List, Optional

from app.research.pipeline_config import PipelineConfig


def build_plan_schema(
    cfg: PipelineConfig, imposed_sections: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Schema de la sortie structuree du plan (sections en liste d'objets).

    Avec `imposed_sections`, le nom de chaque section est limite a ces titres
    (enum) et leur nombre est fixe ; sans, le schema est inchange.
    """
    # Sortie structuree forcee : plus de clefs dynamiques (l'ancien format
    # section_targets = {nom_section: {...}} etait incompatible avec un
    # JSON Schema strict). "sections" devient une liste d'objets, chacun
    # avec son nom en attribut plutot qu'en clef de dictionnaire - le
    # nombre de sections reste totalement libre pour le LLM (1 a 10),
    # seule la FORME de chaque section est garantie.
    plan_schema = {
        "type": "object",
        "properties": {
            "complexity_analysis": {
                "type": "object",
                "properties": {
                    "overall_score": {"type": "number", "minimum": 1.0, "maximum": 5.0},
                    "target_length": {
                        "type": "string",
                        "enum": ["concis", "standard", "détaillé", "approfondi"],
                    },
                    "estimated_words": {"type": "integer", "minimum": 100},
                    "justification": {"type": "string"},
                },
                "required": ["overall_score", "target_length", "estimated_words", "justification"],
                "additionalProperties": False,
            },
            "sections": {
                "type": "array",
                "minItems": 1,
                "maxItems": cfg.plan_max_sections,
                # Le modele produisait 4-5 sections quelle que soit
                # l'ampleur de la demande. Mesure sur 20 taches :
                # correlation -0.29 entre longueur de consigne et score —
                # plus la demande est detaillee, moins le pipeline s'en
                # sort. Cas extreme : une consigne enumerant 6 aspects sur
                # une dizaine d'entreprises a recu 3 sections et 1 770
                # mots, le plus court des 20 rapports, et le plus mauvais
                # score (0.154).
                # La contrainte va dans la description : c'est le seul
                # endroit ou une exigence porte sur ce modele (verifie
                # trois fois : langue, source_axis, operateurs).
                "description": (
                    "Sections du rapport. COUVRE TOUS LES ASPECTS "
                    "EXPLICITEMENT DEMANDES dans la consigne : releve-les "
                    "un par un et donne a chacun sa section. Une consigne "
                    "qui enumere six aspects appelle au moins six "
                    "sections, pas quatre. Une consigne courte et generale "
                    "en appelle trois ou quatre. Ne fusionne pas deux "
                    "aspects distincts pour tenir dans un format."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "words_target": {
                            "type": "integer",
                            "minimum": cfg.plan_words_target_min,
                            "maximum": cfg.plan_words_target_max,
                        },
                        "depth": {"type": "string", "enum": ["light", "moderate", "deep"]},
                        "objectives": {"type": "array", "items": {"type": "string"}},
                        "key_questions": {"type": "array", "items": {"type": "string"}},
                        # Requetes de recherche formulees PAR LE LLM.
                        # La v1 (aujourd'hui morte) faisait deja generer
                        # input.query par etape ; la refonte v2 l'a perdu et
                        # l'a remplace par la concatenation brute
                        # f"{query} {section_name}", qui produit des requetes
                        # de 400+ caracteres sur consigne realiste — donc
                        # zero source, donc rapport vide. Le LLM sait
                        # formuler une requete ; l'extraction de mots-cles
                        # n'est qu'un repli.
                        "source_axis": {
                            "type": "string",
                            "enum": ["generaliste", "academique", "actualite", "technique"],
                            "description": "Axe de sources adapte au contenu de CETTE section",
                        },
                        "search_terms": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": cfg.plan_max_search_terms,
                            # La consigne en prose ne suffit pas : le
                            # modele ignorait la regle sur les operateurs,
                            # comme il avait ignore celle sur la langue.
                            # La description du schema est le seul endroit
                            # ou une contrainte de FORMAT porte.
                            "description": (
                                "Requetes de moteur, 1 a 3. Formule-en UNE "
                                "avec un operateur quand le sujet s'y prete : "
                                "filetype:pdf pour des rapports ou etudes, "
                                "site:domaine pour une source institutionnelle "
                                "sure (site:europa.eu, site:who.int, site:oecd.org), "
                                '"expression exacte" pour une terminologie precise. '
                                "Les autres restent en mots-cles simples. "
                                "Pas d'operateur sur les sections d'introduction "
                                "ou de synthese."
                            ),
                            "items": {
                                "type": "string",
                                "maxLength": cfg.plan_search_term_max_chars,
                            },
                        },
                    },
                    # source_axis en required : sans cela le modele omet
                    # simplement le champ (constate en run reel - le plan
                    # structure etait bien genere, 6 sections, mais aucun
                    # axe declare). Un champ optionnel dans un schema
                    # JSON n'est pas une suggestion polie : c'est une
                    # permission de ne pas repondre.
                    "required": [
                        "name",
                        "words_target",
                        "depth",
                        "objectives",
                        "key_questions",
                        "search_terms",
                        "source_axis",
                    ],
                    "additionalProperties": False,
                },
            },
            "narrative_flow": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "from_section": {"type": "string"},
                        "to_section": {"type": "string"},
                        "transition_type": {
                            "type": "string",
                            "enum": ["zoom-in", "cause-effect", "chronological", "comparison"],
                        },
                        "rationale": {"type": "string"},
                    },
                    "required": ["from_section", "to_section", "transition_type", "rationale"],
                    "additionalProperties": False,
                },
            },
            "search_strategy": {
                "type": "object",
                "properties": {
                    "total_sources_needed": {"type": "integer", "minimum": 1},
                    "sources_per_section": {"type": "integer", "minimum": 1},
                    "search_depth": {"type": "string", "enum": ["quick", "standard", "exhaustive"]},
                    "language": {
                        "type": "string",
                        "description": "Code langue ISO 639-1 de la consigne (fr, en, es...) ou 'all' si indifferent",
                    },
                },
                "required": [
                    "total_sources_needed",
                    "sources_per_section",
                    "search_depth",
                    "language",
                ],
                "additionalProperties": False,
            },
        },
        "required": ["complexity_analysis", "sections", "narrative_flow", "search_strategy"],
        "additionalProperties": False,
    }
    if imposed_sections:
        sections = plan_schema["properties"]["sections"]
        count = len(imposed_sections)
        sections["minItems"] = count
        sections["maxItems"] = count
        sections["description"] = (
            "Sections IMPOSEES par l'appelant : exactement "
            + str(count)
            + " sections, une par titre autorise, dans cet ordre : "
            + " | ".join(imposed_sections)
            + ". N'ajoute aucune autre section et ne change aucun titre."
        )
        sections["items"]["properties"]["name"] = {
            "type": "string",
            "enum": list(imposed_sections),
            "description": "Titre impose, recopie tel quel",
        }
    return plan_schema


def build_fallback_plan(cfg: PipelineConfig) -> Dict[str, Any]:
    """Plan generique rendu quand la planification echoue (Introduction, Analyse, Conclusion)."""
    FALLBACK_PLAN = {
        "complexity_analysis": {
            "overall_score": 3.0,
            "target_length": "standard",
            "estimated_words": 1200,
        },
        "sections": ["Introduction", "Analyse", "Conclusion"],
        "section_targets": {
            "Introduction": {
                "words_target": cfg.plan_fallback_intro_words,
                "depth": "moderate",
                "objectives": ["Introduire le sujet"],
                "key_questions": [],
            },
            "Analyse": {
                "words_target": cfg.plan_fallback_analysis_words,
                "depth": "moderate",
                "objectives": ["Analyser le sujet"],
                "key_questions": [],
            },
            "Conclusion": {
                "words_target": cfg.plan_fallback_conclusion_words,
                "depth": "light",
                "objectives": ["Conclure"],
                "key_questions": [],
            },
        },
        "narrative_flow": [],
        "search_strategy": {
            "total_sources_needed": cfg.plan_fallback_total_sources,
            "sources_per_section": cfg.plan_fallback_sources_per_section,
            "search_depth": "standard",
        },
    }
    return FALLBACK_PLAN
