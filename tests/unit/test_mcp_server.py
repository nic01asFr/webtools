"""Tests du serveur MCP client HTTP (API simulee par httpx.MockTransport).

Ces tests tournent avec l'interpreteur MCP : ils n'importent rien de `app/`.
"""

import asyncio
import json
import os
import pathlib
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
    """Installe une API simulee ; rend la liste des requetes recues."""
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


DEEP_PAYLOAD = {
    "success": True,
    "topic": "Sujet test",
    "processing_time": 12.5,
    "execution": {"steps": 3, "trace": {"nested": 1}},
    "sources": [{"url": "https://a.test"}],
    "result": {
        "summary": "Resume.",
        "sections": [
            {
                "title": "Section A",
                "content": "Texte A.",
                "data": [{"passage": "TEXTE INTEGRAL SECRET"}],
                "sources": ["https://a.test"],
            }
        ],
        "bibliography": [
            {"title": "Source A", "url": "https://a.test", "type": "web", "date": "2024"}
        ],
    },
    "degraded": True,
    "failed_sections": ["Section B"],
}


# --- Independance vis-a-vis de app/ ----------------------------------------


def test_aucun_import_de_app():
    assert not any(name == "app" or name.startswith("app.") for name in sys.modules)
    with open(mcp_server.__file__, encoding="utf-8") as handle:
        source = handle.read()
    assert "from app" not in source
    assert "import app" not in source


# --- Mapping des six outils -------------------------------------------------


async def test_extract_mapping_et_cle_api(api):
    api["handler"] = lambda r: httpx.Response(
        200, json={"success": True, "title": "T", "content": "C", "metadata": {"k": 1}}
    )
    out = await _fn(mcp_server.webtools_extract)(url="https://exemple.test/page")
    request = api["requests"][0]
    assert request.method == "POST"
    assert str(request.url) == "http://api.test/api/v1/extract"
    assert request.headers["X-API-Key"] == "cle-secrete"
    assert json.loads(request.content) == {"url": "https://exemple.test/page"}
    assert out["success"] is True and out["content"] == "C" and out["metadata"] == {"k": 1}


async def test_search_mapping(api):
    api["handler"] = lambda r: httpx.Response(
        200,
        json={"query": "q", "results": [{"title": "t", "url": "u", "snippet": "s"}], "total": 1},
    )
    out = await _fn(mcp_server.webtools_search)(query="q", max_results=7)
    request = api["requests"][0]
    assert request.url.path == "/api/v1/search"
    assert json.loads(request.content) == {"query": "q", "max_results": 7}
    assert out["total"] == 1 and out["results"][0]["url"] == "u"


async def test_vision_mapping(api):
    api["handler"] = lambda r: httpx.Response(200, json={"success": True, "analysis": "Un chat"})
    out = await _fn(mcp_server.webtools_vision)(image_url="https://i.test/a.png", prompt="Decris")
    request = api["requests"][0]
    assert request.url.path == "/api/v1/vision"
    assert json.loads(request.content) == {"image_url": "https://i.test/a.png", "prompt": "Decris"}
    assert out == {"success": True, "analysis": "Un chat", "error": None}


async def test_research_quick_mapping(api):
    api["handler"] = lambda r: httpx.Response(
        200,
        json={
            "success": True,
            "answer": "Reponse [1]",
            "confidence": "high",
            "sources": [{"title": "S", "url": "https://s.test", "relevance": 0.9}],
        },
    )
    out = await _fn(mcp_server.webtools_research_quick)(query="Une question")
    request = api["requests"][0]
    assert request.url.path == "/api/v1/research/quick"
    assert json.loads(request.content) == {"query": "Une question"}
    assert out["sources"] == [{"title": "S", "url": "https://s.test"}]
    assert out["confidence"] == "high"


async def test_research_deep_mapping_sans_data(api):
    api["handler"] = lambda r: httpx.Response(200, json=DEEP_PAYLOAD)
    out = await _fn(mcp_server.webtools_research_deep)(topic="Sujet test", ctx=None)
    request = api["requests"][0]
    assert request.url.path == "/api/v1/research/deep"
    assert json.loads(request.content) == {"topic": "Sujet test"}
    assert out["success"] is True
    assert "Texte A." in out["report_markdown"]
    assert "TEXTE INTEGRAL SECRET" not in json.dumps(out)
    assert out["diagnostics"]["degraded"] is True
    assert out["diagnostics"]["failed_sections"] == ["Section B"]


def test_max_sources_retire_des_outils():
    for name in (
        "webtools_research_quick",
        "webtools_research_deep",
        "webtools_research_deep_start",
    ):
        assert "max_sources" not in _fn(getattr(mcp_server, name)).__code__.co_varnames


async def test_cle_absente_pas_d_entete(api, monkeypatch):
    monkeypatch.delenv("WEBTOOLS_API_KEY")
    await _fn(mcp_server.webtools_search)(query="q")
    assert "X-API-Key" not in api["requests"][0].headers


# --- Echecs HTTP -------------------------------------------------------------


async def test_http_non_200(api):
    api["handler"] = lambda r: httpx.Response(401, json={"detail": "Cle invalide"})
    out = await _fn(mcp_server.webtools_vision)(image_url="https://i.test/a.png", prompt="Decris")
    assert out["success"] is False
    assert "401" in out["error"] and "Cle invalide" in out["error"]


async def test_json_invalide(api):
    api["handler"] = lambda r: httpx.Response(200, content=b"<html>pas du json</html>")
    out = await _fn(mcp_server.webtools_research_deep)(topic="Sujet test", ctx=None)
    assert out["success"] is False and "non JSON" in out["error"]


async def test_api_injoignable(api):
    def boom(request):
        raise httpx.ConnectError("refuse")

    api["handler"] = boom
    out = await _fn(mcp_server.webtools_extract)(url="https://exemple.test/page")
    assert out["success"] is False and "injoignable" in out["error"]


async def test_success_false_deep(api):
    api["handler"] = lambda r: httpx.Response(
        200, json={"success": False, "error": "orchestrateur HS", "execution": {"steps": 1}}
    )
    out = await _fn(mcp_server.webtools_research_deep)(topic="Sujet test", ctx=None)
    assert out["success"] is False and out["error"] == "orchestrateur HS"
    assert out["diagnostics"]["execution"] == {"steps": 1}


# --- Conversion markdown (fonction pure) -------------------------------------


def test_format_deep_report_complet():
    md = mcp_server.format_deep_report(DEEP_PAYLOAD)
    assert md.startswith("# Sujet test")
    assert "## Synthese\n\nResume." in md
    assert "## Section A\n\nTexte A." in md
    assert "1. Source A - https://a.test (web, 2024)" in md
    assert "TEXTE INTEGRAL SECRET" not in md


def test_format_deep_report_tolerant():
    assert mcp_server.format_deep_report({}) == ""
    assert mcp_server.format_deep_report({"result": None, "topic": "T"}) == "# T"
    md = mcp_server.format_deep_report(
        {
            "result": {
                "title": "Titre API",
                "sections": ["parasite", {"content": "Sans titre"}],
                "bibliography": [None, {"url": "https://x.test"}],
                "champ_futur": {"a": 1},
            },
            "champ_futur": 1,
        }
    )
    assert "# Titre API" in md and "## Section\n\nSans titre" in md
    assert "1. https://x.test" in md or "2. https://x.test" in md


def test_diagnostics_api_prioritaire():
    diag = mcp_server.build_deep_diagnostics(
        {"diagnostics": {"degraded": False, "note": "x"}, "degraded": True}
    )
    assert diag["degraded"] is False and diag["note"] == "x"


# --- Taches de fond -----------------------------------------------------------


async def test_plafond_de_taches_simultanees(api, monkeypatch):
    monkeypatch.setenv("MCP_MAX_CONCURRENT_DEEP", "2")
    gate = asyncio.Event()

    async def slow_post(path, payload, timeout):
        await gate.wait()
        return DEEP_PAYLOAD

    monkeypatch.setattr(mcp_server, "_api_post", slow_post)
    start = _fn(mcp_server.webtools_research_deep_start)
    first = await start(topic="Premier sujet")
    second = await start(topic="Second sujet")
    third = await start(topic="Troisieme sujet")
    assert first["status"] == "started" and second["status"] == "started"
    assert third["status"] == "busy" and third["running"] == 2 and third["max_concurrent"] == 2

    gate.set()
    await asyncio.gather(*list(mcp_server._BACKGROUND))
    status = _fn(mcp_server.webtools_research_deep_status)
    done = await status(task_id=first["task_id"])
    assert done["status"] == "completed"
    assert "Texte A." in done["result"]["report_markdown"]
    # le creneau est libere
    assert (await start(topic="Quatrieme sujet"))["status"] == "started"
    gate.set()
    await asyncio.gather(*list(mcp_server._BACKGROUND))


async def test_tache_en_echec(api):
    api["handler"] = lambda r: httpx.Response(500, json={"detail": "panne"})
    started = await _fn(mcp_server.webtools_research_deep_start)(topic="Sujet test")
    await asyncio.gather(*list(mcp_server._BACKGROUND))
    out = await _fn(mcp_server.webtools_research_deep_status)(task_id=started["task_id"])
    assert out["status"] == "failed" and "500" in out["error"]


async def test_reprise_apres_redemarrage(api, monkeypatch):
    gate = asyncio.Event()

    async def blocked_post(path, payload, timeout):
        await gate.wait()
        return DEEP_PAYLOAD

    monkeypatch.setattr(mcp_server, "_api_post", blocked_post)
    running = await _fn(mcp_server.webtools_research_deep_start)(topic="Sujet en cours")
    # Une seconde tache terminee et ecrite sur disque.
    mcp_server._TASKS["abcdef012345"] = {
        "status": "completed",
        "result": {"success": True},
        "error": None,
        "started_at": mcp_server.time.time(),
    }
    mcp_server._persist_task("abcdef012345")
    state_dir = os.environ["WEBTOOLS_STATE_DIR"]
    assert os.path.exists(os.path.join(state_dir, f"{running['task_id']}.json"))

    # Simule la relance : memoire vide, rechargement depuis le disque.
    for handle in list(mcp_server._BACKGROUND):
        handle.cancel()
    await asyncio.gather(*list(mcp_server._BACKGROUND), return_exceptions=True)
    mcp_server._TASKS.clear()
    mcp_server.load_tasks()

    status = _fn(mcp_server.webtools_research_deep_status)
    after = await status(task_id=running["task_id"])
    assert after["status"] == "interrupted"
    assert (await status(task_id="abcdef012345"))["status"] == "completed"
    # une tache interrompue ne compte plus dans le plafond
    assert mcp_server._running_count() == 0
    # l'etat interrompu est lui-meme persiste
    saved = pathlib.Path(state_dir, f"{running['task_id']}.json").read_text(encoding="utf-8")
    assert json.loads(saved)["status"] == "interrupted"


def test_chargement_ignore_fichiers_illisibles(api):
    state_dir = os.environ["WEBTOOLS_STATE_DIR"]
    os.makedirs(state_dir)
    with open(os.path.join(state_dir, "abcdef012345.json"), "w", encoding="utf-8") as handle:
        handle.write("{pas du json")
    with open(os.path.join(state_dir, "autre.json"), "w", encoding="utf-8") as handle:
        handle.write("{}")
    mcp_server.load_tasks()
    assert mcp_server._TASKS == {}


async def test_status_inconnu(api):
    out = await _fn(mcp_server.webtools_research_deep_status)(task_id="inconnu")
    assert out["status"] == "not_found"


def test_ecriture_atomique_sans_residu(api):
    mcp_server._TASKS["abcdef012345"] = {"status": "completed", "started_at": 0}
    mcp_server._persist_task("abcdef012345")
    assert os.listdir(os.environ["WEBTOOLS_STATE_DIR"]) == ["abcdef012345.json"]
