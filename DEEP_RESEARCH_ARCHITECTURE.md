# Architecture de la recherche approfondie

Ce document décrit `/api/v1/research/deep` tel que le code le réalise. Il se
lit avec `app/agents/intelligent_orchestrator.py` et le paquet
`app/research/`. Il ne donne aucun résultat de qualité : les mesures sont
dans `benchmarks/PROTOCOLE.md`.

## 1. Vue d'ensemble

`IntelligentOrchestrator` ne garde que l'enchaînement des étapes. Chaque
étape est une fonction d'un module de `app/research/stages/` ; elle reçoit
ses dépendances (`StageServices`) et l'état de la requête
(`ExecutionContext`). Aucun attribut de l'orchestrateur n'est écrit pendant
une requête : deux requêtes sur la même instance n'interfèrent pas.

Points d'entrée de l'orchestrateur :

| Méthode | Rôle |
|---|---|
| `execute_with_deadline(query, context, deadline_seconds)` | Appelée par l'endpoint. Lance le pipeline sous un délai global |
| `execute_intelligent_research(query, context)` | Même pipeline, sans délai global |
| `_run_pipeline(query, context, ctx)` | L'enchaînement des étapes (section 2) |
| `_partial_report(query, ctx)` | Rapport construit avec les sections déjà rédigées quand le délai expire |
| `_exploratory_phase`, `_analyze_intersections`, `_convert_sources_to_bibliography` | Enveloppes minces vers trois étapes, laissées en place comme points de remplacement des tests |

## 2. Enchaînement

```
execute_with_deadline(topic, contexte, timeout)
 |
 +-- _run_pipeline
      |
      |  ctx.set_domain_constraints      lit domains_whitelist et sources_exclusions
      |  detect_language(topic)          langue de recherche et de rédaction
      |
      +-- PHASE 1  exploration et plan
      |     stages.explore.exploratory_phase          recherche restreinte, champ rich/moderate/limited
      |     stages.required.extract_required_sources  extrait les sources.required (avant le plan)
      |     stages.plan.create_detailed_plan          plan par sections (LLM, repli générique)
      |       '-> stages.imposed.enforce_imposed_sections   si output_format.sections est fourni
      |     boundaries.check_plan                     frontière typée Plan (n'interrompt jamais)
      |
      +-- PHASE 2  construction section par section
      |     stages.collect.collect_section_data       toutes les sections en parallèle :
      |       |  stages.search.section_research_phase     moteurs, requêtes, filtre de domaines
      |       |  stages.extract.section_extraction_phase  extraction, découpage en passages
      |       |  stages.search.targeted_search            complément des sections trop pauvres
      |       '  stages.required.add_required_chunks      sources requises proposées à toutes
      |     boundaries.check_collected                frontière typée Passage
      |     stages.synthesize.write_sections          section après section, dans l'ordre du plan :
      |       |  stages.crossref.cross_reference_data     corroboration entre sources
      |       |  stages.select.semantic_chunk_selection   choix des passages, budget de caractères
      |       '  stages.synthesize.synthesize_single_section  rédaction avec [SOURCE:url]
      |     boundaries.check_drafts                   frontière typée SectionDraft
      |
      +-- PHASE 3  cohérence
      |     stages.coherence.analyze_intersections    un appel LLM sur des aperçus de sections
      |     stages.coherence.apply_coherence_improvements
      |
      +-- PHASE 4  finalisation
            stages.assemble.final_assembly            sections dans l'ordre du plan, bibliographie
            boundaries.check_report                   frontière typée Report
            stages.enrich.iterative_enrichment        sections faibles régénérées
            stages.assemble.convert_sources_to_bibliography   [SOURCE:url] devient [n]
            plan de repli : degraded + plan_fallback_reason
            stages.assemble.strip_source_markers      dernier nettoyage des marqueurs
            stages.required.apply_required_sources    bilan des sources requises
            stages.assemble.add_complete_traces       research_traces
 |
 +-- réponse : answer, execution_context, diagnostics (config, events, invariants)
```

Si le délai global expire, le pipeline est annulé et `_partial_report`
rend le rapport des sections déjà rédigées (section 9).

## 3. Les étapes

### 3.1 Exploration (`stages/explore.py`)

Une recherche restreinte (`exploration_max_results`, 8 par défaut) sur les
moteurs `exploration_engines`. Elle rend les sources trouvées, une
évaluation du champ (`rich` à partir de 6 sources, `moderate` à partir de 3,
sinon `limited`), des sous-thèmes tirés des extraits et un aperçu. Avec le
drapeau `exploration_uses_plan_query`, la requête est réduite et les moteurs
viennent de la cascade (3.4). Un échec est noté (`collecte.exploration_echec`)
et le pipeline continue avec une exploration vide (champ `unknown`).

### 3.2 Sources requises (`stages/required.py`)

Chaque URL de `sources.required` est extraite avant le plan, par le même
gestionnaire d'extraction que le reste du pipeline (garde SSRF, rejeu). Les
passages sont proposés à toutes les sections après la collecte ; la sélection
par pertinence décide où ils servent. La demande explicite prime sur
`domains_whitelist` et `exclusions` (événement `sources.requise_hors_contraintes`).
En fin de pipeline, `apply_required_sources` ajoute au rapport :

- `required_sources_failed` (`url`, `reason`) et `degraded: true` si une URL
  n'a pas pu être extraite ;
- `required_sources_uncited` si une URL extraite n'est pas citée dans le
  texte, sans dégrader le rapport.

### 3.3 Plan (`stages/plan.py`, `plan_prompt.py`, `plan_schema.py`)

Un appel LLM produit le plan : analyse de complexité, sections, cibles par
section (mots, profondeur `light|moderate|deep`, objectifs, questions,
termes de recherche, axe de sources), enchaînements, stratégie de recherche.
Avec un client qui gère les sorties structurées (fournisseur `openai`), la
réponse est contrainte par un schéma (`research_plan`) : 10 sections au plus,
3 termes de recherche de 90 caractères au plus par section, 100 à 1200 mots
par section. Les sections qui décrivent le travail et non le sujet
(« stratégie de recherche », « méthodologie »...) sont filtrées.

Plan de repli : si la réponse est inexploitable, un plan générique (introduction,
analyse, conclusion) est utilisé. L'événement `plan.repli` donne la cause et la
réponse porte `degraded: true` avec `plan_fallback_reason`.

Sections imposées : `enforce_imposed_sections` garantit, après le LLM, que le
plan contient exactement les titres demandés, dans l'ordre demandé. Une
section proposée en trop est retirée, une section manquante est ajoutée avec
une cible générique, un titre proche (casse, espaces) est remplacé par le
titre demandé. Les écarts sont notés (`plan.sections_imposees`). Le repli
conserve les sections imposées.

### 3.4 Recherche par section (`stages/search.py`, `queries.py`)

Pour chaque section : requêtes tirées du plan (`search_terms`, ou une requête
réduite), nombre de sources selon la profondeur (3, 5 ou 8 ;
`max_sources_per_section` les remplace), moteurs de la cascade. La cascade
retient les 3 meilleurs moteurs disponibles de l'axe de la section, d'après le
catalogue de moteurs lu sur SearXNG. Les contraintes de domaines s'appliquent
après la recherche, sur un lot plus large (`search_pool_multiplier`). Une
requête à opérateur (`site:`, `filetype:`) sans résultat est rejouée sans
opérateur (`collecte.repli_sans_operateur`). Un échec de recherche donne une
section sans source (`collecte.recherche_section_echec`), pas un rapport en
échec.

### 3.5 Extraction (`stages/extract.py`)

Chaque URL de la section est extraite (au plus `extraction_url_cap_default`,
5, sauf `max_sources_per_section` ou `extraction_cap_follows_depth`). Un cache
d'extraction par requête évite d'extraire deux fois la même page pour deux
sections. Le document entier est découpé en passages (`app/utils/chunking.py`),
jamais tronqué : tout le texte reste candidat à la sélection.

### 3.6 Collecte et complément (`stages/collect.py`)

Les sections sont traitées en parallèle, avec un plafond de concurrence
(`MAX_CONCURRENT_EXTRACTIONS`, 3) et un budget de temps par section
(`max(60 s, 0,8 x le délai de l'orchestrateur)`). Une section en échec ou trop
lente devient une section sans données, sans annuler les autres. Ensuite,
toute section sous son seuil de sources reçoit une recherche ciblée
(`targeted_search`). Le seuil est `sources_per_section` du plan (2 sans
indication), ajusté de -1, 0 ou +1 selon la profondeur, et au moins 1.

### 3.7 Croisement et sélection (`stages/crossref.py`, `select.py`)

Le croisement mesure la corroboration à deux niveaux : la même URL citée dans
une autre section est un signal faible ; un passage proche (similarité
d'embeddings entre `corroboration_min_similarity` et
`corroboration_max_similarity`) venu d'un autre domaine est un signal fort.
Une similarité plus haute est une reprise verbatim, non comptée. Sans
embeddings (fournisseur sans `embed`), seul le signal faible subsiste et le
compte de corroboration reste à 0 (`selection.repli_mots_cles`).

La sélection note chaque passage (mots-clés, similarité sémantique,
corroboration, bonus de densité factuelle), retient ceux qui franchissent le
seuil de la profondeur de la section, dans la limite d'un budget de
caractères. Si aucun passage ne franchit le seuil, elle garde les 3 premiers.
Repli sur les mots-clés seuls sans embeddings (`selection.repli_mots_cles`).

### 3.8 Rédaction (`stages/synthesize.py`)

L'objectif de mots est réduit pour une section sans source (150 mots au plus)
ou pauvre en sources. Le prompt reçoit les passages sélectionnés (16 000
caractères au plus par défaut) et exige une citation `[SOURCE:url]` après
chaque affirmation. La rédaction est tentée 3 fois, avec attente croissante.
Une sortie tronquée est détectée (`selection.synthese_tronquee`) ; une
fidélité numérique est contrôlée (`selection.chiffres_absents`).

Deux marqueurs internes disent l'échec :

- `[SECTION_SANS_SOURCE]` : pas assez de matière, la section n'est pas
  rédigée. Le LLM n'est pas appelé, pour ne rien écrire hors sources ;
- `[SYNTHESE_ECHOUEE]` : les 3 essais ont échoué.

### 3.9 Cohérence (`stages/coherence.py`)

Un appel LLM lit un aperçu de chaque section (500 caractères) et propose des
améliorations. Les 3 premières de priorité haute sont appliquées. Une
transition est collée à la fin d'une section (`coherence.transition_collee`),
sauf si `coherence_append_enabled` est faux (`coherence.append_desactive`).

### 3.10 Assemblage (`stages/assemble.py`)

`final_assembly` rassemble les sections dans l'ordre du plan, convertit les
citations en bibliographie, retire les sections marquées et construit le
résumé. Les sections `[SYNTHESE_ECHOUEE]` sont retirées et listées dans
`failed_sections`, avec `degraded: true`. Les sections `[SECTION_SANS_SOURCE]`
sont retirées et listées dans `unsourced_sections` ; au-delà de la moitié des
sections (`unsourced_degraded_ratio`), le rapport est dégradé. Le résumé est
l'extrait des 2 premières phrases de la première section, ou vide avec
`summary_mode = none`. Le titre est `Analyse: <sujet>` ou un titre court avec
`clean_title`.

### 3.11 Enrichissement (`stages/enrich.py`, `regenerate.py`)

Après l'assemblage, une analyse de qualité par le LLM note le rapport. Le
travail s'arrête si le score atteint `enrichment_stop_score` (85) et que le
rapport n'est pas trop court. Sinon, les sections dont le score est sous 75
ou qui font moins de 2000 caractères (2 au plus par itération) sont
enrichies par une recherche ciblée puis régénérées. Une seule itération par
défaut (`enrichment_max_iterations`). Un
échec de cette étape est noté (`enrichissement.etape_echouee`) et le rapport
initial est conservé. `enrichment_enabled` faux la saute.

## 4. Modèles de frontière (`app/research/models.py`)

Les étapes manipulent des dictionnaires. Six modèles Pydantic décrivent leur
forme aux frontières : `Plan`, `SectionTarget`, `Source`, `Passage`,
`SectionDraft`, `Report` (avec `ReportSection`). Ils acceptent les champs
supplémentaires et n'imposent que ce que les étapes lisent. Les fonctions
`check_plan`, `check_collected`, `check_drafts` et `check_report`
(`stages/boundaries.py`) valident la sortie d'une étape et notent un écart
(`pipeline.frontiere_invalide`) sans jamais interrompre le pipeline ni
modifier les données.

## 5. Contexte d'exécution (`app/research/context.py`)

`ExecutionContext` porte tout l'état d'une requête : configuration effective
(`config`), journal des dégradations (`diagnostics`), langue de recherche,
contraintes de domaines, sources requises, sections rédigées
(`final_content`), cache d'extraction, cache d'embeddings, historique des
passages extraits, étapes tracées (`steps`) et sources découvertes. Il est
créé par l'appelant (`execute_with_deadline`), pour que le rapport partiel lise
ce qui est déjà rédigé quand le délai expire.

`StageServices` (`stages/services.py`) porte les dépendances : client LLM,
client de recherche, gestionnaire d'extraction, extracteur de données,
configuration par défaut, délai, fonction d'attente. Les tests y injectent des
doublures (`tests/fakes/`).

## 6. Configuration (`app/research/pipeline_config.py`)

`PipelineConfig` regroupe 132 champs : 20 drapeaux de type B (changements dont
l'effet sur la qualité n'est pas connu, livrés désactivés), un plafond de
sources et les budgets, seuils et températures de chaque étape. Chaque champ
se surcharge par `WEBTOOLS_PIPELINE_<NOM>`. La configuration est lue à chaque
requête, jointe à la réponse (`diagnostics.config`) et au manifeste de run du
harnais. La liste complète est dans `docs/CONFIGURATION.md`.

## 7. Diagnostics (`app/research/diagnostics.py`)

Chaque repli, exception avalée ou donnée ignorée se déclare par
`ctx.diagnostics.note(événement, **détails)`. La réponse porte
`diagnostics.events` : un compteur par événement et les 50 derniers
événements. Quelques événements :

| Événement | Sens |
|---|---|
| `plan.repli`, `plan.sections_imposees` | Plan générique utilisé ; écarts avec les sections imposées |
| `collecte.recherche_section_echec`, `collecte.extraction_url_echec` | Une recherche ou une extraction a échoué, la section continue |
| `collecte.catalogue_moteurs_indisponible` | Pas de sélection de moteurs |
| `selection.repli_mots_cles`, `selection.synthese_tronquee`, `selection.chiffres_absents` | Sélection sans embeddings, sortie tronquée, chiffres sans source |
| `coherence.transition_collee`, `coherence.append_desactive` | Texte ajouté à une section par la revue de cohérence |
| `enrichissement.*` | Itérations, régénérations, échecs de l'enrichissement |
| `sources.requise_echec`, `sources.requise_non_citee` | Bilan des sources requises |
| `pipeline.frontiere_invalide` | Une frontière typée n'a pas validé |
| `api.deadline_exceeded`, `api.orchestrator_exception`, `api.invariants_failed` | Délai global, exception du pipeline, contrôle des invariants en échec |

## 8. Invariants (`app/research/invariants.py`)

`check_report` est une fonction pure : elle lit le rapport et rend
`{ok, violations}` sans rien corriger. Le résultat est dans
`diagnostics.invariants`. Les codes, stables :

| Code | Violation |
|---|---|
| `ORPHAN_CITATION` | Citation `[n]` sans entrée de bibliographie |
| `BIBLIO_WITHOUT_URL` | Entrée de bibliographie sans URL |
| `INTERNAL_MARKER` | Marqueur interne dans le titre, le résumé ou le texte |
| `EMPTY_SECTION` | Section sans contenu |
| `META_SECTION` | Titre de section qui décrit le travail et non le sujet |
| `TRUNCATED_SECTION` | Dernière phrase non terminée |
| `CITATION_RESIDUE` | Résidu d'URL collé à une citation, par exemple `[1]/b]` |

## 9. Citations, bibliographie et délai global

### Citations (`app/research/citations.py`)

Le modèle écrit `[SOURCE:url]`. Une conversion en une seule passe
d'expression régulière remplace chaque marqueur par `[n]` et construit la
bibliographie (`id`, `url`, `title`). Elle est idempotente (appelée sur le
chemin principal et sur le rapport partiel). Un marqueur non fermé, que
laisse une génération coupée par la limite de jetons, est traité aussi.
Trois drapeaux, désactivés par défaut :

- `validate_cited_urls` : rapproche une URL citée mais non fournie de la source
  fournie la plus proche, ou retire la citation ;
- `bibliography_cited_only` : limite la bibliographie aux sources citées ;
- `bibliography_real_titles` : titre réel de la page au lieu de « Source n ».

### Délai global

`timeout` (60 à 1200 s) borne la requête. Passé ce délai, la tâche est
annulée, `_partial_report` assemble les sections non vides déjà rédigées,
convertit leurs citations, et la réponse porte `timed_out: true`,
`degraded: true` et la liste `missing_sections`. Toute exception du pipeline
devient une réponse `success: false` de même forme, avec les diagnostics
conservés. L'orchestrateur reçoit le délai `min(300 s, 0,6 x timeout)` ; le budget de
temps par section de la collecte en est 0,8 fois, avec un minimum de 60 s.

## 10. Rejeu de la collecte (`app/research/replay.py`)

Avec `WEBTOOLS_REPLAY_MODE=record`, la recherche et l'extraction sont
enregistrées dans `WEBTOOLS_REPLAY_DIR` ; avec `replay`, elles sont servies
depuis le disque et une requête inconnue échoue explicitement. On mesure ainsi
un changement de rédaction ou de citations à collecte figée. La réponse porte
`diagnostics.replay` (`mode`, `served`, `missed`, `recorded`) ; un run avec
`missed` supérieur à 0 n'est pas fidèle. Un changement de collecte invalide
l'enregistrement. Détails : `benchmarks/replay.md`.

## 11. Vérifier un rapport

De l'intérieur vers l'extérieur :

1. Frontières typées : un écart de forme entre deux étapes est noté.
2. Marqueurs de section : `failed_sections`, `unsourced_sections`,
   `missing_sections`, `degraded`, `timed_out`, `required_sources_failed`.
3. Invariants du rapport : `diagnostics.invariants`.
4. Journal des dégradations : `diagnostics.events`.
5. Hors ligne : tests unitaires et de contrat avec doublures (`tests/`), rejeu
   de la collecte, puis mesure sur DeepResearch Bench (RACE pour la qualité,
   FACT pour les citations) selon `benchmarks/PROTOCOLE.md`.

Les niveaux 1 à 4 détectent une panne ou une incohérence. Seul le niveau 5
mesure la qualité.

## 12. Fichiers

| Fichier | Rôle |
|---|---|
| `app/api/v1/endpoints/research_deep.py` | Requête, réponse, construction de l'orchestrateur |
| `app/agents/intelligent_orchestrator.py` | Enchaînement, délai global, rapport partiel |
| `app/research/stages/` | Une étape par module |
| `app/research/models.py`, `context.py`, `pipeline_config.py` | Modèles de frontière, contexte, configuration |
| `app/research/citations.py`, `invariants.py`, `diagnostics.py`, `replay.py` | Citations, invariants, journal, rejeu |
| `app/core/output_formatter.py` | Forme de la réponse de l'endpoint |
| `app/core/llm/` | Clients LLM |
| `app/services/search_service.py`, `engine_catalog.py` | SearXNG, catalogue et cascade de moteurs |
| `app/manager.py`, `app/extractors/` | Chaîne d'extraction |
