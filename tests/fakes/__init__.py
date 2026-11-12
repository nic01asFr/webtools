"""Doublures du pipeline de recherche approfondie (sans reseau, sans LLM reel).

Elles reproduisent l'interface reelle des objets injectes dans
IntelligentOrchestrator(llm_client, search_client=..., extractor_manager=...).

    from tests.fakes import FakeLLM, FakeSearch, FakeExtractorManager, make_result

FakeLLM (double de OpenAILLMClient)
    llm = FakeLLM()
    llm.on_structured("research_plan", {...})        # par nom de schema
    llm.on_text(r"Rédige la section \"Intro\"", "texte.")  # par motif regex du prompt
    Une reponse est : une valeur, une exception (levee), une fonction
    f(LLMCall) -> valeur, ou une liste consommee dans l'ordre (la derniere se
    repete). Appels : llm.calls, llm.calls_for(kind=, schema_name=, pattern=).
    Appel sans scenario : enregistre dans llm.unmatched et leve FakeLLMError ;
    le pipeline avale souvent cette erreur, donc tester llm.unmatched == [].
    embed() est deterministe (hash du texte).

FakeSearch (double de SearXNGClient.search)
    search = FakeSearch()
    search.add("requete exacte", [make_result(url, titre, extrait)])
    search.add(re.compile("motif"), [...])           # ou un motif
    Sans correspondance : liste vide (ou search.default). Requetes
    enregistrees avec tous leurs parametres : search.queries (SearchCall),
    search.queries_text().

FakeExtractorManager (double de ExtractorManager.extract)
    pages = FakeExtractorManager({"https://x.org/a": "texte"})
    pages.add_page(url, contenu, titre)
    Une URL inconnue rend un WebResult en echec. Appels : pages.calls,
    pages.urls_requested().

Exemple d'assemblage : tests/contract/test_pipeline_contract.py.
"""

from tests.fakes.fake_extractor import ExtractCall, FakeExtractorManager
from tests.fakes.fake_llm import FakeLLM, FakeLLMError, LLMCall
from tests.fakes.fake_search import FakeSearch, SearchCall, make_result

__all__ = [
    "ExtractCall",
    "FakeExtractorManager",
    "FakeLLM",
    "FakeLLMError",
    "FakeSearch",
    "LLMCall",
    "SearchCall",
    "make_result",
]
