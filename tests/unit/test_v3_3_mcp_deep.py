"""Lot V3-3 : serveur MCP et nouvelle forme de la reponse /research/deep.

Tourne avec l'interpreteur MCP (aucun import de app/).
"""

import asyncio
import json
import os
import sys

import pytest

pytest.importorskip("fastmcp")

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("MCP_ACCESS_TOKEN", "test-access-token")

import mcp_server


def _fn(tool):
    return getattr(tool, "fn", tool)


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBTOOLS_API", "http://api.test")
    monkeypatch.setenv("WEBTOOLS_API_KEY", "cle-secrete")
    monkeypatch.setenv("WEBTOOLS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("MCP_MAX_CONCURRENT_DEEP", raising=False)
    mcp_server._TASKS.clear()
    state = {"requests": [], "handler": lambda request: httpx.Response(200, json={})}

    def dispatch(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        return state["handler"](request)

    monkeypatch.setattr(mcp_server, "_TRANSPORT", httpx.MockTransport(dispatch))
    yield state
    mcp_server._TASKS.clear()


# Forme de la reponse de l'API apres le lot V3-3.
API_PAYLOAD = {
    "success": True,
    "degraded": False,
    "timed_out": False,
    "topic": "Sujet test",
    "processing_time": 3.2,
    "error": None,
    "result": {
        "title": "Analyse: Sujet Test",
        "summary": "Resume.",
        "sections": [{"title": "A", "content": "Texte [2].", "data": [{"content": "SECRET"}]}],
        "bibliography": [
            {"id": 2, "title": "Source B", "url": "https://b.test"},
            {"id": 5, "title": "Source E", "url": "https://e.test"},
        ],
        "metadata": {"sections_count": 1},
    },
    "failed_sections": [],
    "unsourced_sections": [],
    "missing_sections": [],
    "execution": {"workflow": [], "summary": {"total_steps": 2, "required_sources_status": "none_required"}},
    "sources": {
        "websites": [{"url": "https://b.test"}, {"url": "https://e.test"}, {"url": "https://z.test"}],
        "apis": [{"url": "https://api.test"}],
    },
    "diagnostics": {
        "config": {"max_sources_per_section": 4},
        "events": {"counts": {"x": 1}, "events": []},
        "invariants": {"ok": True, "violations": []},
    },
}


def test_sources_count_compte_les_sources_et_non_les_categories():
    diag = mcp_server.build_deep_diagnostics(API_PAYLOAD)
    assert diag["sources_count"] == 4  # 3 sites + 1 API, pas 2 categories


def test_diagnostics_garde_config_events_invariants():
    diag = mcp_server.build_deep_diagnostics(API_PAYLOAD)
    assert diag["config"] == {"max_sources_per_section": 4}
    assert diag["events"]["counts"] == {"x": 1}
    assert diag["invariants"]["ok"] is True
    assert "error" not in diag  # error null n'est pas recopie


def test_diagnostics_d_un_echec_garde_les_champs_utiles():
    failure = dict(API_PAYLOAD, success=False, error="panne", timed_out=True, result=None)
    diag = mcp_server.build_deep_diagnostics(failure)
    assert diag["error"] == "panne" and diag["timed_out"] is True


def test_bibliographie_numerotee_par_identifiant():
    md = mcp_server.format_deep_report(API_PAYLOAD)
    assert "2. Source B - https://b.test" in md
    assert "5. Source E - https://e.test" in md
    assert "SECRET" not in md
    assert md.startswith("# Analyse: Sujet Test")


async def test_parametre_synchrone_transmis(api):
    api["handler"] = lambda r: httpx.Response(200, json=API_PAYLOAD)
    tool = _fn(mcp_server.webtools_research_deep)

    await tool(topic="Sujet test", max_sources_per_section=3, ctx=None)
    assert json.loads(api["requests"][0].content) == {
        "topic": "Sujet test",
        "max_sources_per_section": 3,
    }

    await tool(topic="Sujet test", ctx=None)
    assert json.loads(api["requests"][1].content) == {"topic": "Sujet test"}


async def test_parametre_en_tache_de_fond_transmis(api):
    api["handler"] = lambda r: httpx.Response(200, json=API_PAYLOAD)
    started = await _fn(mcp_server.webtools_research_deep_start)(
        topic="Sujet test", max_sources_per_section=6
    )
    await asyncio.gather(*list(mcp_server._BACKGROUND))
    assert json.loads(api["requests"][0].content) == {
        "topic": "Sujet test",
        "max_sources_per_section": 6,
    }
    out = await _fn(mcp_server.webtools_research_deep_status)(task_id=started["task_id"])
    assert out["status"] == "completed"
    assert out["result"]["diagnostics"]["config"] == {"max_sources_per_section": 4}


def test_bornes_du_parametre():
    import typing

    from pydantic import TypeAdapter, ValidationError

    for tool in (mcp_server.webtools_research_deep, mcp_server.webtools_research_deep_start):
        hint = typing.get_type_hints(_fn(tool), include_extras=True)["max_sources_per_section"]
        adapter = TypeAdapter(hint)
        assert adapter.validate_python(None) is None
        assert adapter.validate_python(20) == 20
        for bad in (0, 21):
            with pytest.raises(ValidationError):
                adapter.validate_python(bad)


# --- R2 : un rapport degrade rendu avec success=false n'est pas jete ----------


def test_deep_result_echec_garde_le_rapport_partiel():
    payload = {
        "success": False,
        "topic": "Sujet",
        "error": "delai depasse",
        "degraded": True,
        "timed_out": True,
        "result": {"title": "T", "sections": [{"title": "A", "content": "Texte A [1]."}]},
    }
    out = mcp_server.deep_result(payload)
    assert out["success"] is False
    assert "Texte A" in out["report_markdown"]
    assert out["degraded"] is True and out["timed_out"] is True
    assert "data" not in out["report_markdown"]


def test_deep_result_echec_sans_rapport_reste_minimal():
    out = mcp_server.deep_result({"success": False, "error": "boum", "result": None})
    assert set(out) == {"success", "error", "diagnostics"}
