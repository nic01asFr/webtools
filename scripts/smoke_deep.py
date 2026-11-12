"""Verification de bout en bout d'une API deja lancee (recherche reelle).

Appelle /health, /api/v1/search, /api/v1/extract et /api/v1/research/deep,
puis verifie les invariants. Code de sortie non nul si un invariant est
viole, si une etape echoue ou si aucune source reelle n'a ete extraite.

Usage :
    python scripts/smoke_deep.py --api http://localhost:18000 --key e2e-test-key
Utilise uniquement la bibliotheque standard.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any

TOPIC = "History and uses of the Rust programming language"


def call(
    base: str, path: str, key: str, payload: dict[str, Any] | None = None, timeout: float = 60
) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        method="POST" if payload is not None else "GET",
        headers={"X-API-Key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, {"raw": body[:500]}


def summarize_deep(body: dict[str, Any]) -> dict[str, Any]:
    """Chiffres cles d'une reponse /research/deep."""
    result = body.get("result") or {}
    sections = result.get("sections") or []
    biblio = result.get("bibliography") or []
    text = "\n".join(str(s.get("content", "")) for s in sections if isinstance(s, dict))
    diag = body.get("diagnostics") or {}
    invariants = diag.get("invariants")
    violations = (invariants or {}).get("violations") if isinstance(invariants, dict) else None
    return {
        "success": body.get("success"),
        "degraded": body.get("degraded"),
        "sections": len(sections),
        "bibliography": len(biblio),
        "citations": len(re.findall(r"\[\d+(?:\s*[,;]\s*\d+)*\]", text)),
        "invariants": invariants,
        "violations": violations,
        "event_counts": (diag.get("events") or {}).get("counts") or {},
        "sources": [b.get("url") for b in biblio if isinstance(b, dict)],
    }


def run(api: str, key: str, topic: str, deep_timeout: int) -> int:
    failures: list[str] = []

    status, health = call(api, "/health", key)
    print(f"GET /health -> {status}: {json.dumps(health, ensure_ascii=False)[:400]}")
    if status != 200:
        failures.append("health")

    status, search = call(
        api, "/api/v1/search", key, {"query": "Rust programming language", "language": "en"}
    )
    results = (search or {}).get("results") or []
    print(f"POST /api/v1/search -> {status}: {len(results)} resultat(s)")
    for item in results[:5]:
        print(f"    {item.get('url')}")
    if status != 200 or not results:
        failures.append("search")

    status, extract = call(api, "/api/v1/extract", key, {"url": "https://example.com"}, timeout=120)
    content = json.dumps(extract, ensure_ascii=False)
    print(f"POST /api/v1/extract -> {status}: success={extract.get('success')} {len(content)} car.")
    if status != 200 or "Example Domain" not in content:
        failures.append("extract")

    started = time.monotonic()
    status, body = call(
        api,
        "/api/v1/research/deep",
        key,
        {"topic": topic, "timeout": deep_timeout},
        timeout=deep_timeout + 120,
    )
    duration = time.monotonic() - started
    body = body if isinstance(body, dict) else {}
    info = summarize_deep(body)
    print(f"POST /api/v1/research/deep -> {status} en {duration:.1f}s")
    print(f"    success={info['success']} degraded={info['degraded']} error={body.get('error')}")
    print(
        f"    sections={info['sections']} bibliographie={info['bibliography']} "
        f"citations[n]={info['citations']}"
    )
    print(f"    invariants={json.dumps(info['invariants'], ensure_ascii=False)}")
    print(f"    evenements={json.dumps(info['event_counts'], ensure_ascii=False, sort_keys=True)}")
    print("    sources de la bibliographie :")
    for url in info["sources"]:
        print(f"      {url}")

    if status != 200:
        failures.append("deep.http")
    if info["violations"]:
        failures.append(f"invariants violes: {info['violations']}")
    elif info["invariants"] is None:
        failures.append("invariants absents")
    if info["bibliography"] == 0:
        failures.append("aucune source reelle extraite")

    if failures:
        print("ECHEC : " + " ; ".join(failures))
        return 1
    print("OK")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:18000")
    parser.add_argument("--key", default="e2e-test-key")
    parser.add_argument("--topic", default=TOPIC)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    sys.exit(run(args.api, args.key, args.topic, args.timeout))


if __name__ == "__main__":
    main()
