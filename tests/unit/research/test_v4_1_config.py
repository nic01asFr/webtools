"""Lot V4-1 : les budgets et seuils du pipeline vivent dans PipelineConfig.

Le dictionnaire attendu est ecrit en clair : changer une valeur par defaut sans
le vouloir fait echouer ce test.
"""

from typing import Any

from app.agents.intelligent_orchestrator import ExecutionContext, IntelligentOrchestrator
from app.research.pipeline_config import PipelineConfig
from app.research.stages.queries import build_search_query, search_pool_size

from tests.fakes import FakeExtractorManager, FakeLLM, FakeSearch

EXPECTED_DEFAULTS: dict[str, Any] = {
    "clean_title": False,
    "summary_mode": "extract",
    "validate_cited_urls": False,
    "bibliography_cited_only": False,
    "bibliography_real_titles": False,
    "plan_prompt_v2": False,
    "prompt_payload_minimal": False,
    "max_chunks_per_source": 0,
    "enrichment_enabled": False,
    "coherence_append_enabled": False,
    "regenerate_v2": False,
    "separate_search_terms": False,
    "search_language_from_query": False,
    "extraction_cap_follows_depth": False,
    "exploration_uses_plan_query": False,
    "targeted_search_skip_structured": True,
    "chunking_v2": False,
    "rss_tier_root_only": True,
    "single_llm_pass_enabled": True,
    "extraction_use_agent": True,
    "search_zero_result_fallback": True,
    "quick_uses_chunk_selection": False,
    "quick_verify_answer": False,
    "deep_verify_sections": True,
    "selection_fallback_enabled": True,
    "synthesis_v3": True,
    "search_relevance_gate": True,
    "max_sources_per_section": None,
    "exploration_max_results": 8,
    "exploration_engines": "google,duckduckgo",
    "exploration_snippet_chars": 200,
    "exploration_rich_min_sources": 6,
    "exploration_moderate_min_sources": 3,
    "exploration_preview_snippets": 3,
    "exploration_topics_max": 5,
    "exploration_topic_min_count": 2,
    "plan_max_tokens_structured": 6000,
    "plan_max_tokens_unstructured": 3000,
    "plan_temperature": 0.2,
    "plan_topics_in_prompt": 5,
    "plan_snippet_chars": 150,
    "plan_max_sections": 10,
    "plan_words_target_min": 100,
    "plan_words_target_max": 1200,
    "plan_max_search_terms": 3,
    "plan_search_term_max_chars": 90,
    "plan_fallback_intro_words": 300,
    "plan_fallback_analysis_words": 600,
    "plan_fallback_conclusion_words": 300,
    "plan_fallback_total_sources": 10,
    "plan_fallback_sources_per_section": 3,
    "search_query_max_words": 12,
    "search_query_section_words": 5,
    "search_query_min_query_words": 3,
    "search_query_fallback_chars": 120,
    "search_terms_max_queries": 3,
    "search_sources_light": 3,
    "search_sources_moderate": 5,
    "search_sources_deep": 8,
    "search_pool_multiplier": 3,
    "search_snippet_chars": 300,
    "cascade_engines_per_axis": 3,
    "search_fallback_query_words": 6,
    "complement_default_sources_per_section": 2,
    "complement_depth_light_delta": -1,
    "complement_depth_moderate_delta": 0,
    "complement_depth_deep_delta": 1,
    "complement_min_threshold": 1,
    "section_budget_min_seconds": 60,
    "section_budget_timeout_ratio": 0.8,
    "collection_writing_reserve_ratio": 0.5,
    "targeted_search_pool_size": 5,
    "targeted_search_max_urls": 2,
    "targeted_extract_timeout": 30,
    "targeted_structured_input_chars": 2000,
    "extraction_url_cap_default": 5,
    "step_preview_chars": 100,
    "selection_default_max_chars": 50000,
    "selection_min_score_light": 60.0,
    "selection_min_score_moderate": 40.0,
    "selection_min_score_deep": 35.0,
    "selection_max_chunks_light": 8,
    "selection_max_chunks_moderate": 16,
    "selection_max_chunks_deep": 30,
    "selection_max_chunks_explicit_threshold": 40,
    "selection_deep_min_remaining_chars": 1000,
    "selection_fallback_chunks": 3,
    "selection_weight_keyword": 0.5,
    "selection_weight_semantic": 0.35,
    "selection_weight_corroboration": 0.15,
    "selection_weight_keyword_only": 0.85,
    "selection_fact_bonus_max": 10.0,
    "selection_corroboration_cap": 3,
    "selection_keyword_match_saturation": 8.0,
    "selection_keyword_max_points": 40.0,
    "selection_structured_max_points": 30,
    "selection_structured_numerical_weight": 3,
    "selection_structured_temporal_weight": 2,
    "selection_structured_entity_weight": 1,
    "selection_length_ideal_min_chars": 500,
    "selection_length_ideal_max_chars": 5000,
    "selection_length_max_points": 20,
    "selection_length_floor_points": 10,
    "selection_length_decay_chars": 1000,
    "selection_numbers_max_points": 10,
    "selection_dates_weight": 2,
    "corroboration_min_similarity": 0.75,
    "corroboration_max_similarity": 0.92,
    "synthesis_default_words_target": 500,
    "synthesis_source_budget_chars": 16000,
    "synthesis_no_source_words_cap": 150,
    "synthesis_few_sources_max": 2,
    "synthesis_few_sources_words_threshold": 300,
    "synthesis_few_sources_words_floor": 250,
    "synthesis_few_sources_words_divisor": 2,
    "synthesis_previous_sections_chars": 180,
    "synthesis_previous_sections_max": 6,
    "synthesis_min_payload_chars": 200,
    "synthesis_tokens_per_word": 4.5,
    "synthesis_temperature": 0.3,
    "synthesis_max_attempts": 3,
    "synthesis_retry_backoff_base": 2,
    "synthesis_facts_chars": 3000,
    "synthesis_facts_per_section_chars": 600,
    "verification_model": "",
    "coherence_preview_chars": 500,
    "coherence_max_tokens": 1500,
    "coherence_temperature": 0.2,
    "coherence_max_improvements": 3,
    "summary_sentences": 2,
    "unsourced_degraded_ratio": 0.5,
    "enrichment_max_iterations": 1,
    "enrichment_stop_score": 85,
    "enrichment_thin_chars_per_section": 800,
    "enrichment_min_section_chars": 2000,
    "enrichment_weak_score": 75,
    "enrichment_max_sections_per_iteration": 2,
    "enrichment_report_chars": 5000,
    "enrichment_analysis_max_tokens": 4000,
    "enrichment_analysis_temperature": 0.1,
    "enrichment_data_chars": 3000,
    "enrichment_regenerate_max_tokens": 2000,
    "enrichment_regenerate_v2_max_tokens": 6000,
    "enrichment_regenerate_temperature": 0.1,
}


def _orch(config: PipelineConfig) -> IntelligentOrchestrator:
    return IntelligentOrchestrator(
        FakeLLM(),
        search_client=FakeSearch(),
        extractor_manager=FakeExtractorManager(),
        config=config,
    )


def test_defauts_figes() -> None:
    assert PipelineConfig().to_dict() == EXPECTED_DEFAULTS


def test_defauts_issus_de_l_environnement_vide() -> None:
    assert PipelineConfig.from_env({}).to_dict() == EXPECTED_DEFAULTS


def test_surcharge_par_variable_d_environnement() -> None:
    cfg = PipelineConfig.from_env(
        {
            "WEBTOOLS_PIPELINE_SYNTHESIS_MAX_ATTEMPTS": "5",
            "WEBTOOLS_PIPELINE_SELECTION_MIN_SCORE_DEEP": "20.5",
            "WEBTOOLS_PIPELINE_EXPLORATION_ENGINES": "bing",
        }
    )
    assert cfg.synthesis_max_attempts == 5
    assert cfg.selection_min_score_deep == 20.5
    assert cfg.exploration_engines == "bing"


def test_la_requete_de_recherche_lit_sa_configuration() -> None:
    query = "un deux trois quatre cinq six sept huit neuf dix onze douze treize"
    default = build_search_query(_orch(PipelineConfig()).services(), query=query, section_name="")
    short = build_search_query(
        _orch(PipelineConfig(search_query_max_words=3)).services(), query=query, section_name=""
    )
    assert len(default.split()) > 3
    assert len(short.split()) == 3


def test_la_taille_du_pool_lit_sa_configuration() -> None:
    orch = _orch(PipelineConfig(search_pool_multiplier=7))
    assert search_pool_size(orch.services(), 5) == 5
    ctx = ExecutionContext("q", config=orch.config)
    ctx.domains_whitelist = ["example.org"]
    assert search_pool_size(orch.services(), 5, ctx) == 35
