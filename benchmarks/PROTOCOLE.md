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
