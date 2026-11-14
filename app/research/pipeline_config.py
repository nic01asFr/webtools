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

    # --- Lot V3-2 : assemblage du rapport ---
    clean_title: bool = False  # titre court derive de la consigne au lieu de "Analyse: ..." (C41)
    summary_mode: str = "extract"  # "extract" (2 premieres phrases) ou "none" (resume vide) (C07)

    # --- Lot V3-1 : citations et bibliographie ---
    validate_cited_urls: bool = False  # rapproche ou retire les URL citees non fournies (C45)
    bibliography_cited_only: bool = False  # bibliographie limitee aux sources citees (C45)
    bibliography_real_titles: bool = False  # titre reel de la page au lieu de "Source n" (C45)

    # --- Lot V3-7 : plan de recherche ---
    plan_prompt_v2: bool = False  # prompt de plan sans ancien JSON ni exemples, contexte reduit (C33)

    # --- Lot V3-6 : selection des passages et donnees du prompt ---
    prompt_payload_minimal: bool = False  # le prompt de synthese ne recoit que source, title, content, corroboration_count (C39)
    max_chunks_per_source: int = 0  # plafond de passages retenus par source ; 0 = pas de plafond (C38)

    # --- Lot V3-5 : enrichissement et coherence ---
    # Faux par defaut depuis le 13/11/2026 : toutes les mesures citecheck ont
    # ete faites sans (profil sspcloud-modele-lent), et la regeneration
    # demandait au modele d'ajouter des chiffres, sans verification ensuite.
    # Active, chaque section regeneree passe par la verification (C93).
    enrichment_enabled: bool = False  # vrai : enrichissement iteratif des sections faibles (C42)
    # Faux par defaut depuis le 12/11/2026 : constate en production, la
    # suggestion du modele ("Une fois la section corrigee, ajouter...") etait
    # collee telle quelle dans le rapport (C40).
    coherence_append_enabled: bool = False  # vrai : colle les suggestions de coherence en fin de section
    regenerate_v2: bool = False  # vrai : regeneration de section avec langue, citations et schema (C43)

    # --- Lot V3-4 : collecte (requetes, langue, plafonds) ---
    separate_search_terms: bool = False  # une recherche par terme du plan, resultats fusionnes (C34)
    search_language_from_query: bool = False  # passe la langue detectee aux moteurs (C32)
    extraction_cap_follows_depth: bool = False  # extrait autant d'URL que la profondeur (3/5/8) au lieu de 5 (C35)
    exploration_uses_plan_query: bool = False  # exploration : requete reduite et moteurs de la cascade (C31)
    targeted_search_skip_structured: bool = False  # recherche ciblee sans appel LLM d'extraction structuree (C36, C79)
    chunking_v2: bool = False  # transmis a document_to_chunk_items (C73)
    # Vrai par defaut depuis le 12/11/2026 : constate deux fois en production,
    # le flux RSS d'un site etait rendu comme contenu d'un article (C56).
    rss_tier_root_only: bool = True  # palier RSS reserve a la racine du site ou aux flux
    single_llm_pass_enabled: bool = True  # transmis a ExtractionOptions : reecriture LLM unique du contenu direct

    # --- Lot V3-8 : recherche rapide ---
    quick_uses_chunk_selection: bool = False  # /research/quick: passages choisis par pertinence au lieu de 2000 car. de tete (C63)
    # Mesure citecheck du 12/11/2026 : 25 % d'affirmations soutenues dans les
    # sections redigees apres ce repli, contre 51 % apres une selection normale.
    selection_fallback_enabled: bool = True  # vrai : sans passage au-dessus du seuil, retenir quand meme les meilleurs
    # Vrai par defaut depuis le 13/11/2026 (mesure citecheck, R1 contre R2) :
    # affirmations soutenues 58,9 % -> 69,2 %, en hausse sur 7 sujets sur 8,
    # pour 25 % de temps en plus.
    deep_verify_sections: bool = True  # recherche approfondie : chaque section verifiee contre ses passages, reecriture unique si fautive (C89)
    quick_verify_answer: bool = False  # /research/quick: chaque phrase verifiee contre ses sources, reecriture unique si fautive (C89)
    # Redaction v3 (C93, 13/11/2026) : sources etiquetees [S1], [S2]... au lieu
    # d'URL a recopier, passages en texte et non en JSON, consignes sans
    # contradiction, questions du plan traitees seulement si les sources y
    # repondent, faits deja etablis dans les sections precedentes, citation
    # ajoutee par le code quand le verificateur designe la source d'une phrase.
    synthesis_v3: bool = True  # faux : prompt de redaction precedent (URL recopiees, passages en JSON)

    # --- Lot V3-3 : contrat de l'API de recherche approfondie ---
    # Plafond de sources par section, pour la recherche et pour l'extraction
    # (C20). None : plafond selon la profondeur de la section (3, 5 ou 8) et
    # 5 pages extraites au plus.
    max_sources_per_section: int | None = None

    # --- Extraction : agent navigateur ---
    # Palier agent (browser-use) quand l'extraction directe echoue ou rend
    # trop peu. Observe en production sur un modele a ~10 jetons/s : un agent
    # pilote le LLM pas a pas, une seule page pouvait couter plusieurs minutes,
    # et la collecte d'un rapport depassait le delai global.
    extraction_use_agent: bool = True  # faux : extraction directe seulement (pas d'agent navigateur)

    # --- Collecte : repli quand une recherche ne rend rien ---
    # Mesure sur le run de reference citecheck du 12/11/2026 : 21 recherches
    # de section sur 79 rendaient zero resultat (axe "actualite" route vers
    # des moteurs signales indisponibles, axe "technique" vers github et pypi
    # pour un reglement europeen, requetes de 10 a 14 mots-cles) et 22
    # collectes de section sur 49 n'extrayaient rien.
    search_zero_result_fallback: bool = True  # vrai : requete vide -> cascade complete, puis requete raccourcie
    # Collecte enregistree du 13/11/2026 : 43 resultats arXiv sur 66 et 17
    # resultats bing sur 22 sans rapport avec la requete (C96).
    search_relevance_gate: bool = True  # vrai : resultat sans terme commun avec la requete ecarte (deux pour arXiv)

    # --- Lot V4-1 : budgets et seuils ---
    # Valeurs reprises a l'identique de l'orchestrateur : aucun changement de
    # comportement. Le test test_v4_1_config fige ces defauts.

    # Etape exploration
    exploration_max_results: int = 8  # resultats demandes a l'exploration initiale
    exploration_engines: str = "google,duckduckgo"  # moteurs de l'exploration sans requete de plan
    exploration_snippet_chars: int = 200  # longueur de l'extrait gardee par resultat d'exploration
    exploration_rich_min_sources: int = 6  # sources trouvees a partir desquelles le champ est "rich"
    exploration_moderate_min_sources: int = 3  # sources trouvees a partir desquelles le champ est "moderate"
    exploration_preview_snippets: int = 3  # extraits transmis dans l'apercu de l'exploration
    exploration_topics_max: int = 5  # sous-themes retenus parmi les mots frequents
    exploration_topic_min_count: int = 2  # occurrences minimales d'un mot pour etre un sous-theme

    # Etape plan
    plan_max_tokens_structured: int = 6000  # jetons maximaux de la sortie structuree du plan
    plan_max_tokens_unstructured: int = 3000  # jetons maximaux du plan sans sortie structuree
    plan_temperature: float = 0.2  # temperature de l'appel de plan
    plan_topics_in_prompt: int = 5  # sous-themes cites dans le prompt de plan
    plan_snippet_chars: int = 150  # longueur de l'apercu cite dans le prompt de plan
    plan_max_sections: int = 10  # nombre maximal de sections du schema de plan
    plan_words_target_min: int = 100  # mots minimaux par section dans le schema de plan
    plan_words_target_max: int = 1200  # mots maximaux par section dans le schema de plan
    plan_max_search_terms: int = 3  # requetes de moteur maximales par section dans le schema de plan
    plan_search_term_max_chars: int = 90  # longueur maximale d'une requete de moteur du plan
    plan_fallback_intro_words: int = 300  # mots vises pour l'introduction du plan de repli
    plan_fallback_analysis_words: int = 600  # mots vises pour l'analyse du plan de repli
    plan_fallback_conclusion_words: int = 300  # mots vises pour la conclusion du plan de repli
    plan_fallback_total_sources: int = 10  # sources totales annoncees par le plan de repli
    plan_fallback_sources_per_section: int = 3  # sources par section annoncees par le plan de repli

    # Etape recherche
    search_query_max_words: int = 12  # mots maximaux d'une requete de recherche reduite
    search_query_section_words: int = 5  # mots du nom de section gardes dans la requete reduite
    search_query_min_query_words: int = 3  # mots minimaux de la consigne gardes dans la requete reduite
    search_query_fallback_chars: int = 120  # troncature de la consigne quand la reduction ne rend rien
    search_terms_max_queries: int = 3  # termes du plan cherches un a un (separate_search_terms)
    search_sources_light: int = 3  # sources par section de profondeur "light"
    search_sources_moderate: int = 5  # sources par section de profondeur "moderate" (et profondeur inconnue)
    search_sources_deep: int = 8  # sources par section de profondeur "deep"
    search_pool_multiplier: int = 3  # facteur de resultats demandes quand des contraintes de domaines filtrent apres coup
    search_snippet_chars: int = 300  # longueur de l'extrait gardee par resultat de section
    cascade_engines_per_axis: int = 3  # moteurs retenus par axe dans la cascade
    search_fallback_query_words: int = 6  # mots gardes par la requete raccourcie du dernier repli
    complement_default_sources_per_section: int = 2  # seuil de sources par section si le plan n'en donne pas
    complement_depth_light_delta: int = -1  # ajustement du seuil de sources pour une section "light"
    complement_depth_moderate_delta: int = 0  # ajustement du seuil de sources pour une section "moderate"
    complement_depth_deep_delta: int = 1  # ajustement du seuil de sources pour une section "deep"
    complement_min_threshold: int = 1  # seuil minimal de sources avant le complement pre-redaction
    section_budget_min_seconds: int = 60  # budget de temps minimal par section (recherche et extraction)
    section_budget_timeout_ratio: float = 0.8  # part du delai de l'orchestrateur accordee a une section
    targeted_search_pool_size: int = 5  # resultats demandes par une recherche ciblee
    targeted_search_max_urls: int = 2  # pages extraites par recherche ciblee
    targeted_extract_timeout: int = 30  # delai d'extraction d'une page de recherche ciblee, en secondes
    targeted_structured_input_chars: int = 2000  # caracteres envoyes a l'extraction structuree ciblee

    # Etape extraction
    extraction_url_cap_default: int = 5  # pages extraites par section sans plafond ni suivi de profondeur
    step_preview_chars: int = 100  # longueur des apercus d'entree et de sortie des etapes tracees

    # Etape selection
    selection_default_max_chars: int = 50000  # budget de caracteres par defaut de la selection de passages
    selection_min_score_light: float = 60.0  # score minimal d'un passage en profondeur "light"
    selection_min_score_moderate: float = 40.0  # score minimal d'un passage en profondeur "moderate"
    selection_min_score_deep: float = 35.0  # score minimal d'un passage en profondeur "deep"
    selection_max_chunks_light: int = 8  # passages vises en profondeur "light"
    selection_max_chunks_moderate: int = 16  # passages vises en profondeur "moderate"
    selection_max_chunks_deep: int = 30  # passages vises en profondeur "deep"
    selection_max_chunks_explicit_threshold: int = 40  # passages vises quand l'appelant impose le seuil de score
    selection_deep_min_remaining_chars: int = 1000  # budget restant minimal pour tronquer un dernier passage "deep"
    selection_fallback_chunks: int = 3  # passages retenus quand aucun ne franchit le seuil
    selection_weight_keyword: float = 0.5  # poids du score mots-cles avec embeddings
    selection_weight_semantic: float = 0.35  # poids de la similarite semantique
    selection_weight_corroboration: float = 0.15  # poids de la corroboration (avec ou sans embeddings)
    selection_weight_keyword_only: float = 0.85  # poids du score mots-cles sans embeddings
    selection_fact_bonus_max: float = 10.0  # bonus maximal de densite factuelle, sur 100
    selection_corroboration_cap: int = 3  # sources corroborantes au-dela desquelles le bonus sature
    selection_keyword_match_saturation: float = 8.0  # mots de la consigne retrouves pour le score mots-cles maximal
    selection_keyword_max_points: float = 40.0  # points maximaux du score mots-cles
    selection_structured_max_points: int = 30  # points maximaux du bonus de donnees structurees
    selection_structured_numerical_weight: int = 3  # points par donnee numerique structuree
    selection_structured_temporal_weight: int = 2  # points par donnee temporelle structuree
    selection_structured_entity_weight: int = 1  # points par entite structuree
    selection_length_ideal_min_chars: int = 500  # longueur minimale d'un passage de longueur ideale
    selection_length_ideal_max_chars: int = 5000  # longueur maximale d'un passage de longueur ideale
    selection_length_max_points: int = 20  # points maximaux de qualite de longueur
    selection_length_floor_points: int = 10  # points plancher d'un passage trop long
    selection_length_decay_chars: int = 1000  # caracteres excedentaires coutant un point de longueur
    selection_numbers_max_points: int = 10  # points maximaux de presence de nombres et de dates
    selection_dates_weight: int = 2  # nombre de points d'une date, contre un par nombre
    corroboration_min_similarity: float = 0.75  # similarite minimale pour compter une corroboration
    corroboration_max_similarity: float = 0.92  # similarite maximale (au-dela : reprise verbatim)

    # Etape synthese
    synthesis_default_words_target: int = 500  # mots vises si le plan n'en donne pas pour la section
    synthesis_source_budget_chars: int = 16000  # caracteres de sources transmis au prompt de synthese
    synthesis_no_source_words_cap: int = 150  # plafond de mots d'une section sans aucune source
    synthesis_few_sources_max: int = 2  # sources au plus pour reduire l'objectif de mots
    synthesis_few_sources_words_threshold: int = 300  # objectif de mots au-dela duquel il est reduit
    synthesis_few_sources_words_floor: int = 250  # objectif de mots plancher apres reduction
    synthesis_few_sources_words_divisor: int = 2  # diviseur de l'objectif de mots d'une section pauvre en sources
    synthesis_previous_sections_chars: int = 180  # caracteres cites de chaque section deja redigee
    synthesis_previous_sections_max: int = 6  # sections deja redigees citees dans le prompt
    synthesis_min_payload_chars: int = 200  # caracteres de donnees sous lesquels la section n'est pas redigee
    synthesis_tokens_per_word: float = 4.5  # jetons maximaux par mot vise
    synthesis_temperature: float = 0.3  # temperature de la synthese d'une section
    synthesis_max_attempts: int = 3  # essais de la synthese d'une section
    synthesis_retry_backoff_base: int = 2  # base de l'attente entre deux essais (base ** essai, en secondes)
    synthesis_facts_chars: int = 3000  # caracteres de faits deja etablis transmis a la redaction v3
    synthesis_facts_per_section_chars: int = 600  # caracteres de faits repris de chaque section deja redigee (v3)
    verification_model: str = ""  # modele du verificateur de sections ; vide = modele de redaction

    # Etape coherence
    coherence_preview_chars: int = 500  # caracteres de chaque section envoyes a l'analyse de coherence
    coherence_max_tokens: int = 1500  # jetons maximaux de l'analyse de coherence
    coherence_temperature: float = 0.2  # temperature de l'analyse de coherence
    coherence_max_improvements: int = 3  # ameliorations de priorite haute appliquees

    # Etape assemblage
    summary_sentences: int = 2  # phrases de la premiere section reprises dans le resume
    unsourced_degraded_ratio: float = 0.5  # part de sections sans source au-dela de laquelle le rapport est degrade

    # Etape enrichissement
    enrichment_max_iterations: int = 1  # iterations de l'enrichissement iteratif appele par le pipeline
    enrichment_stop_score: int = 85  # score global a partir duquel l'enrichissement s'arrete
    enrichment_thin_chars_per_section: int = 800  # caracteres par section sous lesquels le rapport est juge trop court
    enrichment_min_section_chars: int = 2000  # longueur sous laquelle une section est a enrichir
    enrichment_weak_score: int = 75  # score de section sous lequel elle est a enrichir
    enrichment_max_sections_per_iteration: int = 2  # sections enrichies par iteration
    enrichment_report_chars: int = 5000  # caracteres du rapport envoyes a l'analyse de qualite
    enrichment_analysis_max_tokens: int = 4000  # jetons maximaux de l'analyse de qualite
    enrichment_analysis_temperature: float = 0.1  # temperature de l'analyse de qualite
    enrichment_data_chars: int = 3000  # caracteres de donnees d'enrichissement envoyes a la regeneration v1
    enrichment_regenerate_max_tokens: int = 2000  # jetons maximaux de la regeneration de section v1
    enrichment_regenerate_v2_max_tokens: int = 6000  # jetons maximaux de la regeneration de section v2
    enrichment_regenerate_temperature: float = 0.1  # temperature de la regeneration de section (v1 et v2)

    # Delai global
    deadline_cancel_grace_seconds: float = 15.0  # attente maximale du nettoyage du pipeline annule avant le rapport partiel

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

    def __post_init__(self) -> None:
        if self.summary_mode not in ("extract", "none"):
            raise ValueError(
                f"summary_mode invalide : {self.summary_mode!r} (attendu 'extract' ou 'none')"
            )
