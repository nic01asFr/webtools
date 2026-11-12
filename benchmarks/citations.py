#!/usr/bin/env python3
"""Mesure de la fiabilite des citations de webtools (voir benchmarks/CITATIONS.md).

Usage :
  python benchmarks/citations.py generer   --corpus benchmarks/citecheck/corpus.jsonl --sortie runs/cit-ref
  python benchmarks/citations.py etalonner --cas benchmarks/citecheck/etalonnage.jsonl --juge <modele> --sortie runs/etal
  python benchmarks/citations.py evaluer   --sortie runs/cit-ref --juge <modele>
  python benchmarks/citations.py auditer   --evaluation runs/cit-ref/evaluation-<modele>
  python benchmarks/citations.py comparer  <evaluation A> <evaluation B>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from citecheck.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
