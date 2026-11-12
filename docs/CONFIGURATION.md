# Configuration de webtools

Ce document liste les réglages du pipeline de recherche approfondie et les
variables d'environnement du service. Les tableaux du pipeline sont générés
depuis la classe `PipelineConfig` (`app/research/pipeline_config.py`) par
`docs/generer_configuration.py` : ne pas les modifier à la main. Pour les
mettre à jour après un changement de la classe :

```bash
python docs/generer_configuration.py
```

Le test `tests/unit/docs/test_documentation.py` échoue si le fichier est
périmé ou si un champ manque.

## 1. Réglages du pipeline (`PipelineConfig`)

### Mécanisme

- Chaque champ se surcharge par la variable
  `WEBTOOLS_PIPELINE_<NOM_DU_CHAMP_EN_MAJUSCULES>`.
- Les booléens acceptent `1/0`, `true/false`, `yes/no`, `on/off`, `oui/non`.
- Une valeur impossible à convertir lève une erreur qui nomme la variable.
  Sur `/research/deep`, elle devient une réponse `success: false` avec ce
  message. Une variable portant le préfixe mais ne correspondant à aucun
  champ est ignorée sans message : vérifier l'orthographe dans
  `diagnostics.config` de la réponse.
- La configuration est lue à chaque requête, pas au démarrage du service.
  Elle est jointe à chaque réponse de `/research/deep`
  (`diagnostics.config`) et au manifeste de run du harnais de benchmark.
- `max_sources_per_section` est aussi un champ de la requête
  `/research/deep` : la valeur de la requête remplace celle de la variable.
- Un drapeau de type B est livré désactivé : sa valeur par défaut reproduit
  le comportement d'avant les correctifs. Il ne s'adopte qu'après la
  campagne de mesure indiquée (`PLAN_FIABILISATION.md`, section 8 ;
  `benchmarks/PROTOCOLE.md`). Tant que les campagnes n'ont pas eu lieu, tous
  les drapeaux gardent leur défaut.
- Les budgets et seuils reprennent à l'identique les valeurs qui étaient
  écrites en dur dans l'orchestrateur. Les changer modifie le comportement :
  le faire sous une campagne de mesure, pas au fil de l'eau.

<!-- DEBUT-GENERE : ne pas modifier a la main, voir docs/generer_configuration.py -->

### Drapeaux à mesurer (type B) : 20 champs

| Nom | Type | Défaut | Variable d'environnement | Effet | Campagne | Valeur essayée |
|---|---|---|---|---|---|---|
| `rss_tier_root_only` | bool | `true` | `WEBTOOLS_PIPELINE_RSS_TIER_ROOT_ONLY` | palier RSS reserve a la racine du site ou aux flux | M2 | `true` |
| `single_llm_pass_enabled` | bool | `true` | `WEBTOOLS_PIPELINE_SINGLE_LLM_PASS_ENABLED` | transmis a ExtractionOptions : reecriture LLM unique du contenu direct | M2 | `false` |
| `clean_title` | bool | `false` | `WEBTOOLS_PIPELINE_CLEAN_TITLE` | titre court derive de la consigne au lieu de "Analyse: ..." (C41) | M3 | `true` |
| `summary_mode` | str | `"extract"` | `WEBTOOLS_PIPELINE_SUMMARY_MODE` | "extract" (2 premieres phrases) ou "none" (resume vide) (C07) | M3 | `none` |
| `enrichment_enabled` | bool | `true` | `WEBTOOLS_PIPELINE_ENRICHMENT_ENABLED` | faux : saute l'enrichissement iteratif (C42) | M3 | `false` |
| `coherence_append_enabled` | bool | `true` | `WEBTOOLS_PIPELINE_COHERENCE_APPEND_ENABLED` | faux : ne colle plus les suggestions de coherence (C40) | M3 | `false` |
| `regenerate_v2` | bool | `false` | `WEBTOOLS_PIPELINE_REGENERATE_V2` | vrai : regeneration de section avec langue, citations et schema (C43) | M3 (essai isole si M3 reste dans le bruit) | `true` |
| `validate_cited_urls` | bool | `false` | `WEBTOOLS_PIPELINE_VALIDATE_CITED_URLS` | rapproche ou retire les URL citees non fournies (C45) | M4 | `true` |
| `bibliography_cited_only` | bool | `false` | `WEBTOOLS_PIPELINE_BIBLIOGRAPHY_CITED_ONLY` | bibliographie limitee aux sources citees (C45) | M4 | `true` |
| `bibliography_real_titles` | bool | `false` | `WEBTOOLS_PIPELINE_BIBLIOGRAPHY_REAL_TITLES` | titre reel de la page au lieu de "Source n" (C45) | M4 | `true` |
| `prompt_payload_minimal` | bool | `false` | `WEBTOOLS_PIPELINE_PROMPT_PAYLOAD_MINIMAL` | le prompt de synthese ne recoit que source, title, content, corroboration_count (C39) | M4 | `true` |
| `max_chunks_per_source` | int | `0` | `WEBTOOLS_PIPELINE_MAX_CHUNKS_PER_SOURCE` | plafond de passages retenus par source ; 0 = pas de plafond (C38) | M5 | valeur a fixer |
| `separate_search_terms` | bool | `false` | `WEBTOOLS_PIPELINE_SEPARATE_SEARCH_TERMS` | une recherche par terme du plan, resultats fusionnes (C34) | M5 | `true` |
| `search_language_from_query` | bool | `false` | `WEBTOOLS_PIPELINE_SEARCH_LANGUAGE_FROM_QUERY` | passe la langue detectee aux moteurs (C32) | M5 | `true` |
| `extraction_cap_follows_depth` | bool | `false` | `WEBTOOLS_PIPELINE_EXTRACTION_CAP_FOLLOWS_DEPTH` | extrait autant d'URL que la profondeur (3/5/8) au lieu de 5 (C35) | M5 | `true` |
| `exploration_uses_plan_query` | bool | `false` | `WEBTOOLS_PIPELINE_EXPLORATION_USES_PLAN_QUERY` | exploration : requete reduite et moteurs de la cascade (C31) | M5 | `true` |
| `chunking_v2` | bool | `false` | `WEBTOOLS_PIPELINE_CHUNKING_V2` | transmis a document_to_chunk_items (C73) | M5 | `true` |
| `plan_prompt_v2` | bool | `false` | `WEBTOOLS_PIPELINE_PLAN_PROMPT_V2` | prompt de plan sans ancien JSON ni exemples, contexte reduit (C33) | hors campagne | `true` |
| `targeted_search_skip_structured` | bool | `false` | `WEBTOOLS_PIPELINE_TARGETED_SEARCH_SKIP_STRUCTURED` | recherche ciblee sans appel LLM d'extraction structuree (C36, C79) | hors campagne | `true` |
| `quick_uses_chunk_selection` | bool | `false` | `WEBTOOLS_PIPELINE_QUICK_USES_CHUNK_SELECTION` | /research/quick: passages choisis par pertinence au lieu de 2000 car. de tete (C63) | hors campagne | `true` |

### Paramètre aussi porté par la requête : 1 champ

| Nom | Type | Défaut | Variable d'environnement | Effet |
|---|---|---|---|---|
| `max_sources_per_section` | int \| None | `None` | `WEBTOOLS_PIPELINE_MAX_SOURCES_PER_SECTION` | Plafond de sources par section, pour la recherche et pour l'extraction (C20). None : plafond selon la profondeur de la section (3, 5 ou 8) et 5 pages extraites au plus. |

### Budgets et seuils : 111 champs

#### Étape exploration

| Nom | Type | Défaut | Variable d'environnement | Effet |
|---|---|---|---|---|
| `exploration_max_results` | int | `8` | `WEBTOOLS_PIPELINE_EXPLORATION_MAX_RESULTS` | resultats demandes a l'exploration initiale |
| `exploration_engines` | str | `"google,duckduckgo"` | `WEBTOOLS_PIPELINE_EXPLORATION_ENGINES` | moteurs de l'exploration sans requete de plan |
| `exploration_snippet_chars` | int | `200` | `WEBTOOLS_PIPELINE_EXPLORATION_SNIPPET_CHARS` | longueur de l'extrait gardee par resultat d'exploration |
| `exploration_rich_min_sources` | int | `6` | `WEBTOOLS_PIPELINE_EXPLORATION_RICH_MIN_SOURCES` | sources trouvees a partir desquelles le champ est "rich" |
| `exploration_moderate_min_sources` | int | `3` | `WEBTOOLS_PIPELINE_EXPLORATION_MODERATE_MIN_SOURCES` | sources trouvees a partir desquelles le champ est "moderate" |
| `exploration_preview_snippets` | int | `3` | `WEBTOOLS_PIPELINE_EXPLORATION_PREVIEW_SNIPPETS` | extraits transmis dans l'apercu de l'exploration |
| `exploration_topics_max` | int | `5` | `WEBTOOLS_PIPELINE_EXPLORATION_TOPICS_MAX` | sous-themes retenus parmi les mots frequents |
| `exploration_topic_min_count` | int | `2` | `WEBTOOLS_PIPELINE_EXPLORATION_TOPIC_MIN_COUNT` | occurrences minimales d'un mot pour etre un sous-theme |

#### Étape plan

| Nom | Type | Défaut | Variable d'environnement | Effet |
|---|---|---|---|---|
| `plan_max_tokens_structured` | int | `6000` | `WEBTOOLS_PIPELINE_PLAN_MAX_TOKENS_STRUCTURED` | jetons maximaux de la sortie structuree du plan |
| `plan_max_tokens_unstructured` | int | `3000` | `WEBTOOLS_PIPELINE_PLAN_MAX_TOKENS_UNSTRUCTURED` | jetons maximaux du plan sans sortie structuree |
| `plan_temperature` | float | `0.2` | `WEBTOOLS_PIPELINE_PLAN_TEMPERATURE` | temperature de l'appel de plan |
| `plan_topics_in_prompt` | int | `5` | `WEBTOOLS_PIPELINE_PLAN_TOPICS_IN_PROMPT` | sous-themes cites dans le prompt de plan |
| `plan_snippet_chars` | int | `150` | `WEBTOOLS_PIPELINE_PLAN_SNIPPET_CHARS` | longueur de l'apercu cite dans le prompt de plan |
| `plan_max_sections` | int | `10` | `WEBTOOLS_PIPELINE_PLAN_MAX_SECTIONS` | nombre maximal de sections du schema de plan |
| `plan_words_target_min` | int | `100` | `WEBTOOLS_PIPELINE_PLAN_WORDS_TARGET_MIN` | mots minimaux par section dans le schema de plan |
| `plan_words_target_max` | int | `1200` | `WEBTOOLS_PIPELINE_PLAN_WORDS_TARGET_MAX` | mots maximaux par section dans le schema de plan |
| `plan_max_search_terms` | int | `3` | `WEBTOOLS_PIPELINE_PLAN_MAX_SEARCH_TERMS` | requetes de moteur maximales par section dans le schema de plan |
| `plan_search_term_max_chars` | int | `90` | `WEBTOOLS_PIPELINE_PLAN_SEARCH_TERM_MAX_CHARS` | longueur maximale d'une requete de moteur du plan |
| `plan_fallback_intro_words` | int | `300` | `WEBTOOLS_PIPELINE_PLAN_FALLBACK_INTRO_WORDS` | mots vises pour l'introduction du plan de repli |
| `plan_fallback_analysis_words` | int | `600` | `WEBTOOLS_PIPELINE_PLAN_FALLBACK_ANALYSIS_WORDS` | mots vises pour l'analyse du plan de repli |
| `plan_fallback_conclusion_words` | int | `300` | `WEBTOOLS_PIPELINE_PLAN_FALLBACK_CONCLUSION_WORDS` | mots vises pour la conclusion du plan de repli |
| `plan_fallback_total_sources` | int | `10` | `WEBTOOLS_PIPELINE_PLAN_FALLBACK_TOTAL_SOURCES` | sources totales annoncees par le plan de repli |
| `plan_fallback_sources_per_section` | int | `3` | `WEBTOOLS_PIPELINE_PLAN_FALLBACK_SOURCES_PER_SECTION` | sources par section annoncees par le plan de repli |

#### Étape recherche

| Nom | Type | Défaut | Variable d'environnement | Effet |
|---|---|---|---|---|
| `search_query_max_words` | int | `12` | `WEBTOOLS_PIPELINE_SEARCH_QUERY_MAX_WORDS` | mots maximaux d'une requete de recherche reduite |
| `search_query_section_words` | int | `5` | `WEBTOOLS_PIPELINE_SEARCH_QUERY_SECTION_WORDS` | mots du nom de section gardes dans la requete reduite |
| `search_query_min_query_words` | int | `3` | `WEBTOOLS_PIPELINE_SEARCH_QUERY_MIN_QUERY_WORDS` | mots minimaux de la consigne gardes dans la requete reduite |
| `search_query_fallback_chars` | int | `120` | `WEBTOOLS_PIPELINE_SEARCH_QUERY_FALLBACK_CHARS` | troncature de la consigne quand la reduction ne rend rien |
| `search_terms_max_queries` | int | `3` | `WEBTOOLS_PIPELINE_SEARCH_TERMS_MAX_QUERIES` | termes du plan cherches un a un (separate_search_terms) |
| `search_sources_light` | int | `3` | `WEBTOOLS_PIPELINE_SEARCH_SOURCES_LIGHT` | sources par section de profondeur "light" |
| `search_sources_moderate` | int | `5` | `WEBTOOLS_PIPELINE_SEARCH_SOURCES_MODERATE` | sources par section de profondeur "moderate" (et profondeur inconnue) |
| `search_sources_deep` | int | `8` | `WEBTOOLS_PIPELINE_SEARCH_SOURCES_DEEP` | sources par section de profondeur "deep" |
| `search_pool_multiplier` | int | `3` | `WEBTOOLS_PIPELINE_SEARCH_POOL_MULTIPLIER` | facteur de resultats demandes quand des contraintes de domaines filtrent apres coup |
| `search_snippet_chars` | int | `300` | `WEBTOOLS_PIPELINE_SEARCH_SNIPPET_CHARS` | longueur de l'extrait gardee par resultat de section |
| `cascade_engines_per_axis` | int | `3` | `WEBTOOLS_PIPELINE_CASCADE_ENGINES_PER_AXIS` | moteurs retenus par axe dans la cascade |
| `complement_default_sources_per_section` | int | `2` | `WEBTOOLS_PIPELINE_COMPLEMENT_DEFAULT_SOURCES_PER_SECTION` | seuil de sources par section si le plan n'en donne pas |
| `complement_depth_light_delta` | int | `-1` | `WEBTOOLS_PIPELINE_COMPLEMENT_DEPTH_LIGHT_DELTA` | ajustement du seuil de sources pour une section "light" |
| `complement_depth_moderate_delta` | int | `0` | `WEBTOOLS_PIPELINE_COMPLEMENT_DEPTH_MODERATE_DELTA` | ajustement du seuil de sources pour une section "moderate" |
| `complement_depth_deep_delta` | int | `1` | `WEBTOOLS_PIPELINE_COMPLEMENT_DEPTH_DEEP_DELTA` | ajustement du seuil de sources pour une section "deep" |
| `complement_min_threshold` | int | `1` | `WEBTOOLS_PIPELINE_COMPLEMENT_MIN_THRESHOLD` | seuil minimal de sources avant le complement pre-redaction |
| `section_budget_min_seconds` | int | `60` | `WEBTOOLS_PIPELINE_SECTION_BUDGET_MIN_SECONDS` | budget de temps minimal par section (recherche et extraction) |
| `section_budget_timeout_ratio` | float | `0.8` | `WEBTOOLS_PIPELINE_SECTION_BUDGET_TIMEOUT_RATIO` | part du delai de l'orchestrateur accordee a une section |
| `targeted_search_pool_size` | int | `5` | `WEBTOOLS_PIPELINE_TARGETED_SEARCH_POOL_SIZE` | resultats demandes par une recherche ciblee |
| `targeted_search_max_urls` | int | `2` | `WEBTOOLS_PIPELINE_TARGETED_SEARCH_MAX_URLS` | pages extraites par recherche ciblee |
| `targeted_extract_timeout` | int | `30` | `WEBTOOLS_PIPELINE_TARGETED_EXTRACT_TIMEOUT` | delai d'extraction d'une page de recherche ciblee, en secondes |
| `targeted_structured_input_chars` | int | `2000` | `WEBTOOLS_PIPELINE_TARGETED_STRUCTURED_INPUT_CHARS` | caracteres envoyes a l'extraction structuree ciblee |

#### Étape extraction

| Nom | Type | Défaut | Variable d'environnement | Effet |
|---|---|---|---|---|
| `extraction_url_cap_default` | int | `5` | `WEBTOOLS_PIPELINE_EXTRACTION_URL_CAP_DEFAULT` | pages extraites par section sans plafond ni suivi de profondeur |
| `step_preview_chars` | int | `100` | `WEBTOOLS_PIPELINE_STEP_PREVIEW_CHARS` | longueur des apercus d'entree et de sortie des etapes tracees |

#### Étape sélection

| Nom | Type | Défaut | Variable d'environnement | Effet |
|---|---|---|---|---|
| `selection_default_max_chars` | int | `50000` | `WEBTOOLS_PIPELINE_SELECTION_DEFAULT_MAX_CHARS` | budget de caracteres par defaut de la selection de passages |
| `selection_min_score_light` | float | `60.0` | `WEBTOOLS_PIPELINE_SELECTION_MIN_SCORE_LIGHT` | score minimal d'un passage en profondeur "light" |
| `selection_min_score_moderate` | float | `40.0` | `WEBTOOLS_PIPELINE_SELECTION_MIN_SCORE_MODERATE` | score minimal d'un passage en profondeur "moderate" |
| `selection_min_score_deep` | float | `35.0` | `WEBTOOLS_PIPELINE_SELECTION_MIN_SCORE_DEEP` | score minimal d'un passage en profondeur "deep" |
| `selection_max_chunks_light` | int | `8` | `WEBTOOLS_PIPELINE_SELECTION_MAX_CHUNKS_LIGHT` | passages vises en profondeur "light" |
| `selection_max_chunks_moderate` | int | `16` | `WEBTOOLS_PIPELINE_SELECTION_MAX_CHUNKS_MODERATE` | passages vises en profondeur "moderate" |
| `selection_max_chunks_deep` | int | `30` | `WEBTOOLS_PIPELINE_SELECTION_MAX_CHUNKS_DEEP` | passages vises en profondeur "deep" |
| `selection_max_chunks_explicit_threshold` | int | `40` | `WEBTOOLS_PIPELINE_SELECTION_MAX_CHUNKS_EXPLICIT_THRESHOLD` | passages vises quand l'appelant impose le seuil de score |
| `selection_deep_min_remaining_chars` | int | `1000` | `WEBTOOLS_PIPELINE_SELECTION_DEEP_MIN_REMAINING_CHARS` | budget restant minimal pour tronquer un dernier passage "deep" |
| `selection_fallback_chunks` | int | `3` | `WEBTOOLS_PIPELINE_SELECTION_FALLBACK_CHUNKS` | passages retenus quand aucun ne franchit le seuil |
| `selection_weight_keyword` | float | `0.5` | `WEBTOOLS_PIPELINE_SELECTION_WEIGHT_KEYWORD` | poids du score mots-cles avec embeddings |
| `selection_weight_semantic` | float | `0.35` | `WEBTOOLS_PIPELINE_SELECTION_WEIGHT_SEMANTIC` | poids de la similarite semantique |
| `selection_weight_corroboration` | float | `0.15` | `WEBTOOLS_PIPELINE_SELECTION_WEIGHT_CORROBORATION` | poids de la corroboration (avec ou sans embeddings) |
| `selection_weight_keyword_only` | float | `0.85` | `WEBTOOLS_PIPELINE_SELECTION_WEIGHT_KEYWORD_ONLY` | poids du score mots-cles sans embeddings |
| `selection_fact_bonus_max` | float | `10.0` | `WEBTOOLS_PIPELINE_SELECTION_FACT_BONUS_MAX` | bonus maximal de densite factuelle, sur 100 |
| `selection_corroboration_cap` | int | `3` | `WEBTOOLS_PIPELINE_SELECTION_CORROBORATION_CAP` | sources corroborantes au-dela desquelles le bonus sature |
| `selection_keyword_match_saturation` | float | `8.0` | `WEBTOOLS_PIPELINE_SELECTION_KEYWORD_MATCH_SATURATION` | mots de la consigne retrouves pour le score mots-cles maximal |
| `selection_keyword_max_points` | float | `40.0` | `WEBTOOLS_PIPELINE_SELECTION_KEYWORD_MAX_POINTS` | points maximaux du score mots-cles |
| `selection_structured_max_points` | int | `30` | `WEBTOOLS_PIPELINE_SELECTION_STRUCTURED_MAX_POINTS` | points maximaux du bonus de donnees structurees |
| `selection_structured_numerical_weight` | int | `3` | `WEBTOOLS_PIPELINE_SELECTION_STRUCTURED_NUMERICAL_WEIGHT` | points par donnee numerique structuree |
| `selection_structured_temporal_weight` | int | `2` | `WEBTOOLS_PIPELINE_SELECTION_STRUCTURED_TEMPORAL_WEIGHT` | points par donnee temporelle structuree |
| `selection_structured_entity_weight` | int | `1` | `WEBTOOLS_PIPELINE_SELECTION_STRUCTURED_ENTITY_WEIGHT` | points par entite structuree |
| `selection_length_ideal_min_chars` | int | `500` | `WEBTOOLS_PIPELINE_SELECTION_LENGTH_IDEAL_MIN_CHARS` | longueur minimale d'un passage de longueur ideale |
| `selection_length_ideal_max_chars` | int | `5000` | `WEBTOOLS_PIPELINE_SELECTION_LENGTH_IDEAL_MAX_CHARS` | longueur maximale d'un passage de longueur ideale |
| `selection_length_max_points` | int | `20` | `WEBTOOLS_PIPELINE_SELECTION_LENGTH_MAX_POINTS` | points maximaux de qualite de longueur |
| `selection_length_floor_points` | int | `10` | `WEBTOOLS_PIPELINE_SELECTION_LENGTH_FLOOR_POINTS` | points plancher d'un passage trop long |
| `selection_length_decay_chars` | int | `1000` | `WEBTOOLS_PIPELINE_SELECTION_LENGTH_DECAY_CHARS` | caracteres excedentaires coutant un point de longueur |
| `selection_numbers_max_points` | int | `10` | `WEBTOOLS_PIPELINE_SELECTION_NUMBERS_MAX_POINTS` | points maximaux de presence de nombres et de dates |
| `selection_dates_weight` | int | `2` | `WEBTOOLS_PIPELINE_SELECTION_DATES_WEIGHT` | nombre de points d'une date, contre un par nombre |
| `corroboration_min_similarity` | float | `0.75` | `WEBTOOLS_PIPELINE_CORROBORATION_MIN_SIMILARITY` | similarite minimale pour compter une corroboration |
| `corroboration_max_similarity` | float | `0.92` | `WEBTOOLS_PIPELINE_CORROBORATION_MAX_SIMILARITY` | similarite maximale (au-dela : reprise verbatim) |

#### Étape synthèse

| Nom | Type | Défaut | Variable d'environnement | Effet |
|---|---|---|---|---|
| `synthesis_default_words_target` | int | `500` | `WEBTOOLS_PIPELINE_SYNTHESIS_DEFAULT_WORDS_TARGET` | mots vises si le plan n'en donne pas pour la section |
| `synthesis_source_budget_chars` | int | `16000` | `WEBTOOLS_PIPELINE_SYNTHESIS_SOURCE_BUDGET_CHARS` | caracteres de sources transmis au prompt de synthese |
| `synthesis_no_source_words_cap` | int | `150` | `WEBTOOLS_PIPELINE_SYNTHESIS_NO_SOURCE_WORDS_CAP` | plafond de mots d'une section sans aucune source |
| `synthesis_few_sources_max` | int | `2` | `WEBTOOLS_PIPELINE_SYNTHESIS_FEW_SOURCES_MAX` | sources au plus pour reduire l'objectif de mots |
| `synthesis_few_sources_words_threshold` | int | `300` | `WEBTOOLS_PIPELINE_SYNTHESIS_FEW_SOURCES_WORDS_THRESHOLD` | objectif de mots au-dela duquel il est reduit |
| `synthesis_few_sources_words_floor` | int | `250` | `WEBTOOLS_PIPELINE_SYNTHESIS_FEW_SOURCES_WORDS_FLOOR` | objectif de mots plancher apres reduction |
| `synthesis_few_sources_words_divisor` | int | `2` | `WEBTOOLS_PIPELINE_SYNTHESIS_FEW_SOURCES_WORDS_DIVISOR` | diviseur de l'objectif de mots d'une section pauvre en sources |
| `synthesis_previous_sections_chars` | int | `180` | `WEBTOOLS_PIPELINE_SYNTHESIS_PREVIOUS_SECTIONS_CHARS` | caracteres cites de chaque section deja redigee |
| `synthesis_previous_sections_max` | int | `6` | `WEBTOOLS_PIPELINE_SYNTHESIS_PREVIOUS_SECTIONS_MAX` | sections deja redigees citees dans le prompt |
| `synthesis_min_payload_chars` | int | `200` | `WEBTOOLS_PIPELINE_SYNTHESIS_MIN_PAYLOAD_CHARS` | caracteres de donnees sous lesquels la section n'est pas redigee |
| `synthesis_tokens_per_word` | float | `4.5` | `WEBTOOLS_PIPELINE_SYNTHESIS_TOKENS_PER_WORD` | jetons maximaux par mot vise |
| `synthesis_temperature` | float | `0.3` | `WEBTOOLS_PIPELINE_SYNTHESIS_TEMPERATURE` | temperature de la synthese d'une section |
| `synthesis_max_attempts` | int | `3` | `WEBTOOLS_PIPELINE_SYNTHESIS_MAX_ATTEMPTS` | essais de la synthese d'une section |
| `synthesis_retry_backoff_base` | int | `2` | `WEBTOOLS_PIPELINE_SYNTHESIS_RETRY_BACKOFF_BASE` | base de l'attente entre deux essais (base ** essai, en secondes) |

#### Étape cohérence

| Nom | Type | Défaut | Variable d'environnement | Effet |
|---|---|---|---|---|
| `coherence_preview_chars` | int | `500` | `WEBTOOLS_PIPELINE_COHERENCE_PREVIEW_CHARS` | caracteres de chaque section envoyes a l'analyse de coherence |
| `coherence_max_tokens` | int | `1500` | `WEBTOOLS_PIPELINE_COHERENCE_MAX_TOKENS` | jetons maximaux de l'analyse de coherence |
| `coherence_temperature` | float | `0.2` | `WEBTOOLS_PIPELINE_COHERENCE_TEMPERATURE` | temperature de l'analyse de coherence |
| `coherence_max_improvements` | int | `3` | `WEBTOOLS_PIPELINE_COHERENCE_MAX_IMPROVEMENTS` | ameliorations de priorite haute appliquees |

#### Étape assemblage

| Nom | Type | Défaut | Variable d'environnement | Effet |
|---|---|---|---|---|
| `summary_sentences` | int | `2` | `WEBTOOLS_PIPELINE_SUMMARY_SENTENCES` | phrases de la premiere section reprises dans le resume |
| `unsourced_degraded_ratio` | float | `0.5` | `WEBTOOLS_PIPELINE_UNSOURCED_DEGRADED_RATIO` | part de sections sans source au-dela de laquelle le rapport est degrade |

#### Étape enrichissement

| Nom | Type | Défaut | Variable d'environnement | Effet |
|---|---|---|---|---|
| `enrichment_max_iterations` | int | `1` | `WEBTOOLS_PIPELINE_ENRICHMENT_MAX_ITERATIONS` | iterations de l'enrichissement iteratif appele par le pipeline |
| `enrichment_stop_score` | int | `85` | `WEBTOOLS_PIPELINE_ENRICHMENT_STOP_SCORE` | score global a partir duquel l'enrichissement s'arrete |
| `enrichment_thin_chars_per_section` | int | `800` | `WEBTOOLS_PIPELINE_ENRICHMENT_THIN_CHARS_PER_SECTION` | caracteres par section sous lesquels le rapport est juge trop court |
| `enrichment_min_section_chars` | int | `2000` | `WEBTOOLS_PIPELINE_ENRICHMENT_MIN_SECTION_CHARS` | longueur sous laquelle une section est a enrichir |
| `enrichment_weak_score` | int | `75` | `WEBTOOLS_PIPELINE_ENRICHMENT_WEAK_SCORE` | score de section sous lequel elle est a enrichir |
| `enrichment_max_sections_per_iteration` | int | `2` | `WEBTOOLS_PIPELINE_ENRICHMENT_MAX_SECTIONS_PER_ITERATION` | sections enrichies par iteration |
| `enrichment_report_chars` | int | `5000` | `WEBTOOLS_PIPELINE_ENRICHMENT_REPORT_CHARS` | caracteres du rapport envoyes a l'analyse de qualite |
| `enrichment_analysis_max_tokens` | int | `4000` | `WEBTOOLS_PIPELINE_ENRICHMENT_ANALYSIS_MAX_TOKENS` | jetons maximaux de l'analyse de qualite |
| `enrichment_analysis_temperature` | float | `0.1` | `WEBTOOLS_PIPELINE_ENRICHMENT_ANALYSIS_TEMPERATURE` | temperature de l'analyse de qualite |
| `enrichment_data_chars` | int | `3000` | `WEBTOOLS_PIPELINE_ENRICHMENT_DATA_CHARS` | caracteres de donnees d'enrichissement envoyes a la regeneration v1 |
| `enrichment_regenerate_max_tokens` | int | `2000` | `WEBTOOLS_PIPELINE_ENRICHMENT_REGENERATE_MAX_TOKENS` | jetons maximaux de la regeneration de section v1 |
| `enrichment_regenerate_v2_max_tokens` | int | `6000` | `WEBTOOLS_PIPELINE_ENRICHMENT_REGENERATE_V2_MAX_TOKENS` | jetons maximaux de la regeneration de section v2 |
| `enrichment_regenerate_temperature` | float | `0.1` | `WEBTOOLS_PIPELINE_ENRICHMENT_REGENERATE_TEMPERATURE` | temperature de la regeneration de section (v1 et v2) |

Total : 132 champs, soit tous ceux de `PipelineConfig`.

<!-- FIN-GENERE -->

## 2. Variables d'environnement du service

Les valeurs par défaut du LLM, de SearXNG et du service sont définies une
seule fois dans `app/core/config.py` (classe `Settings`). `.env.example`,
`start_all.sh` et `docker-compose.yml` reprennent les mêmes noms. Le fichier
`.env` du répertoire courant (ou d'un parent) est chargé au démarrage sans
écraser une variable déjà définie.

### Obligatoires

| Variable | Lue par | Rôle |
|---|---|---|
| `WEBTOOLS_API_KEY` | API, serveur MCP, harnais | Clé de l'API REST (en-tête `X-API-Key`). Sans elle, l'API refuse de démarrer, sauf `WEBTOOLS_ALLOW_ANONYMOUS=1`. Le serveur MCP l'envoie à l'API |
| `DEFAULT_LLM_API_KEY` | API | Clé du fournisseur LLM. Sans clé, `/health` signale le LLM en échec et les endpoints qui l'utilisent renvoient une erreur |
| `MCP_ACCESS_TOKEN` | serveur MCP | Clé d'accès partagée du flux OAuth. Le serveur MCP refuse de démarrer sans elle |
| `SEARXNG_SECRET` | SearXNG (`docker-compose.yml`, `start_all.sh`) | Clé de session de SearXNG |
| `SEARXNG_CONTACT_URL` | SearXNG | Contact de l'exploitant (URL ou `mailto:`), exigé par les moteurs wikipedia et wikidata |

### Service (API)

| Variable | Défaut | Rôle |
|---|---|---|
| `WEBTOOLS_ALLOW_ANONYMOUS` | absent | `1` : démarre sans clé d'API et laisse l'API ouverte (un avertissement est journalisé) |
| `API_TITLE` | `WebTools API` | Titre de l'application |
| `API_VERSION` | `3.0.0` | Version annoncée par `/health` et la racine |
| `API_HOST`, `API_PORT` | `0.0.0.0`, `8000` | Adresse d'écoute en lancement direct (`python -m app.main`) |
| `LOG_LEVEL` | `INFO` | Niveau de journalisation |
| `MAX_CONCURRENT_EXTRACTIONS` | `3` | Sections traitées en parallèle pendant la collecte de `/research/deep` |
| `ENABLE_CACHING`, `CACHE_TTL_SECONDS` | `true`, `3600` | Cache d'extraction du gestionnaire (durée de vie en secondes) |
| `WEBTOOLS_COMMIT` | `unknown` | Commit du code chargé, publié par `/health`. Passé au build de l'image (`--build-arg`), car l'image n'a pas de dépôt git |
| `WEBTOOLS_PIPELINE_<CHAMP>` | voir section 1 | Réglages du pipeline |

### LLM

| Variable | Défaut | Rôle |
|---|---|---|
| `DEFAULT_LLM_PROVIDER` | `openai` | `openai` (tout service compatible OpenAI), `anthropic` ou `albert` |
| `DEFAULT_LLM_MODEL` | `qwen3-6-35b-moe` | Modèle |
| `DEFAULT_LLM_BASE_URL` | `https://llm.lab.sspcloud.fr/api` | URL du LLM compatible OpenAI (SSPCloud) |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ALBERT_API_KEY` | vide | Clé propre au fournisseur, prioritaire sur `DEFAULT_LLM_API_KEY` |
| `ALBERT_API_URL` | `https://albert.api.etalab.gouv.fr` | URL du fournisseur `albert` (remplace `DEFAULT_LLM_BASE_URL` pour lui) |
| `OPENAI_BASE_URL` | déduite de `DEFAULT_LLM_BASE_URL` | Lue par browser-use ; `start_all.sh` et `docker-compose.yml` la renseignent |
| `DEFAULT_LLM_TOTAL_TIMEOUT` | absent | Durée totale maximale, en secondes, d'un appel LLM avec ses reprises |
| `DEFAULT_LLM_STREAM` | `1` | Génération en flux pour le client de service. Sans flux, une sortie longue sur un modèle lent dépasse le délai par tentative (90 s) et n'aboutit jamais ; `0` la désactive |

### Profils de déploiement

Les fichiers de `docker/profils/` regroupent des drapeaux du pipeline pour un
contexte donné. `sspcloud-modele-lent.env` a été établi en production le
12 novembre 2026 : sur le proxy SSPCloud à environ 10 jetons par seconde,
une recherche approfondie ne finissait pas en 1 200 s avec les défauts, et
finissait en 885 s avec ce profil (4 sections, 11 sources, 62 citations,
aucune violation d'invariant). Docker Compose le charge avec
`WEBTOOLS_PROFIL_ENV=docker/profils/sspcloud-modele-lent.env` ; hors Docker,
le sourcer avant `start_all.sh`. Ses trois premiers drapeaux restent à
mesurer (campagnes M2 et M5).

Seul le fournisseur `openai` rend les sorties structurées et les embeddings
utilisés par le pipeline approfondi. Avec `anthropic` ou `albert`, le
pipeline tourne en mode dégradé.

### Recherche et extraction

| Variable | Défaut | Rôle |
|---|---|---|
| `SEARXNG_BASE_URL` | `http://searxng:8080` | Instance SearXNG. `start_all.sh` la fixe à `http://localhost:8081` |
| `SEARXNG_MIN_INTERVAL` | `1.0` | Espacement minimal, en secondes, entre deux recherches |
| `PLAYWRIGHT_BROWSERS_PATH` | emplacement par défaut de Playwright | Dossier des navigateurs ; `docker-compose.yml` le fixe à `/ms-playwright` |
| `WEBTOOLS_REPLAY_MODE` | absent | `record` ou `replay` : enregistre ou rejoue la collecte (`benchmarks/replay.md`) |
| `WEBTOOLS_REPLAY_DIR` | absent | Répertoire de l'enregistrement, obligatoire avec le mode |

Variables déclarées dans `Settings` mais que le code ne lit pas :
`PLAYWRIGHT_HEADLESS`, `PLAYWRIGHT_TIMEOUT`, `DEFAULT_EXTRACTION_TIMEOUT`,
`MAX_RETRIES`, `ALBERT_MODEL`. Les définir ne change rien.

### Serveur MCP

| Variable | Défaut | Rôle |
|---|---|---|
| `WEBTOOLS_API` | `http://localhost:8000` | URL de l'API vue du serveur MCP (`http://webtools:8000` sous Docker) |
| `WEBTOOLS_API_KEY` | vide | Clé envoyée à l'API |
| `MCP_PUBLIC_URL` | `http://localhost:8090` | URL publique, base du flux OAuth |
| `WEBTOOLS_DEEP_TIMEOUT` | `1200` | Délai global (60 à 1 200 s) que les outils de recherche approfondie demandent à l'API ; 600 s, le défaut de l'API, coupait les rapports en production |
| `MCP_PUBLIC_HOST` | `localhost` | Nom d'hôte de l'étiquette Traefik (`docker-compose.yml` seulement) |
| `MCP_MAX_CONCURRENT_DEEP` | `2` | Recherches approfondies simultanées en tâche de fond ; au-delà, `webtools_research_deep_start` répond `busy` |
| `WEBTOOLS_STATE_DIR` | `<tmp>/webtools_mcp_state` | Dossier de l'état des tâches (un fichier JSON par tâche) |

### Lancement sans Docker (`start_all.sh`)

| Variable | Défaut | Rôle |
|---|---|---|
| `WEBTOOLS_API_PYTHON`, `WEBTOOLS_MCP_PYTHON` | `python3` | Interpréteurs des deux environnements Python |
| `SEARXNG_PYTHON` | `$HOME/work/projects/searxng-venv/bin/python` | Interpréteur de SearXNG |
| `SEARXNG_SETTINGS_PATH` | `docker/searxng/settings.yml` | Configuration des moteurs |
| `SEARXNG_PORT`, `SEARXNG_BIND_ADDRESS` | `8081`, `127.0.0.1` | Écoute de SearXNG |
| `WEBTOOLS_LOG_DIR` | `/tmp/webtools_logs` | Journaux des trois services |
| `WEBTOOLS_STATE_DIR` | `/tmp/webtools_state` | État du serveur MCP |
| `WEBTOOLS_CONNEXION_README` | absent | Si défini, fichier où écrire au démarrage la configuration de connexion (URL MCP, jeton MCP, URL et clé d'API), en droits 600 |
| `WEBTOOLS_PUBLIC_API` | absent | URL publique de l'API, reprise dans ce fichier |

### Harnais de benchmark

Voir `benchmarks/INSTALL.md` (`DRB_HOME`, `WEBTOOLS_API`, `WEBTOOLS_API_KEY`,
`SOVEREIGN_LLM_API_KEY`, `OPENROUTER_API_KEY`, `JINA_API_KEY`, `JUDGE`,
`WEBTOOLS_LLM_MODEL`).
