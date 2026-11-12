#!/usr/bin/env python3
"""
Compare deux runs DeepResearch Bench sur les SEULES taches presentes dans les
deux, avec un score par tache.

Affiche : nombre de taches appariees, delta moyen (B - A), ecart-type des
deltas, effet minimal detectable.

Effet minimal detectable : delta le plus petit que n paires distinguent du
bruit, pour un test apparie bilateral (alpha = 0,05, puissance 0,80) :
    MDE = (z_{1-alpha/2} + z_{puissance}) * sd / sqrt(n)  ~  2,80 * sd / sqrt(n)
L'ecart-type des deltas est estime sur les paires ; entre deux runs
identiques (M0), il donne le plancher de bruit.

Entree : fichier .jsonl (une ligne JSON par tache) ou .json (liste, ou objet
{id: score}). Les cles par defaut sont `id` et `overall_score` ; adapter avec
--id-key / --score-key selon le fichier de resultats RACE utilise.

Usage :
  python compare.py run_a/race/raw_results.jsonl run_b/race/raw_results.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

Z_ALPHA_2 = 1.959964
Z_POWER_80 = 0.841621


def load_scores(
    path: Path, id_key: str = "id", score_key: str = "overall_score"
) -> dict[str, float]:
    """Retourne {id: score}. Les lignes sans id ou sans score numerique sont ignorees."""
    text = Path(path).read_text(encoding="utf-8")
    rows: list[Any]
    stripped = text.strip()
    if not stripped:
        return {}
    if stripped[0] in "[{" and not _is_jsonl(stripped):
        data = json.loads(stripped)
        if isinstance(data, dict):
            return {str(k): float(v) for k, v in data.items() if _is_number(v)}
        rows = data
    else:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    scores: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict) or id_key not in row:
            continue
        value = row.get(score_key)
        if _is_number(value):
            scores[str(row[id_key])] = float(value)
    return scores


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_jsonl(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return len(lines) > 1 and all(ln.lstrip().startswith("{") for ln in lines)


def compare(a: dict[str, float], b: dict[str, float]) -> dict[str, Any]:
    paired = sorted(set(a) & set(b))
    deltas = [b[i] - a[i] for i in paired]
    n = len(deltas)
    mean = statistics.fmean(deltas) if n else None
    sd = statistics.stdev(deltas) if n >= 2 else None
    mde = (Z_ALPHA_2 + Z_POWER_80) * sd / math.sqrt(n) if sd is not None else None
    return {
        "paired": n,
        "only_in_a": sorted(set(a) - set(b)),
        "only_in_b": sorted(set(b) - set(a)),
        "mean_delta": mean,
        "sd_delta": sd,
        "mde": mde,
    }


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.4f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("run_a")
    ap.add_argument("run_b")
    ap.add_argument("--id-key", default="id")
    ap.add_argument("--score-key", default="overall_score")
    args = ap.parse_args(argv)

    a = load_scores(Path(args.run_a), args.id_key, args.score_key)
    b = load_scores(Path(args.run_b), args.id_key, args.score_key)
    res = compare(a, b)

    print(f"A : {args.run_a} ({len(a)} taches)")
    print(f"B : {args.run_b} ({len(b)} taches)")
    print(f"Taches appariees      : {res['paired']}")
    print(f"Seulement dans A      : {len(res['only_in_a'])}")
    print(f"Seulement dans B      : {len(res['only_in_b'])}")
    print(f"Delta moyen (B - A)   : {_fmt(res['mean_delta'])}")
    print(f"Ecart-type des deltas : {_fmt(res['sd_delta'])}")
    print(f"Effet minimal detectable (alpha 0,05, puissance 0,80) : {_fmt(res['mde'])}")
    if res["paired"] < 20:
        print("ATTENTION : moins de 20 paires, le delta ne mesure rien de fiable.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
