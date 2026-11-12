# Installer le harnais de benchmark

Le pod qui hebergeait le benchmark n'existe plus. Ce document decrit comment
rendre le harnais executable depuis zero. Aucun chemin ni aucune cle n'est
code en dur : tout passe par l'environnement.

## 1. DeepResearch Bench

- Depot amont : **A COMPLETER** (URL du depot DeepResearch Bench, non
  verifiee ici).
- Commit a figer : **A COMPLETER** (noter le hash utilise pour la reference,
  puis ne plus le changer entre deux runs que l'on compare).
- Emplacement : n'importe quel dossier, designe par `DRB_HOME`.

```bash
git clone <URL du depot amont> "$DRB_HOME"
cd "$DRB_HOME" && git checkout <commit fige>
pip install -r requirements.txt     # dans l'environnement qui lancera evaluate.sh
```

Fichiers attendus par le harnais :

- `$DRB_HOME/data/prompt_data/query.jsonl` (champs `id`, `prompt`, `language`)
- `$DRB_HOME/data/test_data/raw_data/` (sortie de `generate.py`)
- `deepresearch_bench_race.py` et le paquet `utils` (utilises par `evaluate.sh`)

## 2. Variables d'environnement

| Variable | Utilisee par | Obligatoire | Role |
|---|---|---|---|
| `DRB_HOME` | generate.py, evaluate.sh | oui | depot DeepResearch Bench |
| `WEBTOOLS_API` | generate.py | oui | URL du service, ex. `http://localhost:8000` |
| `WEBTOOLS_API_KEY` | generate.py | oui, sauf `--anonymous` | cle du service webtools |
| `SOVEREIGN_LLM_API_KEY` | evaluate.sh | si `JUDGE=sovereign` | cle du LLM juge souverain |
| `SOVEREIGN_BASE_URL` | evaluate.sh | non | defaut `https://llm.lab.sspcloud.fr/api` |
| `OPENROUTER_API_KEY` | evaluate.sh | si `JUDGE=official` | juge officiel |
| `JINA_API_KEY` | evaluate.sh | pour la phase FACT | scrape des pages citees |
| `JUDGE` | evaluate.sh, generate.py | non | `sovereign` (defaut) ou `official` |
| `PYTHON` | evaluate.sh | non | interpreteur de DeepResearch Bench |
| `WEBTOOLS_LLM_MODEL` | generate.py | non | modele du service, consigne dans le manifeste |

Une variable obligatoire absente arrete le script avec un message qui la
nomme. Ne jamais ecrire de cle dans le depot.

## 3. Service webtools

`generate.py` interroge `GET /health` et exige les champs `commit`,
`commit_dirty` et `started_at`. Il refuse de demarrer si le commit servi
differe du commit du depot, s'il est `unknown`, si l'arbre servi est modifie
ou si ces champs manquent, sauf avec `--allow-mismatch`. Le service doit
donc etre lance depuis le commit que l'on veut mesurer.

## 4. Verification

Sans service ni reseau (tests du harnais) :

```bash
python -m pytest tests/unit/benchmarks -q
```

Installation complete (genere un rapport, consomme du quota LLM) :

```bash
export DRB_HOME=... WEBTOOLS_API=http://localhost:8000 WEBTOOLS_API_KEY=...
python benchmarks/generate.py --lang en --limit 1 --model-name verif
```

La commande doit ecrire `verif_manifest.json` a cote de `raw_data/`. Un
refus sur le commit ou sur une variable manquante est un resultat attendu et
lisible, pas une panne du harnais.

## 5. Comparer deux runs

```bash
python benchmarks/compare.py <resultats_run_a> <resultats_run_b>
```

Seules les taches presentes dans les deux runs sont comparees. Les cles
`id` et `overall_score` sont celles supposees pour le fichier de resultats
RACE ; les adapter avec `--id-key` et `--score-key` si le depot amont les
nomme autrement (a verifier apres l'installation).
