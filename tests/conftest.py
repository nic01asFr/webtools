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


@pytest.fixture(autouse=True)
def _memoire_des_refus_videe():
    """Memoire des domaines qui refusent l'extraction : partagee par le
    processus, videe entre deux tests pour qu'aucun n'herite d'un autre.
    Le module n'est pas importe ici : les tests du serveur MCP verifient
    qu'aucun module de app/ n'est charge."""
    import sys

    def clear() -> None:
        module = sys.modules.get("app.utils.block_page")
        if module is not None:
            module.domain_refusals.clear()

    clear()
    yield
    clear()
