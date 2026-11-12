"""Rend les scripts de benchmarks/ importables sans en faire un paquet."""

import sys
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parents[3] / "benchmarks"
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))
