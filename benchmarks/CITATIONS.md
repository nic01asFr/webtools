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

### Effet des correctifs (runs R1 à R3, 12 et 13 novembre 2026)

Même corpus, même juge (`qwen3-8-27b`), un run par configuration.

| Run | Service | Changement mesuré |
|---|---|---|
| référence | `dbbffef` | — |
| R1 `apres-5aa320d` | `5aa320d` | repli de recherche sur zéro résultat, refus mieux détectés |
| R2 `verif-d2ec2b4` | `d2ec2b4` | R1 + vérification des sections (deep) et de la réponse (quick) |
| R3 `selverif-d2ec2b4` | `d2ec2b4` | R2 + sélection des passages en recherche rapide (quick seul) |

Recherche approfondie, affirmations soutenues :

| Sujet | Référence | R1 | R2 |
|---|---|---|---|
| d01 néonicotinoïdes | 43 % (13/30) | 54 % (46/85) | 69 % (50/72) |
| d02 microplastiques | 48 % (20/42) | 52 % (48/93) | 40 % (27/68) |
| d03 ZFE | 66 % (31/47) | 88 % (73/83) | 90 % (35/39) |
| d04 pérovskites | 8 % (3/37) | 53 % (61/116) | 60 % (55/92) |
| d05 trait de côte | 41 % (11/27) | 35 % (23/66) | 73 % (57/78) |
| d06 MACF | 27 % (17/62) | 55 % (57/103) | 62 % (28/45) |
| d07 hydrogène | 42 % (16/38) | 64 % (50/78) | 73 % (40/55) |
| d08 antibiorésistance | 25 % (25/101) | 44 % (36/82) | 55 % (60/109) |
| **Ensemble** | **43,0 %** (IC95 31–53) | **58,9 %** (IC95 52–68) | **69,2 %** (IC95 64–76) |
| Contredites | 4,4 % | 2,1 % | 1,6 % |
| Source inaccessible | 17,6 % | 5,2 % | 8,6 % |
| Durée totale des 8 sujets | 4 104 s | 3 711 s | 4 622 s |

- **R1** : plus aucune section sans source (22 collectes vides sur 55, puis
  0 sur 52), deux fois plus d'affirmations citées (669 contre 316).
- **R2** : hausse sur 7 sujets sur 8 (moyenne +9,6 points) ; 33 sections
  réécrites sur 46 vérifiées, 2 réécritures rejetées ; 25 % de temps en
  plus. Rapports plus courts (887 phrases contre 1 262), part de phrases
  citées un peu plus haute (64 % contre 61 %).
- Un seul run par configuration : l'IC95 rend compte de la variété des
  sujets, pas de la variation d'un run à l'autre (les moteurs ne rendent pas
  deux fois les mêmes sources). La hausse sur 7 sujets sur 8 est le meilleur
  indice que l'effet de R2 n'est pas du bruit.

Recherche rapide : aucun effet mesurable, à 25 affirmations par run.
Affirmations soutenues 84,6 % (référence), 84,0 % (R1), 80,0 % (R2),
86,4 % (R3) ; réponses exactes 16, 15, 14 et 14 sur 16. La vérification
de la réponse a fait plus de mal que de bien : sur `q01`, elle a réécrit
une réponse juste en « les sources ne précisent pas l'année », la date
étant au-delà des 2 000 caractères transmis par source (C90).

Défaut du service trouvé par ces runs : trafilatura jette le texte des
balises `<time>`, où Wikipédia met ses dates. La page du télescope James
Webb devenait « Son lancement a lieu le au centre spatial de Kourou », et
`q06` échouait dans R1, R2 et R3. Corrigé au commit `2c1803b`.

Réglages retenus d'après ces chiffres :

| Drapeau | Défaut | Motif |
|---|---|---|
| `deep_verify_sections` | `true` (depuis `d3a33da`) | R1 contre R2 ci-dessus |
| `quick_verify_answer` | `false` | aucun gain, une réponse juste dégradée |
| `quick_uses_chunk_selection` | `false` | aucun gain mesurable (R2 contre R3) |
| `selection_fallback_enabled` | `true` | R4 ci-dessous : le couper retire du contenu sans gain |
| `synthesis_v3` | `true` (depuis `dbf399b`) | R5 ci-dessous : +14 points à collecte identique |

### Repli de sélection coupé (run R4 `repli0-d3a33da`, 13 novembre 2026)

Service au commit `d3a33da` (vérification des sections active, balises
`<time>` conservées), `selection_fallback_enabled=false`.

| | R2 (repli actif) | R4 (repli coupé) |
|---|---|---|
| Affirmations soutenues, deep | 69,2 % (352/509), IC95 64–76 | 67,8 % (251/370), IC95 60–77 |
| Affirmations contredites, deep | 1,6 % | 2,7 % |
| Sections sans passage au-dessus du seuil | 14 (rédigées sur repli) | 26 (retirées) |
| Affirmations soutenues, quick | 80,0 % (20/25) | 80,8 % (21/26) |
| Citations contredites, quick | 12,5 % | 0 % |
| Réponses exactes, quick | 14/16 | 16/16 |

Couper le repli retire un quart des affirmations (370 contre 509) sans
améliorer leur fiabilité : le repli reste actif. L'écart mesuré sur la
référence (25 % contre 51 %, C91) ne se retrouve pas une fois les sections
vérifiées. En recherche rapide, R4 répond juste aux 16 questions, dont `q06`
grâce au correctif des balises `<time>`.

`d06` a atteint le délai global de 1 200 s et rendu un rapport partiel ;
`d07` a pris 1 052 s contre 492 s dans R2. Le proxy LLM était plus lent ce
jour-là, mais la vérification ajoute des appels : sous le délai par défaut
de l'API (600 s), une recherche approfondie a plus de chances d'être
partielle. Le serveur MCP demande 1 200 s.

### Rédaction v3 à collecte identique (run R5, 13 et 14 novembre 2026)

Service au commit `dbf399b`. Une collecte enregistrée (`v3rec`, rédaction v3),
puis quatre rejeux alternés de cette même collecte et de son plan : v2, v3,
v2, v3 (`synthesis_v3` faux puis vrai). Seules la rédaction et la
vérification diffèrent d'un rejeu à l'autre. Écart apparié sujet par sujet
(`citations.py ecart`), rejeux seulement, 2 répétitions contre 2 :

| Mesure, deep | v2 | v3 | Écart moyen [IC95] | Sujets en hausse |
|---|---|---|---|---|
| Affirmations soutenues | 69,5 % et 65,9 % | 83,0 % et 82,4 % | +14,2 points [+6,2 ; +21,0] | 7 sur 8 |
| Phrases citées | mesuré par sujet | mesuré par sujet | +15,8 points [+10,5 ; +21,3] | 8 sur 8 |
| Affirmations soutenues, nombre | 333 et 326 | 333 et 337 | | |
| Affirmations jugées, nombre | 479 et 495 | 401 et 409 | | |
| Affirmations contredites | 0,8 % et 0,6 % | 1,0 % et 2,4 % | | |

Le bruit entre deux rejeux de la même configuration est de 9 à 12 points
par sujet ; l'écart moyen le dépasse et son intervalle exclut zéro. La v3 ne
perd aucune affirmation soutenue : elle en produit autant, et retire environ
80 affirmations non soutenues par rapport. Elle garde moins de sections (sur
`d03`, 2 contre 4) : celles que les sources ne couvrent pas sont déclarées
non traitées au lieu d'être écrites. Seul `d05` baisse, de 79,5 à 72,2 %,
dans le bruit. Les contradictions passent de 7 à 14 affirmations sur les
deux rejeux, sur 8 sujets : à surveiller dans les runs suivants.

Limite du rejeu trouvée sur `d08` : 13 requêtes et pages manquaient dans
chacun des quatre rejeux, à l'identique. En direct, les deux dernières
sections de `d08` avaient épuisé leur budget de 240 s en attendant une place
(trois sections au plus en parallèle) et étaient sorties vides, sans trace
dans les diagnostics ; en rejeu, la collecte instantanée leur laissait le
temps de chercher. La comparaison v2 contre v3 reste appariée (les quatre
rejeux ont les mêmes manques), mais `d08` y est rédigé sur une collecte
partielle. Défaut corrigé (C97) : le budget court à partir de l'obtention de
la place, et un dépassement est noté (`collecte.budget_section_depasse`).

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
