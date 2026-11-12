"""Configuration pytest commune.

Les tests marques `live` exigent le reseau ou un LLM reel : ils sont ignores
sauf si WEBTOOLS_LIVE=1. `make check` les exclut en plus par `-m "not live"`.
"""

import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("WEBTOOLS_LIVE") == "1":
        return
    skip_live = pytest.mark.skip(reason="test live : definir WEBTOOLS_LIVE=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
