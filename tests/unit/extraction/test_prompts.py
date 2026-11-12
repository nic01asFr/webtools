"""Tests des gabarits de prompt (lot V1-8, C57)."""

from app.utils.prompts import PromptTemplates

URL = "https://example.com/page"


def test_prompt_personnalise_complete_le_gabarit() -> None:
    prompt = PromptTemplates.get_prompt("general", URL, "Extrait uniquement le titre")
    assert URL in prompt
    assert PromptTemplates.AGENT_SAFETY_RULES in prompt
    assert "TITRE:" in prompt and "CONTENU:" in prompt
    assert "Extrait uniquement le titre" in prompt


def test_prompt_personnalise_substitue_l_url() -> None:
    prompt = PromptTemplates.get_prompt("general", URL, "Resume {url}")
    assert f"Resume {URL}" in prompt
    assert "{url}" not in prompt


def test_prompt_personnalise_avec_accolades_ne_leve_pas() -> None:
    custom = 'Reponds en JSON {"titre": "..."} pour {url}'
    prompt = PromptTemplates.get_prompt("general", URL, custom)
    assert '{"titre": "..."}' in prompt
    assert URL in prompt


def test_prompt_personnalise_conserve_le_gabarit_du_type() -> None:
    prompt = PromptTemplates.get_prompt("article", URL, "Sois bref")
    assert "AUTEUR:" in prompt


def test_prompt_sans_personnalisation_inchange() -> None:
    prompt = PromptTemplates.get_prompt("general", URL)
    assert prompt == PromptTemplates.AGENT_SAFETY_RULES + "\n\n" + PromptTemplates.GENERAL.format(
        url=URL
    )
