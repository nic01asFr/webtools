"""Verification de bout en bout reelle (SearXNG et pages publiques, LLM substitut).

Exige une API deja lancee par scripts/run_stub_api.py : WEBTOOLS_E2E_API
(URL) et, si besoin, WEBTOOLS_E2E_KEY. Marque `live` : exclu par defaut.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import smoke_deep

API = os.environ.get("WEBTOOLS_E2E_API", "")
KEY = os.environ.get("WEBTOOLS_E2E_KEY", "e2e-test-key")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not API, reason="WEBTOOLS_E2E_API non defini"),
]


def test_smoke_deep_exit_code_zero(capsys: pytest.CaptureFixture[str]) -> None:
    code = smoke_deep.run(API, KEY, smoke_deep.TOPIC, 300)
    out = capsys.readouterr().out
    assert code == 0, out
