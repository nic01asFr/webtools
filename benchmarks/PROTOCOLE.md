# Protocole de benchmark — webtools sur DeepResearch Bench

## Ce qu'on mesure, et à quoi ça sert

DeepResearch Bench : 100 tâches de niveau doctoral rédigées par des experts
de 22 domaines (50 en anglais, 50 en chinois). Deux métriques
complémentaires, qui ne mesurent pas la même chose :

| Métrique | Ce qu'elle note | Ce qu'elle révèle chez nous |
|---|---|---|
| **RACE** | Qualité du rapport : exhaustivité, profondeur, respect de la consigne, lisibilité — par comparaison à des rapports de référence | La chaîne plan → synthèse → cohérence |
| **FACT** | Citations : extraction des triplets (affirmation, index, URL), vérification que l'URL soutient l'affirmation, nombre de citations effectives | L'extraction, la corroboration, la bibliographie |

Les deux se dégradent indépendamment. Un pipeline qui rédige bien mais cite
mal aura un bon RACE et un mauvais FACT — c'est précisément le profil qu'on
veut pouvoir détecter.

## LA règle à ne jamais enfreindre : un score n'existe pas sans son juge

RACE fait noter les rapports par un LLM. Le benchmark officiel utilise
gpt-5.5. Nous avons deux modes, et **mélanger leurs chiffres n'a aucun sens** :

- `JUDGE=sovereign` — juge sur SSPCloud. Gratuit, aucune donnée ne sort.
  Scores **non comparables** au classement public.
  → mode **itération** : mesurer si un changement améliore ou dégrade, à juge
  constant. C'est 90 % de l'usage.

- `JUDGE=official` — juge gpt-5.5 via OpenRouter. Payant, les rapports
  sortent de l'infrastructure. Scores comparables au classement public.
  → mode **preuve**, réservé aux jalons.

Reporter toujours le couple **(score, juge)**. Une amélioration mesurée en
souverain doit être reconfirmée en officiel avant d'être communiquée.

## Ce qui rend une comparaison honnête

**Le modèle de fond est un facteur confondant.** Les systèmes publiés
utilisent GPT-4o/GPT-5 pour planifier et rédiger ; webtools utilise
qwen3-6-35b-moe. Un score brut mesure le couple (pipeline + modèle).

Conséquence pratique : pour attribuer un gain au pipeline plutôt qu'au
modèle, **ne changer qu'une variable à la fois**. Un run de comparaison doit
figer le modèle, le juge, le nombre de sources et le sous-ensemble de tâches.

**Ne jamais filtrer les rapports faibles.** `generate.py` enregistre même les
rapports que le diagnostic juge inexploitables. Les écarter gonflerait le
score en cachant les vrais échecs du pipeline — exactement ce qu'un benchmark
doit révéler.

**FACT valide les citations avec un outil tiers (Jina), pas avec webtools.**
Faire vérifier les citations de webtools par webtools serait juge et partie.

## Marche à suivre

```bash
# 1. Générer les rapports (long : 4-7 min par tâche)
cd /home/onyxia/work/benchmarks/harness
export WEBTOOLS_API_KEY="$(cat /tmp/webtools_api_key.txt)"

python3 generate.py --lang en --limit 10 --model-name webtools-v2     # pilote
python3 generate.py --lang en --model-name webtools-v2                # complet

# Interruption sans risque : relancer la même commande reprend où ça s'est
# arrêté (les tâches déjà faites sont sautées).

# 2. Évaluer
JUDGE=sovereign ./evaluate.sh webtools-v2 all      # itération
JUDGE=official  ./evaluate.sh webtools-v2 all      # jalon (OPENROUTER_API_KEY requis)
```

## Avant de croire un chiffre — vérifications obligatoires

1. **Le fichier de diagnostics** (`*_diagnostics.jsonl`) : combien de
   rapports `usable: false` ? Un taux élevé invalide la lecture du score.
2. **Les citations orphelines** : un `[7]` dans le texte sans entrée 7 en
   bibliographie fait chuter FACT sans toucher RACE. C'est le mode de panne
   silencieux du convertisseur — à surveiller à chaque changement du format
   de sortie du pipeline.
3. **Les échecs** : une tâche absente du `.jsonl` n'est pas une tâche ratée
   à zéro, c'est une tâche non mesurée. Distinguer les deux.

## Nommage des runs

`webtools-<version>-<variante>` — par exemple `webtools-v2-stealth`,
`webtools-v2-sources5`. Le nom devient le nom du fichier de résultats : il
doit dire ce qui a changé, sinon deux runs deviennent incomparables une
semaine plus tard.

## Ce que ce harnais ne mesure pas

- La latence et le coût (à suivre séparément : `elapsed` est dans les
  diagnostics)
- `research_quick` : format différent (réponse courte + sources), demanderait
  un adaptateur distinct — FACT serait applicable, RACE non
- Le corpus chinois : exclu par défaut (`ONLY_EN=1`), il doublerait le coût
  sans rien apprendre de plus sur le pipeline


---

## Puissance statistique — mesuré, pas supposé

Deux runs de 5 tâches comparés (baseline vs v2) ont donné un delta moyen de
**0,004** pour un **écart-type de 0,066 par tâche**. Les deltas individuels
allaient de −0,098 à +0,063 : quinze fois l'effet mesuré.

**Conséquence : aucune conclusion n'est possible sur 5 tâches.**

| Effet à détecter | Tâches nécessaires |
|---|---|
| 0,10 (énorme) | 4 |
| 0,05 (net) | 14 |
| 0,03 (réaliste) | 38 |
| 0,02 (fin) | 85 — au-delà du corpus |

Le corpus anglais complet (50 tâches) plafonne à un effet détectable de
**0,026**. Aucune amélioration plus fine ne sera jamais mesurable ici.

### Ce que ça impose

- **Le pilote à 5 tâches ne mesure pas.** Il vérifie qu'un changement ne
  casse rien : 0 échec, format valide, pas de régression visible. Ne pas
  lire ses scores.
- **Une décision d'amélioration exige 20 tâches minimum** — environ 1h15 de
  production plus 10 minutes de notation.
- **Grouper les changements**, contrairement au réflexe d'isoler une
  variable. Isoler demanderait 20 tâches par changement, pour un effet
  unitaire de l'ordre de 0,01-0,02 — indétectable. Mieux vaut regrouper 3 à
  4 améliorations cohérentes et mesurer le paquet.
- **Toujours comparer contre la même référence** (`webtools-ref20`), pas
  contre le run précédent.

## Métriques de référence (juge souverain)

| Métrique | Valeur | Date |
|---|---|---|
| RACE global | 0,3811 | 5 tâches |
| — Exhaustivité | 0,3687 | |
| — Profondeur | 0,4027 | point fort |
| — Respect consigne | 0,3797 | |
| — Lisibilité | 0,3354 | point faible |
| FACT — citations/tâche | 10,2 | 5 tâches |
| FACT — taux de validité | **56,9 %** | 5 tâches |

Le taux de validité FACT est l'indicateur le plus actionnable : ~4 citations
sur 10 ne sont pas confirmées par la page qu'elles désignent.

## Pièges d'intégration rencontrés

- `reasoning_effort` est rejeté en 400 par le proxy litellm de SSPCloud →
  `SUPPORTS_REASONING_EFFORT=0`
- `JINA_API_KEY` doit être propagée explicitement par `evaluate.sh` : sans
  elle, les 27 récupérations de pages échouent en 401
- `utils/scrape.py` reprend là où il s'est arrêté et considère les entrées
  en échec comme traitées (« processing 0 instances »). **Purger le
  répertoire `fact/` complet** avant toute relance, pas seulement
  `scraped.jsonl` et `validated.jsonl`.


---

## Résultats mesurés — session du 16-21 août

Référence `webtools-ref20` → état actuel `webtools-v11` (juge souverain,
20 tâches anglaises).

| Indicateur | ref20 | v11 |
|---|---|---|
| RACE global | 0,3697 | **0,3797** |
| — Exhaustivité | 0,3735 | 0,3802 |
| — Profondeur | 0,3737 | 0,3892 |
| — Respect consigne | 0,3980 | 0,3970 |
| — Lisibilité | 0,2995 | **0,3194** |
| Rapports produits | 17/20 | **20/20** |
| FACT — citations valides/tâche | 8,1 | **30,0** |
| FACT — taux de validité | 61,4 % | 56,6 % |
| Rapports avec tableau | 0 % | **80 %** |

Le gain principal n'est pas dans RACE (+0,010, sous le seuil de détection de
0,05) mais dans la **robustesse** : plus aucune tâche n'échoue, et le nombre
de citations réellement vérifiées est multiplié par quatre. Le taux de
validité baisse de 4,8 points, ce qui est le compromis attendu quand le
volume quadruple — les citations faciles sortent en premier.

## Ce qui a marché, et ce qui n'a pas marché

**Les mécanismes déterministes ont produit tous les gains solides** :
découpage complet des documents (au lieu d'une troncature à 3 000
caractères), correction de `max_tokens`, filtre anti-méta, détection de
langue, cascade de moteurs, espacement par moteur.

**Les contraintes de schéma JSON portent, les règles en prose non.** Vérifié
quatre fois : langue, `source_axis`, opérateurs de recherche, couverture des
sections. Une règle dans la `description` d'un champ est respectée ; la même
règle en prose dans le prompt est ignorée.

**À défaut de schéma, un exemple concret fonctionne** — mais avec une limite
importante. L'exemple de tableau a fait passer les rapports mis en forme de
0 % à 80 %. En revanche, ajouter un exemple de liste a **fait perdre 0,069**
sur toutes les dimensions et ramené les tableaux à 50 % : un exemple de plus
peut déplacer l'attention du modèle au lieu de s'y ajouter. Le prompt de
synthèse porte déjà beaucoup (citations, fidélité numérique, langue,
structure, présentation).

**Les listes restent hors d'atteinte par le prompt.** Deux approches
opposées testées — restreindre le tableau, ajouter un exemple de liste — ont
toutes deux échoué. Le modèle privilégie le tableau ; mieux vaut exploiter
cette préférence que la combattre. Si le besoin se confirme, une
transformation déterministe après génération serait plus fiable.

## Pièges d'infrastructure rencontrés

- `start_all.sh` **supervise et relance** le service : un `kill` simple ne
  suffit pas, et on peut tester pendant une heure du code non chargé.
  **Toujours vérifier l'heure de démarrage du processus contre la date de
  modification du fichier** avant d'interpréter un test.
- La configuration SearXNG (`searx/settings.yml`) vit sur le pod et **n'est
  pas versionnée** : les moteurs activés seraient perdus à une
  réinstallation.
- Un correctif en amont peut être annulé par une étape en aval. Rencontré
  cinq fois : plafonds de chunks, `selected_data[:12]`, conversion des
  citations non appelée, résumé construit avant conversion, `max_tokens`
  calibré pour l'ancien budget de sources.
