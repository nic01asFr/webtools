"""Tests du manager d'extraction : paliers, cache, budget (lot V1-8).

Aucun reseau ni navigateur : les extracteurs sont remplaces par des doublures.
"""

import asyncio
from typing import Any

import pytest
from app.api.models import ExtractionOptions, WebResult
from app.manager import ExtractorManager, is_site_root_or_feed
from pydantic import ValidationError

ARTICLE = "https://example.com/blog/un-article"
ROOT = "https://example.com/"


class FakeTier:
    """Doublure d'un palier : try_extract (pdf, rss) et extract (direct, agent)."""

    def __init__(self, result: WebResult | None = None, delay: float = 0.0):
        self.result = result
        self.delay = delay
        self.calls = 0
        self.options: dict[str, Any] | None = None

    async def try_extract(self, url: str, timeout: int = 10) -> WebResult | None:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result.model_copy(deep=True) if self.result else None

    async def extract(self, url, prompt, llm_client=None, options=None) -> WebResult:
        self.calls += 1
        self.options = options
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result.model_copy(deep=True)


def ok(url: str, content: str = "contenu", **metadata: Any) -> WebResult:
    return WebResult.from_success(
        url=url, content_type="webpage", title="t", content=content, metadata=dict(metadata)
    )


def make_manager(pdf=None, rss=None, direct=None, agent=None, **kwargs: Any) -> ExtractorManager:
    manager = ExtractorManager(cache_ttl_seconds=300, enable_caching=True, **kwargs)
    manager.pdf_extractor = pdf or FakeTier(None)
    manager.rss_extractor = rss or FakeTier(None)
    manager.direct_extractor = direct or FakeTier(ok(ARTICLE))
    manager.agent_extractor = agent or FakeTier(ok(ARTICLE))
    return manager


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok(url: str, resolver=None) -> None:
        return None

    monkeypatch.setattr("app.utils.url_safety.assert_safe_url_async", _ok, raising=False)


class FakeLLM:
    pass


# --- palier producteur ---------------------------------------------------


async def test_tier_pdf() -> None:
    manager = make_manager(pdf=FakeTier(ok(ARTICLE)))
    result = await manager.extract(ARTICLE)
    assert result.metadata["extraction_tier"] == "pdf"


async def test_tier_rss() -> None:
    manager = make_manager(rss=FakeTier(ok(ARTICLE)))
    # Palier RSS force pour une URL d'article : verifie l'etiquette du palier.
    result = await manager.extract(ARTICLE, options=ExtractionOptions(rss_tier_root_only=False))
    assert result.metadata["extraction_tier"] == "rss"


async def test_tier_direct() -> None:
    manager = make_manager()
    result = await manager.extract(ARTICLE, options=ExtractionOptions(use_agent=False))
    assert result.metadata["extraction_tier"] == "direct"


async def test_tier_agent_et_tier_pose_par_l_agent_respecte() -> None:
    manager = make_manager(agent=FakeTier(ok(ARTICLE)))
    result = await manager.extract(ARTICLE, llm_client=FakeLLM())
    assert result.metadata["extraction_tier"] == "agent"

    manager = make_manager(agent=FakeTier(ok(ARTICLE, extraction_tier="llm")))
    result = await manager.extract(ARTICLE, llm_client=FakeLLM())
    assert result.metadata["extraction_tier"] == "llm"


# --- rss_tier_root_only --------------------------------------------------


async def test_rss_sert_une_url_d_article_si_le_drapeau_est_coupe() -> None:
    rss = FakeTier(ok(ARTICLE, "liste des titres"))
    manager = make_manager(rss=rss)
    opts = ExtractionOptions(use_agent=False, rss_tier_root_only=False)
    result = await manager.extract(ARTICLE, options=opts)
    assert result.metadata["extraction_tier"] == "rss"


async def test_drapeau_rss_root_only_n_appelle_pas_le_flux_pour_un_article() -> None:
    rss = FakeTier(ok(ARTICLE, "liste des titres"))
    direct = FakeTier(ok(ARTICLE, "texte de l'article"))
    manager = make_manager(rss=rss, direct=direct)
    opts = ExtractionOptions(use_agent=False, rss_tier_root_only=True)
    result = await manager.extract(ARTICLE, options=opts)
    assert rss.calls == 0
    assert result.content == "texte de l'article"
    assert result.metadata["extraction_tier"] == "direct"


@pytest.mark.parametrize(
    "url",
    [
        ROOT,
        "https://example.com",
        "https://example.com/feed",
        "https://example.com/rss.xml",
        "https://example.com/news/atom.xml",
    ],
)
async def test_drapeau_rss_root_only_garde_le_flux_pour_racine_et_flux(url: str) -> None:
    assert is_site_root_or_feed(url)
    rss = FakeTier(ok(url, "flux"))
    manager = make_manager(rss=rss)
    result = await manager.extract(url, options=ExtractionOptions(rss_tier_root_only=True))
    assert rss.calls == 1
    assert result.metadata["extraction_tier"] == "rss"


def test_url_d_article_n_est_ni_racine_ni_flux() -> None:
    assert not is_site_root_or_feed(ARTICLE)
    assert not is_site_root_or_feed("https://example.com/?p=12")


# --- cache ---------------------------------------------------------------


async def test_cache_distingue_prompt_et_options() -> None:
    direct = FakeTier(ok(ARTICLE))
    manager = make_manager(direct=direct)
    opts = ExtractionOptions(use_agent=False)
    await manager.extract(ARTICLE, prompt="a", options=opts)
    await manager.extract(ARTICLE, prompt="a", options=opts)
    assert direct.calls == 1
    await manager.extract(ARTICLE, prompt="b", options=opts)
    assert direct.calls == 2
    await manager.extract(
        ARTICLE, prompt="a", options=ExtractionOptions(use_agent=False, headless=False)
    )
    assert direct.calls == 3


async def test_cache_rend_une_copie() -> None:
    manager = make_manager()
    opts = ExtractionOptions(use_agent=False)
    first = await manager.extract(ARTICLE, options=opts)
    first.content = "modifie par l'appelant"
    first.metadata["x"] = 1
    second = await manager.extract(ARTICLE, options=opts)
    assert second.content == "contenu"
    assert "x" not in second.metadata
    assert second is not first


async def test_cache_borne_en_taille() -> None:
    manager = make_manager(cache_max_entries=3)
    opts = ExtractionOptions(use_agent=False)
    for i in range(10):
        await manager.extract(f"https://example.com/p{i}", options=opts)
    assert len(manager._cache) == 3


async def test_options_de_l_appelant_non_modifiees() -> None:
    manager = make_manager()
    opts = ExtractionOptions(use_agent=True)

    async def boom() -> None:
        raise ValueError("pas de cle")

    manager._create_default_llm_client = boom  # type: ignore[assignment]
    await manager.extract(ARTICLE, options=opts)
    assert opts.use_agent is True


# --- ExtractionOptions ---------------------------------------------------


def test_options_refusent_les_champs_inconnus() -> None:
    with pytest.raises(ValidationError):
        ExtractionOptions(extract_images=False)


def test_options_defauts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEBTOOLS_PIPELINE_RSS_TIER_ROOT_ONLY", raising=False)
    monkeypatch.delenv("WEBTOOLS_PIPELINE_SINGLE_LLM_PASS_ENABLED", raising=False)
    opts = ExtractionOptions()
    # C56 : un article n'est plus servi par le flux RSS de son site.
    assert opts.rss_tier_root_only is True
    assert opts.single_llm_pass_enabled is True
    assert opts.try_rss_first is True


def test_options_suivent_la_configuration_du_deploiement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBTOOLS_PIPELINE_RSS_TIER_ROOT_ONLY", "0")
    monkeypatch.setenv("WEBTOOLS_PIPELINE_SINGLE_LLM_PASS_ENABLED", "0")
    opts = ExtractionOptions()
    assert opts.rss_tier_root_only is False
    assert opts.single_llm_pass_enabled is False
    # Une valeur explicite de l'appelant prime toujours.
    assert ExtractionOptions(rss_tier_root_only=True).rss_tier_root_only is True


# --- budget global -------------------------------------------------------


async def test_budget_borne_un_palier_lent() -> None:
    direct = FakeTier(ok(ARTICLE), delay=30)
    manager = make_manager(direct=direct)
    opts = ExtractionOptions.model_construct(
        use_agent=False,
        timeout=0.3,
        headless=True,
        try_rss_first=True,
        rss_tier_root_only=False,
        single_llm_pass_enabled=True,
        enable_ocr=False,
    )
    loop = asyncio.get_running_loop()
    start = loop.time()
    result = await manager.extract(ARTICLE, options=opts)
    assert loop.time() - start < 3
    assert not result.success
    assert "Budget" in (result.error or "")


async def test_budget_partage_entre_paliers() -> None:
    # Horloge simulee : chaque palier consomme 0.4 s de budget sans attente
    # reelle. La version precedente dormait vraiment et echouait sous charge
    # (un reliquat de budget de quelques millisecondes laissait passer le
    # palier direct).
    now = [0.0]
    direct = FakeTier(ok(ARTICLE))

    class SlowTier(FakeTier):
        async def try_extract(self, url: str, timeout: int = 10) -> WebResult | None:
            self.calls += 1
            now[0] += 0.4
            return None

    pdf = SlowTier(None)
    rss = SlowTier(None)
    manager = make_manager(pdf=pdf, rss=rss, direct=direct)
    manager._clock = lambda: now[0]
    opts = ExtractionOptions.model_construct(
        use_agent=False,
        timeout=0.6,
        headless=True,
        try_rss_first=True,
        rss_tier_root_only=False,
        single_llm_pass_enabled=True,
        enable_ocr=False,
    )
    result = await manager.extract(ARTICLE, options=opts)
    # 0.4 + 0.4 > 0.6 : le budget est epuise apres le RSS, le direct n'est
    # jamais atteint.
    assert pdf.calls == 1 and rss.calls == 1
    assert not result.success
    assert direct.calls == 0


async def test_paliers_suivants_recoivent_le_temps_restant() -> None:
    now = [0.0]
    direct = FakeTier(ok(ARTICLE))
    manager = make_manager(direct=direct)
    manager._clock = lambda: now[0]

    class SlowRss(FakeTier):
        async def try_extract(self, url: str, timeout: int = 10) -> WebResult | None:
            now[0] += 20.0
            return None

    manager.rss_extractor = SlowRss(None)
    await manager.extract(
        ARTICLE, options=ExtractionOptions(use_agent=False, timeout=45, rss_tier_root_only=False)
    )
    assert direct.options["timeout"] == pytest.approx(25.0)


async def test_echec_pdf_explicite_rendu_tel_quel_sans_autres_paliers() -> None:
    echec = WebResult.from_error(url=ARTICLE, error_message="telechargement PDF impossible")
    rss = FakeTier(ok(ARTICLE))
    direct = FakeTier(ok(ARTICLE))
    manager = make_manager(pdf=FakeTier(echec), rss=rss, direct=direct)
    result = await manager.extract(ARTICLE, options=ExtractionOptions(use_agent=False))
    assert not result.success
    assert result.error == "telechargement PDF impossible"
    assert rss.calls == 0 and direct.calls == 0
