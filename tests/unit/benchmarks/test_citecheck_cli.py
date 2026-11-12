"""Outil de mesure des citations : pages et commandes de bout en bout,
service, pages et juge simules par httpx.MockTransport."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
import pytest

from citecheck import cli
from citecheck.pages import PageStore, default_client, fetch_page

HTML = (
    "<html><head><script>var cache = 'secret';</script><style>p{}</style></head><body>"
    "<nav>Menu</nav><p>Depuis le 19 decembre 2018, l'usage en plein champ est interdit.</p>"
    "<table><tr><td>Imidaclopride</td><td>interdit 2018</td></tr></table>"
    + "<p>Paragraphe de remplissage pour depasser le seuil de texte minimal.</p>" * 5
    + "</body></html>"
)


def _page_transport(calls: list[str] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        path = request.url.path
        if path == "/interdit":
            return httpx.Response(403, text="Forbidden")
        if path == "/image":
            return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
        if path == "/vide":
            return httpx.Response(200, text="<html><body>x</body></html>", headers={"content-type": "text/html"})
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html; charset=utf-8"})

    return httpx.MockTransport(handler)


async def test_fetch_page_html_garde_le_texte_visible() -> None:
    async with default_client(_page_transport()) as client:
        page = await fetch_page(client, "https://a.example/page")
    assert page.ok
    assert "19 decembre 2018" in page.text and "Imidaclopride" in page.text
    assert "secret" not in page.text


@pytest.mark.parametrize(("path", "status"), [("/interdit", "http_error"), ("/image", "non_textuel"),
                                              ("/vide", "vide")])
async def test_fetch_page_inaccessible(path: str, status: str) -> None:
    async with default_client(_page_transport()) as client:
        page = await fetch_page(client, f"https://a.example{path}")
    assert page.status == status and not page.ok


async def test_page_store_cache_disque(tmp_path: Path) -> None:
    calls: list[str] = []
    async with default_client(_page_transport(calls)) as client:
        store = PageStore(tmp_path, client)
        first = await store.get("https://a.example/page")
        second = await store.get("https://a.example/page")
    async with default_client(_page_transport(calls)) as client:
        third = await PageStore(tmp_path, client).get("https://a.example/page")
    assert len(calls) == 1
    assert first.text == second.text == third.text


# --- bout en bout -------------------------------------------------------------------

QUICK_PAYLOAD = {
    "query": "q",
    "answer": "L'usage en plein champ est interdit depuis 2018 [1]. Il l'etait deja en 2013 [2]. Voir [3].",
    "sources": [
        {"url": "https://a.example/page", "title": "A", "content": "Depuis le 19 decembre 2018, l'usage en plein champ est interdit."},
        {"url": "https://b.example/interdit", "title": "B", "content": "rien"},
    ],
    "success": True,
}
DEEP_PAYLOAD = {
    "success": True,
    "result": {
        "title": "T", "summary": "",
        "sections": [{"title": "S", "content": "L'usage en plein champ est interdit depuis 2013 [1].",
                      "data": [{"source": "https://a.example/page", "content": "interdit depuis 2013 en plein champ"}]}],
        "bibliography": [{"id": 1, "url": "https://a.example/page", "title": "A"}],
    },
    "diagnostics": {"config": {"rss_tier_root_only": True}},
}


def _service_transport(commit: str, seen: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"commit": commit, "commit_dirty": False,
                                             "started_at": "t", "llm_model": "m"})
        body = json.loads(request.content)
        seen.append({"path": request.url.path, **body})
        if request.url.path.endswith("/quick"):
            return httpx.Response(200, json=QUICK_PAYLOAD)
        return httpx.Response(200, json=DEEP_PAYLOAD)

    return httpx.MockTransport(handler)


def _judge_transport(calls: list[str]) -> httpx.MockTransport:
    """Juge simule : contredite si l'affirmation dit 2013 et que le texte dit 2018."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        user = body["messages"][1]["content"]
        calls.append(user)
        claim = user.split("AFFIRMATION : ")[1].split("\n")[0]
        text = user.split("<<<\n")[1]
        if "2013" in claim and "2018" in text:
            out = {"extrait": "Depuis le 19 decembre 2018", "analyse": "autre date", "verdict": "contredite"}
        elif "2013" in claim and "2013" in text:
            out = {"extrait": "interdit depuis 2013 en plein champ", "analyse": "ok", "verdict": "soutenue"}
        else:
            out = {"extrait": "l'usage en plein champ est interdit", "analyse": "ok", "verdict": "soutenue"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(out)}}]})

    return httpx.MockTransport(handler)


async def test_generer_puis_evaluer(tmp_path: Path) -> None:
    from generate import repo_state

    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        json.dumps({"id": "q1", "mode": "quick", "query": "Quand ?", "attendu": [r"\b2018\b"]}) + "\n"
        + json.dumps({"id": "d1", "mode": "deep", "query": "Sujet approfondi"}) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "run"
    seen: list[dict] = []
    env = {"WEBTOOLS_API": "http://svc", "WEBTOOLS_API_KEY": "k", "CITECHECK_LLM_API_KEY": "j"}
    gen_args = argparse.Namespace(corpus=str(corpus), sortie=str(out), mode="tous", limit=None,
                                  concurrence=2, timeout=1200, allow_mismatch=False)
    assert await cli.cmd_generate(gen_args, env, transport=_service_transport(repo_state()[0], seen)) == 0
    quick_body = next(s for s in seen if s["path"].endswith("/quick"))
    deep_body = next(s for s in seen if s["path"].endswith("/deep"))
    assert quick_body["include_source_content"] is True
    assert deep_body["output_format"] == {"include_section_data": True} and deep_body["timeout"] == 1200
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["diagnostics_config"] == {"rss_tier_root_only": True}

    # Reprise : rien a regenerer.
    seen.clear()
    assert await cli.cmd_generate(gen_args, env, transport=_service_transport(repo_state()[0], seen)) == 0
    assert seen == []

    judge_calls: list[str] = []
    eval_args = argparse.Namespace(sortie=str(out), juge="juge-test", plongements="aucun",
                                   concurrence=2, sans_pipeline=False, raisonnement=False)
    assert await cli.cmd_evaluate(eval_args, env, llm_transport=_judge_transport(judge_calls),
                                  page_transport=_page_transport()) == 0
    summary = json.loads((out / "evaluation-juge-test" / "resume.json").read_text(encoding="utf-8"))
    quick = summary["par_mode"]["quick"]
    # [1] soutenue ; [2] page 403 : inverifiable ; [3] orpheline.
    assert quick["statuts"] == {"inverifiable": 1, "orpheline": 1, "soutenue": 1}
    assert quick["reponses_exactes"] == {"n": 1, "sur": 1, "taux": 1.0}
    deep = summary["par_mode"]["deep"]
    # 2013 contredit par la page, mais present dans le texte du pipeline :
    # la faute vient de l'extraction.
    assert deep["statuts"] == {"contredite": 1}
    assert deep["origine_des_fautes"] == {"extraction": 1}
    # Affirmations : soutenue, page inaccessible, numero sans source.
    assert quick["statuts_affirmations"] == {"inverifiable": 1, "orpheline": 1, "soutenue": 1}
    assert deep["statuts_affirmations"] == {"contredite": 1}
    report = (out / "evaluation-juge-test" / "RAPPORT.md").read_text(encoding="utf-8")
    assert "Citations fautives" in report and "contredite" in report

    # Reprise : aucune citation rejugee.
    before = len(judge_calls)
    assert await cli.cmd_evaluate(eval_args, env, llm_transport=_judge_transport(judge_calls),
                                  page_transport=_page_transport()) == 0
    assert len(judge_calls) == before


async def test_generer_refuse_un_commit_different(tmp_path: Path) -> None:
    from generate import MismatchError

    corpus = tmp_path / "c.jsonl"
    corpus.write_text(json.dumps({"id": "q1", "mode": "quick", "query": "Quand ?"}) + "\n", encoding="utf-8")
    args = argparse.Namespace(corpus=str(corpus), sortie=str(tmp_path / "r"), mode="tous", limit=None,
                              concurrence=1, timeout=60, allow_mismatch=False)
    with pytest.raises(MismatchError):
        await cli.cmd_generate(args, {"WEBTOOLS_API": "http://svc"},
                               transport=_service_transport("0" * 40, []))


def test_calibration_metrics() -> None:
    rows = [
        {"attendu": "soutenue", "status": "soutenue", "categorie": "paraphrase"},
        {"attendu": "soutenue", "status": "partielle", "categorie": "traduction"},
        {"attendu": "contredite", "status": "contredite", "categorie": "date"},
        {"attendu": "non_soutenue", "status": "soutenue", "categorie": "absente"},
        {"attendu": "contredite", "status": "non_soutenue", "categorie": "chiffre"},
    ]
    m = cli.calibration_metrics(rows)
    assert m["fausses_acceptations"] == {"n": 1, "sur": 3, "taux": 0.3333}
    assert m["faux_rejets"]["n"] == 1
    assert m["accord_binaire"] == 0.6
    assert m["verdict_exact"] == 0.4
    assert m["juge_accepte"] is False


async def test_etalonner(tmp_path: Path) -> None:
    cases = tmp_path / "cas.jsonl"
    page = "Depuis le 19 decembre 2018, l'usage en plein champ est interdit."
    cases.write_text("\n".join(json.dumps(c) for c in [
        {"id": "c1", "texte": page, "affirmation": "Interdit en plein champ depuis 2018.", "attendu": "soutenue"},
        {"id": "c2", "texte": page, "affirmation": "Interdit en plein champ depuis 2013.", "attendu": "contredite"},
    ]) + "\n", encoding="utf-8")
    args = argparse.Namespace(cas=str(cases), juge="j", sortie=str(tmp_path), plongements="aucun", concurrence=2,
                              raisonnement=False)
    code = await cli.cmd_calibrate(args, {"CITECHECK_LLM_API_KEY": "k"}, llm_transport=_judge_transport([]))
    metrics = json.loads((tmp_path / "etalonnage-j.json").read_text(encoding="utf-8"))
    assert metrics["accord_binaire"] == 1.0 and metrics["fausses_acceptations"]["n"] == 0
    # Seuils atteints, mais deux cas ne suffisent pas a l'echelle reelle :
    # le code de retour suit les seuils.
    assert code == 0 and metrics["juge_accepte"] is True


def test_auditer_echantillon_puis_accord(tmp_path: Path) -> None:
    rows = [{"key": f"k{i}", "task_id": "t", "ref": 1, "url": "u", "claim": "c", "context": "",
             "status": "soutenue" if i % 2 else "contredite", "quote": "", "analysis": "",
             "missing_numbers": []} for i in range(10)]
    (tmp_path / "citations.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert cli.cmd_audit(argparse.Namespace(evaluation=str(tmp_path), par_statut=2, graine=1, accord=False)) == 0
    sample = cli.read_jsonl(tmp_path / "audit.jsonl")
    assert len(sample) == 4
    for row in sample:
        row["verdict_auditeur"] = "soutenue"
    (tmp_path / "audit.jsonl").write_text("\n".join(json.dumps(r) for r in sample) + "\n", encoding="utf-8")
    assert cli.cmd_audit(argparse.Namespace(evaluation=str(tmp_path), par_statut=2, graine=1, accord=True)) == 0
    result = json.loads((tmp_path / "audit-accord.json").read_text(encoding="utf-8"))
    assert result["accord_binaire"] == 0.5 and len(result["desaccords"]) == 2


# --- affirmations citant plusieurs sources ----------------------------------------


class _Store:
    def __init__(self, texts: dict[str, str]) -> None:
        self.texts = texts

    async def get(self, url: str):
        from citecheck.pages import Page

        return Page(url=url, status="ok", text=self.texts[url])


class _GroupJudge:
    def __init__(self, status: str) -> None:
        self.status = status
        self.calls: list[list[str]] = []

    async def judge_group(self, claim, context, sources):
        from citecheck.juge import Judgement

        self.calls.append([url for url, _ in sources])
        return Judgement(status=self.status, quote="q", analysis="a")


def _member(url: str, status: str, page_status: str = "ok") -> dict:
    return {"key": url, "task_id": "t", "mode": "quick", "claim": "c", "context": "", "ref": 1,
            "url": url, "status": status, "page_status": page_status}


async def test_affirmation_soutenue_par_la_reunion_de_ses_sources() -> None:
    judge = _GroupJudge("soutenue")
    group = {"key": "g", "task_id": "t", "mode": "quick", "claim": "5 700 ans [1], 5 730 ans [2]",
             "context": "", "members": [_member("u1", "partielle"), _member("u2", "partielle")]}
    result = await cli.evaluate_group(group, _Store({"u1": "a", "u2": "b"}), judge)
    assert (result["status"], result["via"]) == ("soutenue", "reunion")
    assert judge.calls == [["u1", "u2"]]


async def test_affirmation_soutenue_par_une_source_sans_appel_au_juge() -> None:
    judge = _GroupJudge("non_soutenue")
    group = {"key": "g", "task_id": "t", "mode": "quick", "claim": "c", "context": "",
             "members": [_member("u1", "partielle"), _member("u2", "soutenue")]}
    result = await cli.evaluate_group(group, _Store({}), judge)
    assert (result["status"], result["via"]) == ("soutenue", "une_source") and judge.calls == []


async def test_affirmation_non_soutenue_avec_page_inaccessible_est_inverifiable() -> None:
    judge = _GroupJudge("partielle")
    group = {"key": "g", "task_id": "t", "mode": "quick", "claim": "c", "context": "",
             "members": [_member("u1", "partielle"), _member("u2", "partielle"),
                         _member("u3", "inverifiable", page_status="http_error")]}
    result = await cli.evaluate_group(group, _Store({"u1": "a", "u2": "b"}), judge)
    assert result["status"] == "inverifiable"


async def test_affirmation_contredite_par_ses_sources() -> None:
    judge = _GroupJudge("contredite")
    group = {"key": "g", "task_id": "t", "mode": "quick", "claim": "c", "context": "",
             "members": [_member("u1", "contredite"), _member("u2", "non_soutenue")]}
    result = await cli.evaluate_group(group, _Store({"u1": "a", "u2": "b"}), judge)
    assert (result["status"], result["via"]) == ("contredite", "reunion")
