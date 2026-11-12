"""Tests de benchmarks/convert.py (aucun reseau)."""

from convert import degradation_info, report_has_text, report_to_markdown, sanity_check

PAYLOAD = {
    "success": True,
    "result": {
        "title": "Sujet",
        "summary": "Resume court.",
        "sections": [
            {"title": "A", "content": "Texte [1] et [2]."},
            {"title": "Vide", "content": "   "},
            {"title": "Marque", "content": "x [SECTION_SANS_SOURCE]"},
        ],
        "bibliography": [
            {"title": "Un", "url": "https://a.example/1"},
            {"title": "Sans url", "url": ""},
            {"title": "Deux", "url": "https://b.example/2"},
        ],
    },
}


def test_report_to_markdown_structure() -> None:
    md = report_to_markdown(PAYLOAD)
    assert md.startswith("# Sujet")
    assert "## A" in md and "Texte [1] et [2]." in md
    assert "## Vide" not in md and "SECTION_SANS_SOURCE" not in md
    assert "[1] Un. https://a.example/1" in md
    assert "Sans url" not in md


def test_report_to_markdown_accepts_report_key_and_bare_payload() -> None:
    bare = {"title": "T", "sections": [{"title": "S", "content": "c"}]}
    assert "## S" in report_to_markdown({"report": bare})
    assert "## S" in report_to_markdown(bare)


def test_report_has_text() -> None:
    assert report_has_text(PAYLOAD)
    assert not report_has_text({"result": {"title": "T", "bibliography": [{"url": "u"}]}})
    assert not report_has_text({"result": {"sections": [{"content": " "}]}})
    assert not report_has_text({})


def test_sanity_check_counts_and_orphans() -> None:
    md = report_to_markdown(PAYLOAD) + "\nAutre [7]."
    diag = sanity_check(md)
    assert diag["bibliography_entries"] == 2
    # [2] designe une source sans URL (non rendue), [7] n existe pas
    assert diag["orphan_citations"] == [2, 7]
    assert diag["has_urls"] is True
    assert diag["usable"] is False  # moins de 200 mots
    assert "degraded" not in diag


def test_sanity_check_with_payload_adds_degradation_fields() -> None:
    payload = {
        "success": False,
        "degraded": True,
        "failed_sections": ["B"],
        "unsourced_sections": ["C", "D"],
        "result": PAYLOAD["result"],
    }
    diag = sanity_check(report_to_markdown(payload), payload)
    assert diag["degraded"] is True
    assert diag["success"] is False
    assert diag["failed_sections"] == ["B"]
    assert diag["unsourced_sections"] == ["C", "D"]


def test_degradation_info_defaults_and_nested() -> None:
    assert degradation_info({"success": True}) == {
        "success": True,
        "degraded": False,
        "failed_sections": [],
        "unsourced_sections": [],
    }
    nested = degradation_info({"result": {"degraded": True, "failed_sections": ["x"]}})
    assert nested["degraded"] is True and nested["failed_sections"] == ["x"]
