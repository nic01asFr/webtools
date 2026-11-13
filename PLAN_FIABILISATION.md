# Plan de fiabilisation de webtools

Cadrage pour des équipes d'agents Claude Sonnet. Établi le 12 novembre 2026
sur le commit `0ae36e6` (branche `main`), à partir d'une lecture du code et
de deux reproductions exécutées.

## 1. Comment lire ce document

- **Un agent chargé d'un lot** lit les sections 2 à 4, puis la fiche de son
  lot (section 7) et les constats qu'elle cite (annexe A). Rien d'autre
  n'est nécessaire.
- **L'intégrateur** lit tout et tient le tableau d'état (annexe C).
- Les numéros de ligne sont ceux du commit `0ae36e6`. Ils dérivent dès la
  première fusion : repérez-vous par le **nom de la fonction**.

## 2. But

webtools produit des rapports de recherche sourcés, par API REST et par
serveur MCP, sur une infrastructure souveraine (SSPCloud, modèle
`qwen3-6-35b-moe`, moteur SearXNG). La qualité est suivie sur DeepResearch
Bench selon `benchmarks/PROTOCOLE.md`.

Trois objectifs, dans cet ordre de priorité :

1. **Ce qui est mesuré est ce qui est utilisé.** Aujourd'hui le benchmark
   passe par l'API REST alors que l'usage réel passe par le serveur MCP, qui
   n'exécute pas le même pipeline (constat C12).
2. **Toute panne est visible.** Un repli, une troncature ou une exception
   avalée doit laisser une trace dans la réponse, pas seulement dans un
   journal.
3. **Tout changement de comportement est mesuré avant d'être adopté.** Les
   agents livrent le code ; la mesure se fait sur le pod (section 8).

Ce plan ne cherche pas à améliorer les scores directement. Il retire ce qui
les fausse, puis prépare des paquets de changements mesurables.

## 3. État de départ (vérifié)

| Élément | État |
|---|---|
| Tests | 1 fichier, 8 tests, tous verts. Ils couvrent `ContentDetector` et `PromptTemplates` seulement |
| Environnement local | `langchain_core`, `langchain_openai`, `browser_use`, `trafilatura` absents : `app.agents.intelligent_orchestrator` ne s'importe pas tant que `pip install -e ".[dev]"` n'a pas été fait |
| ruff | 62 erreurs (dont 1 `F821` nom non défini, 10 `except:` nus). 44 fichiers sur 58 ne sont pas au format `ruff format` |
| Orchestrateur | `app/agents/intelligent_orchestrator.py`, 4 367 lignes, dont 1 314 mortes (C30) |
| Intégration continue | aucune |
| Benchmark | exécutable uniquement sur le pod (chemins et clés en dur, C03) |
| Dépôt | propre, 51 commits, dernier commit le 21 août 2026 |

## 4. Règles communes à tous les agents

### 4.1 Trois types de changement

Chaque lot, et chaque commit, annonce son type.

| Type | Définition | Preuve exigée |
|---|---|---|
| **N** (neutre) | Ne change pas ce que le pipeline produit : tests, outillage, déplacement de code, suppression de code mort | Tests verts ; pour un déplacement, le test de contrat (lot V1-4) reste vert sans modification |
| **A** (correctif sûr) | Corrige un défaut qui n'a qu'un seul comportement correct possible | Un test qui échoue avant et passe après |
| **B** (à mesurer) | Change un choix du pipeline dont l'effet sur la qualité n'est pas connu d'avance | Livré derrière un drapeau, **désactivé par défaut**, avec test des deux branches |

En cas de doute entre A et B, c'est B. Un prompt modifié est toujours B.

### 4.2 Drapeaux

Les changements de type B se pilotent par un champ booléen ou numérique de
`PipelineConfig` (créé par le lot V1-4), dont la valeur par défaut reproduit
le comportement actuel. Le nom du champ dit ce qu'il active, par exemple
`separate_search_terms`. La configuration effective est jointe à chaque
réponse (bloc `diagnostics`) et au manifeste de run. Un drapeau est retiré
par le lot V5-2 une fois tranché par la mesure.

### 4.3 Périmètre de fichiers

Chaque fiche liste les fichiers ou fonctions que le lot **possède**. Un
agent ne modifie rien d'autre. S'il découvre un défaut hors périmètre, il
le signale dans son compte rendu et n'y touche pas : c'est ce qui permet à
plusieurs agents de travailler sur le même fichier sans se gêner.

### 4.4 Git

- Toutes les fusions vont dans la branche d'intégration `fiabilisation`,
  partie de `main`. `main` n'est pas modifiée tant que le responsable du
  projet n'a pas validé l'ensemble.
- Un lot = une branche `lot/<identifiant>` dans son propre worktree, partie
  de la tête de `fiabilisation` au moment du lancement.
- Commits en français, sans accents, au présent, comme l'historique
  existant (« Corrige la troncature des sections »).
- Pas de rebase : l'intégrateur fusionne et résout les conflits.
- Un agent ne pousse pas et ne fusionne pas. La fusion revient à
  l'intégrateur.

### 4.5 Vérifications avant de rendre la main

```bash
python -m pytest tests -q                 # tout vert
python -m ruff check <fichiers touches>   # aucune erreur nouvelle
python -m ruff format <fichiers crees>    # fichiers nouveaux uniquement
```

Ne lancez pas `ruff format` sur un fichier existant : 44 fichiers seraient
reformatés et les diffs deviendraient illisibles. Les tests ne font aucun
appel réseau et aucun appel LLM réel ; un test qui en a besoin porte le
marqueur `@pytest.mark.live` et est exclu par défaut.

### 4.6 Conventions de code

- Pas d'emoji dans le code ajouté, y compris dans les messages de journal.
  N'enlevez pas ceux qui existent hors de votre périmètre.
- Commentaires en français sans accents, comme le reste du code. Un
  commentaire explique une contrainte ou une raison, pas l'historique du
  changement : l'historique va dans le message de commit.
- Annotations de type sur toute fonction nouvelle.
- À partir de la vague 3, tout `except` qui avale une erreur ou déclenche
  un repli dans votre périmètre appelle `Diagnostics.note(...)` : la
  réponse doit dire ce qui s'est dégradé (C46).
- Une contrainte destinée au modèle va dans le schéma JSON de sortie
  (champ `description`, `enum`, `required`), pas dans la prose du prompt :
  c'est le constat central de `PROTOCOLE.md`, vérifié quatre fois.

### 4.7 Ce qu'un agent ne fait pas

- Lancer le benchmark ou interpréter un score. Sous 20 tâches, un score ne
  mesure rien (`PROTOCOLE.md`, section « Puissance statistique »).
- Modifier un prompt sans drapeau.
- Affaiblir ou supprimer un test pour le faire passer.
- Ajouter une dépendance sans la déclarer dans `pyproject.toml` et le dire
  dans son compte rendu.

### 4.8 Compte rendu de fin de lot

Cinq rubriques, courtes :

1. Ce qui est fait, constat par constat (identifiants de l'annexe A).
2. Ce qui n'est pas fait, et pourquoi.
3. Drapeaux ajoutés : nom, défaut, effet.
4. Défauts découverts hors périmètre.
5. Sortie de `pytest` et de `ruff check`, copiée telle quelle.

## 5. Rôles

| Rôle | Nombre | Charge |
|---|---|---|
| Intégrateur | 1, permanent | Lance les lots d'une vague, fusionne dans l'ordre, relance les tests après chaque fusion, tient l'annexe C. Ne code pas de lot |
| Réalisateur | 1 par lot | Exécute une fiche, dans son worktree |
| Relecteur | 1 par vague | Relit chaque branche contre sa fiche. Il reçoit la fiche et le diff, jamais le compte rendu du réalisateur, pour ne pas hériter de ses conclusions |
| Opérateur de mesure | 1, sur le pod | Exécute les campagnes de la section 8. Seul rôle qui touche au pod et aux clés |

Une vague mobilise au plus 9 réalisateurs à la fois.

## 6. Vagues

| Vague | Lots | Mode | Entrée | Sortie |
|---|---|---|---|---|
| 0 | V0-1 | 1 agent | décisions de la section 9 confirmées | environnement installable, `make check` existe |
| 1 Socle | V1-1 à V1-9 | 9 en parallèle | vague 0 fusionnée | test de contrat vert sur `main` ; campagne M0 lançable |
| 2 Élagage | V2-1 | 1 agent, seul sur `app/` | vague 1 fusionnée | orchestrateur sans code mort, test de contrat inchangé et vert |
| 3 Pipeline | V3-1 à V3-8 | 8 en parallèle, périmètre par fonction | vague 2 fusionnée | correctifs A actifs, changements B derrière drapeaux |
| 4 Structure | V4-1, puis V4-2 ; V4-3 en parallèle | séquentiel sur l'orchestrateur | vague 3 fusionnée, campagne M1 passée | orchestrateur découpé en étapes |
| 5 Clôture | V5-1, V5-2 | 2 en parallèle | campagnes M2 à M5 tranchées | documentation à jour, drapeaux retirés |

Pourquoi cet ordre : la vague 1 n'a presque aucun fichier en commun entre
lots. La vague 2 retire 30 % de l'orchestrateur avant que sept agents n'y
travaillent. La vague 3 se partage l'orchestrateur par fonction. La vague 4
déplace tout le code et ne tolère donc aucun travail parallèle sur ce
fichier.

## 7. Fiches de lot

### V0-1 Environnement et commande de vérification — type N

- **Possède** : `Makefile`, `pyproject.toml` en entier pendant cette
  vague, `.github/workflows/check.yml`, `tests/conftest.py` (création).
- **Travail** :
  1. Faire en sorte que `pip install -e ".[dev]"` suffise pour importer
     tout `app/` sans navigateur installé. Si un import de module exige
     Chromium ou un réseau, le rendre paresseux. Déclarer les dépendances
     importées mais absentes de `pyproject.toml` : `pdfplumber`, `pypdf`,
     et `h2` si le client Albert est maintenu (C58).
  2. Créer `make check` : `ruff check` limité aux règles `F821,F811,E9`
     sur tout le dépôt (les erreurs qui cassent à l'exécution), puis
     `pytest tests -q -m "not live"`.
  3. Déclarer le marqueur `live` dans la configuration `pytest`.
  4. Workflow GitHub qui lance `make check` sur chaque poussée.
  5. Supprimer `app/core/content_cleaner.py` (409 lignes) et ses deux
     références dans `app/extractors/direct_extractor.py` : le module est
     instancié mais jamais utilisé, et porte la seule erreur `F821` du
     dépôt (C17, C71). Prouver l'absence d'appelant avant de supprimer.
- **Acceptation** : sur un environnement vierge,
  `pip install -e ".[dev]" && make check` passe ;
  `python -c "import app.agents.intelligent_orchestrator"` réussit.
- **Constats** : C17, C58.

### V1-1 Harnais de benchmark honnête — type N

- **Possède** : `benchmarks/` en entier, `tests/unit/benchmarks/`.
- **Travail** :
  1. `generate.py` enregistre tout rapport qui contient du texte, y
     compris quand la réponse porte `success: false` et `degraded: true`.
     Le diagnostic note `degraded`, `failed_sections`,
     `unsourced_sections`. Seule une réponse sans rapport est un échec (C01).
  2. Retirer `--max-sources` tant que le paramètre est sans effet côté
     service (C02) ; il reviendra avec le lot V3-3.
  3. Chemins et clés par variables d'environnement
     (`DRB_HOME`, `WEBTOOLS_API`, clés lues dans l'environnement), avec
     échec explicite si elles manquent (C03).
  4. Manifeste `<run>_manifest.json` écrit au démarrage : commit du dépôt,
     commit et heure de démarrage renvoyés par `/health` (lot V1-2), modèle,
     juge prévu, liste des identifiants de tâches, moteurs SearXNG actifs,
     bloc `diagnostics.config` de la première réponse (C04).
  5. Refus de démarrer si le commit servi par `/health` diffère du commit
     du dépôt, sauf option `--allow-mismatch`.
  6. Nouveau script `compare.py` : compare deux runs **sur les seules
     tâches présentes dans les deux**, affiche le nombre de tâches
     appariées, le delta moyen, l'écart-type des deltas et l'effet minimal
     détectable (C05).
  7. Remplacer `sanity_check` par un appel au module d'invariants du lot
     V1-4 dès qu'il est fusionné ; en attendant, garder l'existant.
- **Acceptation** : tests unitaires de `convert.py`, de la reprise après
  interruption, de l'enregistrement d'un rapport dégradé (réponse simulée)
  et de `compare.py` sur deux fichiers d'exemple dont les tâches ne se
  recouvrent qu'en partie.
- **Hors périmètre** : le dépôt DeepResearch Bench lui-même.

### V1-2 Identité du code chargé et santé réelle — type A

- **Possède** : routes `/`, `/health`, `/ready` de `app/main.py`, classe
  `HealthResponse` de `app/api/models.py`, `app/core/build_info.py`
  (création), `app/api/auth.py`, `tests/unit/test_health.py`,
  `tests/unit/test_auth.py`.
- **Travail** :
  1. `build_info.py` : commit git lu une fois à l'import (commande `git`,
     repli sur la variable `WEBTOOLS_COMMIT`, sinon `"unknown"`), heure de
     démarrage du processus, indicateur « arbre de travail modifié ».
  2. `/health` renvoie ces trois valeurs (C10).
  3. `/health` vaut `healthy` seulement si Playwright, SearXNG **et** le
     LLM répondent. Le LLM est testé par un appel minimal, mis en cache 60
     secondes pour ne pas consommer de quota à chaque sonde (C11).
  4. Le catalogue de moteurs chargé (nombre de moteurs par axe) figure dans
     `/health`.
  5. Authentification (C68, décision D6) : sans `WEBTOOLS_API_KEY`, le
     service refuse de démarrer, sauf si `WEBTOOLS_ALLOW_ANONYMOUS=1` est
     posé explicitement. Un en-tête `X-API-Key` non ASCII rend un 401 et
     non une erreur 500.
- **Acceptation** : tests avec dépendances simulées couvrant les quatre
  combinaisons panne SearXNG / panne LLM ; tests d'authentification (clé
  absente, clé fausse, en-tête non ASCII, mode anonyme explicite).
- **Hors périmètre** : la façon dont la configuration est lue (lot V1-5).

### V1-3 Tests unitaires des mécanismes déterministes — type N, A pour les défauts trouvés

- **Possède** : `tests/unit/utils/`, `tests/unit/services/`,
  `tests/unit/core/` ; et, pour correction de défaut uniquement,
  `app/utils/chunking.py`, `language.py`, `numeric_fidelity.py`,
  `factual_density.py`, `content_detector.py`,
  `app/services/engine_catalog.py`.
- **Travail, type N** : une suite par module pour `chunking`, `language`,
  `numeric_fidelity`, `factual_density`, `content_detector`,
  `engine_catalog` (cascade par axe, mise à l'écart puis réhabilitation
  d'un moteur, catalogue non chargé). Chaque suite couvre les cas limites :
  entrée vide, texte non latin, document de 100 000 caractères.
- **Travail, type A** :
  1. `numeric_fidelity` (C72) : l'expression régulière coupe tout entier
     de quatre chiffres ou plus à ses trois premiers (`2021` donne `202`),
     ignore les espaces insécables des milliers et confond virgule
     décimale et séparateur. Le contrôle rend aujourd'hui une fidélité de
     1,0 entre « 1500 en 2020 » et « 1509 en 2021 ». Le corriger, ces cas
     en tests.
  2. `language` (C80) : une consigne française à élisions (« l'impact de
     l'IA sur l'emploi ») est détectée `all` ; les entrées sans accent du
     dictionnaire ne correspondent jamais.
  3. `engine_catalog` (C78) : séparer `ingest(config: dict)` de l'appel
     HTTP ; `load` retente si SearXNG n'est pas prêt au démarrage ; l'état
     est vidé avant remplissage ; horloge injectable pour tester les
     mises à l'écart.
- **Travail, type B** : `document_to_chunk_items` reçoit un paramètre
  `v2: bool = False` (C73). En mode `v2`, un fragment de moins de 200
  caractères est rattaché à son voisin au lieu d'être supprimé, et un
  texte sans ponctuation est coupé sur une espace, avec chevauchement. Le
  branchement sur `PipelineConfig` revient au lot V3-4.
- **Acceptation** : couverture de lignes supérieure à 85 % sur ces
  modules (`pytest --cov`), aucun test réseau ; les reproductions de C72 et
  C73 en tests.
- **Constats** : C72, C73, C78, C80.

### V1-4 Socle de test du pipeline — type N

Le lot le plus structurant de la vague 1 : tout ce qui suit s'appuie dessus.

- **Possède** : `app/research/` (création : `__init__.py`,
  `pipeline_config.py`, `invariants.py`, `diagnostics.py`),
  `tests/fakes/`, `tests/contract/`, et dans l'orchestrateur uniquement
  `IntelligentOrchestrator.__init__`, `ExecutionContext.__init__` et les
  lignes d'import.
- **Travail** :
  1. **Coutures d'injection.** `IntelligentOrchestrator.__init__` accepte
     `search_client`, `extractor_manager`, `config` en paramètres
     optionnels ; par défaut, les objets actuels. Toutes les références à
     `searxng_client` dans la classe passent par `self.search_client`.
  2. **`PipelineConfig`** : dataclass figée, vide de drapeaux pour
     l'instant, avec `to_dict()` et chargement depuis des variables
     `WEBTOOLS_PIPELINE_<NOM>`.
  3. **`Diagnostics`** : objet porté par `ExecutionContext`, avec
     `note(evenement, **details)` et `to_dict()`. Il compte les replis et
     les exceptions avalées. Ce lot crée l'objet ; les lots de la vague 3
     l'alimentent chacun dans leur périmètre.
  4. **Doublures** dans `tests/fakes/` : faux LLM (réponses scénarisées par
     nom de schéma ou motif de prompt, `generate`, `generate_structured`,
     `embed`), fausse recherche (résultats par requête), faux extracteur
     (pages par URL).
  5. **`invariants.py`** : fonction pure `check_report(report) -> dict`
     qui vérifie : aucune citation `[n]` sans entrée de bibliographie ;
     aucune entrée de bibliographie sans URL ; aucun marqueur interne
     (`[SOURCE:`, `[SECTION_SANS_SOURCE]`, `[SYNTHESE_ECHOUEE]`) dans le
     texte, le titre ou le résumé ; aucune section vide ; aucun titre de
     section méta ; dernière phrase de chaque section terminée.
  6. **Test de contrat** : exécute `execute_intelligent_research` de bout
     en bout avec les doublures sur trois scénarios (nominal en anglais,
     une section sans source, échec LLM sur une synthèse) et applique
     `check_report`. Un invariant qui échoue sur le code actuel est marqué
     `xfail` avec l'identifiant du constat (C41, C44) : il sera levé par le
     lot de la vague 3 qui corrige le défaut.
- **Acceptation** : test de contrat vert (hors `xfail` documentés), en
  moins de 30 secondes, sans réseau.
- **Hors périmètre** : corriger les défauts que le test révèle.

### V1-5 Configuration et déploiement uniques — type N, A pour les incohérences

- **Possède** : `app/core/config.py`, `.env.example`, `start_all.sh`,
  `docker-compose.yml`, `docker/`, `pyproject.toml` (dépendances
  d'exécution), fichier de verrou, `GETTING_STARTED.md`.
- **Travail** :
  1. Une seule valeur par défaut pour le fournisseur, le modèle et l'URL du
     LLM, définie dans `config.py` et reprise partout (C15).
  2. `SEARXNG_BASE_URL` devient un champ de `Settings` ; exposer un
     accesseur que `main.py`, `search_service.py` et `mcp_server.py`
     pourront adopter (ne modifiez pas ces trois fichiers : signalez les
     trois points d'appel à l'intégrateur).
  3. `docker/searxng/settings.yml` devient la source unique.
     `start_all.sh` la désigne par `SEARXNG_SETTINGS_PATH`. Le contenu
     réel du pod est récupéré par l'opérateur de mesure (campagne M0) : ce
     lot prépare l'emplacement et la procédure.
  4. Image SearXNG épinglée par condensat. Dépendances verrouillées par un
     fichier de verrou généré.
  5. L'image Docker démarre les mêmes services que `start_all.sh` (C16).
  6. `.env.example` reflète la configuration réellement utilisée.
- **Acceptation** : `docker compose config` valide ; test unitaire de
  `Settings` (défauts, surcharge par environnement).

### V1-6 Serveur MCP client de l'API REST — type A

- **Possède** : `mcp_server.py`, `tests/unit/test_mcp_server.py`.
- **Travail** :
  1. Les six outils appellent l'API REST par HTTP (`WEBTOOLS_API`,
     `WEBTOOLS_API_KEY`) au lieu d'importer les fonctions d'endpoint. Le
     serveur MCP n'importe plus rien de `app/` (C12, C13).
  2. `webtools_research_deep` rend un rapport lisible par un agent :
     markdown du rapport, bibliographie, bloc `diagnostics`. Jamais le
     champ `data` des sections (C24).
  3. Retirer `max_sources` des outils tant que le paramètre est sans effet
     (C02) ; il reviendra avec le lot V3-3.
  4. Plafond de recherches approfondies simultanées
     (`MCP_MAX_CONCURRENT_DEEP`, défaut 2) : au-delà, l'outil `start`
     répond `busy` avec le nombre de tâches en cours (C14).
  5. Tâches de fond écrites sur disque (`WEBTOOLS_STATE_DIR`) pour
     survivre à une relance du superviseur : une tâche `running` retrouvée
     au démarrage devient `interrupted` (C14).
- **Acceptation** : tests avec API simulée (`httpx.MockTransport`) :
  mapping des six outils, plafond, reprise après redémarrage, clé d'API
  transmise.
- **Hors périmètre** : le flux OAuth et `AuthorizeGateMiddleware`.

### V1-7 Client LLM fiable — type A

- **Possède** : `app/core/llm/` en entier, `tests/unit/llm/`.
- **Travail** :
  1. `generate` lit `finish_reason`. Un contenu `None` ou vide lève
     `LLMClientError` ; une sortie coupée par `max_tokens` est signalée à
     l'appelant (exception dédiée ou valeur de retour enrichie, au choix,
     mais jamais un texte tronqué rendu comme un succès) (C51).
  2. `generate_structured` enveloppe ses erreurs en `LLMClientError`,
     traite le contenu `None`, distingue une sortie tronquée d'un schéma
     non respecté, et journalise le refus du schéma par le proxy (C52).
  3. Cycle de vie : `get_llm_client()` rend réellement une instance
     partagée par processus, comme les commentaires du code le supposent
     déjà, et le service la ferme à l'arrêt. Aujourd'hui chaque appel crée
     un client et son pool HTTP, jamais fermés (C50).
  4. Échéance totale par appel, paramétrable, en plus du délai par
     tentative. Les valeurs actuelles (5 réessais, 90 secondes) restent les
     défauts (C53).
  5. `get_langchain_wrapper` passe la clé et l'URL en arguments, sans
     écrire dans `os.environ` (C54).
  6. `BaseLLMClient` déclare les capacités (`supports_structured`,
     `supports_embeddings`, `supports_vision`) ; les appelants n'ont plus à
     tester `hasattr`. Ne modifiez pas les appelants : exposez les
     attributs, l'adoption se fera dans les lots de la vague 3 (C55).
  7. Injection du transport HTTP à la construction, pour tester sans
     réseau. Fabrique de clients extensible par nom, pour qu'un test puisse
     enregistrer un faux fournisseur.
  8. Selon la décision D5 : même suite de tests d'interface pour les trois
     clients, ou retrait d'Albert et d'Anthropic.
- **Acceptation** : tests avec `httpx.MockTransport` couvrant réponse
  nominale, contenu `None`, `finish_reason: length`, erreur 500 réessayée,
  schéma refusé, échéance totale dépassée ; test que deux appels à
  `get_llm_client()` rendent le même objet.
- **Hors périmètre** : la cause des fermetures de transport observées en
  production reste inconnue (annexe B). Ne retirez pas `_ensure_open`.

### V1-8 Chaîne d'extraction — types A et B

- **Possède** : `app/manager.py`, `app/extractors/agent_extractor.py`,
  `app/extractors/base.py`, `app/utils/prompts.py`, classe
  `ExtractionOptions` de `app/api/models.py`, `tests/unit/extraction/`.
- **Travail, type A** :
  1. Chaque résultat d'extraction porte le palier qui l'a produit
     (`pdf`, `rss`, `direct`, `llm`, `agent`) dans ses métadonnées.
  2. Un prompt personnalisé complète le gabarit au lieu de le remplacer :
     l'URL, les règles de sécurité de l'agent et le format de sortie
     restent présents (C57).
  3. Clé de cache incluant le prompt et les options ; cache borné en
     taille ; l'objet rendu depuis le cache est une copie (C59).
  4. `ExtractionOptions` refuse les champs inconnus au lieu de les ignorer
     (C60). Signalez à l'intégrateur les appelants qui en passent.
  5. `timeout` devient un budget global de l'extraction, tous paliers
     confondus. Aujourd'hui chaque palier a son propre délai et la durée
     réelle peut dépasser plusieurs fois la valeur demandée (C60).
  6. Un repli de l'agent vers le résultat direct est signalé dans les
     métadonnées au lieu de passer pour un succès ordinaire.
- **Travail, type B** :
  7. `rss_tier_root_only` : le palier RSS ne s'applique que si l'URL
     demandée est la racine du site ou le flux lui-même. Aujourd'hui, pour
     toute URL d'un site qui expose un flux, le contenu rendu est la liste
     des derniers titres du site, attribuée à l'URL demandée (C56). C'est
     le drapeau à mesurer en premier (campagne M2).
  8. `single_llm_pass_enabled` (défaut : vrai, comportement actuel) : pour
     une page dont l'extraction directe rend 100 à 500 caractères, le
     contenu renvoyé est aujourd'hui une réécriture par le LLM, attribuée
     à l'URL (C76). Drapeau à faux : le texte extrait est rendu tel quel.
- **Acceptation** : tests avec extracteurs simulés pour chaque palier ;
  test montrant que, drapeau actif, une URL d'article n'est jamais servie
  par le flux du site ; test du budget global.
- **Constats** : C56, C57, C59, C60, C76.

### V1-9 Extracteurs : robustesse et garde SSRF — type A

- **Possède** : `app/utils/url_safety.py`,
  `app/extractors/direct_extractor.py`, `pdf_extractor.py`,
  `rss_extractor.py`, `app/core/browser/playwright_manager.py`,
  `tests/unit/extractors/`.
- **Travail** :
  1. Extraction directe (C75) : lire le statut HTTP de la navigation. Une
     réponse 4xx ou 5xx est un échec, et n'entre pas dans le cache.
  2. PDF (C77) : une URL reconnue comme PDF dont le téléchargement échoue
     rend un échec explicite au lieu de retomber sur les paliers RSS et
     navigateur ; taille vérifiée pendant le téléchargement ; troncature à
     120 pages signalée dans les métadonnées ; échec du `HEAD` journalisé
     et suivi d'un essai en `GET`.
  3. Boucle d'événements (C61) : résolution DNS, analyse PDF et
     installation de Playwright sortent de la boucle
     (`asyncio.to_thread`).
  4. Playwright (C74) : ne plus écraser `PLAYWRIGHT_BROWSERS_PATH` ;
     chercher les navigateurs là où la variable les désigne ; un échec
     d'installation est mémorisé au lieu d'être retenté à chaque requête.
  5. Garde SSRF (C62) : redirections revérifiées sur les trois chemins
     (PDF, RSS, navigation) ; requêtes du navigateur filtrées par
     `page.route` ; plages `100.64.0.0/10` et 6to4 bloquées ; une URL mal
     formée rend un refus et non une erreur 500 ; la docstring ne promet
     que ce qui est réalisé.
  6. Client HTTP et fabrique de navigateur injectables, pour tester sans
     réseau.
- **Acceptation** : tests avec `httpx.MockTransport` : PDF en 403, PDF de
  taille excessive, redirection vers une adresse interne sur chaque
  chemin, URL mal formée ; test du statut HTTP avec une fabrique de
  navigateur simulée.
- **Hors périmètre** : l'agent navigateur n'a aucune restriction de
  domaines (C62) ; le traiter demande de choisir une politique, à décider
  avec le responsable du projet.
- **Constats** : C61, C62, C74, C75, C77.

### V2-1 Suppression du code mort — type N

- **Possède** : tout `app/`, seul agent actif sur ce répertoire pendant la
  vague.
- **Travail** :
  1. Dans l'orchestrateur, retirer les dix méthodes non atteintes depuis
     `execute_intelligent_research` (C30), puis les imports, classes et
     attributs devenus inutiles (`Tool`, `ToolType`, `_init_tools_catalog`,
     `self.tools`, `DataProcessor`, `DataValidator`, et
     `AdaptiveNavigator` s'il n'a plus d'appelant vivant).
  2. Retirer le code mort hors orchestrateur listé en C71. Si la décision
     D4 est confirmée, retirer aussi les routes héritées `/research` et
     `/search-site` avec leurs agents (C67).
  3. Avant chaque suppression, prouver l'absence d'appelant par une
     recherche sur tout le dépôt, `mcp_server.py` et `tests/` compris.
  4. Mettre à jour la docstring de module de l'orchestrateur. Une fois
     `AdaptiveNavigator` retiré, `IntelligentOrchestrator.close()` n'a plus
     rien à fermer : le supprimer aussi (C26).
- **Acceptation** : test de contrat vert **sans avoir été modifié** ;
  `ruff check --select F401,F811,F841` propre sur les fichiers touchés ;
  le compte rendu liste chaque symbole supprimé avec la commande qui prouve
  qu'il était mort.
- **Hors périmètre** : toute méthode vivante, même manifestement fautive.

### V3-1 Citations et bibliographie — types A et B

- **Possède** : `_convert_sources_to_bibliography`, nouveau module
  `app/research/citations.py`, `tests/unit/research/test_citations.py`.
- **Travail, type A** :
  1. Déplacer la conversion dans `citations.py` sous forme de fonctions
     pures ; la méthode de l'orchestrateur n'est plus qu'un appel.
  2. Remplacer les substitutions de chaînes successives par une seule passe
     d'expression régulière avec fonction de remplacement. Reproduction du
     défaut actuel : `[SOURCE:https://x.org/a]` puis
     `[SOURCE:https://x.org/a/b]` donne `[1]` puis `[1]/b]` (C44).
  3. Retirer la ponctuation finale d'une URL issue d'un marqueur non
     fermé : `[SOURCE:https://y.org/z.` entre aujourd'hui en bibliographie
     avec le point final (C45).
  4. Garder l'idempotence (deux appels successifs donnent le même
     résultat).
- **Travail, type B** (un drapeau chacun) :
  5. `validate_cited_urls` : une URL citée absente des sources fournies à
     la section est rapprochée de la source fournie la plus proche (même
     domaine et chemin préfixe), sinon la citation est retirée et notée
     dans `Diagnostics`.
  6. `bibliography_cited_only` : ne plus ajouter en bibliographie les
     sources jamais citées dans le texte.
  7. `bibliography_real_titles` : titre réel de la page au lieu de
     « Source n - domaine ».
- **Acceptation** : les deux reproductions ci-dessus en tests ; test
  d'idempotence ; les `xfail` C44 du test de contrat sont levés.

### V3-2 Assemblage du rapport — types A et B

- **Possède** : `_final_assembly`, `_add_complete_traces`.
- **Travail, type A** : construire le résumé **après** le retrait des
  sections marquées, pour qu'un marqueur `[SECTION_SANS_SOURCE]` ne puisse
  plus devenir le résumé (C41).
- **Travail, type B** :
  1. `clean_title` : titre court dans la langue de la consigne, au lieu de
     `Analyse: {query.title()}` qui recopie la consigne entière (C41).
  2. `summary_mode` : `"extract"` (actuel, deux premières phrases de la
     première section, donc répétées dans le rapport) ou `"none"` (C07).
- **Acceptation** : tests unitaires sur un contexte construit à la main ;
  `xfail` C41 levé.

### V3-3 Contrat de l'API de recherche approfondie — type A

- **Possède** : `app/api/v1/endpoints/research_deep.py`,
  `app/core/output_formatter.py` (fonction
  `format_deep_research_output`), le début de
  `execute_intelligent_research` jusqu'à la phase 1 (lecture des
  paramètres), la description OpenAPI de `/research/deep` dans
  `app/main.py`, `tests/unit/api/`.
- **Travail** :
  1. Pour chaque paramètre de `DeepResearchRequest`, appliquer la décision
     D1 : le brancher réellement, ou le retirer du schéma. Aucun champ
     accepté ne reste sans effet (C20, C21, C22).
  2. `timeout` devient un délai global : passé ce délai, le pipeline rend
     ce qu'il a, avec `degraded: true` (C48).
  3. Le nombre de sources par section devient un paramètre réel, transmis
     par `PipelineConfig`.
  4. La réponse conserve `title`, `metadata` et l'identifiant de chaque
     entrée de bibliographie (C23). Le champ `data` des sections n'est
     renvoyé que si la requête le demande (C24).
  5. Retirer les valeurs inventées : `completeness`, `confidence` et tout
     score par défaut sont soit calculés, soit absents (C25).
  6. Ajouter le bloc `diagnostics` : configuration effective, compteurs de
     `Diagnostics`, résultat de `check_report`.
  7. Adapter `benchmarks/convert.py` et le mapping de `mcp_server.py` à la
     nouvelle forme, en accord avec l'intégrateur (fichiers d'autres lots,
     déjà fusionnés à ce stade).
  8. L'endpoint rend toujours le même type de réponse, succès ou échec
     (C66). Une réponse d'échec garde la trace d'exécution et les sources
     (C65). `required_sources_status` et `total_records_processed` sont
     calculés ou retirés (C65).
- **Acceptation** : un test par champ de requête prouvant son effet avec
  les doublures ; test de forme de la réponse ; schéma OpenAPI régénéré
  sans champ fantôme.

### V3-4 Collecte : requêtes, langue, plafonds — types A et B

- **Possède** : `_exploratory_phase`, `_extract_topics_from_snippets`,
  `_build_search_query`, `_section_research_phase`,
  `_section_extraction_phase`, `_targeted_search`, et dans
  `execute_intelligent_research` le bloc de détection de langue.
- **Travail, type A** :
  1. Détecter la langue **avant** la première recherche. Aujourd'hui la
     détection s'exécute après les recherches de section, qui partent donc
     toutes en `"all"` (C32). Le comportement de recherche reste `"all"`
     tant que le drapeau du point 5 n'est pas activé : ce point ne corrige
     que l'ordre.
  2. Appliquer le filtre de domaines avant la troncature au nombre de
     résultats demandé (C35).
  3. `_targeted_search` passe par la cascade de moteurs, comme la
     recherche de section (C32).
- **Travail, type B** :
  4. `separate_search_terms` : une recherche par terme du plan, résultats
     fusionnés et dédoublonnés. Aujourd'hui les 1 à 3 termes sont
     concaténés puis coupés à 12 mots : un opérateur `filetype:pdf` et une
     expression entre guillemets se retrouvent dans la même requête (C34).
  5. `search_language_from_query` : passer la langue détectée aux moteurs.
  6. `extraction_cap_follows_depth` : extraire autant d'URL que la
     profondeur en demande (3, 5 ou 8) au lieu du plafond fixe de 5 (C35).
  7. `exploration_uses_plan_query` : requête d'exploration réduite par
     `_build_search_query` et moteurs pris dans la cascade, au lieu de la
     consigne entière envoyée à `google,duckduckgo` (C31).
  8. `targeted_search_skip_structured` : supprimer l'appel LLM
     d'extraction structurée par URL (C36, C79).
  9. `chunking_v2` : transmettre le drapeau à `document_to_chunk_items`,
     dont le paramètre est ajouté par le lot V1-3 (C73).
- **Acceptation** : tests avec la fausse recherche qui enregistre les
  requêtes reçues (texte, langue, moteurs) pour chaque drapeau, dans les
  deux positions.

### V3-5 Enrichissement et cohérence — type B

- **Possède** : `_iterative_enrichment`, `_analyze_report_quality`,
  `_regenerate_section`, `_analyze_intersections`,
  `_apply_coherence_improvements`.
- **Contexte** : ces deux étapes s'exécutent sur chaque rapport et
  réécrivent du texte déjà rédigé, sans jamais avoir été mesurées
  isolément (C40, C42, C43).
- **Travail** :
  1. Instrumenter d'abord, sans rien changer : `Diagnostics` note combien
     de sections sont régénérées, leur longueur avant et après, le nombre
     de citations avant et après, et combien de « transitions » sont
     collées.
  2. Drapeau `enrichment_enabled` (défaut : vrai, comportement actuel).
  3. Drapeau `coherence_append_enabled` (défaut : vrai).
  4. Drapeau `regenerate_v2` : nouvelle régénération qui reçoit la consigne
     de langue, exige le format `[SOURCE:url]`, conserve les champs `data`
     et `metadata` de la section, n'impose plus « 2 à 5 paragraphes », ne
     coupe plus les données à 3 000 caractères et utilise
     `generate_structured`.
  5. Remplacer les scores de repli inventés (70, 75) par une absence de
     score notée dans `Diagnostics` (C25).
- **Acceptation** : tests des trois drapeaux dans les deux positions ; test
  montrant que `regenerate_v2` conserve les citations existantes d'une
  section.

### V3-6 Sélection des passages et données du prompt — types A et B

- **Possède** : `_cross_reference_data`, `_score_relevance`,
  `_semantic_chunk_selection`, et dans `_synthesize_single_section` la
  seule construction du bloc « DONNÉES DISPONIBLES ».
- **Travail, type A** : `_semantic_chunk_selection` réutilise
  `embedding_cache` ; chaque passage n'est plus envoyé deux fois au
  service d'embeddings. Clé de cache par empreinte du contenu entier
  (C37). Le repli sur les mots-clés seuls est noté dans `Diagnostics`.
- **Travail, type B** :
  1. `prompt_payload_minimal` : le prompt de synthèse reçoit `source`,
     `title`, `content` et le nombre de corroborations ; plus les listes
     `cross_references` ni `metadata` (C39).
  2. `max_chunks_per_source` : plafond de passages par source, pour qu'un
     seul document long ne remplisse pas tout le budget (C38).
- **Acceptation** : test comptant les appels au faux service
  d'embeddings ; tests de sélection sur un jeu de passages construit.

### V3-7 Plan de recherche — types A et B

- **Possède** : `_create_detailed_plan`, `_is_meta_section`,
  `_META_SECTION_PATTERNS`.
- **Travail, type A** : l'usage du plan de repli est noté dans
  `Diagnostics` et rend le rapport `degraded` (C33). Un plan dont toutes
  les sections sont filtrées comme méta déclenche le plan de repli au lieu
  d'un rapport vide.
- **Travail, type B** : `plan_prompt_v2`, prompt débarrassé de l'ancien
  format JSON et des anciens exemples qui contredisent le schéma strict, et
  qui ne reçoit plus le dictionnaire de contexte entier (C33).
- **Acceptation** : test du repli (faux LLM en échec) ; test que les deux
  prompts produisent un plan valide avec le faux LLM.

### V3-8 Endpoints simples : recherche rapide, vision, recherche, extraction — type A

Ces endpoints sont ceux que les agents appellent le plus par MCP.

- **Possède** : `app/api/v1/endpoints/research_quick.py`, `vision.py`,
  `search.py`, `extract.py`, leurs modèles de requête et de réponse dans
  `app/api/models.py`, `tests/unit/api/`.
- **Travail** :
  1. Recherche rapide (C63) : retirer la pertinence calculée sur le seul
     rang et la confiance calculée sur le seul nombre de sources, ou les
     remplacer par des valeurs mesurées. Une réponse `None` devient une
     erreur. La réponse indique combien de sources ont été tentées,
     extraites et écartées.
  2. Recherche rapide : la troncature de chaque source à 2 000 caractères
     est remplacée, derrière le drapeau `quick_uses_chunk_selection` (type
     B), par le découpage et la sélection de passages du pipeline
     approfondi.
  3. Vision (C64) : une analyse `None` devient une erreur ; la
     configuration du LLM passe par la même résolution que le reste du
     service.
  4. Extraction (C69) : `llm_config.base_url` fourni par l'appelant est
     soumis à la garde SSRF, ou le champ est retiré.
  5. Entrées bornées en longueur ; les messages d'erreur renvoyés au
     client ne contiennent plus le texte brut de l'exception (C70).
- **Acceptation** : un test par défaut corrigé, avec le faux LLM et le
  faux extracteur.

### V4-1 Configuration complète du pipeline — type N

- **Possède** : l'orchestrateur entier, `app/research/pipeline_config.py`.
  Seul agent sur ces fichiers.
- **Travail** : déplacer dans `PipelineConfig` les plafonds, budgets,
  seuils et températures en dur (61 occurrences relevées, C47), sans en
  changer une seule valeur. Un test fige les valeurs par défaut.
- **Acceptation** : test de contrat vert sans modification ; rejeu (lot
  V4-3) identique avant et après si disponible.

### V4-2 Découpage de l'orchestrateur en étapes — type N

- **Possède** : l'orchestrateur entier, `app/research/stages/`.
- **Travail** : une étape par fichier (`explore`, `plan`, `search`,
  `extract`, `select`, `synthesize`, `coherence`, `assemble`), reliées par
  des modèles Pydantic (`Plan`, `SectionTarget`, `Source`, `Passage`,
  `SectionDraft`, `Report`). `IntelligentOrchestrator` ne garde que
  l'enchaînement. Procéder étape par étape, un commit par étape, test de
  contrat vert à chaque commit.
- **Acceptation** : aucun fichier de `app/research/` au-delà de 500
  lignes ; test de contrat vert sans modification ; un test unitaire par
  étape avec les doublures.

### V4-3 Enregistrement et rejeu de la collecte — type N

Peut démarrer dès la fin de la vague 1 : n'attend que les coutures du lot
V1-4.

- **Possède** : `app/research/replay.py`, `benchmarks/replay.md`,
  `tests/unit/research/test_replay.py`.
- **Travail** : deux enveloppes autour du client de recherche et de
  l'extracteur. En mode `record`, elles écrivent chaque requête et chaque
  page dans un répertoire par tâche. En mode `replay`, elles servent ces
  fichiers et échouent explicitement sur une requête inconnue. Activation
  par `WEBTOOLS_REPLAY_MODE` et `WEBTOOLS_REPLAY_DIR`.
- **Intérêt** : mesurer un changement de synthèse ou de citations à
  collecte figée. Le web cesse d'être une source de bruit, les moteurs
  fragiles ne sont plus sollicités, un run coûte seulement les appels LLM.
- **Limite à écrire dans `replay.md`** : un changement de collecte (lot
  V3-4) invalide l'enregistrement et se mesure sur le web réel.
- **Acceptation** : enregistrement puis rejeu d'un scénario du test de
  contrat, sorties identiques avec le faux LLM.

### V5-1 Documentation — type N

- **Possède** : `README.md`, `DEEP_RESEARCH_ARCHITECTURE.md`,
  `CHANGELOG.md`, `USE_CASES.md`, description générale de l'API dans
  `app/main.py`, `benchmarks/PROTOCOLE.md`.
- **Travail** : décrire le service tel qu'il est après les vagues 1 à 4
  (modèle et infrastructure réels, paramètres réellement honorés, pipeline
  par étapes, échelle de vérification, rejeu). Retirer toute capacité
  annoncée et absente (C22).

### V5-2 Retrait des drapeaux tranchés — type N

- **Possède** : `PipelineConfig` et les branches concernées.
- **Travail** : pour chaque drapeau tranché par une campagne, garder la
  branche retenue, supprimer l'autre et ses tests. Un drapeau non tranché
  reste en place.

## 8. Campagnes de mesure (opérateur, sur le pod)

Règles de `PROTOCOLE.md`, inchangées : juge souverain, 20 tâches anglaises
au minimum, comparaison contre une référence fixe, jamais contre le run
précédent.

| Campagne | Quand | Contenu | Décision |
|---|---|---|---|
| **M0** Référence et bruit | après la vague 1 | Copier la configuration SearXNG réelle du pod dans `docker/searxng/settings.yml`. Produire `ref20b` (nouveau harnais, code inchangé). Produire `ref20b-bis` au même commit, pour mesurer le bruit entre deux runs identiques (C06). Recalculer `ref20` contre `v11` sur tâches appariées (C05). Compter dans les journaux du run les lignes « Flux RSS trouve et exploite » et les extractions `direct_playwright_single_llm_pass` : c'est la part des sources dont le contenu ne vient pas de la page citée (C56, C76) | L'écart-type des deltas entre `ref20b` et `ref20b-bis` devient le plancher de bruit. Tout seuil de décision ultérieur en découle |
| **M1** Non-régression | après la vague 3, tous drapeaux à leur défaut | 20 tâches contre `ref20b`, en mode `record` | Delta dans le bruit, invariants à zéro, sinon un correctif A est fautif |
| **M2** Paquet attribution | après M1, web réel | `rss_tier_root_only`, `single_llm_pass_enabled=false`. Indicateur principal : taux de validité FACT. Le rejeu est impossible ici : ces drapeaux changent ce qui est extrait. Réenregistrer la collecte à l'issue | Adopter si le taux de validité monte sans que le nombre de sections sans source augmente. C'est la campagne dont l'effet attendu est le plus fort |
| **M3** Paquet présentation | après M2, par rejeu | `clean_title`, `summary_mode=none`, `coherence_append_enabled=false`, `enrichment_enabled=false` | Adopter si le delta dépasse le plancher. Sinon, tester `regenerate_v2` seul |
| **M4** Paquet citations | après M2, par rejeu | `validate_cited_urls`, `bibliography_cited_only`, `bibliography_real_titles`, `prompt_payload_minimal`. Indicateur principal : taux de validité FACT | idem |
| **M5** Paquet collecte | après M3 et M4, web réel | `separate_search_terms`, `extraction_cap_follows_depth`, `exploration_uses_plan_query`, `search_language_from_query`, `max_chunks_per_source`, `chunking_v2` | idem ; surveiller les suspensions de moteurs |
| **M6** Jalon | paquets adoptés fusionnés | Corpus anglais complet, juge officiel | Seuls chiffres communicables |

Un paquet dont le delta reste dans le bruit n'est ni adopté ni rejeté sur
ce seul critère : on regarde les invariants, le taux de validité FACT et la
durée. À qualité égale, on garde la version la plus simple.

## 9. Décisions à confirmer avant la vague 0

Chaque décision a une valeur par défaut. Sans avis contraire, les agents
l'appliquent.

| | Question | Défaut proposé |
|---|---|---|
| D1 | Paramètres de requête sans effet (C20, C21) : brancher ou retirer ? | Brancher `timeout`, le nombre de sources, `sources.required`, `output_format.sections`. Retirer `min_sources`, `date_range`, `include_data`, `include_reports`, `include_academic`, `sources.suggested`, `include_charts`, `include_timeline`, `output_format.structure` |
| D2 | Serveur MCP : client HTTP de l'API, ou monté dans le même processus ? | Client HTTP (lot V1-6) |
| D3 | Qui tient le rôle d'opérateur de mesure sur le pod ? | Le responsable du projet |
| D4 | Routes héritées masquées (`research.py`, `search_site.py`) : supprimer ? | Supprimer. Elles restent joignables, rendent des succès sur échec et lancent un agent navigateur avec `disable_security=True` (C67) |
| D5 | Clients Albert et Anthropic : les maintenir ? | Les garder s'ils passent la même suite de tests d'interface que le client OpenAI, sinon les retirer. Aujourd'hui ils n'ont ni sorties structurées ni embeddings : le pipeline approfondi tourne en mode dégradé avec eux (C55) |
| D6 | API sans clé configurée : refuser de démarrer ? | Oui, sauf `WEBTOOLS_ALLOW_ANONYMOUS=1` posé explicitement (C68) |

`sources.required` et `output_format.sections` demandent un vrai
développement (récupération forcée d'URL, sections imposées au plan). S'ils
sont confirmés, ils forment un lot V4-4 de type B, après la vague 3.

## Annexe A. Constats de l'audit

Chaque constat a été lu dans le code. Ceux marqués « reproduit » ont été
exécutés.

### Mesure

| | Lieu | Constat |
|---|---|---|
| C01 | `benchmarks/generate.py`, `run_one` | Une réponse `success: false` est comptée en échec et n'est pas enregistrée, même si elle contient un rapport dégradé. Contredit la règle « ne jamais filtrer » |
| C02 | `generate.py` ; `research_deep.py` ; `mcp_server.py` | `--max-sources` devient `max_results`, recopié dans `max_steps`, que l'orchestrateur ne lit jamais. Le paramètre n'a aucun effet, côté harnais comme côté MCP |
| C03 | `generate.py`, `evaluate.sh` | Chemins `/home/onyxia/...` et clés lues dans `/tmp/*.txt` en dur |
| C04 | `benchmarks/` | Rien n'enregistre le commit, le modèle ou les réglages d'un run |
| C05 | `PROTOCOLE.md` | `ref20` compte 17 rapports et `v11` 20 : la comparaison n'a peut-être pas porté sur les mêmes tâches. À vérifier sur le pod |
| C06 | orchestrateur | Températures de 0,1 à 0,3, aucune graine. Le bruit entre deux runs du même code n'a jamais été mesuré |
| C07 | `benchmarks/convert.py` | Le titre de l'article est la consigne entière (le titre du rapport est perdu avant, C23). Le résumé répète les deux premières phrases de la première section |

### Service

| | Lieu | Constat |
|---|---|---|
| C10 | `app/main.py`, `/health` | Ni commit ni heure de démarrage : impossible de savoir quel code tourne |
| C11 | `app/main.py`, `/health` | `healthy` même si SearXNG est tombé ; le LLM est seulement « configuré » |
| C12 | `mcp_server.py` ; `app/main.py:49` | Le serveur MCP appelle les fonctions d'endpoint dans son propre processus. `engine_catalog.load` n'est appelé que dans le démarrage de FastAPI : via MCP, la cascade de moteurs par axe est inactive |
| C13 | `app/services/search_service.py` | Espacement par moteur et mise à l'écart tenus en mémoire par processus : API et MCP sollicitent les moteurs sans se coordonner |
| C14 | `mcp_server.py` | Tâches de fond et sessions OAuth en mémoire, perdues à chaque relance ; aucun plafond de tâches simultanées |
| C15 | `.env.example`, `config.py`, `start_all.sh` | Trois défauts de LLM contradictoires (Albert, `gpt-4o`, qwen). `SEARXNG_BASE_URL` lu à trois endroits avec deux défauts |
| C16 | `docker/`, `docker-compose.yml`, `pyproject.toml` | L'image ne lance pas le serveur MCP ; `searxng:latest` ; `settings.yml` versionné de 17 lignes, différent de celui du pod ; dépendances non verrouillées |
| C17 | dépôt | Pas d'intégration continue. `F821` : `current_level` non défini, `app/core/content_cleaner.py:261` |

### API

| | Lieu | Constat |
|---|---|---|
| C20 | `research_deep.py` vers l'orchestrateur | Jamais lus : `max_steps`, `max_results`, `timeout`, `min_sources`, `date_range`, `include_data`, `include_reports`, `include_academic`, `sources.suggested`, `include_charts`, `include_timeline` |
| C21 | orchestrateur, lignes 601 à 1242 | `sources.required`, `output_format.sections`, `output_format.structure` ne sont lus que par des méthodes mortes. Sur le chemin actif ils n'arrivent au modèle que noyés dans un `json.dumps(context)` |
| C22 | `app/main.py`, `README.md` | Mode `stream: true` documenté, absent du modèle de requête |
| C23 | `research_deep.py`, modèle `Report` | Le titre, `metadata`, `research_traces` et l'identifiant des entrées de bibliographie sont perdus à la sérialisation |
| C24 | `research_deep.py`, `output_formatter.py` | Chaque section renvoie `data` : tous les passages extraits, texte intégral. Le serveur MCP transmet le tout à l'agent appelant |
| C25 | `research_deep.py:440` ; `_analyze_intersections` ; `_analyze_report_quality` | Valeurs par défaut présentées comme des mesures : `completeness` 85, `confidence` « medium », cohérence 75, qualité 70 |
| C26 | `research_deep.py` | `orchestrator.close()` n'est jamais appelé |

### Pipeline (chemin actif de l'orchestrateur)

| | Fonction | Constat |
|---|---|---|
| C30 | 10 méthodes | 1 314 lignes non atteintes : `_analyze_and_plan`, `_execute_step`, `_evaluate_result`, `_synthesize_answer`, `_post_process_sections`, `_validate_numerical_coherence`, `_synthesize_sections`, `_assemble_and_evaluate`, `_identify_gaps`, `_fill_gaps_iteration` |
| C31 | `_exploratory_phase` | La consigne entière sert de requête ; moteurs `google,duckduckgo` en dur, les plus fragiles ; pas de langue |
| C32 | `execute_intelligent_research`, `_targeted_search` | La langue est détectée après les recherches de section : elles partent toutes en `"all"`. `_targeted_search` ne passe ni langue ni sélection de moteurs |
| C33 | `_create_detailed_plan` | Le prompt décrit encore l'ancien format JSON et d'anciens exemples, en contradiction avec le schéma strict ; il reçoit tout le dictionnaire de contexte ; le plan de repli est silencieux |
| C34 | `_build_search_query` | Reproduit. Les 1 à 3 `search_terms` du plan sont concaténés et coupés à 12 mots : une seule recherche par section, opérateurs mélangés |
| C35 | `_section_research_phase`, `_section_extraction_phase` | 3, 5 ou 8 résultats demandés selon la profondeur, mais extraction plafonnée à `urls[:5]` ; filtre de domaines appliqué après la troncature |
| C36 | `_targeted_search` | Deux URL seulement ; un appel LLM d'extraction structurée par URL sur 2 000 caractères, dont le résultat ne sert qu'à un bonus de score |
| C37 | `_semantic_chunk_selection`, `_cross_reference_data` | Chaque passage est envoyé deux fois au service d'embeddings (le cache n'est pas consulté par la sélection) ; clé de cache sur les 100 premiers caractères ; repli silencieux |
| C38 | `_semantic_chunk_selection` | Aucune diversité de sources : un seul document long peut remplir le budget |
| C39 | `_synthesize_single_section` | Le prompt reçoit `json.dumps(selected_data)` avec `cross_references`, `metadata` et compteurs |
| C40 | `_apply_coherence_improvements` | Le champ `suggestion` renvoyé par le modèle, qui décrit une amélioration, est collé tel quel à la fin d'une section. Le prompt de cohérence ne fixe pas la langue |
| C41 | `_final_assembly` | Le résumé est construit avant le retrait des sections marquées : `[SECTION_SANS_SOURCE]` peut devenir le résumé. Titre `Analyse: {query.title()}` |
| C42 | `_iterative_enrichment` | S'exécute sur chaque rapport. Toute section de moins de 2 000 caractères ou notée sous 75 est « faible » ; les deux premières sont régénérées. L'analyse de qualité ne voit que les 5 000 premiers caractères du rapport. Son but annoncé, retenter les sections en échec, est inatteignable : elles ont déjà été retirées |
| C43 | `_regenerate_section` | Prompt sans consigne de langue ni format de citation, qui demande « 2 à 5 paragraphes » ; données coupées à 3 000 caractères ; sortie JSON plafonnée à 2 000 jetons ; la section renvoyée perd `data` et `metadata` |
| C44 | `_convert_sources_to_bibliography` | Reproduit. Remplacement par préfixe : `[SOURCE:https://x.org/a/b]` devient `[1]/b]` quand `https://x.org/a` est aussi cité. Mauvais numéro de citation et résidu dans le texte |
| C45 | `_convert_sources_to_bibliography` | Reproduit pour le premier point. Ponctuation finale gardée dans l'URL d'un marqueur non fermé ; aucune vérification que l'URL citée a été fournie au modèle ; sources jamais citées ajoutées à la bibliographie ; titres « Source n - domaine » |
| C46 | orchestrateur | 28 `except Exception`, dont plusieurs replis sans trace dans la réponse |
| C47 | orchestrateur | 61 plafonds, budgets et températures en dur |
| C48 | `execute_intelligent_research` | Budget par section fixe (0,8 × 300 s) ; aucun délai global |

### Périphérie (extraction, clients LLM, endpoints hérités)

Constats issus de deux audits délégués, chaque fichier lu en entier. Ceux
marqués « recoupé » ont été relus ou exécutés une seconde fois.

| | Lieu | Constat |
|---|---|---|
| C50 | `app/core/llm/__init__.py`, `get_llm_client` | Recoupé. Crée un client et son pool HTTP à chaque appel, jamais fermés. Les commentaires de `openai.py` et `extract.py` supposent une instance partagée qui n'existe pas. La cause des fermetures de transport observées en production reste donc inconnue |
| C51 | `openai.py`, `generate` | Recoupé. `finish_reason` n'est jamais lu : un contenu `None` ou coupé par `max_tokens` est rendu comme un succès |
| C52 | `openai.py`, `generate_structured` | Aucune enveloppe d'erreur ; `json.loads(None)` lève `TypeError` ; une sortie tronquée est rapportée comme « schéma non respecté » ; aucun repli si le proxy refuse le schéma |
| C53 | `openai.py`, construction du client | 5 réessais de 90 secondes : un appel peut durer environ 9 minutes, sans échéance globale |
| C54 | `openai.py`, `get_langchain_wrapper` | Écrit la clé et l'URL dans `os.environ` du processus, y compris des valeurs fournies par une requête |
| C55 | `app/core/llm/` | Les trois clients n'ont en commun que `generate`, `get_langchain_wrapper` et `close`. Sorties structurées, embeddings et vision sont détectés par `hasattr` ; Albert et Anthropic n'ont ni sorties structurées ni embeddings |
| C56 | `app/manager.py`, palier RSS ; `rss_extractor.py`, `try_extract` | Recoupé. Pour toute URL, le palier sonde les flux à la racine du domaine. S'il en trouve un, le contenu rendu avec succès est la liste des 30 derniers titres du site, attribuée à l'URL demandée. Non désactivable : l'option `try_rss_first` n'existe pas |
| C57 | `app/utils/prompts.py`, `get_prompt` | Un prompt personnalisé remplace le gabarit : la tâche de l'agent navigateur perd l'URL, les règles de sécurité et le format de sortie. Un prompt contenant `{url}` et d'autres accolades lève une exception non attrapée |
| C58 | `pdf_extractor.py` ; `pyproject.toml` | Recoupé. `pdfplumber` et `pypdf` sont importés mais non déclarés. Sans eux, tout PDF rend « probablement scanné » |
| C59 | `app/manager.py`, cache | Clé sans prompt ni options ; aucune borne de taille ; objet rendu par référence ; un cache par site d'appel, aucun n'est partagé |
| C60 | `ExtractionOptions` ; orchestrateur, `_section_extraction_phase` | Les champs inconnus sont ignorés en silence : l'orchestrateur passe `extract_images`, `extract_links`, `clean_html`, qui n'existent pas. `timeout` n'est pas un budget global. `enable_ocr` et, côté agent, `headless` ne sont jamais lus. Les réglages `playwright_timeout` et `default_extraction_timeout` n'ont aucun lecteur |
| C61 | `url_safety.py`, `pdf_extractor.py`, `playwright_manager.py` | Résolution DNS, analyse PDF et installation de Playwright s'exécutent dans la boucle d'événements et gèlent toutes les requêtes en cours |
| C62 | `url_safety.py` et les trois extracteurs | La garde SSRF ne porte que sur l'URL initiale. Redirections suivies sans contrôle (PDF, RSS, navigation) ; sous-ressources non filtrées ; plage `100.64.0.0/10` acceptée ; une URL mal formée donne une erreur 500 ; l'agent navigateur n'a aucune restriction de domaines |
| C63 | `research_quick.py` | Pertinence égale à 1 moins 0,1 fois le rang ; confiance déduite du seul nombre de sources ; chaque source coupée à 2 000 caractères ; une réponse `None` rendue en succès ; échecs d'extraction invisibles |
| C64 | `vision.py` | Analyse `None` rendue en succès ; URL de LLM par défaut différente du reste du service ; santé « ok » sans appel |
| C65 | `output_formatter.py` | La trace d'exécution est supprimée de la réponse précisément en cas d'erreur. `required_sources_status` vaut toujours `all_success`, `total_records_processed` toujours 0 |
| C66 | `research_deep.py` | Rend un dictionnaire sur échec de l'orchestrateur et une réponse en flux sinon ; le serveur MCP échoue alors en masquant l'erreur réelle |
| C67 | `research.py`, `search_site.py`, `research_agent.py`, `site_search_agent.py` | Routes masquées mais joignables. `/search-site` rend `success: true` sans condition, ne ferme jamais son navigateur, lance l'agent avec `disable_security=True` et insère la requête de l'appelant dans la tâche de l'agent. `/research` ne suit jamais aucun lien |
| C68 | `app/api/auth.py` | L'API est ouverte si `WEBTOOLS_API_KEY` manque ; le `CMD` du Dockerfile et `make run` démarrent sans. Un en-tête non ASCII donne une erreur 500 |
| C69 | `extract.py` | `llm_config.base_url` fourni par l'appelant est utilisé sans garde SSRF : le serveur envoie le texte de la page à un hôte arbitraire |
| C70 | `app/api/models.py`, endpoints | Entrées sans longueur maximale ; texte brut des exceptions renvoyé au client |
| C71 | hors orchestrateur | Code mort : `app/core/content_cleaner.py` en entier, `adaptive_navigator.py`, `data_processor.py`, `DataValidator`, `direct_extractor._clean_content`, `ExtractionError`, `engine_catalog.resolve`, `validate` et `prompt_listing`, quatre méthodes de `output_formatter`, quatre de `source_registry`, sept de `execution_tracer` |
| C72 | `app/utils/numeric_fidelity.py` | Reproduit. L'expression régulière coupe tout entier de quatre chiffres ou plus à ses trois premiers. Le contrôle de fidélité numérique ne détecte donc pas la dérive pour laquelle il a été écrit |
| C73 | `app/utils/chunking.py` | Reproduit. Un fragment de moins de 200 caractères est supprimé : un chapô chiffré disparaît. Un texte sans ponctuation est coupé net à 2 000 caractères, au milieu d'un nombre, sans chevauchement |
| C74 | `playwright_manager.py` | Recoupé. Écrase `PLAYWRIGHT_BROWSERS_PATH` par `"0"` puis cherche les navigateurs dans `~/.cache/ms-playwright`. Un échec d'installation est retenté à chaque requête, par un sous-processus bloquant de 300 secondes au plus |
| C75 | `direct_extractor.py` | Recoupé. Le statut HTTP n'est jamais lu : une page 404 ou 403 de plus de 100 caractères est un succès, mis en cache une heure |
| C76 | `agent_extractor.py` | Pour une page dont l'extraction directe rend 100 à 500 caractères, le contenu renvoyé est une génération du LLM, attribuée à l'URL |
| C77 | `pdf_extractor.py` | Un PDF en échec retombe sur les paliers RSS et navigateur ; troncature à 120 pages non signalée ; limite de 40 Mo vérifiée après chargement complet ; échec du `HEAD` avalé |
| C78 | `engine_catalog.py`, `load` | Si SearXNG n'est pas prêt au démarrage, le catalogue reste vide pour toute la vie du processus |
| C79 | `app/core/data_extractor.py` | Un seul item mal formé fait perdre toutes les données extraites, sans journal ; entrée coupée à 8 000 caractères ; `DataValidator` reçoit toujours des listes vides |
| C80 | `language.py`, `content_detector.py` | Reproduit pour la langue. Les élisions françaises font échouer la détection. Le type de contenu est déduit par sous-chaînes (`capital.fr` devient « documentation ») |
| C81 | `pyproject.toml` ; `mcp_server.py` | Mesuré à l'intégration. Les dépendances ne se résolvent pas en un seul environnement : `browser-use==0.1.41` exige `pydantic<2.11`, `langchain-core==0.3.49`, `langchain-openai==0.3.11` et `langchain-anthropic==0.3.3`, tandis que les `mcp` récents (tirés par `fastmcp>=3`) exigent `pydantic>=2.12` ; pip échoue en « resolution too deep ». En production le serveur MCP tourne déjà sous un autre interpréteur que l'API. Traité par V0-1 (extra `mcp` séparé) puis V1-6 (le MCP devient un client HTTP de l'API) |

## Annexe B. Ce qui n'a pas été vérifié

- Le pod : configuration SearXNG réelle, résultats de benchmark, journaux.
- La fréquence réelle des défauts C40 à C45 dans les rapports produits :
  le code les rend possibles, seule la campagne M1 dira combien de fois ils
  surviennent.
- La taille réelle des réponses (C24).
- Le comportement du proxy LLM (sorties structurées, embeddings) hors de ce
  que le code en dit.
- La part des sources servies par le palier RSS (C56) ou par une
  réécriture du LLM (C76) : la campagne M0 la compte dans les journaux.
- La cause des fermetures de transport HTTP du client LLM en production
  (C50) : `_ensure_open` la masque sans l'expliquer.
- Les effets de C74 en conteneur et sur le pod : le code est établi,
  aucune image n'a été construite.
- La présence de `pdfplumber` et `pypdf` par dépendance transitive dans
  l'image de production (C58).
- browser-use 0.1.41, non installé sur le poste d'audit : mode d'affichage
  par défaut de l'agent, fermeture du navigateur à l'expiration du délai.

## Annexe C. Tableau d'état

Tenu par l'intégrateur seul. Relevé au 12 novembre 2026 sur la branche
`fiabilisation`, d'après `git log --merges`. Chaque lot est fusionné par un
commit de fusion de sa branche `lot/<ID>`.

| Lot | État | Branche | Commit de fusion (12 nov. 2026) | Remarques |
|---|---|---|---|---|
| V0-1 | fusionné | `lot/V0-1` | `a198269` | Environnement, `make check`, extra `mcp` |
| V1-1 | fusionné | `lot/V1-1` | `58f87a2` | Harnais honnête, manifeste, `compare.py`, `INSTALL.md` |
| V1-2 | fusionné | `lot/V1-2` | `bf6c28d` | `/health` : commit, démarrage, dépendances |
| V1-3 | fusionné | `lot/V1-3` | `83e5caa` | Tests des mécanismes déterministes, correctifs trouvés |
| V1-4 | fusionné | `lot/V1-4` | `1163cd4` | Doublures, test de contrat du pipeline |
| V1-5 | fusionné | `lot/V1-5` | `9107edf` | Configuration et déploiement uniques |
| V1-6 | fusionné | `lot/V1-6` | `6c30424` | Serveur MCP client HTTP de l'API |
| V1-7 | fusionné | `lot/V1-7` | `0f833cf` | Client LLM fiable |
| V1-8 | fusionné | `lot/V1-8` | `24aaea8` | Suite : garde SSRF asynchrone du manager, `1dced4a` |
| V1-9 | fusionné | `lot/V1-9` | `1415869` | Extracteurs robustes, garde SSRF |
| V2-1 | fusionné | `lot/V2-1` | `d72de73` | Code mort supprimé, routes héritées retirées |
| V3-1 | fusionné | `lot/V3-1` | `82e5990` | Citations et bibliographie (drapeaux B) |
| V3-2 | fusionné | `lot/V3-2` | `f56a9e3` | Assemblage du rapport (drapeaux B) |
| V3-3 | fusionné | `lot/V3-3` | `0dff659` | Contrat de `/research/deep` |
| V3-4 | fusionné | `lot/V3-4` | `ebe422c` | Collecte : requêtes, langue, plafonds (drapeaux B) |
| V3-5 | fusionné | `lot/V3-5` | `943e807` | Enrichissement et cohérence (drapeaux B) |
| V3-6 | fusionné | `lot/V3-6` | `c2a9a81` | Sélection des passages (drapeaux B) |
| V3-7 | fusionné | `lot/V3-7` | `53ae1fc` | Plan de recherche (drapeau B) |
| V3-8 | fusionné | `lot/V3-8` | `8f2da79` | Endpoints simples |
| V4-1 | fusionné | `lot/V4-1` | `1edd102` | Budgets et seuils dans `PipelineConfig` |
| V4-2 | fusionné | `lot/V4-2` | `c7f7341` | Orchestrateur découpé en étapes |
| V4-3 | fusionné | `lot/V4-3` | `11c24ae` | Enregistrement et rejeu de la collecte |
| V4-4 | fusionné | `lot/V4-4` | `98b9f16` | Ajouté après D1 : sources requises, sections imposées |
| R1 | fusionné | `lot/R1` | `663a54d` | Ajouté : correctifs de la relecture de la vague 1 |
| R2 | fusionné | `lot/R2` | `7ec3ee7` | Ajouté : correctifs de la relecture de la vague 3 |
| D1 | fusionné | `lot/D1` | `ee0b50d` | Ajouté : image construite, déploiement vérifié localement |
| E2E | fusionné | `lot/E2E` | `c10e886` | Ajouté : vérification de bout en bout avec LLM substitut (`scripts/`, `tests/e2e/`) |
| V5-1 | fusionné | `lot/V5-1` | `fea54cc` | Documentation, `docs/CONFIGURATION.md`, version 3.0.0 |
| V5-2 | en attente des mesures | | | Dépend des campagnes M2 à M5 (section 8) |

## État d'avancement

Au 12 novembre 2026, version 3.0.0.

- **Fait** : les lots des vagues 0 à 4, plus V4-4, R1, R2, D1 et E2E, sont
  fusionnés dans `fiabilisation` (annexe C), ainsi que V5-1. `main` n'est pas
  modifiée : la fusion attend la validation du responsable du projet.
- **Aucun changement de type B n'est actif** : les 20 drapeaux sont à leur
  défaut (`docs/CONFIGURATION.md`).
- **Reste, la mesure** : campagnes M0 à M6 (section 8), conduites par le
  responsable du projet sur un serveur avec web, LLM SSPCloud, SearXNG et juge.
- **Reste, le code** : V5-2 (retrait des drapeaux tranchés), après les campagnes.
- **Points ouverts** : URL et commit de DeepResearch Bench à compléter dans
  `benchmarks/INSTALL.md` ; la configuration SearXNG du pod d'origine est
  perdue, M0 repart de `docker/searxng/settings.yml`.
- **Vérifié hors mesure** : 600 tests de l'API et 30 du serveur MCP verts ;
  image reconstruite au commit final, trois conteneurs sains, `/health`
  correct (Playwright lancé, SearXNG et 58 moteurs), recherche, extraction,
  authentification et refus des champs retirés vérifiés en conteneur ;
  recherche approfondie de bout en bout sur données réelles avec un LLM
  substitut (`scripts/smoke_deep.py`).
- **En production (12 novembre 2026, pod Onyxia `proj-webtools`, vrai LLM)** :
  - C82, corrigé : à ~10 jetons/s, toute génération longue dépassait le délai
    de 90 s par tentative ; le plan n'aboutissait jamais. Client en flux.
  - C56 confirmé en direct (flux RSS attribué à un article) ; corrigé dans le
    profil `docker/profils/sspcloud-modele-lent.env`.
  - Avec ce profil : 885 s, 4 sections, 11 sources, 62 citations, aucune
    violation ; sans lui, le délai de 1 200 s expirait à 2 sections sur 5.
  - C83, ouvert : consigne en anglais, titres de section rédigés en français
    par le plan (prompt de plan en français) ; les termes français passent
    dans les requêtes de recherche. À traiter par une contrainte de schéma.
  - C84, ouvert : une source hors sujet (page de supermarchés) a franchi la
    sélection. À examiner avec les seuils de pertinence (M5).
- **Mesure de la fiabilité des citations (12 novembre 2026)** : outil
  `benchmarks/citecheck`, méthode dans `benchmarks/CITATIONS.md`. Chaque
  affirmation citée est confrontée à la page citée, lue indépendamment du
  service, par un juge LLM contrôlé par l'extrait qu'il cite. Juges étalonnés
  sur 59 cas au verdict connu : `qwen3-8-27b` et `gemma4-26b-moe` 59/59, aucune
  fausse acceptation. Run de référence `ref-dbbffef` : 16 questions rapides,
  8 recherches approfondies. Constats :
  - C85, corrigé (instrument) : Wikimedia renvoie 403 à tout client sans
    contact dans le User-Agent ; PubMed et une page Springer servent une page
    anti-robot. User-Agent déclaré pour Wikimedia, API E-utilities pour
    PubMed, détection des pages de vérification, repli par navigateur.
  - C86, corrigé (collecte) : 21 recherches de section sur 79 rendaient zéro
    résultat et 22 collectes de section sur 49 n'extrayaient rien. L'axe
    « actualité » était routé vers trois moteurs indisponibles, l'axe
    « technique » vers github, stackoverflow et pypi pour un règlement
    européen. Repli sur la cascade complète puis sur une requête raccourcie
    (`search_zero_result_fallback`).
  - C87, corrigé : un refus du modèle était publié comme section. Les
    marqueurs `[SOURCE:url]` repoussaient la formule de refus hors de la
    fenêtre examinée.
  - C88, ouvert (sélection) : pour « recul du trait de côte », trois sources
    sur le recul de l'emploi industriel ont été retenues. Le modèle a refusé
    à juste titre ; la sélection n'a pas vu le contresens.
  - C89, ouvert (rédaction) : erreurs de lecture mesurées en recherche
    rapide, la source sous les yeux : « quarantième jour » pour
    « quatre-vingt-dixième », donnée déclarée absente alors que présente,
    « douze pays » tiré des connaissances du modèle contre la source.
  - Débit du proxy LLM mesuré à ~39 jetons/s en charge, et non 10 : le
    profil « modèle lent » repose sur une mesure prise en forte charge.
  - Référence mesurée (juge `qwen3-8-27b`, audit manuel 96 % d'accord) :
    affirmations soutenues par leurs sources 84,6 % en recherche rapide,
    43,0 % en recherche approfondie ; en approfondie, 162 fautes sur 181
    viennent de la rédaction (embellissements), 47 % des phrases ne portent
    aucune citation. Détail dans `benchmarks/CITATIONS.md`.
  - C90, ouvert (recherche rapide) : le modèle ne voit que les 2 000
    premiers caractères de chaque source (`quick_uses_chunk_selection`
    désactivé). Mesuré sur R2 : la réécriture vérifiée affirme « les sources
    ne mentionnent pas l'année » alors que la page la donne plus bas. Le
    contexte conditionne la vérification ; R3 mesure la sélection des
    passages.
  - C91, mesuré : 25 % d'affirmations soutenues dans les sections rédigées
    après le repli de sélection, contre 51 % après une sélection normale.
    Drapeau `selection_fallback_enabled`.
  - Repli de recherche (C86) mesuré sur R1 : collectes de section vides
    22/55 → 0/52, refus du modèle 12 → 3, citations par corpus 434 → 744.
  - Runs R1 à R3 évalués (13 novembre 2026, détail dans
    `benchmarks/CITATIONS.md`). Recherche approfondie, affirmations
    soutenues : 43,0 % (référence) → 58,9 % (R1, repli de recherche) →
    69,2 % (R2, vérification des sections), hausse de R1 à R2 sur 7 sujets
    sur 8 pour 25 % de temps en plus. `deep_verify_sections` vrai par défaut
    (commit `d3a33da`). Recherche rapide : aucun effet mesurable de la
    vérification ni de la sélection des passages (R3) ; `quick_verify_answer`
    et `quick_uses_chunk_selection` restent faux.
  - C92, corrigé (extraction) : trafilatura 2.3 jette le texte des balises
    `<time>`, où Wikipédia met ses dates (« Son lancement a lieu le au
    centre spatial de Kourou »). Balises retirées avant trafilatura, texte
    gardé (commit `2c1803b`).
  - C91 en mesure : R4 (`repli0-d3a33da`) coupe le repli de sélection sur
    les nouveaux défauts.
- **Où commencer** : construire l'image
  (`WEBTOOLS_COMMIT=$(git rev-parse HEAD) docker compose build`), lancer les
  trois services, vérifier le harnais sur une tâche (`benchmarks/INSTALL.md`,
  section 4), puis M0 et M1 en mode `record` (`benchmarks/PROTOCOLE.md`).
- **Vérification** : `make check` (API) et `make check-mcp` (serveur MCP).
