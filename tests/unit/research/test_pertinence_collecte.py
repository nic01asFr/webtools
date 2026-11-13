"""Filtre des resultats de recherche sans rapport avec la requete (C96).

Cas reels de la collecte enregistree du 13/11/2026 (corpus citecheck).
"""

from __future__ import annotations

import re

from app.research.stages.relevance import is_relevant, keep_relevant, stems
from app.research.stages.search import section_research_phase

from tests.fakes import FakeSearch, make_result
from tests.unit.research.test_v4_2_stages import make

NEONIC = (
    "Effets des néonicotinoïdes sur les abeilles et évolution de la réglementation "
    "européenne depuis 2013"
)
TRAIT = "financement recul trait côte France projets désartificialisation littoral France 2023 2024"


def test_racines_sans_accents_ni_operateurs() -> None:
    assert stems("site:europa.eu Néonicotinoïdes abeilles") == {"neonic", "abeill"}
    assert "dans" not in stems("dans le texte")


def test_resultats_hors_sujet_reels_ecartes() -> None:
    q = stems(NEONIC)
    assert not is_relevant(q, "Supermarchés Lidl en France", "", "bing")
    assert not is_relevant(q, "Lidl, ça vaut le coup. En supermarché et en ligne.", "", "bing")
    assert is_relevant(q, "Néonicotinoïdes : l'UE interdit trois substances", "abeilles", "bing")


def test_arxiv_exige_deux_termes() -> None:
    q = stems(TRAIT)
    # Un seul terme commun ("France") : bruit de disjonction.
    assert not is_relevant(q, "Ad Orientem: the Orientation of Gothic Cathedrals of France", "", "arxiv")
    assert is_relevant(q, "Coastal retreat financing in France: littoral policy", "", "arxiv")
    # Hors arXiv, un terme suffit.
    assert is_relevant(q, "Le littoral face a la montee des eaux", "", "duckduckgo")


def test_mots_apparentes_d_une_autre_langue_gardes() -> None:
    q = stems("microplastiques santé humaine")
    assert is_relevant(q, "Microplastics in human blood", "", "semantic scholar")
    q = stems("Hydrogène renouvelable en Europe")
    assert is_relevant(q, "Renewable hydrogen costs", "", "crossref")


def test_requete_sans_terme_significatif_garde_tout() -> None:
    kept, dropped = keep_relevant("le la les", [make_result("https://a.org", "Rien")])
    assert len(kept) == 1 and dropped == 0


async def test_section_toute_hors_sujet_declenche_le_repli() -> None:
    # La requete de section ne rend que du bruit : filtree, la liste est
    # vide et le repli par requete raccourcie (six mots) retrouve une source.
    long_q = "neonicotinoides abeilles reglementation europeenne evolution depuis 2013 interdiction"
    search = FakeSearch(default=[
        make_result("https://www.lidl.fr/", "Supermarchés Lidl en France", engine="bing"),
    ])
    search.add(re.compile(r"^\S+(?:\s\S+){0,5}$"), [
        make_result("https://efsa.eu/n", "Néonicotinoïdes et abeilles : avis EFSA", engine="bing"),
    ])
    svc, ctx = make(search=search)
    out = await section_research_phase(
        svc, "q", "Reglementation", {"depth": "light", "search_terms": [long_q]}, {}, ctx
    )
    assert [s["url"] for s in out["sources"]] == ["https://efsa.eu/n"]
    assert ctx.diagnostics.count("collecte.resultats_hors_sujet") >= 1
    assert ctx.diagnostics.count("collecte.repli_requete_courte") == 1
