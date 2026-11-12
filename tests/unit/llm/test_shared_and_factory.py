"""Instance partagee, fermeture et fabrique extensible."""

import pytest
from app.core import llm
from app.core.llm import LLMFactory, close_llm_client, get_llm_client
from app.core.llm.base import BaseLLMClient, LLMClientError


@pytest.fixture(autouse=True)
def environnement_llm(monkeypatch):
    llm._shared_clients.clear()
    from app.core.config import settings

    monkeypatch.setattr(settings, "default_llm_provider", "openai")
    monkeypatch.setattr(settings, "default_llm_model", "gpt-test")
    monkeypatch.setattr(settings, "default_llm_api_key", "cle-test")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "default_llm_base_url", "https://llm.test/api")
    monkeypatch.delenv("DEFAULT_LLM_TOTAL_TIMEOUT", raising=False)
    yield
    llm._shared_clients.clear()


async def test_deux_appels_rendent_le_meme_objet() -> None:
    first = await get_llm_client()
    second = await get_llm_client()
    assert first is second
    await close_llm_client()


async def test_configuration_differente_autre_instance(monkeypatch) -> None:
    first = await get_llm_client()
    monkeypatch.setattr(llm.settings, "default_llm_model", "autre-modele")
    second = await get_llm_client()
    assert first is not second
    await close_llm_client()


async def test_close_est_sans_effet_sur_l_instance_partagee() -> None:
    client = await get_llm_client()
    await client.close()
    assert client.client.is_closed() is False
    assert await get_llm_client() is client
    await close_llm_client()


async def test_close_llm_client_ferme_et_vide_le_registre() -> None:
    client = await get_llm_client()
    await close_llm_client()
    assert client.client.is_closed() is True
    assert await get_llm_client() is not client
    await close_llm_client()


async def test_close_ferme_une_instance_non_partagee() -> None:
    client = LLMFactory.create(
        "openai", api_key="k", model="gpt-test", base_url="https://llm.test/api"
    )
    await client.close()
    assert client.client.is_closed() is True


async def test_echeance_totale_lue_dans_l_environnement(monkeypatch) -> None:
    monkeypatch.setenv("DEFAULT_LLM_TOTAL_TIMEOUT", "42")
    client = await get_llm_client()
    assert client.total_timeout == 42.0
    await close_llm_client()


async def test_echeance_totale_absente_par_defaut() -> None:
    client = await get_llm_client()
    assert client.total_timeout is None
    await close_llm_client()


class FauxClient(BaseLLMClient):
    async def generate(self, messages: list[dict], **kwargs) -> str:
        return "faux"

    def get_langchain_wrapper(self):
        raise NotImplementedError

    async def _aclose(self) -> None:
        pass


def build_faux(api_key, model, base_url, **options) -> FauxClient:
    return FauxClient(api_key, base_url, model)


async def test_fabrique_extensible_par_nom() -> None:
    LLMFactory.register("faux", build_faux)
    try:
        client = LLMFactory.create("FAUX", api_key="k", model="m")
        assert await client.generate([]) == "faux"
        assert "faux" in LLMFactory.providers()
    finally:
        LLMFactory.unregister("faux")
    with pytest.raises(LLMClientError):
        LLMFactory.create("faux", api_key="k", model="m")


async def test_get_llm_client_utilise_un_fournisseur_enregistre(monkeypatch) -> None:
    LLMFactory.register("faux", build_faux)
    try:
        monkeypatch.setattr(llm.settings, "default_llm_provider", "faux")
        client = await get_llm_client()
        assert isinstance(client, FauxClient)
        assert await get_llm_client() is client
        await close_llm_client()
    finally:
        LLMFactory.unregister("faux")


def test_fournisseur_inconnu() -> None:
    with pytest.raises(LLMClientError):
        LLMFactory.create("inconnu", api_key="k", model="m")
