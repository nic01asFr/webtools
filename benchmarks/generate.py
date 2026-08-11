#!/usr/bin/env python3
"""
Genere les rapports webtools pour DeepResearch Bench.

Concu pour tourner plusieurs heures sans surveillance :
  - reprise apres interruption (les taches deja faites sont sautees)
  - une tache en echec n'arrete jamais le lot
  - journal separe des echecs, pour distinguer "webtools a mal repondu"
    de "le harnais a plante"
  - concurrence bornee (le pod sature au-dela de quelques rapports
    simultanes : chaque rapport lance lui-meme jusqu'a 3 extractions
    paralleles)

Usage :
  python generate.py --lang en --limit 10 --model-name webtools-v2
  python generate.py --lang en                 # les 50 taches anglaises
"""
import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from convert import report_to_markdown, sanity_check

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("generate")

BENCH = Path("/home/onyxia/work/benchmarks/deep_research_bench")
QUERIES = BENCH / "data/prompt_data/query.jsonl"
API = os.environ.get("WEBTOOLS_API", "http://localhost:8000")
API_KEY = os.environ.get("WEBTOOLS_API_KEY", "")


def load_queries(lang: str, limit: int | None):
    tasks = []
    with open(QUERIES, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if lang != "all" and d.get("language") != lang:
                continue
            tasks.append(d)
    return tasks[:limit] if limit else tasks


def load_done(out_path: Path):
    """Ids deja generes : permet la reprise sans refaire le travail."""
    done = set()
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["id"])
                except Exception:
                    continue
    return done


async def run_one(client, task, max_sources, timeout_s):
    """Un rapport. Retourne (record | None, diagnostic)."""
    tid, prompt = task["id"], task["prompt"]
    t0 = time.time()
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY

    try:
        resp = await client.post(
            f"{API}/api/v1/research/deep",
            json={"topic": prompt, "max_results": max_sources},
            headers=headers,
            timeout=timeout_s,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        return None, {"id": tid, "error": f"{type(e).__name__}: {e}", "elapsed": round(time.time() - t0, 1)}

    if not payload.get("success", True):
        return None, {"id": tid, "error": payload.get("error", "success=false"), "elapsed": round(time.time() - t0, 1)}

    article = report_to_markdown(payload)
    diag = sanity_check(article)
    diag.update({"id": tid, "elapsed": round(time.time() - t0, 1)})

    # Un article inexploitable est journalise ET enregistre : le benchmark
    # doit voir la vraie production du systeme, pas une version filtree.
    # Filtrer ici gonflerait artificiellement le score.
    return {"id": tid, "prompt": prompt, "article": article}, diag


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="en", choices=["en", "zh", "all"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--model-name", default="webtools-v2",
                    help="nom du systeme evalue (= nom du fichier .jsonl)")
    ap.add_argument("--max-sources", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--out-dir", default=str(BENCH / "data/test_data/raw_data"))
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.model_name}.jsonl"
    diag_path = out_dir.parent / f"{args.model_name}_diagnostics.jsonl"

    tasks = load_queries(args.lang, args.limit)
    done = load_done(out_path)
    todo = [t for t in tasks if t["id"] not in done]

    log.info(f"{len(tasks)} tache(s) [{args.lang}] — {len(done)} deja faite(s) — {len(todo)} a faire")
    if not todo:
        log.info("Rien a faire.")
        return

    sem = asyncio.Semaphore(args.concurrency)
    out_lock = asyncio.Lock()
    counters = {"ok": 0, "failed": 0, "unusable": 0}

    async with httpx.AsyncClient() as client:
        async def worker(task):
            async with sem:
                record, diag = await run_one(client, task, args.max_sources, args.timeout)
                async with out_lock:
                    # Ecriture immediate : une interruption ne perd que la
                    # tache en cours, jamais celles deja terminees.
                    with open(diag_path, "a", encoding="utf-8") as fd:
                        fd.write(json.dumps(diag, ensure_ascii=False) + "\n")
                    if record:
                        with open(out_path, "a", encoding="utf-8") as fo:
                            fo.write(json.dumps(record, ensure_ascii=False) + "\n")
                        counters["ok"] += 1
                        if not diag.get("usable"):
                            counters["unusable"] += 1
                            log.warning(f"  #{task['id']} enregistre mais FAIBLE "
                                        f"({diag['words']} mots, {diag['bibliography_entries']} refs)")
                        else:
                            log.info(f"  #{task['id']} ok — {diag['words']} mots, "
                                     f"{diag['bibliography_entries']} refs, {diag['elapsed']}s")
                    else:
                        counters["failed"] += 1
                        log.error(f"  #{task['id']} ECHEC — {diag.get('error')}")

        await asyncio.gather(*[worker(t) for t in todo], return_exceptions=True)

    total = counters["ok"] + counters["failed"]
    log.info("=" * 60)
    log.info(f"Termine : {counters['ok']}/{total} rapports produits "
             f"({counters['unusable']} faibles), {counters['failed']} echecs")
    log.info(f"Rapports    : {out_path}")
    log.info(f"Diagnostics : {diag_path}")


if __name__ == "__main__":
    asyncio.run(main())
