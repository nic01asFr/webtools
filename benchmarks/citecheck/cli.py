"""Commandes : generer, evaluer, etalonner, auditer, comparer.

  generer   interroge le service sur un corpus et garde chaque reponse brute
  evaluer   juge chaque citation contre la page reelle (et contre le texte
            du pipeline quand la reponse le joint)
  etalonner mesure l'exactitude du juge sur des cas au verdict connu
  auditer   tire un echantillon a relire a la main, puis calcule l'accord
  comparer  met deux evaluations cote a cote
  ecart     ecart apparie par tache entre deux series de repetitions
            (runs rejoues sur la meme collecte : WEBTOOLS_REPLAY_MODE)

Environnement :
  WEBTOOLS_API, WEBTOOLS_API_KEY        service evalue (generer)
  CITECHECK_LLM_API_KEY                 juge et plongements (evaluer, etalonner)
  CITECHECK_LLM_BASE_URL                defaut https://llm.lab.sspcloud.fr/api
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import random
import sys
import time
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from citecheck.juge import Judge, Judgement, embedder_for
from citecheck.llm import Endpoint, LLMError
from citecheck.mesures import (
    JUDGED,
    PROBLEMS,
    answer_correct,
    paired_difference,
    ratio_ci,
    summarize,
)
from citecheck.pages import BrowserFetcher, PageStore, default_client
from citecheck.texte import fold
from citecheck.triplets import parse_answer, pipeline_texts

DEFAULT_LLM_BASE_URL = "https://llm.lab.sspcloud.fr/api"
DEFAULT_EMBED_MODEL = "qwen3-embedding-8b"
# Raisonnement du juge coupe par defaut : sur le proxy SSPCloud, un modele
# qwen3 raisonne avant de repondre et epuise max_tokens sans rendre de verdict
# (mesure le 12/11/2026). Un modele sans raisonnement ignore ce champ.
NO_REASONING = {"chat_template_kwargs": {"enable_thinking": False}}
# Seuils d'acceptation du juge, fixes avant toute mesure.
MAX_FALSE_ACCEPT = 0.05
MIN_BINARY_AGREEMENT = 0.90


class ConfigError(Exception):
    pass


def _env(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"variable d'environnement manquante : {name}")
    return value


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_corpus(path: Path, mode: str, limit: int | None) -> list[dict[str, Any]]:
    tasks = [t for t in read_jsonl(path) if mode == "tous" or t["mode"] == mode]
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        raise ConfigError("identifiants en double dans le corpus")
    return tasks[:limit] if limit else tasks


# --- generer ------------------------------------------------------------------

def request_for(task: Mapping[str, Any], timeout_s: int) -> tuple[str, dict[str, Any]]:
    if task["mode"] == "quick":
        return "/api/v1/research/quick", {
            "query": task["query"],
            "max_sources": task.get("max_sources", 5),
            "include_source_content": True,
        }
    return "/api/v1/research/deep", {
        "topic": task["query"],
        "timeout": timeout_s,
        "output_format": {"include_section_data": True},
    }


async def generate_one(
    client: httpx.AsyncClient, api: str, key: str, task: Mapping[str, Any], timeout_s: int
) -> dict[str, Any]:
    from generate import auth_headers

    path, body = request_for(task, timeout_s)
    record: dict[str, Any] = {
        "id": task["id"], "mode": task["mode"], "query": task["query"],
        "lang": task.get("lang"), "attendu": task.get("attendu") or [],
    }
    t0 = time.time()
    try:
        response = await client.post(
            f"{api}{path}", json=body, headers=auth_headers(key), timeout=timeout_s + 120
        )
        record["http_status"] = response.status_code
        payload = response.json()
        if response.status_code >= 400:
            record["error"] = f"HTTP {response.status_code} : {json.dumps(payload)[:300]}"
        record["payload"] = payload
    except Exception as exc:  # noqa: BLE001 - une tache en echec n'arrete pas le lot
        record["error"] = f"{type(exc).__name__}: {exc}"[:300]
    record["elapsed"] = round(time.time() - t0, 1)
    return record


async def cmd_generate(
    args: argparse.Namespace,
    env: Mapping[str, str],
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    from generate import check_commit, fetch_health, now_iso, repo_state

    api = _env(env, "WEBTOOLS_API").rstrip("/")
    key = env.get("WEBTOOLS_API_KEY", "")
    out = Path(args.sortie)
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    tasks = load_corpus(Path(args.corpus), args.mode, args.limit)

    class _Settings:
        def __init__(self) -> None:
            self.api, self.api_key = api, key

    async with httpx.AsyncClient(transport=transport) as client:
        health = await fetch_health(client, _Settings())
        repo_commit, repo_dirty = repo_state()
        warnings = check_commit(health, repo_commit, args.allow_mismatch)
        manifest_path = out / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        if manifest and manifest.get("served_commit") != health.get("commit"):
            raise ConfigError(
                f"le run {out} a ete commence sur le commit {manifest.get('served_commit')}, "
                f"le service sert {health.get('commit')}"
            )
        manifest.update({
            "served_commit": health.get("commit"),
            "served_started_at": health.get("started_at"),
            "repo_commit": repo_commit,
            "repo_dirty": repo_dirty,
            "mismatch_warnings": warnings,
            "llm_model": health.get("llm_model"),
            "corpus": str(args.corpus),
            "corpus_sha256": hashlib.sha256(Path(args.corpus).read_bytes()).hexdigest(),
        })
        manifest.setdefault("created_at", now_iso())
        manifest.setdefault("sessions", []).append({"started_at": now_iso(), "mode": args.mode})
        write_json(manifest_path, manifest)

        pending = []
        for task in tasks:
            path = raw_dir / f"{task['id']}.json"
            if path.exists() and not json.loads(path.read_text(encoding="utf-8")).get("error"):
                continue
            pending.append(task)
        print(f"{len(tasks)} tache(s), {len(pending)} a generer")
        semaphore = asyncio.Semaphore(args.concurrence)

        async def worker(task: Mapping[str, Any]) -> None:
            async with semaphore:
                record = await generate_one(client, api, key, task, args.timeout)
            write_json(raw_dir / f"{task['id']}.json", record)
            state = record.get("error") or "ok"
            print(f"  {task['id']} ({task['mode']}) {record['elapsed']} s : {state}", flush=True)
            config = ((record.get("payload") or {}).get("diagnostics") or {}).get("config")
            if config and "diagnostics_config" not in manifest:
                manifest["diagnostics_config"] = config
                write_json(manifest_path, manifest)

        await asyncio.gather(*(worker(t) for t in pending))
    return 0


# --- evaluer ------------------------------------------------------------------

def citation_key(task_id: str, claim: str, url: str | None) -> str:
    raw = json.dumps([task_id, fold(claim), url or ""], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def judge_endpoint(
    env: Mapping[str, str],
    transport: httpx.AsyncBaseTransport | None = None,
    reasoning: bool = False,
) -> Endpoint:
    return Endpoint(env.get("CITECHECK_LLM_BASE_URL") or DEFAULT_LLM_BASE_URL,
                    _env(env, "CITECHECK_LLM_API_KEY"), transport=transport,
                    chat_extra=None if reasoning else NO_REASONING)


def judge_label(model: str, reasoning: bool) -> str:
    """Nom du juge dans les sorties : modele, et mode de raisonnement s'il est actif."""
    return f"{model}-raisonnement" if reasoning else model


def collect(raw_records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Citations dedoublonnees de toutes les reponses, et comptes par tache."""
    items: dict[str, dict[str, Any]] = {}
    tasks: dict[str, Any] = {}
    for record in raw_records:
        payload = record.get("payload")
        info: dict[str, Any] = {"mode": record["mode"], "query": record["query"],
                                "elapsed": record.get("elapsed")}
        tasks[record["id"]] = info
        if record.get("error") or not isinstance(payload, dict):
            info["echec"] = record.get("error") or "reponse absente"
            continue
        parsed = parse_answer(record["id"], record["mode"], payload)
        texts = pipeline_texts(record["mode"], payload)
        info.update({
            "unites": parsed.units_total, "unites_citees": parsed.units_cited,
            "sections_ecartees": parsed.skipped_sections, "sources": len(parsed.sources),
        })
        if record["mode"] == "quick" and record.get("attendu"):
            info["reponse_exacte"] = answer_correct(payload.get("answer"), record["attendu"])
            info["reponse"] = payload.get("answer")
        for c in parsed.citations:
            k = citation_key(c.task_id, c.claim, c.url)
            if k in items:
                items[k]["occurrences"] += 1
                continue
            items[k] = {
                "key": k, "task_id": c.task_id, "mode": record["mode"], "location": c.location,
                "ref": c.ref, "url": c.url, "claim": c.claim, "context": c.context,
                "occurrences": 1, "pipeline_text": texts.get(c.url or ""),
            }
    return list(items.values()), tasks


async def evaluate_item(item: dict[str, Any], store: PageStore, judge: Judge,
                        with_pipeline: bool) -> dict[str, Any]:
    pipeline_text = item.pop("pipeline_text", None)
    result = dict(item)
    if item["url"] is None:
        result.update({"status": "orpheline"})
        return result
    page = await store.get(item["url"])
    result["page_status"] = page.status
    result["page_error"] = page.error
    result["page_method"] = page.method
    # Empreinte du texte juge : une page qui change entraine un nouveau jugement.
    result["page_sha"] = hashlib.sha256(page.text.encode("utf-8")).hexdigest()[:16]
    if page.ok:
        judgement = await judge.judge(item["claim"], item["context"], page.text, item["url"], key=item["url"])
    else:
        judgement = Judgement(status="inverifiable", error=page.error)
    result.update(judgement.to_dict())
    if with_pipeline and pipeline_text:
        pj = await judge.judge(item["claim"], item["context"], pipeline_text, item["url"],
                               key=f"pipeline::{item['url']}")
        result["pipeline_status"] = pj.status
        result["pipeline_quote"] = pj.quote
        result["pipeline_analysis"] = pj.analysis
    return result


def build_groups(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Affirmations : les citations d'une meme phrase d'une meme tache."""
    groups: dict[str, dict[str, Any]] = {}
    for r in records:
        raw = json.dumps([r["task_id"], fold(r["claim"])], ensure_ascii=False)
        key = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
        group = groups.setdefault(key, {
            "key": key, "task_id": r["task_id"], "mode": r["mode"], "claim": r["claim"],
            "context": r.get("context", ""), "members": [],
        })
        group["members"].append(r)
    return groups


async def evaluate_group(group: dict[str, Any], store: PageStore, judge: Judge) -> dict[str, Any]:
    """Statut d'une affirmation au vu de toutes les sources qu'elle cite.

    - soutenue si une source la soutient seule, ou si la reunion des pages
      accessibles la soutient ;
    - sinon inverifiable si une page citee est inaccessible (elle pourrait
      la soutenir) ;
    - sinon le verdict de la reunion (ou de la seule page accessible).
    """
    members = group["members"]
    result: dict[str, Any] = {
        "key": group["key"], "task_id": group["task_id"], "mode": group["mode"],
        "claim": group["claim"], "refs": sorted({m["ref"] for m in members}),
        "urls": sorted({m["url"] for m in members if m.get("url")}),
    }
    if any(m["status"] == "soutenue" for m in members):
        return {**result, "status": "soutenue", "via": "une_source"}
    accessible = list(dict.fromkeys(m["url"] for m in members if m.get("page_status") == "ok"))
    union_status = None
    if len(accessible) >= 2:
        pages = [(url, (await store.get(url)).text) for url in accessible]
        judgement = await judge.judge_group(group["claim"], group["context"], pages)
        result.update({"quote": judgement.quote, "analysis": judgement.analysis})
        if judgement.status == "soutenue":
            return {**result, "status": "soutenue", "via": "reunion"}
        union_status = judgement.status
    elif len(accessible) == 1:
        union_status = next(m["status"] for m in members if m.get("url") == accessible[0])
    if any(m["status"] == "inverifiable" for m in members):
        return {**result, "status": "inverifiable", "via": "page_inaccessible"}
    return {**result, "status": union_status or "orpheline", "via": "reunion" if len(accessible) >= 2 else "une_source"}


async def cmd_evaluate(
    args: argparse.Namespace,
    env: Mapping[str, str],
    llm_transport: httpx.AsyncBaseTransport | None = None,
    page_transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    out = Path(args.sortie)
    raw_records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((out / "raw").glob("*.json"))]
    if not raw_records:
        raise ConfigError(f"aucune reponse dans {out / 'raw'} : lancer generer d'abord")
    items, tasks = collect(raw_records)
    eval_dir = out / f"evaluation-{judge_label(args.juge, args.raisonnement)}"
    eval_dir.mkdir(parents=True, exist_ok=True)
    results_path = eval_dir / "citations.jsonl"
    done = {r["key"]: r for r in read_jsonl(results_path)}

    endpoint = judge_endpoint(env, llm_transport, args.raisonnement)
    try:
        await endpoint.ping(args.juge)
    except LLMError as exc:
        await endpoint.close()
        raise ConfigError(f"juge indisponible : {exc}") from exc
    embedder = None if args.plongements == "aucun" else embedder_for(endpoint, args.plongements)
    judge = Judge(endpoint, args.juge, embedder)
    semaphore = asyncio.Semaphore(args.concurrence)
    lock = asyncio.Lock()
    progress = {"n": 0}
    browser_cm = contextlib.nullcontext() if args.sans_navigateur else BrowserFetcher()
    async with default_client(page_transport) as http, browser_cm as browser:
        store = PageStore(out / "pages", http, browser=browser)

        async def needs_judging(item: dict[str, Any]) -> bool:
            """Nouvelle citation, ou page dont le texte ou l'acces a change
            depuis le jugement (repli navigateur, page de blocage detectee)."""
            previous = done.get(item["key"])
            if previous is None:
                return True
            if item["url"] is None:
                return False
            page = await store.get(item["url"])
            sha = hashlib.sha256(page.text.encode("utf-8")).hexdigest()[:16]
            if previous.get("page_sha") and previous["page_sha"] != sha:
                return True
            return (previous.get("page_status") == "ok") != page.ok

        flags = await asyncio.gather(*(needs_judging(i) for i in items))
        pending = [i for i, flag in zip(items, flags) if flag]
        print(f"{len(items)} citation(s) distincte(s), {len(pending)} a juger, juge {args.juge}")

        async def worker(item: dict[str, Any]) -> None:
            async with semaphore:
                result = await evaluate_item(item, store, judge, not args.sans_pipeline)
            async with lock:
                with results_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")
                done[result["key"]] = result
                progress["n"] += 1
                print(f"  [{progress['n']}/{len(pending)}] {result['status']:<13} {result['task_id']} "
                      f"[{result['ref']}]", flush=True)

        await asyncio.gather(*(worker(i) for i in pending))

        keys = {i["key"] for i in items}
        records = [r for k, r in done.items() if k in keys]
        # Affirmations : une phrase est verifiable si ses sources, ensemble,
        # la soutiennent.
        groups_path = eval_dir / "affirmations.jsonl"
        groups_done = {g["key"]: g for g in read_jsonl(groups_path)}
        groups = build_groups(records)
        # Une affirmation est reevaluee si ses citations ou leurs verdicts changent.
        signature = {
            k: sorted(f"{m['key']}:{m['status']}:{m.get('page_sha', '')}" for m in g["members"])
            for k, g in groups.items()
        }

        async def group_worker(group: dict[str, Any]) -> None:
            async with semaphore:
                result = await evaluate_group(group, store, judge)
            result["members"] = signature[group["key"]]
            async with lock:
                with groups_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")
                groups_done[result["key"]] = result

        pending_groups = [
            g for k, g in groups.items()
            if k not in groups_done or groups_done[k].get("members") != signature[k]
        ]
        print(f"{len(groups)} affirmation(s), {len(pending_groups)} a evaluer")
        await asyncio.gather(*(group_worker(g) for g in pending_groups))
    await endpoint.close()

    claims = [groups_done[k] for k in groups]
    report = build_summary(records, tasks, judge_label(args.juge, args.raisonnement), claims)
    from generate import repo_state

    instrument_commit, instrument_dirty = repo_state()
    report["instrument"] = {"commit": instrument_commit, "modifie": instrument_dirty}
    write_json(eval_dir / "resume.json", report)
    (eval_dir / "RAPPORT.md").write_text(render_report(report, records, out), encoding="utf-8")
    print(f"Resultats : {eval_dir}")
    return 0


def build_summary(
    records: list[dict[str, Any]],
    tasks: dict[str, Any],
    judge_model: str,
    claims: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {"juge": judge_model, "par_mode": {}, "taches": tasks}
    for mode in ("quick", "deep"):
        mode_records = [r for r in records if r["mode"] == mode]
        mode_tasks = {k: v for k, v in tasks.items() if v["mode"] == mode}
        if not mode_tasks:
            continue
        block = summarize(mode_records)
        mode_claims = [c for c in claims or [] if c["mode"] == mode]
        if mode_claims:
            block["affirmations"] = len(mode_claims)
            block["affirmations_soutenues"] = ratio_ci(
                mode_claims, lambda c: c["status"] == "soutenue", lambda c: c["status"] in JUDGED
            )
            block["affirmations_contredites"] = ratio_ci(
                mode_claims, lambda c: c["status"] == "contredite", lambda c: c["status"] in JUDGED
            )
            block["affirmations_inverifiables"] = ratio_ci(
                mode_claims, lambda c: c["status"] == "inverifiable", lambda c: True
            )
            block["statuts_affirmations"] = dict(sorted(Counter(c["status"] for c in mode_claims).items()))
        block["taches_en_echec"] = sorted(k for k, v in mode_tasks.items() if v.get("echec"))
        units = sum(v.get("unites", 0) for v in mode_tasks.values())
        cited = sum(v.get("unites_citees", 0) for v in mode_tasks.values())
        block["unites_citees"] = {"n": cited, "sur": units, "taux": round(cited / units, 4) if units else None}
        graded = [v for v in mode_tasks.values() if "reponse_exacte" in v]
        if graded:
            ok = sum(1 for v in graded if v["reponse_exacte"])
            block["reponses_exactes"] = {"n": ok, "sur": len(graded), "taux": round(ok / len(graded), 4)}
        summary["par_mode"][mode] = block
    return summary


def _fmt_rate(block: Mapping[str, Any] | None) -> str:
    if not block or block.get("taux") is None:
        return "-"
    text = f"{100 * block['taux']:.1f} % ({block['n']}/{block['sur']})"
    if block.get("ic95"):
        low, high = block["ic95"]
        text += f" [IC95 {100 * low:.1f}-{100 * high:.1f}]"
    return text


def render_report(summary: Mapping[str, Any], records: list[dict[str, Any]], out: Path) -> str:
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    lines = [
        f"# Fiabilite des citations - {out.name}",
        "",
        f"- Commit servi : `{manifest.get('served_commit')}` ; modele du service : `{manifest.get('llm_model')}`",
        f"- Juge : `{summary['juge']}` (exactitude mesuree par `etalonner`, a lire avant ces chiffres)",
        "- Exactes : la page citee soutient chaque element de l'affirmation. IC95 par bootstrap sur les taches.",
        "",
    ]
    labels = [
        ("affirmations_soutenues", "Affirmations soutenues par leurs sources (ensemble)"),
        ("affirmations_contredites", "Affirmations contredites par leurs sources"),
        ("affirmations_inverifiables", "Affirmations dont une source est inaccessible"),
        ("exactes", "Citations exactes (source par source)"),
        ("exactes_ou_partielles", "Exactes ou partielles"),
        ("contredites", "Contredites par la page"),
        ("non_etayees_par_le_juge", "Verdict du juge non etaye"),
        ("inverifiables", "Pages inaccessibles"),
        ("orphelines", "Numeros sans source"),
        ("fidelite_au_texte_du_pipeline", "Soutenues par le texte du pipeline"),
        ("unites_citees", "Phrases portant une citation"),
        ("reponses_exactes", "Reponses exactes (quick)"),
    ]
    for mode, block in summary["par_mode"].items():
        lines += [f"## {'Recherche rapide' if mode == 'quick' else 'Recherche approfondie'}", "",
                  f"{block['taches']} tache(s) avec citations, {block['citations']} citation(s) distincte(s). "
                  f"Taches en echec : {', '.join(block['taches_en_echec']) or 'aucune'}.", "",
                  "| Mesure | Valeur |", "|---|---|"]
        for key, label in labels:
            if key in block:
                lines.append(f"| {label} | {_fmt_rate(block[key])} |")
        lines.append(f"| Statuts des citations | {block['statuts']} |")
        if block.get("statuts_affirmations"):
            lines.append(f"| Statuts des affirmations | {block['statuts_affirmations']} |")
        if block.get("origine_des_fautes"):
            lines.append(f"| Origine des fautes | {block['origine_des_fautes']} |")
        lines.append("")
    faults = [r for r in records if r["status"] in PROBLEMS]
    if faults:
        lines += ["## Citations fautives", ""]
        for r in sorted(faults, key=lambda r: (r["task_id"], r["ref"])):
            lines += [
                f"- **{r['status']}** - {r['task_id']} [{r['ref']}] {r['url']}",
                f"  - Affirmation : {r['claim']}",
                f"  - Juge : {r.get('analysis') or '-'}",
            ]
            if r.get("pipeline_status"):
                lines.append(f"  - Contre le texte du pipeline : {r['pipeline_status']}")
    return "\n".join(lines) + "\n"


# --- etalonner ----------------------------------------------------------------

def calibration_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Exactitude du juge : verdict exact, accord binaire juste/fautive,
    fausses acceptations (le pire defaut d'un juge de citations)."""
    judged = [r for r in rows if r["status"] != "erreur_juge"]
    exact = sum(1 for r in judged if r["status"] == r["attendu"])
    binary = sum(1 for r in judged if (r["status"] == "soutenue") == (r["attendu"] == "soutenue"))
    negatives = [r for r in judged if r["attendu"] != "soutenue"]
    positives = [r for r in judged if r["attendu"] == "soutenue"]
    false_accept = sum(1 for r in negatives if r["status"] == "soutenue")
    false_reject = sum(1 for r in positives if r["status"] != "soutenue")
    by_category: dict[str, list[int]] = {}
    for r in judged:
        counts = by_category.setdefault(r.get("categorie", "-"), [0, 0])
        counts[1] += 1
        counts[0] += int((r["status"] == "soutenue") == (r["attendu"] == "soutenue"))
    confusion = Counter(f"{r['attendu']} -> {r['status']}" for r in rows)
    fa_rate = false_accept / len(negatives) if negatives else 0.0
    agreement = binary / len(judged) if judged else 0.0
    return {
        "cas": len(rows),
        "erreurs_juge": len(rows) - len(judged),
        "verdict_exact": round(exact / len(judged), 4) if judged else None,
        "accord_binaire": round(agreement, 4),
        "fausses_acceptations": {"n": false_accept, "sur": len(negatives), "taux": round(fa_rate, 4)},
        "faux_rejets": {"n": false_reject, "sur": len(positives),
                        "taux": round(false_reject / len(positives), 4) if positives else None},
        "par_categorie": {k: f"{v[0]}/{v[1]}" for k, v in sorted(by_category.items())},
        "confusion": dict(sorted(confusion.items())),
        "juge_accepte": fa_rate <= MAX_FALSE_ACCEPT and agreement >= MIN_BINARY_AGREEMENT
                        and len(rows) == len(judged),
        "seuils": {"fausses_acceptations_max": MAX_FALSE_ACCEPT, "accord_binaire_min": MIN_BINARY_AGREEMENT},
    }


def load_cases(path: Path) -> list[dict[str, Any]]:
    """Cas d'etalonnage ; le texte d'un cas vient de pages/<page>.txt a cote
    du fichier quand il n'est pas en ligne (pages figees dans le depot)."""
    cases = read_jsonl(path)
    for case in cases:
        if "texte" not in case:
            page_dir = path.parent / "pages"
            case["texte"] = (page_dir / f"{case['page']}.txt").read_text(encoding="utf-8")
            url_file = page_dir / f"{case['page']}.url"
            if "url" not in case and url_file.exists():
                case["url"] = url_file.read_text(encoding="utf-8").strip()
        if case["attendu"] not in ("soutenue", "partielle", "contredite", "non_soutenue"):
            raise ConfigError(f"verdict attendu inconnu pour {case['id']} : {case['attendu']}")
    return cases


async def cmd_calibrate(
    args: argparse.Namespace,
    env: Mapping[str, str],
    llm_transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    cases = load_cases(Path(args.cas))
    endpoint = judge_endpoint(env, llm_transport, args.raisonnement)
    try:
        await endpoint.ping(args.juge)
    except LLMError as exc:
        await endpoint.close()
        raise ConfigError(f"juge indisponible : {exc}") from exc
    embedder = None if args.plongements == "aucun" else embedder_for(endpoint, args.plongements)
    judge = Judge(endpoint, args.juge, embedder)
    semaphore = asyncio.Semaphore(args.concurrence)

    async def run(case: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            t0 = time.time()
            j = await judge.judge(case["affirmation"], case.get("contexte", ""), case["texte"],
                                  case.get("url", ""), key=case.get("page") or case["id"])
        row = {"id": case["id"], "categorie": case.get("categorie"), "attendu": case["attendu"],
               "duree_s": round(time.time() - t0, 1), **j.to_dict()}
        mark = "ok " if (row["status"] == "soutenue") == (case["attendu"] == "soutenue") else "ECART"
        print(f"  {mark} {case['id']:<28} attendu {case['attendu']:<13} juge {row['status']}", flush=True)
        return row

    rows = await asyncio.gather(*(run(c) for c in cases))
    await endpoint.close()
    metrics = calibration_metrics(list(rows))
    label = judge_label(args.juge, args.raisonnement)
    metrics["juge"] = label
    metrics["duree_mediane_s"] = sorted(r["duree_s"] for r in rows)[len(rows) // 2] if rows else None
    out = Path(args.sortie)
    write_json(out / f"etalonnage-{label}.json", metrics)
    (out / f"etalonnage-{label}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in metrics.items() if k != "confusion"}, ensure_ascii=False, indent=2))
    return 0 if metrics["juge_accepte"] else 1


# --- auditer ------------------------------------------------------------------

def draw_sample(records: list[dict[str, Any]], per_status: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    sample: list[dict[str, Any]] = []
    by_status: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        if r["status"] not in ("orpheline", "inverifiable"):
            by_status.setdefault(r["status"], []).append(r)
    for status in sorted(by_status):
        group = sorted(by_status[status], key=lambda r: r["key"])
        sample.extend(rng.sample(group, min(per_status, len(group))))
    return sample


def cmd_audit(args: argparse.Namespace) -> int:
    eval_dir = Path(args.evaluation)
    audit_path = eval_dir / "audit.jsonl"
    if args.accord:
        rows = [r for r in read_jsonl(audit_path) if r.get("verdict_auditeur")]
        if not rows:
            raise ConfigError(f"aucun verdict_auditeur rempli dans {audit_path}")
        agree = sum(1 for r in rows if (r["status"] == "soutenue") == (r["verdict_auditeur"] == "soutenue"))
        exact = sum(1 for r in rows if r["status"] == r["verdict_auditeur"])
        accepted_wrongly = sum(1 for r in rows if r["status"] == "soutenue" and r["verdict_auditeur"] != "soutenue")
        accepted = sum(1 for r in rows if r["status"] == "soutenue")
        result = {
            "relus": len(rows),
            "accord_binaire": round(agree / len(rows), 4),
            "verdict_exact": round(exact / len(rows), 4),
            "soutenues_a_tort": f"{accepted_wrongly}/{accepted}",
            "desaccords": [
                {"key": r["key"], "juge": r["status"], "auditeur": r["verdict_auditeur"],
                 "note": r.get("note_auditeur", "")}
                for r in rows if r["status"] != r["verdict_auditeur"]
            ],
        }
        write_json(eval_dir / "audit-accord.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    records = read_jsonl(eval_dir / "citations.jsonl")
    sample = draw_sample(records, args.par_statut, args.graine)
    with audit_path.open("w", encoding="utf-8") as f:
        for r in sample:
            row = {k: r.get(k) for k in ("key", "task_id", "ref", "url", "claim", "context", "status",
                                         "quote", "analysis", "missing_numbers")}
            row["verdict_auditeur"] = ""
            row["note_auditeur"] = ""
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{len(sample)} citation(s) a relire : {audit_path}")
    return 0


# --- comparer -----------------------------------------------------------------

def cmd_compare(args: argparse.Namespace) -> int:
    a = json.loads((Path(args.a) / "resume.json").read_text(encoding="utf-8"))
    b = json.loads((Path(args.b) / "resume.json").read_text(encoding="utf-8"))
    if a.get("juge") != b.get("juge"):
        print(f"ATTENTION : juges differents ({a.get('juge')} / {b.get('juge')}), comparaison invalide")
    for mode in sorted(set(a["par_mode"]) | set(b["par_mode"])):
        print(f"\n== {mode} ==")
        for key in ("affirmations_soutenues", "affirmations_contredites", "exactes",
                    "exactes_ou_partielles", "contredites", "inverifiables",
                    "fidelite_au_texte_du_pipeline", "reponses_exactes"):
            va = (a["par_mode"].get(mode) or {}).get(key)
            vb = (b["par_mode"].get(mode) or {}).get(key)
            print(f"  {key:<32} {_fmt_rate(va):<40} {_fmt_rate(vb)}")
    return 0


def series_counts(evaluation: Path, mode: str) -> dict[str, dict[str, list[int]]]:
    """Comptes par tache d'une evaluation : affirmations soutenues et phrases citees."""
    tasks = json.loads((evaluation / "resume.json").read_text(encoding="utf-8"))["taches"]
    keep = {t for t, v in tasks.items() if v.get("mode") == mode}
    claims: dict[str, list[int]] = {t: [0, 0] for t in keep}
    for c in read_jsonl(evaluation / "affirmations.jsonl"):
        if c["task_id"] in keep and c["status"] in JUDGED:
            claims[c["task_id"]][1] += 1
            claims[c["task_id"]][0] += c["status"] == "soutenue"
    units = {t: [int(tasks[t].get("unites_citees", 0)), int(tasks[t].get("unites", 0))] for t in keep}
    return {"affirmations_soutenues": claims, "phrases_citees": units}


def cmd_series(args: argparse.Namespace) -> int:
    judges = set()
    a_counts, b_counts = [], []
    for target, dirs in ((a_counts, args.a), (b_counts, args.b)):
        for d in dirs:
            path = Path(d)
            judges.add(json.loads((path / "resume.json").read_text(encoding="utf-8")).get("juge"))
            target.append(series_counts(path, args.mode))
    if len(judges) > 1:
        print(f"ATTENTION : juges differents {sorted(map(str, judges))}, comparaison invalide")
    result = {
        metric: paired_difference([c[metric] for c in a_counts], [c[metric] for c in b_counts])
        for metric in ("affirmations_soutenues", "phrases_citees")
    }
    for metric, r in result.items():
        ic = r["ic95"]
        ic_text = f" [IC95 {100 * ic[0]:+.1f} ; {100 * ic[1]:+.1f}]" if ic else ""
        mean = r["ecart_moyen"]
        print()
        print(f"== {metric} ({args.mode}, repetitions {r['repetitions'][0]} contre {r['repetitions'][1]}) ==")
        print(f"  ecart moyen B - A : {'-' if mean is None else f'{100 * mean:+.1f} points'}{ic_text}")
        print(f"  taches en hausse / en baisse : {r['taches_en_hausse']} / {r['taches_en_baisse']} sur {r['taches']}")
        for side in ("a", "b"):
            n = r[f"bruit_{side}"]
            print(f"  bruit entre repetitions {side.upper()} : {'-' if n is None else f'{100 * n:.1f} points'}")
        for task, v in r["par_tache"].items():
            print(f"    {task:<24} {100 * v['a']:5.1f} % -> {100 * v['b']:5.1f} %")
    if args.sortie:
        write_json(Path(args.sortie), result)
    return 0


def judges_agreement(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> dict[str, Any]:
    """Accord de deux juges sur les memes elements (citations ou affirmations).

    Binaire : soutenue ou non. Le kappa de Cohen corrige l'accord du hasard.
    Les elements non juges par l'un des deux (page inaccessible, orpheline,
    erreur) sont exclus.
    """
    by_key = {r["key"]: r for r in b}
    pairs = [
        (r, by_key[r["key"]]) for r in a
        if r["key"] in by_key and r["status"] in JUDGED and by_key[r["key"]]["status"] in JUDGED
    ]
    n = len(pairs)
    if not n:
        return {"elements": 0}
    agree = sum(1 for x, y in pairs if (x["status"] == "soutenue") == (y["status"] == "soutenue"))
    pa = sum(1 for x, _ in pairs if x["status"] == "soutenue") / n
    pb = sum(1 for _, y in pairs if y["status"] == "soutenue") / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    observed = agree / n
    kappa = (observed - expected) / (1 - expected) if expected < 1 else 1.0
    return {
        "elements": n,
        "accord_binaire": round(observed, 4),
        "kappa": round(kappa, 4),
        "verdict_identique": round(sum(1 for x, y in pairs if x["status"] == y["status"]) / n, 4),
        "desaccords": [
            {"key": x["key"], "task_id": x["task_id"], "claim": x["claim"][:200],
             "juge_a": x["status"], "juge_b": y["status"]}
            for x, y in pairs if (x["status"] == "soutenue") != (y["status"] == "soutenue")
        ],
    }


def cmd_judges(args: argparse.Namespace) -> int:
    a_dir, b_dir = Path(args.a), Path(args.b)
    result = {
        "juge_a": a_dir.name, "juge_b": b_dir.name,
        "citations": judges_agreement(read_jsonl(a_dir / "citations.jsonl"), read_jsonl(b_dir / "citations.jsonl")),
        "affirmations": judges_agreement(
            read_jsonl(a_dir / "affirmations.jsonl"), read_jsonl(b_dir / "affirmations.jsonl")
        ),
    }
    write_json(a_dir.parent / f"accord-{a_dir.name}-{b_dir.name}.json", result)
    print(json.dumps({k: ({kk: vv for kk, vv in v.items() if kk != "desaccords"} if isinstance(v, dict) else v)
                      for k, v in result.items()}, ensure_ascii=False, indent=2))
    return 0


# --- point d'entree -----------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="citations", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="commande", required=True)

    g = sub.add_parser("generer")
    g.add_argument("--corpus", required=True)
    g.add_argument("--sortie", required=True)
    g.add_argument("--mode", choices=("quick", "deep", "tous"), default="tous")
    g.add_argument("--limit", type=int)
    g.add_argument("--concurrence", type=int, default=1)
    g.add_argument("--timeout", type=int, default=1200)
    g.add_argument("--allow-mismatch", action="store_true")

    e = sub.add_parser("evaluer")
    e.add_argument("--sortie", required=True)
    e.add_argument("--juge", required=True)
    e.add_argument("--plongements", default=DEFAULT_EMBED_MODEL)
    e.add_argument("--concurrence", type=int, default=4)
    e.add_argument("--sans-pipeline", action="store_true")
    e.add_argument("--sans-navigateur", action="store_true",
                   help="pas de repli par navigateur pour les pages vides ou bloquees")
    e.add_argument("--raisonnement", action="store_true", help="laisser le juge raisonner (coupe par defaut)")

    c = sub.add_parser("etalonner")
    c.add_argument("--cas", required=True)
    c.add_argument("--juge", required=True)
    c.add_argument("--sortie", required=True)
    c.add_argument("--plongements", default=DEFAULT_EMBED_MODEL)
    c.add_argument("--concurrence", type=int, default=4)
    c.add_argument("--raisonnement", action="store_true", help="laisser le juge raisonner (coupe par defaut)")

    a = sub.add_parser("auditer")
    a.add_argument("--evaluation", required=True)
    a.add_argument("--par-statut", type=int, default=8)
    a.add_argument("--graine", type=int, default=1)
    a.add_argument("--accord", action="store_true")

    m = sub.add_parser("comparer")
    m.add_argument("a")
    m.add_argument("b")

    j = sub.add_parser("accord-juges", help="accord de deux juges sur le meme run")
    j.add_argument("a")
    j.add_argument("b")

    s = sub.add_parser("ecart", help="ecart apparie par tache entre deux series de repetitions")
    s.add_argument("--a", nargs="+", required=True, help="evaluations de la configuration A")
    s.add_argument("--b", nargs="+", required=True, help="evaluations de la configuration B")
    s.add_argument("--mode", choices=("quick", "deep"), default="deep")
    s.add_argument("--sortie", help="fichier JSON du resultat")
    return parser


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = dict(os.environ) if env is None else env
    try:
        if args.commande == "generer":
            return asyncio.run(cmd_generate(args, env))
        if args.commande == "evaluer":
            return asyncio.run(cmd_evaluate(args, env))
        if args.commande == "etalonner":
            return asyncio.run(cmd_calibrate(args, env))
        if args.commande == "auditer":
            return cmd_audit(args)
        if args.commande == "accord-juges":
            return cmd_judges(args)
        if args.commande == "ecart":
            return cmd_series(args)
        return cmd_compare(args)
    except ConfigError as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 2
