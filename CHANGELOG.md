# Changelog - Webtools API

## [3.0.0] - 2026-11-12

Chantier de fiabilisation (`PLAN_FIABILISATION.md`, plus de 110 commits depuis
le commit `0ae36e6`). Il rend le service mesurable, visible quand il tombe en
panne, protégé et reproductible. Il ne cherche pas à relever les scores : les
changements dont l'effet sur la qualité n'est pas connu sont livrés derrière
des drapeaux désactivés (`docs/CONFIGURATION.md`). Les campagnes de mesure
qui décideront de leur adoption ne sont pas encore faites.

### Changements incompatibles

- **`/api/v1/research/deep` refuse les champs inconnus (422).** Ont été
  retirés, car sans effet : `max_steps`, `max_results`, `context.min_sources`,
  `context.date_range`, `context.include_data`, `context.include_reports`,
  `context.include_academic`, `sources.suggested`, `output_format.structure`,
  `output_format.include_charts`, `output_format.include_timeline`. Une requête
  qui les envoie échoue au lieu d'être acceptée en silence.
- **Paramètres qui ont désormais un effet.** `timeout` (60 à 1200 s, délai
  global), `max_sources_per_section` (1 à 20), `sources.required`,
  `output_format.sections` et `output_sections`, `output_format.include_section_data`.
- **Forme de la réponse de `/research/deep`.** Un JSON unique, de même forme
  en succès, en rapport dégradé et en échec : `success`, `degraded`,
  `timed_out`, `error`, `result`, `failed_sections`, `unsourced_sections`,
  `missing_sections`, `execution`, `sources`, `diagnostics`. Il n'y a plus de
  réponse en flux. `success` vaut `false` pour un rapport dégradé. Le champ
  `data` des sections (texte intégral des passages) n'est joint que sur demande.
- **Le serveur MCP est un client HTTP de l'API REST.** Il appelle l'API
  (`WEBTOOLS_API`, `WEBTOOLS_API_KEY`) au lieu d'importer ses fonctions :
  l'usage réel et la mesure passent par le même pipeline. L'API doit donc être
  joignable depuis le serveur MCP.
- **Le service refuse de démarrer sans `WEBTOOLS_API_KEY`**, sauf si
  `WEBTOOLS_ALLOW_ANONYMOUS=1` est posé explicitement. Tous les endpoints
  `/api/v1/...` exigent l'en-tête `X-API-Key`.
- **Deux environnements Python.** L'API (`requirements.lock`) et le serveur MCP
  (`requirements-mcp.lock`) ne se résolvent pas ensemble (`browser-use` exige
  `pydantic<2.11`, `fastmcp` exige `pydantic>=2.12`). L'extra `mcp` de
  `pyproject.toml` ne s'installe pas avec les dépendances de l'API.
- **Routes héritées supprimées** : `/api/v1/research` et `/api/v1/search-site`,
  qui restaient joignables, rendaient des succès sur échec et lançaient un agent
  navigateur avec la sécurité désactivée.
- **Défauts du LLM.** Le modèle par défaut est `qwen3-6-35b-moe` sur le LLM
  compatible OpenAI de SSPCloud (`https://llm.lab.sspcloud.fr/api`), défini une
  seule fois dans `app/core/config.py`. Auparavant le défaut était `gpt-4o`
  sans URL.
- **Erreurs HTTP plus franches.** `/research/quick` rend 502 ou 504 quand le
  moteur de recherche ou le LLM échoue, et non une réponse vide en succès ;
  `/vision` rend 503 sans clé LLM, 501 si le modèle n'a pas la vision.
  `ExtractionOptions` refuse les champs inconnus.

### Mesure

- Le harnais de benchmark est exécutable hors du pod d'origine, sans chemin ni
  clé en dur (`benchmarks/INSTALL.md`). Il refuse de mesurer si le commit servi
  diffère de celui du dépôt et écrit un manifeste de run (commit, modèle, juge,
  tâches, configuration effective).
- Les rapports dégradés ou faibles sont enregistrés, plus écartés.
- `benchmarks/compare.py` compare deux runs sur les tâches appariées et donne
  l'effet minimal détectable.
- `/health` publie le commit chargé, l'heure de démarrage et l'état réel de
  chaque dépendance (Playwright, SearXNG, LLM, catalogue de moteurs).
- Enregistrement et rejeu de la collecte (`WEBTOOLS_REPLAY_MODE`,
  `benchmarks/replay.md`) pour mesurer un changement de rédaction à collecte
  figée.

### Robustesse

- Journal de diagnostics par requête : chaque repli ou exception avalée laisse
  une trace dans `diagnostics.events` de la réponse.
- Invariants d'un rapport (`diagnostics.invariants`) : citation orpheline,
  marqueur interne, section vide ou tronquée, résidu de citation.
- Délai global sur `/research/deep` : passé le délai, la réponse porte les
  sections déjà rédigées avec `degraded: true` et `timed_out: true`.
- Une section sans source n'est plus rédigée hors sources
  (`unsourced_sections`) ; une synthèse en échec est signalée
  (`failed_sections`) ; un plan de repli rend le rapport dégradé.
- Citations : conversion en une passe (plus de résidu `[1]/b]` pour des URL à
  préfixe commun), marqueurs non fermés traités.
- Client LLM : contenu vide et troncature détectés, instance partagée,
  échéance totale appliquée aux trois fournisseurs.
- Extraction : échec explicite sur statut HTTP supérieur ou égal à 400, sur PDF
  illisible, taille lue en flux, redirections revalidées, budget de temps par
  palier. Sans clé LLM, `/extract` se limite à l'extraction directe.
- `.env` chargé au démarrage ; catalogue de moteurs rechargé après un échec.

### Sécurité

- Garde SSRF durcie et asynchrone (CGNAT, 6to4, URL mal formées), appliquée à
  `llm_config.base_url`, aux images de `/vision`, aux redirections PDF et RSS.
- Clé d'API (`X-API-Key`) : l'absence de clé configurée n'ouvre plus l'API,
  elle empêche le démarrage ; comparaison sur des octets (plus de 500 sur une
  clé non ASCII).
- Clés et secrets lus dans l'environnement uniquement.

### API

- `/research/quick` : comptes de sources tentées, extraites et écartées ;
  `confidence` n'est plus déduite du nombre de sources (`high`, `medium`,
  `low`) : elle vaut toujours `null`, aucune mesure fiable n'existe.
- `/vision` : client LLM partagé, analyse vide rendue en erreur.
- `/extract` : le prompt personnalisé complète le gabarit au lieu de le
  remplacer.
- `sources.required` : les URL sont extraites avant le plan et proposées à
  toutes les sections ; un échec rend le rapport dégradé
  (`required_sources_failed`).
- `output_format.sections` : le plan contient exactement les sections demandées.

### Configuration

- `PipelineConfig` : 132 champs (20 drapeaux de type B, budgets, seuils et
  températures qui étaient écrits en dur), surchargeables par
  `WEBTOOLS_PIPELINE_<NOM>`, joints à chaque réponse. Liste complète dans
  `docs/CONFIGURATION.md`, générée depuis la classe.
- Défauts du LLM, de l'URL SearXNG et du service définis une seule fois dans
  `app/core/config.py`.

### Déploiement

- Image construite et déploiement vérifié localement : torch en roues CPU,
  verrous compilés pour Linux x86_64, commit passé au build
  (`WEBTOOLS_COMMIT`).
- Trois services identiques avec Docker Compose et `start_all.sh` : SearXNG
  (image épinglée par condensat, configuration versionnée dans
  `docker/searxng/settings.yml`), API, serveur MCP.
- Intégration continue et commandes `make check` et `make check-mcp`.

### Structure

- L'orchestrateur ne garde que l'enchaînement. Chaque étape est un module de
  `app/research/stages/` (exploration, plan, recherche, extraction,
  croisement, sélection, synthèse, cohérence, enrichissement, assemblage),
  reliées par des modèles de frontière Pydantic (`Plan`, `SectionTarget`,
  `Source`, `Passage`, `SectionDraft`, `Report`). L'état d'une requête vit
  dans `ExecutionContext`. Voir `DEEP_RESEARCH_ARCHITECTURE.md`.
- Code mort supprimé : `content_cleaner.py`, dix méthodes de l'orchestrateur,
  `AdaptiveNavigator`, `DataProcessor`, `DataValidator`, le catalogue d'outils,
  les routes héritées.
- Suites de tests : mécanismes déterministes, extracteurs, client LLM, API,
  configuration, étapes du pipeline, test de contrat du pipeline avec
  doublures (`tests/fakes/`), tests de documentation.

### Reste à faire

Les campagnes de mesure M0 à M6 (`PLAN_FIABILISATION.md`, section 8) et le
retrait des drapeaux qu'elles tranchent (lot V5-2).

---

## [2.0.0] - 2025-11-14

Refonte de l'architecture de recherche approfondie.

### Ajouts

- Workflow en 4 phases : exploration et plan détaillé, construction section par
  section, cohérence, finalisation. Fonctions d'étape actuelles :
  `exploratory_phase`, `create_detailed_plan`, `section_research_phase`,
  `section_extraction_phase`, `cross_reference_data`,
  `synthesize_single_section`, `analyze_intersections`,
  `apply_coherence_improvements`, `final_assembly`, `add_complete_traces`
  (modules de `app/research/stages/` depuis la 3.0).
- Plan avec analyse de complexité sur 5 critères et profondeur par section.
- Extraction des sous-thèmes depuis les extraits (`extract_topics_from_snippets`).
- Sélection sémantique des passages selon la profondeur (`light`, `moderate`,
  `deep`).
- Traces complètes du processus dans le rapport.
- Nettoyage avancé du contenu (module `content_cleaner.py`, supprimé en 3.0
  car jamais appelé).

### Modifications

- `execute_intelligent_research()` refondu sur le workflow en 4 phases.
- `ExecutionContext.initialize_sections()` : prise en charge du plan.
- `app/extractors/direct_extractor.py` : contenu structuré au lieu du HTML brut.
- Dépendance ajoutée : `beautifulsoup4>=4.12.0`.

### Corrections de la 3.0 sur cette entrée

Cette entrée annonçait une analyse de cohérence « distribuée » au-delà de 10
sections, des rapports de plus de 1000 sections et une réduction de 87 % du
HTML. Le code actuel n'a qu'une analyse de cohérence, sur des aperçus de
section, et ces chiffres n'ont pas été reproduits. Les mesures de l'époque
(rapports de 2 à 3 minutes sur trois sujets) ne sont plus citées.

---

## [1.0.0] - Versions antérieures

- Endpoints de recherche (search, search_site)
- Extraction web (directe, adaptative)
- Navigation API
- Traitement de données
- Traçage basique
