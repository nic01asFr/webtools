"""Lance l'API avec le fournisseur LLM 'stub' (voir scripts/stub_llm.py).

Usage (depuis la racine du depot, interpreteur de l'API) :
    python scripts/run_stub_api.py --port 18000 --searxng http://localhost:18081
La cle d'API de test est lue dans WEBTOOLS_API_KEY (defaut : e2e-test-key).
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--searxng", default="http://localhost:18081")
    args = parser.parse_args()

    os.environ.setdefault("WEBTOOLS_API_KEY", "e2e-test-key")
    os.environ["SEARXNG_BASE_URL"] = args.searxng
    os.environ["DEFAULT_LLM_PROVIDER"] = "stub"
    os.environ["DEFAULT_LLM_MODEL"] = "stub-llm"
    os.environ["DEFAULT_LLM_API_KEY"] = "stub"
    os.environ.pop("DEFAULT_LLM_BASE_URL", None)
    os.chdir(ROOT)

    from stub_llm import register_stub

    register_stub()

    import uvicorn
    from app.main import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
