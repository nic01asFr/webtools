#!/usr/bin/env bash
# Evaluation RACE + FACT d'un systeme sur DeepResearch Bench.
#
# ─────────────────────────────────────────────────────────────────────
# LE CHOIX DU JUGE EST LA DECISION STRUCTURANTE DE TOUT LE PROTOCOLE
# ─────────────────────────────────────────────────────────────────────
# RACE fait noter les rapports par un LLM. Le benchmark officiel utilise
# gpt-5.5 (RACE) et gpt-5.4-mini (FACT). Deux usages, deux configurations,
# et il ne faut JAMAIS melanger leurs chiffres :
#
#   JUDGE=sovereign  -> juge sur SSPCloud (gratuit, aucune donnee ne sort)
#                       Scores NON comparables au classement public.
#                       C'est le mode d'ITERATION : mesurer si un
#                       changement ameliore ou degrade, a juge constant.
#
#   JUDGE=official   -> juge gpt-5.5 via OpenRouter (payant, les rapports
#                       sortent de l'infra). Scores comparables au
#                       classement public. C'est le mode PREUVE, a
#                       reserver aux jalons.
#
# Regle : un chiffre ne veut rien dire sans son juge. Toujours reporter
# le couple (score, juge). Comparer deux scores obtenus avec des juges
# differents n'a aucun sens.
set -euo pipefail

MODEL_NAME="${1:?usage: evaluate.sh <model-name> [race|fact|all]}"
PHASE="${2:-all}"
JUDGE="${JUDGE:-sovereign}"

BENCH="/home/onyxia/work/benchmarks/deep_research_bench"
cd "$BENCH"

case "$JUDGE" in
  sovereign)
    export LLM_BACKEND="openai"
    export OPENAI_BASE_URL="https://llm.lab.sspcloud.fr/api"
    export OPENAI_API_KEY="$(cat /tmp/llm_key.txt)"
    export RACE_MODEL="${RACE_MODEL:-qwen3-6-35b-moe}"
    export FACT_MODEL="${FACT_MODEL:-qwen3-6-35b-moe}"
    # Le proxy litellm de SSPCloud rejette reasoning_effort en 400 pour
    # qwen3-6-35b-moe. Le parametre est optionnel cote benchmark.
    export SUPPORTS_REASONING_EFFORT=0
    echo "JUGE : SOUVERAIN (SSPCloud / $RACE_MODEL)"
    echo "  -> scores NON comparables au classement public"
    ;;
  official)
    export LLM_BACKEND="openrouter"
    : "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY requis pour le mode official}"
    export RACE_MODEL="${RACE_MODEL:-openai/gpt-5.5}"
    export FACT_MODEL="${FACT_MODEL:-openai/gpt-5.4-mini}"
    echo "JUGE : OFFICIEL (OpenRouter / $RACE_MODEL)"
    echo "  -> les rapports sortent de l'infrastructure"
    ;;
  *) echo "JUDGE doit valoir sovereign ou official"; exit 1 ;;
esac

# Cle Jina pour l'etape scrape de FACT (recuperation des pages citees pour
# verifier qu'elles soutiennent l'affirmation). Lue depuis un fichier hors
# depot si la variable n'est pas deja definie. Sans elle, Jina limite le
# debit et toutes les recuperations echouent en 401.
if [ -z "${JINA_API_KEY:-}" ] && [ -f /tmp/jina_key.txt ]; then
  export JINA_API_KEY="$(cat /tmp/jina_key.txt)"
fi

RAW="$BENCH/data/test_data/raw_data/${MODEL_NAME}.jsonl"
[ -f "$RAW" ] || { echo "Introuvable : $RAW — lancer generate.py d'abord"; exit 1; }
echo "Systeme evalue : $MODEL_NAME ($(wc -l < "$RAW") rapport(s))"

RESULTS="$BENCH/results/${MODEL_NAME}_${JUDGE}"
mkdir -p "$RESULTS"

if [ "$PHASE" = "race" ] || [ "$PHASE" = "all" ]; then
  echo "=== RACE (qualite du rapport) ==="
  # ONLY_EN=1 restreint au sous-ensemble anglais (50 taches) : c'est le
  # mode par defaut du pilote, le corpus chinois doublerait le cout sans
  # rien apprendre de plus sur le pipeline.
  RACE_EXTRA=""
  [ "${ONLY_EN:-1}" = "1" ] && RACE_EXTRA="--only_en"

  python -u deepresearch_bench_race.py "$MODEL_NAME" \
    --raw_data_dir "$BENCH/data/test_data/raw_data" \
    --query_file "$BENCH/data/prompt_data/query.jsonl" \
    --output_dir "$RESULTS/race" \
    --max_workers "${WORKERS:-4}" $RACE_EXTRA
fi

if [ "$PHASE" = "fact" ] || [ "$PHASE" = "all" ]; then
  echo "=== FACT (citations) ==="
  # FACT scrape les URLs citees pour les verifier. Il utilise Jina, un
  # outil TIERS : c'est methodologiquement necessaire — faire valider les
  # citations de webtools par webtools lui-meme serait juge et partie.
  FACT_OUT="$RESULTS/fact"
  mkdir -p "$FACT_OUT"
  QUERY="$BENCH/data/prompt_data/query.jsonl"
  NPROC="${WORKERS:-4}"

  python -u -m utils.extract --raw_data_path "$RAW" \
    --output_path "$FACT_OUT/extracted.jsonl" \
    --query_data_path "$QUERY" --n_total_process "$NPROC"

  python -u -m utils.deduplicate --raw_data_path "$FACT_OUT/extracted.jsonl" \
    --output_path "$FACT_OUT/deduplicated.jsonl" \
    --query_data_path "$QUERY" --n_total_process "$NPROC"

  python -u -m utils.scrape --raw_data_path "$FACT_OUT/deduplicated.jsonl" \
    --output_path "$FACT_OUT/scraped.jsonl" --n_total_process "$NPROC"

  python -u -m utils.validate --raw_data_path "$FACT_OUT/scraped.jsonl" \
    --output_path "$FACT_OUT/validated.jsonl" \
    --query_data_path "$QUERY" --n_total_process "$NPROC"

  python -u -m utils.stat --input_path "$FACT_OUT/validated.jsonl" \
    --output_path "$FACT_OUT/fact_result.txt"
fi

echo "Resultats : $RESULTS"
