"""Tests de benchmarks/generate.py : transport simule, aucun reseau."""

import argparse
import json
from pathlib import Path
from typing import Any

import generate
import httpx
import pytest
from generate import ConfigError, MismatchError, Settings

COMMIT = "a" * 40
HEALTH_OK = {
    "status": "healthy",
    "commit": COMMIT,
    "commit_dirty": False,
    "started_at": "2026-01-01T00:00:00Z",
    "engines": {"general": 3},
}


def good_payload(topic: str) -> dict:
    return {
        "success": True,
        "result": {
            "title": topic,
            "summary": "Resume.",
            "sections": [{"title": "S", "content": "Texte [1]."}],
            "bibliography": [{"title": "T", "url": "https://x.example/1"}],
        },
        "diagnostics": {"config": {"flag": False}},
    }


class Server:
    """Faux service : enregistre les requetes et repond par tache."""

    def __init__(self, health: dict | None = None, responses: dict | None = None) -> None:
        self.health = HEALTH_OK if health is None else health
        self.responses = responses or {}
        self.posted: list[str] = []
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json=self.health)
        body = json.loads(request.content)
        self.bodies.append(body)
        self.posted.append(body["topic"])
        handler = self.responses.get(body["topic"])
        if handler is None:
            return httpx.Response(200, json=good_payload(body["topic"]))
        return handler(body)


@pytest.fixture
def env(tmp_path: Path) -> Settings:
    home = tmp_path / "drb"
    (home / "data/prompt_data").mkdir(parents=True)
    rows = [{"id": i, "prompt": f"p{i}", "language": "en"} for i in (1, 2, 3)]
    (home / "data/prompt_data/query.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
    )
    return Settings(home, "http://svc", "k")


def make_args(**kw: Any) -> argparse.Namespace:
    args = generate.build_parser().parse_args([])
    args.model_name = "m"
    for k, v in kw.items():
        setattr(args, k, v)
    return args


async def run(env: Settings, server: Server, args: argparse.Namespace | None = None) -> dict:
    args = args or make_args()
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
        return await generate.run_batch(client, env, args, repo_commit=COMMIT, repo_dirty=False)


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def read_manifest(env: Settings) -> dict:
    return json.loads((env.default_out_dir.parent / "m_manifest.json").read_text(encoding="utf-8"))


def test_load_settings_lists_every_missing_variable() -> None:
    with pytest.raises(ConfigError) as exc:
        generate.load_settings({})
    msg = str(exc.value)
    assert "DRB_HOME" in msg and "WEBTOOLS_API" in msg and "WEBTOOLS_API_KEY" in msg


def test_load_settings_anonymous_skips_key(tmp_path: Path) -> None:
    s = generate.load_settings(
        {"DRB_HOME": str(tmp_path), "WEBTOOLS_API": "http://x/"}, anonymous=True
    )
    assert s.api == "http://x" and s.api_key == ""


def test_check_commit_rules() -> None:
    assert generate.check_commit(HEALTH_OK, COMMIT, False) == []
    assert generate.check_commit(HEALTH_OK, COMMIT[:7], False) == []
    bads = (
        {**HEALTH_OK, "commit": "b" * 40},
        {**HEALTH_OK, "commit": "unknown"},
        {**HEALTH_OK, "commit_dirty": True},
        {"status": "healthy"},
    )
    for bad in bads:
        with pytest.raises(MismatchError):
            generate.check_commit(bad, COMMIT, False)
        assert generate.check_commit(bad, COMMIT, True)


async def test_degraded_report_is_recorded(env: Settings) -> None:
    degraded = {
        "success": False,
        "degraded": True,
        "error": "1 section(s) non generee(s)",
        "failed_sections": ["B"],
        "unsourced_sections": ["C"],
        "result": good_payload("p2")["result"],
    }
    server = Server(responses={"p2": lambda body: httpx.Response(200, json=degraded)})
    counters = await run(env, server)
    out = env.default_out_dir
    assert sorted(r["id"] for r in read_jsonl(out / "m.jsonl")) == [1, 2, 3]
    diag = {d["id"]: d for d in read_jsonl(out.parent / "m_diagnostics.jsonl")}
    assert diag[2]["degraded"] is True and diag[2]["success"] is False
    assert diag[2]["failed_sections"] == ["B"] and diag[2]["unsourced_sections"] == ["C"]
    assert diag[1]["degraded"] is False
    assert counters["ok"] == 3 and counters["degraded"] == 1 and counters["failed"] == 0


async def test_response_without_report_is_failure(env: Settings) -> None:
    empty = {"success": False, "error": "rien", "result": {"title": "T"}}
    server = Server(
        responses={
            "p1": lambda body: httpx.Response(200, json=empty),
            "p3": lambda body: httpx.Response(500, json={}),
        }
    )
    counters = await run(env, server)
    out = env.default_out_dir
    assert [r["id"] for r in read_jsonl(out / "m.jsonl")] == [2]
    assert counters["failed"] == 2 and counters["ok"] == 1
    diags = read_jsonl(out.parent / "m_diagnostics.jsonl")
    errors = {d["id"]: d["error"] for d in diags if "error" in d}
    assert errors[1] == "rien" and "500" in errors[3]


async def test_request_has_no_max_results(env: Settings) -> None:
    server = Server()
    await run(env, server, make_args(limit=1))
    assert server.bodies == [{"topic": "p1"}]


async def test_resume_skips_done_tasks(env: Settings) -> None:
    out = env.default_out_dir
    out.mkdir(parents=True)
    (out / "m.jsonl").write_text(
        json.dumps({"id": 1, "prompt": "p1", "article": "x"}) + "\n", encoding="utf-8"
    )
    server = Server()
    await run(env, server)
    assert sorted(server.posted) == ["p2", "p3"]
    assert sorted(r["id"] for r in read_jsonl(out / "m.jsonl")) == [1, 2, 3]


async def test_resume_after_interruption_records_resumption(env: Settings) -> None:
    first = Server(responses={"p3": lambda body: httpx.Response(500, json={})})
    await run(env, first)
    server = Server()
    await run(env, server)
    assert server.posted == ["p3"]
    assert len(read_manifest(env)["resumptions"]) == 1


async def test_resume_with_other_commit_is_refused(env: Settings) -> None:
    await run(env, Server())
    other = {**HEALTH_OK, "commit": "b" * 40}
    with pytest.raises(MismatchError):
        await run(env, Server(health=other))
    await run(env, Server(health=other), make_args(allow_mismatch=True))


async def test_manifest_content(env: Settings) -> None:
    await run(env, Server(), make_args(llm_model="qwen", judge="official"))
    manifest = read_manifest(env)
    assert manifest["repo_commit"] == COMMIT and manifest["served_commit"] == COMMIT
    assert manifest["served_started_at"] == "2026-01-01T00:00:00Z"
    assert manifest["model"] == "qwen" and manifest["judge"] == "official"
    assert manifest["task_ids"] == [1, 2, 3]
    assert manifest["searxng_engines"] == {"general": 3}
    assert manifest["diagnostics_config"] == {"flag": False}


async def test_mismatch_refuses_before_any_generation(env: Settings) -> None:
    server = Server(health={"status": "healthy"})
    with pytest.raises(MismatchError):
        await run(env, server)
    assert server.posted == []
    assert not (env.default_out_dir.parent / "m_manifest.json").exists()


async def test_allow_mismatch_starts_and_traces_warnings(env: Settings) -> None:
    server = Server(health={"status": "healthy"})
    await run(env, server, make_args(allow_mismatch=True))
    assert read_manifest(env)["mismatch_warnings"]
    assert len(server.posted) == 3


async def test_max_sources_per_section_is_sent_and_recorded(env: Settings) -> None:
    server = Server()
    await run(env, server, make_args(limit=1, max_sources_per_section=3))
    assert server.bodies == [{"topic": "p1", "max_sources_per_section": 3}]
    assert read_manifest(env)["max_sources_per_section"] == 3


async def test_max_sources_per_section_absent_by_default(env: Settings) -> None:
    await run(env, Server(), make_args(limit=1))
    assert read_manifest(env)["max_sources_per_section"] is None
