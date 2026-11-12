# Fiabilité des citations : méthode de mesure

Outil : `benchmarks/citecheck/`, commande `python benchmarks/citations.py`.
Il répond à une seule question : quand webtools cite une source pour une
affirmation, la page citée dit-elle bien ce que l'affirmation lui attribue ?

## Principe

1. **Générer** : le service répond à un corpus fixe de questions
   (`citecheck/corpus.jsonl` : 16 questions factuelles en recherche rapide,
   avec réponse attendue, et 8 sujets en recherche approfondie, en français
   et en anglais). Chaque réponse brute est conservée avec le manifeste
   (commit servi, configuration effective).
2. **Extraire les triplets** (affirmation, numéro, URL) : une affirmation
   est la phrase, la ligne de tableau ou l'élément de liste qui porte
   l'appel `[n]`. Un numéro sans source est une citation orpheline.
3. **Récupérer la page citée indépendamment du service**, par une requête
   HTTP simple, sans aucun de ses extracteurs. Le texte visible est gardé en
   entier ; un PDF est lu par pypdf. Les domaines Wikimedia reçoivent un
   User-Agent déclaré avec contact (variable `CITECHECK_CONTACT`), exigé
   par leur politique.
4. **Juger** chaque affirmation contre la page, avec un LLM juge à
   température nulle et un schéma de réponse imposé. Une page courte
   (≤ 14 000 caractères) est donnée en entier ; une page longue est réduite
   à ses passages les plus proches de l'affirmation (plongements
   multilingues, mots communs, chiffres communs).
5. **Contrôler le juge** : un verdict « soutenue », « partielle » ou
   « contredite » doit citer un extrait mot pour mot de la page. Un extrait
   introuvable fait passer le statut à `non_etaye`, qui n'est jamais compté
   comme exact.
6. **Attribuer l'origine des fautes** : la même affirmation est jugée
   contre le texte que le pipeline avait pour cette source (champ `data`
   des sections en recherche approfondie, `include_source_content` en
   recherche rapide). Si ce texte la soutient et la page réelle non, la
   faute vient de l'extraction (contenu attribué à une page dont il ne
   vient pas). Sinon, elle vient de la rédaction (le modèle a mal lu,
   extrapolé ou cité le mauvais numéro).

## Verdicts et statuts

| Statut | Sens |
|---|---|
| `soutenue` | chaque élément vérifiable (acteurs, chiffres, dates, portée) figure dans la page |
| `partielle` | l'élément principal figure, un élément secondaire manque |
| `contredite` | la page dit autre chose sur le même point (autre chiffre, autre date, sens inverse) |
| `non_soutenue` | la page ne contient pas l'élément principal |
| `non_etaye` | le juge dit « soutenue », « partielle » ou « contredite » sans extrait vérifiable |
| `inverifiable` | page inaccessible (erreur HTTP, blocage, vide) : comptée à part, jamais comme fausse |
| `orpheline` | numéro cité absent de la liste des sources |

Indicateur principal : **taux de citations exactes**, soit les citations
`soutenue` rapportées aux citations jugées (`soutenue`, `partielle`,
`contredite`, `non_soutenue`, `non_etaye`). L'intervalle de confiance à
95 % est calculé par bootstrap sur les tâches, pas sur les citations : les
citations d'une même réponse ne sont pas indépendantes.

## Vérification de l'instrument

Un instrument non vérifié produirait des chiffres faux. Trois contrôles,
dans cet ordre :

1. **Tests** (`tests/unit/benchmarks/test_citecheck*.py`, sans réseau) :
   découpage des affirmations, lecture des chiffres, recherche d'extrait,
   sélection des passages, contrôle du juge, mesures, commandes de bout en
   bout.
2. **Étalonnage du juge** (`citecheck/etalonnage/`) : 59 affirmations au
   verdict connu, écrites à la main sur 12 pages réelles figées dans le
   dépôt (dont 10 dépassent le budget de texte, ce qui met aussi la
   sélection des passages à l'épreuve). Il y a 26 cas exacts : reformulations
   et traductions. Les 33 cas fautifs sont des chiffres modifiés, la date
   d'un autre événement (le cas « 2013 » observé en production), un sens
   inversé, un fait plausible mais absent, une page sans rapport ou un
   détail ajouté.
   Seuils fixés avant toute mesure : **fausses acceptations ≤ 5 %** (un
   cas fautif jugé exact) et **accord binaire ≥ 90 %**. Un juge qui ne les
   atteint pas n'est pas utilisé.
3. **Audit sur les vraies réponses** : `auditer` tire un échantillon
   stratifié par statut ; chaque citation est relue à la main contre la
   page, puis `auditer --accord` calcule l'accord. L'étalonnage mesure le
   juge sur des cas construits, l'audit le mesure sur la distribution
   réelle.

## Marche à suivre

```bash
export WEBTOOLS_API=http://localhost:8000 WEBTOOLS_API_KEY=...   # service évalué
export CITECHECK_LLM_API_KEY=...                                   # juge (proxy SSPCloud par défaut)

python benchmarks/citations.py etalonner --cas benchmarks/citecheck/etalonnage/cas.jsonl \
    --juge <modele> --sortie runs/etalonnage
python benchmarks/citations.py generer --corpus benchmarks/citecheck/corpus.jsonl --sortie runs/cit-<nom>
python benchmarks/citations.py evaluer --sortie runs/cit-<nom> --juge <modele>
python benchmarks/citations.py auditer --evaluation runs/cit-<nom>/evaluation-<modele>
python benchmarks/citations.py comparer runs/cit-a/evaluation-<modele> runs/cit-b/evaluation-<modele>
```

`generer` refuse de démarrer si le service ne sert pas le commit du dépôt,
et reprend où il s'était arrêté. `evaluer` garde les pages en cache
(`<sortie>/pages/`) : deux évaluations du même run jugent exactement le
même texte, et une évaluation interrompue reprend sans rejuger.

## Limites connues

- Le juge est un LLM : son exactitude est celle mesurée par l'étalonnage et
  l'audit, pas davantage. Ne comparer deux runs qu'à juge identique.
- Une page qui a changé depuis la génération peut ne plus contenir ce que
  le service y a lu : évaluer peu après la génération.
- Une page rendue en JavaScript peut sortir vide : elle est comptée
  invérifiable, et ce taux est publié.
- Le contrôle des chiffres (`missing_numbers`) est un indicateur
  déterministe, pas un verdict : « 1,2 million » et « 1 200 000 » se
  correspondent, mais un chiffre écrit en lettres ne correspond à rien.

## Résultats mesurés

### Étalonnage des juges (59 cas, 26 exacts, 33 fautifs)

| Juge | Verdict exact | Fausses acceptations | Faux rejets |
|---|---|---|---|
| `qwen3-8-27b` (juge principal) | 59/59 | 0/33 | 0/26 |
| `gemma4-26b-moe` | 59/59 | 0/33 | 0/26 |
| `qwen3-6-35b-moe` (modèle rédacteur du service) | 57/59 | 0/33 | 1/26 |

Deux étiquettes du jeu ont été corrigées après relecture des pages (`mb-05`,
`zfe-03`, note dans `etalonnage/cas.jsonl`). `gemma4-26b-moe` a cessé de
répondre sur le proxy pendant la mesure : le second juge du run de
référence est `qwen3-6-35b-moe`.

### Run de référence `ref-dbbffef` (12 novembre 2026)

Service au commit `dbbffef`, profil `sspcloud-modele-lent`, juge
`qwen3-8-27b`, instrument au commit `5aa320d`. Affirmation soutenue : ses
sources, ensemble, soutiennent chaque élément vérifiable.

| | Recherche rapide (16) | Recherche approfondie (8) |
|---|---|---|
| Affirmations soutenues | 84,6 % (22/26), IC95 68–96 | 43,0 % (136/316), IC95 31–53 |
| Soutenues au moins en partie | 92 % | 80 % |
| Affirmations contredites | 2 | 14 (4,4 %) |
| Affirmations dont une source est inaccessible | 0 | 17,7 % |
| Phrases portant une citation | 93 % | 53 % |
| Réponses exactes (motif attendu) | 16/16 | — |
| Origine des fautes (rédaction / extraction) | 15 / 2 | 162 / 19 |

Contrôles :

- **Audit manuel** de 28 citations tirées au sort (7 par statut), relues
  contre la page : accord binaire 96,4 %, aucune citation jugée soutenue à
  tort (0/7), un faux rejet (juge trop strict). Le taux mesuré est donc
  plutôt un plancher.
- **Second juge** (`qwen3-6-35b-moe`) : accord binaire 85 % (kappa 0,69)
  citation par citation, désaccords sur les cas limites (partielle ou
  soutenue). Les taux globaux restent proches : 39,9 % contre 43,0 % en
  recherche approfondie, 88,5 % contre 84,6 % en recherche rapide.
- **Repli de sélection** : 25 % d'affirmations soutenues (19/76) dans les
  sections rédigées sur des passages sous le seuil, contre 51 % (105/206)
  après une sélection normale.

### Défauts de l'instrument trouvés et corrigés pendant la mesure

Chacun faussait le résultat et a son test :

- Wikimedia refusait le récupérateur (403) : User-Agent déclaré.
- Une page de vérification anti-robot de 226 caractères était comptée comme
  lue : ses 8 citations sortaient non soutenues à tort.
- Pages rendues en JavaScript lues vides : repli par navigateur.
- PubMed bloque tout client programmatique : API officielle E-utilities.
- Exposant HTML (`<sup>14</sup>C`) : l'extrait du juge n'était pas retrouvé.
- Juge muet sur le proxy : chaque citation attendait 3 × 300 s ; contrôle
  préalable du juge.
- Lecture par navigateur sans fin : échéance globale de 120 s.
