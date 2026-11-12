"""Tests de app.services.engine_catalog (constat C78). Aucun appel reseau."""

from typing import Any

import pytest
from app.services.engine_catalog import (
    AXIS_PREFERENCE,
    FAILURE_COOLDOWN_SECONDS,
    EngineCatalog,
)


def _engine(name: str, *cats: str, enabled: bool = True) -> dict[str, Any]:
    return {"name": name, "categories": list(cats), "enabled": enabled}


CONFIG: dict[str, Any] = {
    "engines": [
        _engine("wikipedia search", "general"),
        _engine("duckduckgo", "general", "web"),
        _engine("brave", "general"),
        _engine("startpage", "general"),
        _engine("google scholar", "science"),
        _engine("arxiv", "science"),
        _engine("pubmed", "science"),
        _engine("semantic scholar", "science"),
        _engine("github", "it", "repos"),
        _engine("stackoverflow", "it", "q&a"),
        _engine("pypi", "packages"),
        _engine("google news", "news"),
        _engine("wikipedia", "wikimedia"),
        _engine("wikidata", "wikimedia"),
        _engine("bing images", "images"),
        _engine("soundcloud", "music"),
        _engine("deepl", "general"),
        _engine("pirate", "files", enabled=True),
        _engine("desactive", "general", enabled=False),
        {"categories": ["general"], "enabled": True},
    ]
}


class Horloge:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def horloge() -> Horloge:
    return Horloge()


@pytest.fixture
def catalogue(horloge: Horloge) -> EngineCatalog:
    cat = EngineCatalog(clock=horloge)
    assert cat.ingest(CONFIG) is True
    return cat


# --- ingest ---------------------------------------------------------------


def test_ingest_filtre_categories_et_moteurs_exclus(catalogue: EngineCatalog) -> None:
    noms = set(catalogue._engines)
    assert "bing images" not in noms
    assert "soundcloud" not in noms
    assert "deepl" not in noms
    assert "pirate" not in noms
    assert "desactive" not in noms
    assert "github" in noms
    assert catalogue.available is True


def test_ingest_vide_ou_sans_moteur() -> None:
    cat = EngineCatalog()
    assert cat.ingest({}) is False
    assert cat.ingest({"engines": []}) is False
    assert cat.available is False


def test_ingest_vide_l_etat_avant_remplissage() -> None:
    cat = EngineCatalog()
    cat.ingest(CONFIG)
    cat.ingest({"engines": [_engine("arxiv", "science")]})
    assert list(cat._engines) == ["arxiv"]


def test_ingest_vide_apres_un_chargement_reussi_rend_indisponible() -> None:
    cat = EngineCatalog()
    cat.ingest(CONFIG)
    assert cat.ingest({"engines": []}) is False
    assert cat.available is False
    assert cat.cascade() == []


# --- load ------------------------------------------------------------------


class FauxSearxng:
    def __init__(self, reponses: list[Any]) -> None:
        self.reponses = list(reponses)
        self.appels = 0

    async def __call__(self, base_url: str, timeout: float) -> dict[str, Any]:
        self.appels += 1
        rep = self.reponses.pop(0)
        if isinstance(rep, Exception):
            raise rep
        return rep


async def _sans_attente(_: float) -> None:
    return None


async def test_load_reussit_du_premier_coup(monkeypatch: pytest.MonkeyPatch) -> None:
    cat = EngineCatalog()
    faux = FauxSearxng([CONFIG])
    monkeypatch.setattr(cat, "_fetch_config", faux)
    assert await cat.load("http://searxng:8080", sleep=_sans_attente) is True
    assert faux.appels == 1
    assert cat.available


async def test_load_retente_quand_searxng_n_est_pas_pret(monkeypatch: pytest.MonkeyPatch) -> None:
    cat = EngineCatalog()
    faux = FauxSearxng([ConnectionError("pas pret"), ConnectionError("pas pret"), CONFIG])
    monkeypatch.setattr(cat, "_fetch_config", faux)
    attentes: list[float] = []

    async def sleep(s: float) -> None:
        attentes.append(s)

    assert await cat.load("http://searxng:8080", retries=3, delay=2.0, sleep=sleep) is True
    assert faux.appels == 3
    assert attentes == [2.0, 4.0]
    assert cat.available


async def test_load_abandonne_apres_les_tentatives(monkeypatch: pytest.MonkeyPatch) -> None:
    cat = EngineCatalog()
    faux = FauxSearxng([ConnectionError("x")] * 3)
    monkeypatch.setattr(cat, "_fetch_config", faux)
    assert await cat.load("http://searxng:8080", retries=3, sleep=_sans_attente) is False
    assert faux.appels == 3
    assert cat.available is False
    assert cat.cascade() == []


async def test_load_reponse_sans_moteur_est_un_echec(monkeypatch: pytest.MonkeyPatch) -> None:
    cat = EngineCatalog()
    faux = FauxSearxng([{"engines": []}, CONFIG])
    monkeypatch.setattr(cat, "_fetch_config", faux)
    assert await cat.load("http://s", retries=2, sleep=_sans_attente) is True
    assert faux.appels == 2


async def test_ensure_loaded_ne_recharge_pas_un_catalogue_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cat = EngineCatalog()
    faux = FauxSearxng([CONFIG])
    monkeypatch.setattr(cat, "_fetch_config", faux)
    await cat.load("http://s", sleep=_sans_attente)
    assert await cat.ensure_loaded(sleep=_sans_attente) is True
    assert faux.appels == 1


async def test_ensure_loaded_retente_un_catalogue_vide(monkeypatch: pytest.MonkeyPatch) -> None:
    cat = EngineCatalog()
    faux = FauxSearxng([ConnectionError("x"), CONFIG])
    monkeypatch.setattr(cat, "_fetch_config", faux)
    assert await cat.load("http://s", retries=1, sleep=_sans_attente) is False
    assert await cat.ensure_loaded(sleep=_sans_attente) is True
    assert cat.available


async def test_ensure_loaded_sans_url_connue() -> None:
    assert await EngineCatalog().ensure_loaded() is False


def test_catalogue_non_charge() -> None:
    cat = EngineCatalog()
    assert cat.available is False
    assert cat.cascade() == []


# --- axes et cascade ----------------------------------------------------------


def test_axis_of(catalogue: EngineCatalog) -> None:
    assert catalogue.axis_of("duckduckgo") == "generaliste"
    assert catalogue.axis_of("arxiv") == "academique"
    assert catalogue.axis_of("github") == "technique"
    assert catalogue.axis_of("wikipedia") == "encyclopedique"
    assert catalogue.axis_of("google news") == "actualite"
    assert catalogue.axis_of("moteur inconnu") == "generaliste"


def test_cascade_respecte_l_ordre_de_preference_et_le_plafond(catalogue: EngineCatalog) -> None:
    sel = catalogue.cascade(per_axis=2)
    generaliste = [n for n in sel if catalogue.axis_of(n) == "generaliste"]
    assert generaliste == ["wikipedia search", "duckduckgo"]
    academique = [n for n in sel if catalogue.axis_of(n) == "academique"]
    assert academique == ["google scholar", "semantic scholar"]
    technique = [n for n in sel if catalogue.axis_of(n) == "technique"]
    assert technique == ["github", "stackoverflow"]


def test_cascade_filtre_par_axes(catalogue: EngineCatalog) -> None:
    sel = catalogue.cascade(per_axis=5, axes=["academique"])
    assert set(sel) == {"google scholar", "semantic scholar", "pubmed", "arxiv"}


def test_cascade_moteur_hors_preference_passe_apres(horloge: Horloge) -> None:
    cat = EngineCatalog(clock=horloge)
    cat.ingest(
        {
            "engines": [
                _engine("zzz", "science"),
                _engine("aaa", "science"),
                _engine("arxiv", "science"),
            ]
        }
    )
    assert cat.cascade(per_axis=3) == ["arxiv", "aaa", "zzz"]


# --- mise a l'ecart et rehabilitation ---------------------------------------


def test_moteur_en_echec_est_ecarte_et_remplace(catalogue: EngineCatalog) -> None:
    avant = catalogue.cascade(per_axis=2, axes=["generaliste"])
    assert avant[0] == "wikipedia search"
    catalogue.mark_failure("Wikipedia Search", "suspendu")
    apres = catalogue.cascade(per_axis=2, axes=["generaliste"])
    assert "wikipedia search" not in apres
    assert len(apres) == 2


def test_rehabilitation_apres_cooldown(catalogue: EngineCatalog, horloge: Horloge) -> None:
    catalogue.mark_failure("wikipedia search")
    horloge.t += FAILURE_COOLDOWN_SECONDS - 1
    assert "wikipedia search" not in catalogue.cascade(per_axis=1, axes=["generaliste"])
    horloge.t += 2
    assert catalogue.cascade(per_axis=1, axes=["generaliste"]) == ["wikipedia search"]
    assert "wikipedia search" not in catalogue._failures


def test_rehabilitation_immediate_sur_succes(catalogue: EngineCatalog) -> None:
    catalogue.mark_failure("wikipedia search")
    catalogue.mark_success("WIKIPEDIA SEARCH")
    assert catalogue.cascade(per_axis=1, axes=["generaliste"]) == ["wikipedia search"]


def test_mark_failure_et_success_sur_nom_inconnu(catalogue: EngineCatalog) -> None:
    catalogue.mark_failure("inconnu")
    catalogue.mark_success("inconnu")
    assert catalogue._failures == {}


def test_tous_les_moteurs_d_un_axe_en_echec_vide_l_axe(catalogue: EngineCatalog) -> None:
    for n in ("google scholar", "semantic scholar", "pubmed", "arxiv"):
        catalogue.mark_failure(n)
    assert catalogue.cascade(per_axis=3, axes=["academique"]) == []


def test_preferences_ne_contiennent_pas_de_doublons() -> None:
    for axe, noms in AXIS_PREFERENCE.items():
        assert len(noms) == len(set(noms)), axe
