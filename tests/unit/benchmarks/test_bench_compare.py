"""Tests de benchmarks/compare.py sur des fichiers d'exemple."""

import json
import math
from pathlib import Path

from compare import compare, load_scores, main


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_compare_uses_only_common_tasks(tmp_path: Path) -> None:
    a = write_jsonl(
        tmp_path / "a.jsonl",
        [
            {"id": 1, "overall_score": 0.40},
            {"id": 2, "overall_score": 0.50},
            {"id": 3, "overall_score": 0.60},
            {"id": 4, "overall_score": 0.90},  # absent de B
        ],
    )
    b = write_jsonl(
        tmp_path / "b.jsonl",
        [
            {"id": 1, "overall_score": 0.50},
            {"id": 2, "overall_score": 0.70},
            {"id": 3, "overall_score": 0.60},
            {"id": 9, "overall_score": 0.10},  # absent de A
            {"id": 10, "overall_score": None},  # sans score : ignore
        ],
    )
    res = compare(load_scores(a), load_scores(b))
    assert res["paired"] == 3
    assert res["only_in_a"] == ["4"] and res["only_in_b"] == ["9"]
    # deltas : 0.10, 0.20, 0.00
    assert math.isclose(res["mean_delta"], 0.10)
    assert math.isclose(res["sd_delta"], 0.10)
    assert math.isclose(res["mde"], (1.959964 + 0.841621) * 0.10 / math.sqrt(3))


def test_compare_without_overlap_has_no_statistics() -> None:
    res = compare({"1": 0.5}, {"2": 0.5})
    assert res["paired"] == 0
    assert res["mean_delta"] is None and res["sd_delta"] is None and res["mde"] is None


def test_single_pair_has_no_sd() -> None:
    res = compare({"1": 0.5}, {"1": 0.7})
    assert res["paired"] == 1
    assert math.isclose(res["mean_delta"], 0.2)
    assert res["sd_delta"] is None and res["mde"] is None


def test_load_scores_json_formats(tmp_path: Path) -> None:
    listing = tmp_path / "l.json"
    listing.write_text(json.dumps([{"id": "a", "overall_score": 1}]), encoding="utf-8")
    mapping = tmp_path / "m.json"
    mapping.write_text(json.dumps({"a": 1, "b": "x"}), encoding="utf-8")
    assert load_scores(listing) == {"a": 1.0}
    assert load_scores(mapping) == {"a": 1.0}


def test_main_prints_paired_count(tmp_path: Path, capsys) -> None:
    a = write_jsonl(tmp_path / "a.jsonl", [{"id": 1, "s": 1.0}, {"id": 2, "s": 2.0}])
    b = write_jsonl(tmp_path / "b.jsonl", [{"id": 2, "s": 3.0}, {"id": 3, "s": 4.0}])
    assert main([str(a), str(b), "--score-key", "s"]) == 0
    out = capsys.readouterr().out
    assert "Taches appariees      : 1" in out
    assert "Delta moyen (B - A)   : 1.0000" in out
