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
        if len(body["messages"]) == 1:
            # Controle prealable du juge (ping) : non compte comme jugement.
            return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
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
                                   concurrence=2, sans_pipeline=False, raisonnement=False,
                                   sans_navigateur=True)
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


def test_accord_des_juges() -> None:
    def rows(statuses: list[str]) -> list[dict]:
        return [{"key": f"k{i}", "task_id": "t", "claim": "c", "status": s} for i, s in enumerate(statuses)]

    a = rows(["soutenue", "soutenue", "contredite", "partielle", "inverifiable"])
    b = rows(["soutenue", "partielle", "contredite", "non_soutenue", "soutenue"])
    result = cli.judges_agreement(a, b)
    # k4 exclu (inverifiable pour le juge a) ; k1 en desaccord.
    assert result["elements"] == 4
    assert result["accord_binaire"] == 0.75
    assert [d["key"] for d in result["desaccords"]] == ["k1"]
    assert result["verdict_identique"] == 0.5
    assert cli.judges_agreement(a, rows(["soutenue", "soutenue", "contredite", "partielle"]))["kappa"] == 1.0


async def test_repli_navigateur_pour_page_vide_ou_bloquee(tmp_path: Path) -> None:
    from citecheck.pages import Page

    opened: list[str] = []

    async def browser(url: str) -> Page:
        opened.append(url)
        if url.endswith("/vide"):
            return Page(url=url, status="ok", text="texte rendu par JavaScript " * 20, method="navigateur")
        return Page(url=url, status="http_error", http_status=403, error="HTTP 403", method="navigateur")

    async with default_client(_page_transport()) as client:
        store = PageStore(tmp_path, client, browser=browser)
        rendered = await store.get("https://a.example/vide")
        blocked = await store.get("https://a.example/interdit")
        direct = await store.get("https://a.example/page")
        image = await store.get("https://a.example/image")
        # Deuxieme lecture : servie par le cache, sans rouvrir le navigateur.
        await store.get("https://a.example/interdit")
    assert rendered.ok and rendered.method == "navigateur"
    assert not blocked.ok and blocked.method == "http+navigateur"
    assert direct.method == "http" and not image.ok
    assert opened == ["https://a.example/vide", "https://a.example/interdit"]


async def test_page_en_cache_sans_navigateur_reessayee_avec(tmp_path: Path) -> None:
    from citecheck.pages import Page

    async with default_client(_page_transport()) as client:
        first = await PageStore(tmp_path, client).get("https://a.example/vide")

        async def browser(url: str) -> Page:
            return Page(url=url, status="ok", text="rendu " * 60, method="navigateur")

        second = await PageStore(tmp_path, client, browser=browser).get("https://a.example/vide")
    assert first.status == "vide" and second.ok and second.method == "navigateur"


async def test_pubmed_lu_par_l_api_officielle() -> None:
    from citecheck.pages import official_api_url

    assert official_api_url("https://www.ncbi.nlm.nih.gov/pubmed/37419366") == (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=37419366&rettype=abstract&retmode=text"
    )
    assert official_api_url("https://pubmed.ncbi.nlm.nih.gov/37419366/").endswith("id=37419366&rettype=abstract&retmode=text")
    assert official_api_url("https://example.org/pubmed/1") is None

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(200, text="Resume de l'article. " * 20, headers={"content-type": "text/plain"})

    async with default_client(httpx.MockTransport(handler)) as client:
        page = await fetch_page(client, "https://pubmed.ncbi.nlm.nih.gov/37419366/")
    assert page.ok and page.method == "api" and seen == ["eutils.ncbi.nlm.nih.gov"]
    assert page.url == "https://pubmed.ncbi.nlm.nih.gov/37419366/"


def test_page_de_verification_anti_robot_non_comptee_comme_lue() -> None:
    from citecheck.pages import block_reason, page_from_response

    challenge = ("Client Challenge A required part of this site couldn't load. This may be due to a "
                 "browser extension, network issues, or browser settings. Please check your connection, "
                 "disable any ad blockers, or try using a different browser.")
    response = httpx.Response(200, text=f"<html><body><p>{challenge}</p></body></html>",
                              headers={"content-type": "text/html"},
                              request=httpx.Request("GET", "https://oadoi.org/x"))
    page = page_from_response("https://oadoi.org/x", response)
    assert page.status == "bloquee" and not page.ok and page.browser_may_help
    # Un long article qui parle de captcha reste un article.
    assert block_reason("Les captcha sont etudies dans cet article. " * 200) is None


async def test_page_bloquee_en_cache_requalifiee(tmp_path: Path) -> None:
    from dataclasses import asdict

    from citecheck.pages import Page

    url = "https://oadoi.org/x"
    async with default_client(_page_transport()) as client:
        store = PageStore(tmp_path, client)
        store._path(url).write_text(json.dumps(asdict(Page(
            url=url, status="ok", text="Client Challenge A required part of this site couldn't load. " * 3))),
            encoding="utf-8")
        page = await store.get(url)
    assert page.status == "bloquee"


async def test_citation_rejugee_si_la_page_change(tmp_path: Path) -> None:
    import hashlib

    out = tmp_path / "run"
    (out / "raw").mkdir(parents=True)
    (out / "raw" / "q1.json").write_text(json.dumps({
        "id": "q1", "mode": "quick", "query": "q", "attendu": [], "payload": QUICK_PAYLOAD,
    }), encoding="utf-8")
    env = {"CITECHECK_LLM_API_KEY": "j"}
    args = argparse.Namespace(sortie=str(out), juge="j", plongements="aucun", concurrence=2,
                              sans_pipeline=True, raisonnement=False, sans_navigateur=True)
    calls: list[str] = []
    await cli.cmd_evaluate(args, env, llm_transport=_judge_transport(calls), page_transport=_page_transport())
    first = len(calls)
    await cli.cmd_evaluate(args, env, llm_transport=_judge_transport(calls), page_transport=_page_transport())
    assert len(calls) == first

    # La page en cache change : sa citation est rejugee, les autres non.
    url = "https://a.example/page"
    cached = out / "pages" / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()[:24]}.json"
    page = json.loads(cached.read_text(encoding="utf-8"))
    page["text"] = page["text"] + "\nParagraphe ajoute depuis le premier jugement."
    cached.write_text(json.dumps(page), encoding="utf-8")
    await cli.cmd_evaluate(args, env, llm_transport=_judge_transport(calls), page_transport=_page_transport())
    assert len(calls) == first + 1


async def test_lecture_navigateur_sans_fin_bornee(tmp_path: Path) -> None:
    import asyncio

    async def hung(url: str):
        await asyncio.sleep(3600)

    async with default_client(_page_transport()) as client:
        page = await PageStore(tmp_path, client, browser=hung, browser_deadline_s=0.05).get("https://a.example/vide")
    assert not page.ok and page.method == "http+navigateur" and "echeance" in page.error


async def test_juge_muet_arrete_l_evaluation(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("muet", request=request)

    cases = tmp_path / "cas.jsonl"
    cases.write_text(json.dumps({"id": "c1", "texte": "t", "affirmation": "a", "attendu": "soutenue"}) + "\n",
                     encoding="utf-8")
    args = argparse.Namespace(cas=str(cases), juge="muet", sortie=str(tmp_path), plongements="aucun",
                              concurrence=1, raisonnement=False)
    with pytest.raises(cli.ConfigError, match="juge indisponible"):
        await cli.cmd_calibrate(args, {"CITECHECK_LLM_API_KEY": "k"}, llm_transport=httpx.MockTransport(handler))


# --- ecart : series de repetitions -------------------------------------------


def _evaluation(root: Path, name: str, rates: dict[str, tuple[int, int]], units: tuple[int, int]) -> Path:
    """Evaluation minimale : rates = tache -> (soutenues, jugees)."""
    path = root / name
    path.mkdir()
    rows, tasks = [], {}
    for task, (ok, judged) in rates.items():
        tasks[task] = {"mode": "deep", "unites_citees": units[0], "unites": units[1]}
        for i in range(judged):
            rows.append({"key": f"{task}-{i}", "task_id": task, "mode": "deep",
                         "status": "soutenue" if i < ok else "non_soutenue"})
    (path / "affirmations.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    (path / "resume.json").write_text(json.dumps({"juge": "j", "taches": tasks}), encoding="utf-8")
    return path


def test_ecart_apparie_par_tache_avec_bruit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a1 = _evaluation(tmp_path, "a1", {"t1": (5, 10), "t2": (2, 10)}, (5, 10))
    a2 = _evaluation(tmp_path, "a2", {"t1": (7, 10), "t2": (2, 10)}, (5, 10))
    b1 = _evaluation(tmp_path, "b1", {"t1": (8, 10), "t2": (6, 10)}, (8, 10))
    b2 = _evaluation(tmp_path, "b2", {"t1": (8, 10), "t2": (4, 10)}, (8, 10))
    out = tmp_path / "ecart.json"
    rc = cli.main(["ecart", "--a", str(a1), str(a2), "--b", str(b1), str(b2), "--sortie", str(out)])
    assert rc == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    claims = result["affirmations_soutenues"]
    # t1 : 60 % -> 80 % ; t2 : 20 % -> 50 % ; ecart moyen +25 points.
    assert claims["par_tache"] == {"t1": {"a": 0.6, "b": 0.8}, "t2": {"a": 0.2, "b": 0.5}}
    assert claims["ecart_moyen"] == 0.25
    assert claims["taches_en_hausse"] == 2 and claims["taches_en_baisse"] == 0
    # Bruit : A varie de 20 points sur t1, 0 sur t2 ; B de 0 et 20.
    assert claims["bruit_a"] == 0.1 and claims["bruit_b"] == 0.1
    assert claims["repetitions"] == [2, 2]
    assert result["phrases_citees"]["ecart_moyen"] == 0.3
    assert "+25.0 points" in capsys.readouterr().out
