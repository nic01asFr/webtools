#!/usr/bin/env python3
"""
Genere les rapports webtools pour DeepResearch Bench.

Concu pour tourner plusieurs heures sans surveillance :
  - reprise apres interruption (les taches deja faites sont sautees)
  - une tache en echec n'arrete jamais le lot
  - journal separe des echecs, pour distinguer "webtools a mal repondu"
    de "le harnais a plante"
  - concurrence bornee (chaque rapport lance lui-meme jusqu'a 3 extractions
    paralleles)
  - manifeste de run : commit, modele, juge prevu, taches, reglages
  - refus de demarrer si le service ne sert pas le commit du depot

Environnement (aucune valeur par defaut, echec explicite si absent) :
  DRB_HOME            depot DeepResearch Bench (voir benchmarks/INSTALL.md)
  WEBTOOLS_API        URL de base du service, ex. http://localhost:8000
  WEBTOOLS_API_KEY    cle d'API (sauf --anonymous)

Usage :
  python generate.py --lang en --limit 10 --model-name webtools-v2
  python generate.py --lang en                 # les 50 taches anglaises
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from convert import report_has_text, report_to_markdown, sanity_check

log = logging.getLogger("generate")

REPO_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    """Variable d'environnement ou option obligatoire manquante."""


class MismatchError(Exception):
    """Le service ne sert pas le code attendu : le run serait ininterpretable."""


class Settings:
    def __init__(self, drb_home: Path, api: str, api_key: str) -> None:
        self.drb_home = drb_home
        self.api = api.rstrip("/")
        self.api_key = api_key

    @property
    def queries(self) -> Path:
        return self.drb_home / "data/prompt_data/query.jsonl"

    @property
    def default_out_dir(self) -> Path:
        return self.drb_home / "data/test_data/raw_data"


def load_settings(env: Mapping[str, str], anonymous: bool = False) -> Settings:
    """Lit la configuration dans l'environnement ; liste TOUT ce qui manque."""
    missing = [k for k in ("DRB_HOME", "WEBTOOLS_API") if not env.get(k)]
    if not anonymous and not env.get("WEBTOOLS_API_KEY"):
        missing.append("WEBTOOLS_API_KEY (ou --anonymous si le service l'autorise)")
    if missing:
        raise ConfigError("variable(s) d'environnement manquante(s) : " + ", ".join(missing))
    home = Path(env["DRB_HOME"])
    if not home.is_dir():
        raise ConfigError(f"DRB_HOME ne designe pas un dossier : {home}")
    return Settings(home, env["WEBTOOLS_API"], env.get("WEBTOOLS_API_KEY", ""))


def load_queries(path: Path, lang: str, limit: int | None) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if lang != "all" and d.get("language") != lang:
                continue
            tasks.append(d)
    return tasks[:limit] if limit else tasks


def load_done(out_path: Path) -> set[Any]:
    """Ids deja generes : permet la reprise sans refaire le travail."""
    done: set[Any] = set()
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["id"])
                except Exception:
                    continue
    return done


def repo_state(root: Path = REPO_ROOT) -> tuple[str, bool]:
    """Commit courant du depot et indicateur d'arbre modifie."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=root, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return commit, bool(status)
    except Exception:  # noqa: BLE001 - git absent ou depot invalide
        return "unknown", False


def check_commit(health: Mapping[str, Any], repo_commit: str, allow_mismatch: bool) -> list[str]:
    """Compare le code servi au depot. Retourne les avertissements ignores.

    Leve MismatchError si /health ne fournit pas l'identite du code, si le
    serveur tourne sur un arbre modifie, ou si les commits different, sauf
    si allow_mismatch est vrai.
    """
    problems: list[str] = []
    served = health.get("commit")
    if not isinstance(served, str) or not served:
        problems.append("/health ne renvoie pas `commit`")
    elif served == "unknown":
        problems.append("le service ne connait pas son commit (`unknown`)")
    elif repo_commit == "unknown":
        problems.append("le commit du depot local est inconnu")
    elif not (served.startswith(repo_commit) or repo_commit.startswith(served)):
        problems.append(f"commit servi {served[:12]} different du depot {repo_commit[:12]}")
    if "commit_dirty" not in health:
        problems.append("/health ne renvoie pas `commit_dirty`")
    elif health.get("commit_dirty"):
        problems.append("le service tourne sur un arbre de travail modifie (`commit_dirty`)")
    if "started_at" not in health:
        problems.append("/health ne renvoie pas `started_at`")
    if problems and not allow_mismatch:
        raise MismatchError("; ".join(problems) + " (--allow-mismatch pour passer outre)")
    return problems


def auth_headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def fetch_health(client: httpx.AsyncClient, settings: Settings) -> dict[str, Any]:
    resp = await client.get(f"{settings.api}/health", headers=auth_headers(settings.api_key), timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, dict) else {}


def build_manifest(
    *,
    run: str,
    args: Mapping[str, Any],
    health: Mapping[str, Any],
    repo_commit: str,
    repo_dirty: bool,
    task_ids: list[Any],
    warnings: list[str],
) -> dict[str, Any]:
    engines = health.get("engines") or health.get("engine_catalog")
    return {
        "run": run,
        "created_at": now_iso(),
        "repo_commit": repo_commit,
        "repo_dirty": repo_dirty,
        "served_commit": health.get("commit"),
        "served_commit_dirty": health.get("commit_dirty"),
        "served_started_at": health.get("started_at"),
        "mismatch_warnings": warnings,
        "model": args.get("llm_model") or health.get("llm_model") or "unknown",
        "judge": args.get("judge"),
        "lang": args.get("lang"),
        "task_ids": task_ids,
        "searxng_engines": engines,
        "diagnostics_config": None,
        "resumptions": [],
    }


def write_json(path: Path, data: Mapping[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


async def run_one(
    client: httpx.AsyncClient, settings: Settings, task: Mapping[str, Any], timeout_s: float
) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any] | None]:
    """Un rapport. Retourne (record | None, diagnostic, diagnostics.config)."""
    tid, prompt = task["id"], task["prompt"]
    t0 = time.time()

    def elapsed() -> float:
        return round(time.time() - t0, 1)

    try:
        resp = await client.post(
            f"{settings.api}/api/v1/research/deep",
            json={"topic": prompt},
            headers=auth_headers(settings.api_key),
            timeout=timeout_s,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:  # noqa: BLE001 - toute panne reseau ou JSON devient un echec de tache
        return None, {"id": tid, "error": f"{type(e).__name__}: {e}", "elapsed": elapsed()}, None

    if not isinstance(payload, dict):
        return None, {"id": tid, "error": "reponse JSON inattendue", "elapsed": elapsed()}, None

    config = (payload.get("diagnostics") or {}).get("config")

    # Seule une reponse sans aucun texte de rapport est un echec. Une reponse
    # `success: false` / `degraded: true` qui porte du texte est enregistree :
    # le benchmark doit voir la vraie production du systeme, pas une version
    # filtree (filtrer gonflerait artificiellement le score).
    if not report_has_text(payload):
        return None, {
            "id": tid,
            "error": payload.get("error") or "reponse sans rapport",
            "success": payload.get("success"),
            "elapsed": elapsed(),
        }, config

    article = report_to_markdown(payload)
    diag = sanity_check(article, payload)
    diag.update({"id": tid, "elapsed": elapsed()})
    if payload.get("error"):
        diag["error"] = payload["error"]
    return {"id": tid, "prompt": prompt, "article": article}, diag, config


async def run_batch(
    client: httpx.AsyncClient,
    settings: Settings,
    args: argparse.Namespace,
    *,
    repo_commit: str | None = None,
    repo_dirty: bool | None = None,
) -> dict[str, int]:
    """Verifie le code servi, ecrit le manifeste, puis genere les rapports."""
    if repo_commit is None:
        repo_commit, repo_dirty = repo_state()
    out_dir = Path(args.out_dir) if args.out_dir else settings.default_out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.model_name}.jsonl"
    diag_path = out_dir.parent / f"{args.model_name}_diagnostics.jsonl"
    manifest_path = out_dir.parent / f"{args.model_name}_manifest.json"

    health = await fetch_health(client, settings)
    warnings = check_commit(health, repo_commit, args.allow_mismatch)

    tasks = load_queries(settings.queries, args.lang, args.limit)
    done = load_done(out_path)
    todo = [t for t in tasks if t["id"] not in done]

    if manifest_path.exists():
        # Reprise : melanger deux commits dans un meme run le rendrait
        # ininterpretable, on garde le manifeste initial et on trace la reprise.
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("served_commit") != health.get("commit") and not args.allow_mismatch:
            raise MismatchError(
                f"reprise d'un run demarre au commit {manifest.get('served_commit')} "
                f"alors que le service sert {health.get('commit')} (--allow-mismatch pour passer outre)"
            )
        manifest["resumptions"].append(
            {"at": now_iso(), "served_commit": health.get("commit"), "started_at": health.get("started_at")}
        )
    else:
        manifest = build_manifest(
            run=args.model_name,
            args=vars(args),
            health=health,
            repo_commit=repo_commit,
            repo_dirty=bool(repo_dirty),
            task_ids=[t["id"] for t in tasks],
            warnings=warnings,
        )
    write_json(manifest_path, manifest)

    log.info(f"{len(tasks)} tache(s) [{args.lang}] - {len(done)} deja faite(s) - {len(todo)} a faire")
    counters = {"ok": 0, "failed": 0, "unusable": 0, "degraded": 0}
    if not todo:
        log.info("Rien a faire.")
        return counters

    sem = asyncio.Semaphore(args.concurrency)
    out_lock = asyncio.Lock()

    async def worker(task: Mapping[str, Any]) -> None:
        async with sem:
            record, diag, config = await run_one(client, settings, task, args.timeout)
            async with out_lock:
                # Ecriture immediate : une interruption ne perd que la tache
                # en cours, jamais celles deja terminees.
                with open(diag_path, "a", encoding="utf-8") as fd:
                    fd.write(json.dumps(diag, ensure_ascii=False) + "\n")
                if manifest.get("diagnostics_config") is None and config is not None:
                    manifest["diagnostics_config"] = config
                    write_json(manifest_path, manifest)
                if record is None:
                    counters["failed"] += 1
                    log.error(f"  #{task['id']} ECHEC - {diag.get('error')}")
                    return
                with open(out_path, "a", encoding="utf-8") as fo:
                    fo.write(json.dumps(record, ensure_ascii=False) + "\n")
                counters["ok"] += 1
                if diag.get("degraded"):
                    counters["degraded"] += 1
                    log.warning(
                        f"  #{task['id']} enregistre DEGRADE - "
                        f"{len(diag['failed_sections'])} echec(s), "
                        f"{len(diag['unsourced_sections'])} sans source"
                    )
                if not diag.get("usable"):
                    counters["unusable"] += 1
                    log.warning(
                        f"  #{task['id']} enregistre mais FAIBLE "
                        f"({diag['words']} mots, {diag['bibliography_entries']} refs)"
                    )
                else:
                    log.info(
                        f"  #{task['id']} ok - {diag['words']} mots, "
                        f"{diag['bibliography_entries']} refs, {diag['elapsed']}s"
                    )

    async def guarded(task: Mapping[str, Any]) -> None:
        try:
            await worker(task)
        except Exception as e:  # noqa: BLE001 - une panne du harnais ne doit pas arreter le lot
            # Une panne du harnais ne doit pas arreter le lot, mais doit se voir.
            counters["failed"] += 1
            log.error(f"  #{task['id']} PLANTAGE HARNAIS - {type(e).__name__}: {e}")

    await asyncio.gather(*[guarded(t) for t in todo])

    total = counters["ok"] + counters["failed"]
    log.info("=" * 60)
    log.info(
        f"Termine : {counters['ok']}/{total} rapports produits ({counters['degraded']} degrades, "
        f"{counters['unusable']} faibles), {counters['failed']} echecs"
    )
    log.info(f"Rapports    : {out_path}")
    log.info(f"Diagnostics : {diag_path}")
    log.info(f"Manifeste   : {manifest_path}")
    return counters


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="en", choices=["en", "zh", "all"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--model-name", default="webtools-v2",
                    help="nom du systeme evalue (= nom du fichier .jsonl)")
    ap.add_argument("--llm-model", default=os.environ.get("WEBTOOLS_LLM_MODEL"),
                    help="modele LLM du service, pour le manifeste (sinon lu dans /health)")
    ap.add_argument("--judge", default=os.environ.get("JUDGE", "sovereign"),
                    help="juge prevu pour evaluate.sh, pour le manifeste")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--out-dir", default=None,
                    help="defaut : $DRB_HOME/data/test_data/raw_data")
    ap.add_argument("--allow-mismatch", action="store_true",
                    help="demarre meme si le commit servi differe du depot ou est inconnu")
    ap.add_argument("--anonymous", action="store_true",
                    help="n'exige pas WEBTOOLS_API_KEY (service en mode anonyme)")
    return ap


async def amain(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    try:
        settings = load_settings(os.environ if env is None else env, anonymous=args.anonymous)
        async with httpx.AsyncClient() as client:
            await run_batch(client, settings, args)
    except (ConfigError, MismatchError) as e:
        log.error(str(e))
        return 2
    return 0


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
