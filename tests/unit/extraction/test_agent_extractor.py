"""Tests de l'extracteur agent : paliers internes et replis (lot V1-8).

Ni navigateur ni LLM reel : Playwright, le palier direct, le client LLM et
la classe Agent sont des doublures.
"""

import asyncio
from typing import Any

import pytest
from app.api.models import WebResult
from app.extractors import agent_extractor as module
from app.extractors.agent_extractor import AgentExtractor

URL = "https://example.com/article"


class FakeDirect:
    def __init__(self, content: str, success: bool = True):
        self.content = content
        self.success = success
        self.options: dict[str, Any] | None = None

    async def extract(self, url, prompt, llm_client=None, options=None) -> WebResult:
        self.options = options
        if not self.success:
            return WebResult.from_error(url=url, error_message="vide")
        return WebResult.from_success(
            url=url, content_type="webpage", title="Titre", content=self.content, metadata={}
        )


class FakeLLM:
    def __init__(self, response: str = "TITRE: T\nCONTENU: " + "r" * 150):
        self.response = response
        self.calls = 0

    async def generate(self, messages) -> str:
        self.calls += 1
        return self.response

    def get_langchain_wrapper(self) -> object:
        return object()


class HangingAgent:
    def __init__(self, **kwargs: Any):
        pass

    async def run(self, max_steps: int = 8) -> None:
        await asyncio.sleep(30)


def build(direct: FakeDirect) -> AgentExtractor:
    extractor = AgentExtractor()
    extractor.direct_extractor = direct  # type: ignore[assignment]
    return extractor


@pytest.fixture(autouse=True)
def _playwright_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_ensure() -> bool:
        return True

    monkeypatch.setattr(module, "ensure_playwright_installed", fake_ensure)


async def test_contenu_direct_substantiel_porte_le_palier_direct() -> None:
    result = await build(FakeDirect("x" * 800)).extract(URL, "p", FakeLLM(), {"timeout": 10})
    assert result.metadata["extraction_tier"] == "direct"


async def test_reecriture_llm_porte_le_palier_llm() -> None:
    result = await build(FakeDirect("x" * 300)).extract(URL, "p", FakeLLM(), {"timeout": 10})
    assert result.metadata["extraction_tier"] == "llm"
    assert result.content.startswith("r")


async def test_drapeau_single_llm_pass_desactive_rend_le_texte_tel_quel() -> None:
    llm = FakeLLM()
    options = {"timeout": 10, "single_llm_pass_enabled": False}
    result = await build(FakeDirect("x" * 300)).extract(URL, "p", llm, options)
    assert llm.calls == 0
    assert result.content == "x" * 300
    assert result.metadata["extraction_tier"] == "direct"


async def test_repli_vers_le_direct_est_signale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "Agent", HangingAgent)
    # contenu < 100 caracteres : pas de passe LLM, escalade vers l'agent
    result = await build(FakeDirect("court")).extract(URL, "p", FakeLLM(), {"timeout": 0.3})
    assert result.success
    assert result.content == "court"
    assert result.metadata["agent_fallback"] is True
    assert "timeout" in result.metadata["agent_fallback_reason"]
    assert result.metadata["extraction_tier"] == "direct"


async def test_le_palier_direct_recoit_le_temps_restant_et_non_le_timeout() -> None:
    direct = FakeDirect("x" * 800)
    await build(direct).extract(URL, "p", FakeLLM(), {"timeout": 10})
    assert direct.options["timeout"] <= 10
    assert direct.options["timeout"] > 9
