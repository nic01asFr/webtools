"""Taux, intervalles de confiance et origine des erreurs.

Les citations d'une meme reponse ne sont pas independantes (meme modele,
memes sources) : l'intervalle de confiance est obtenu par bootstrap sur les
taches (on retire des reponses entieres), pas sur les citations.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

JUDGED = ("soutenue", "partielle", "contredite", "non_soutenue", "non_etaye")
PROBLEMS = ("partielle", "contredite", "non_soutenue", "non_etaye")
BOOTSTRAP_ROUNDS = 2000
BOOTSTRAP_SEED = 20261112


def ratio_ci(
    records: list[dict[str, Any]],
    numerator: Callable[[dict[str, Any]], bool],
    denominator: Callable[[dict[str, Any]], bool],
    rounds: int = BOOTSTRAP_ROUNDS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Taux (somme des numerateurs / somme des denominateurs) et IC 95 %
    par bootstrap sur les taches."""
    by_task: dict[str, list[int]] = {}
    for r in records:
        if denominator(r):
            counts = by_task.setdefault(r["task_id"], [0, 0])
            counts[1] += 1
            if numerator(r):
                counts[0] += 1
    num = sum(c[0] for c in by_task.values())
    den = sum(c[1] for c in by_task.values())
    result: dict[str, Any] = {"n": num, "sur": den, "taux": round(num / den, 4) if den else None}
    tasks = list(by_task.values())
    if den and len(tasks) >= 2:
        rng = random.Random(seed)
        samples = []
        for _ in range(rounds):
            picked = [tasks[rng.randrange(len(tasks))] for _ in tasks]
            d = sum(c[1] for c in picked)
            if d:
                samples.append(sum(c[0] for c in picked) / d)
        samples.sort()
        result["ic95"] = [
            round(samples[int(0.025 * (len(samples) - 1))], 4),
            round(samples[int(0.975 * (len(samples) - 1))], 4),
        ]
    else:
        result["ic95"] = None
    return result


def origin(record: dict[str, Any]) -> str:
    """Origine d'une citation fautive : extraction ou redaction.

    - extraction : le texte que le pipeline avait pour cette URL soutient
      l'affirmation, la page reelle non. Le contenu attribue a la page ne
      vient pas d'elle (flux RSS, reecriture, autre page).
    - redaction : meme le texte du pipeline ne la soutient pas. Le modele a
      mal lu, extrapole ou cite le mauvais numero.
    - inconnue : texte du pipeline absent de la reponse.
    """
    pipeline = record.get("pipeline_status")
    if not pipeline:
        return "inconnue"
    if pipeline == "soutenue":
        return "extraction"
    if pipeline in ("inverifiable", "erreur_juge"):
        return "inconnue"
    return "redaction"


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Mesures d'un ensemble de citations (deja dedoublonnees)."""
    status = Counter(r["status"] for r in records)
    not_orphan = [r for r in records if r["status"] != "orpheline"]
    summary: dict[str, Any] = {
        "citations": len(records),
        "taches": len({r["task_id"] for r in records}),
        "statuts": dict(sorted(status.items())),
        "exactes": ratio_ci(records, lambda r: r["status"] == "soutenue", lambda r: r["status"] in JUDGED),
        "exactes_ou_partielles": ratio_ci(
            records, lambda r: r["status"] in ("soutenue", "partielle"), lambda r: r["status"] in JUDGED
        ),
        "contredites": ratio_ci(records, lambda r: r["status"] == "contredite", lambda r: r["status"] in JUDGED),
        "non_etayees_par_le_juge": ratio_ci(
            records, lambda r: r["status"] == "non_etaye", lambda r: r["status"] in JUDGED
        ),
        "inverifiables": ratio_ci(not_orphan, lambda r: r["status"] == "inverifiable", lambda r: True),
        "orphelines": ratio_ci(records, lambda r: r["status"] == "orpheline", lambda r: True),
        "soutenues_avec_chiffre_absent": sum(
            1 for r in records if r["status"] == "soutenue" and r.get("missing_numbers")
        ),
    }
    with_pipeline = [r for r in records if r.get("pipeline_status")]
    if with_pipeline:
        summary["fidelite_au_texte_du_pipeline"] = ratio_ci(
            with_pipeline,
            lambda r: r["pipeline_status"] == "soutenue",
            lambda r: r["pipeline_status"] in JUDGED,
        )
        summary["origine_des_fautes"] = dict(Counter(
            origin(r) for r in records if r["status"] in PROBLEMS
        ))
    return summary


def task_rates(runs: list[dict[str, list[int]]]) -> dict[str, dict[str, Any]]:
    """Par tache, comptes cumules sur les repetitions et taux de chaque run.

    runs : un dictionnaire par run, tache -> [numerateur, denominateur].
    """
    out: dict[str, dict[str, Any]] = {}
    for run in runs:
        for task, (num, den) in run.items():
            entry = out.setdefault(task, {"n": 0, "sur": 0, "par_run": []})
            entry["n"] += num
            entry["sur"] += den
            if den:
                entry["par_run"].append(num / den)
    for entry in out.values():
        entry["taux"] = entry["n"] / entry["sur"] if entry["sur"] else None
    return out


def paired_difference(
    a_runs: list[dict[str, list[int]]],
    b_runs: list[dict[str, list[int]]],
    rounds: int = BOOTSTRAP_ROUNDS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Ecart B - A apparie par tache, sur des series de repetitions.

    Chaque tache compte une fois, avec son taux cumule sur les repetitions :
    les sujets faciles ou difficiles s'annulent dans la difference. L'IC95
    est obtenu par bootstrap sur les taches. Le bruit d'une configuration est
    l'ecart moyen, par tache, entre ses repetitions (max - min) : une
    difference du meme ordre ne prouve rien.
    """
    a, b = task_rates(a_runs), task_rates(b_runs)
    common = sorted(t for t in a if t in b and a[t]["taux"] is not None and b[t]["taux"] is not None)
    diffs = [b[t]["taux"] - a[t]["taux"] for t in common]
    result: dict[str, Any] = {
        "taches": len(common),
        "par_tache": {t: {"a": round(a[t]["taux"], 4), "b": round(b[t]["taux"], 4)} for t in common},
        "ecart_moyen": round(sum(diffs) / len(diffs), 4) if diffs else None,
        "taches_en_hausse": sum(1 for d in diffs if d > 0),
        "taches_en_baisse": sum(1 for d in diffs if d < 0),
        "ic95": None,
    }
    if len(diffs) >= 2:
        rng = random.Random(seed)
        means = sorted(
            sum(diffs[rng.randrange(len(diffs))] for _ in diffs) / len(diffs) for _ in range(rounds)
        )
        result["ic95"] = [
            round(means[int(0.025 * (len(means) - 1))], 4),
            round(means[int(0.975 * (len(means) - 1))], 4),
        ]

    def noise(rates: dict[str, dict[str, Any]]) -> float | None:
        spreads = [max(e["par_run"]) - min(e["par_run"]) for e in rates.values() if len(e["par_run"]) >= 2]
        return round(sum(spreads) / len(spreads), 4) if spreads else None

    result["bruit_a"] = noise(a)
    result["bruit_b"] = noise(b)
    result["repetitions"] = [len(a_runs), len(b_runs)]
    return result


def answer_correct(answer: str | None, expected: Iterable[str]) -> bool:
    """Vrai si la reponse contient l'un des motifs attendus (expressions
    regulieres, insensibles a la casse)."""
    text = answer or ""
    return any(re.search(p, text, re.IGNORECASE) for p in expected)
