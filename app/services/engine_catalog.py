"""
Catalogue des moteurs de recherche disponibles.

Decouvert au demarrage depuis /config de SearXNG plutot que code en dur :
la liste reste juste meme si la configuration SearXNG change, et le LLM ne
peut pas inventer de noms puisqu'on valide contre le catalogue reel.

Pourquoi ce module existe : sans selection, SearXNG interroge TOUS les
moteurs actifs de la categorie - 74 sources par recherche, dont Pinterest,
SoundCloud, PirateBay, la meteo et des dictionnaires. Sur une journee de
tests, 233 recherches ont ainsi produit ~17 000 requetes sortantes et fait
suspendre l'acces par Google, Brave, DuckDuckGo et Startpage. Le bruit
degradait aussi le scoring, qui se dispersait sur des resultats hors sujet.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Categories sans interet pour une recherche documentaire : elles ne
# produisent jamais de texte exploitable par la synthese.
EXCLUDED_CATEGORIES = {
    "images", "videos", "music", "files", "social media", "map",
    # Utilitaires : meteo, taux de change, traduction, dictionnaires,
    # recettes. Ils repondent a une requete documentaire par du bruit, et
    # encombrent la liste proposee au LLM.
    "translate", "dictionaries", "define", "currency", "weather",
}

# Quelques moteurs isoles sans categorie disqualifiante mais sans interet
# documentaire.
EXCLUDED_ENGINES = {"chefkoch", "wttr.in", "currency", "dictzone", "wordnik",
                    "etymonline", "wiktionary", "lingva", "libretranslate",
                    "deepl", "mymemory translated"}

# --- Regroupement en axes -----------------------------------------------
#
# Les categories de SearXNG servent les ONGLETS de son interface web ("je
# cherche une image", "je cherche du code"), pas un usage programmatique.
# D'ou leurs defauts pour nous, constates sur le catalogue reel :
#   - general et web contiennent exactement les memes moteurs (doublon)
#   - science et scientific publications se recouvrent largement
#   - le domaine technique est eclate en 5 (it, packages, q&a, repos,
#     software wikis), ce qui lui donnerait 5x plus de representants qu'a
#     la science dans une cascade uniforme
#
# On regroupe donc en axes equilibres, qui correspondent a la NATURE de la
# source plutot qu'a un onglet.
CATEGORY_TO_AXIS: Dict[str, str] = {
    "general": "generaliste",
    "web": "generaliste",
    "science": "academique",
    "scientific publications": "academique",
    "news": "actualite",
    "it": "technique",
    "packages": "technique",
    "q&a": "technique",
    "repos": "technique",
    "software wikis": "technique",
    "wikimedia": "encyclopedique",
}

DEFAULT_AXIS = "generaliste"

# Ordre de preference DANS chaque axe. Explicite et non alphabetique : un
# tri alphabetique retenait "arch linux wiki" et "askubuntu" avant "github"
# et "stackoverflow", ce qui aurait annule le benefice de la cascade.
# Les moteurs absents de cette table passent apres ceux qui y figurent.
AXIS_PREFERENCE: Dict[str, List[str]] = {
    # "wikipedia search" en TETE : c'est la seule source generaliste a API
    # publique, sans cle, sans quota et jamais suspendue. Tous les autres
    # moteurs de cet axe sont scrapes par SearXNG faute d'API gratuite, et
    # se suspendent regulierement (jusqu'a 6 simultanement observes, ce qui
    # vidait entierement l'axe et faisait echouer des taches completes).
    # A ne pas confondre avec "wikipedia" (lookup par titre exact, concu
    # pour l'infobox de l'interface web - rend 0 resultat sur une requete
    # qui n'est pas un titre d'article).
    # Axe generaliste elargi de 3 a ~13 moteurs.
    #
    # Il ne comptait que google/duckduckgo/brave/startpage — tous scrapes par
    # SearXNG faute d'API gratuite, donc tous sujets a suspension. Quand ils
    # tombaient ENSEMBLE (observe : 5 simultanement), l'axe se vidait et des
    # taches echouaient completement : trois rapports du run v8 sont passes
    # de ~2 900 a ~1 500 mots, faisant chuter le score global de 0.3697 a
    # 0.3049 — une regression imputable a la collecte, pas au code.
    #
    # Ordre : d'abord les sources a API publique (jamais suspendues), puis
    # les index alternatifs valides par test (3 requetes de domaines
    # differents chacun, aucun n'a flanche), enfin les grands moteurs
    # scrapes en dernier recours.
    #   searchzee 225 | startpagina 219 | swisscows 207 | neosearch 206
    #   resulthunter 59 | naver 45 | seznam 30 | yacy 30
    # mojeek exclu : 0 resultat sur tous les tests.
    "generaliste": ["wikipedia search", "wiby", "wikibooks",
                    "swisscows", "neosearch", "searchzee", "startpagina",
                    "resulthunter", "seznam", "yacy", "naver",
                    "google cse", "duckduckgo", "brave", "startpage",
                    "wikiversity", "wikisource"],
    "academique": ["google scholar", "semantic scholar", "pubmed", "arxiv",
                   "crossref", "openairepublications", "openairedatasets"],
    "actualite": ["google news", "reuters", "bing news", "duckduckgo news",
                  "brave.news", "yahoo news", "startpage news"],
    "technique": ["github", "stackoverflow", "pypi", "docker hub",
                  "askubuntu", "superuser", "arch linux wiki", "gentoo"],
    "encyclopedique": ["wikipedia", "wikidata", "wikibooks", "wikinews"],
}

# Duree pendant laquelle un moteur en echec est ecarte avant reessai. Sans
# rehabilitation, un incident passager l'eliminerait definitivement et le
# catalogue s'appauvrirait silencieusement.
FAILURE_COOLDOWN_SECONDS = 900


class EngineCatalog:
    """Catalogue des moteurs reellement disponibles, decouvert a l'execution."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._engines: Dict[str, dict] = {}
        self._loaded = False
        # moteur -> horodatage du dernier echec (voir mark_failure)
        self._failures: Dict[str, float] = {}
        # Horloge injectable : les tests avancent le temps sans attendre.
        self._clock = clock
        self._base_url: str | None = None

    async def _fetch_config(self, base_url: str, timeout: float) -> dict:
        """Appel HTTP a /config, isole pour etre remplace dans les tests."""
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/config")
            resp.raise_for_status()
            return resp.json()

    def ingest(self, config: dict) -> bool:
        """Construit le catalogue depuis le contenu de /config, sans reseau.

        L'etat precedent est vide avant remplissage : un moteur disparu de la
        configuration ne doit pas survivre a un rechargement."""
        self._engines = {}
        for eng in (config or {}).get("engines", []):
            if not eng.get("enabled"):
                continue
            cats = set(eng.get("categories", []))
            if cats & EXCLUDED_CATEGORIES:
                continue
            name = eng.get("name")
            if name and name.lower() in EXCLUDED_ENGINES:
                continue
            if name:
                self._engines[name] = {
                    "categories": sorted(cats),
                    "time_range_support": eng.get("time_range_support", False),
                }

        self._loaded = bool(self._engines)
        logger.info(f"Catalogue de moteurs charge : {len(self._engines)} moteurs texte disponibles")
        return self._loaded

    async def load(
        self,
        base_url: str,
        timeout: float = 10.0,
        retries: int = 3,
        delay: float = 2.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> bool:
        """Interroge /config, avec nouvelles tentatives si SearXNG demarre
        encore. Best-effort : un echec final laisse le catalogue vide, et
        l'appelant retombe sur le comportement sans selection."""
        self._base_url = base_url
        for attempt in range(1, max(retries, 1) + 1):
            try:
                data = await self._fetch_config(base_url, timeout)
                if self.ingest(data):
                    return True
                logger.warning("Catalogue de moteurs : /config sans moteur exploitable")
            except Exception as e:
                logger.warning(f"Catalogue de moteurs indisponible ({e}) - tentative {attempt}/{retries}")
            if attempt < retries:
                await sleep(delay * 2 ** (attempt - 1))

        self._loaded = False
        return False

    async def ensure_loaded(
        self,
        timeout: float = 10.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> bool:
        """Retente un chargement si le catalogue est vide et qu'une URL est connue."""
        if self._loaded:
            return True
        if not self._base_url:
            return False
        return await self.load(self._base_url, timeout=timeout, retries=1, sleep=sleep)

    @property
    def available(self) -> bool:
        return self._loaded

    # --- Disponibilite ---------------------------------------------------
    #
    # UNIQUEMENT la disponibilite, jamais la pertinence.
    #
    # Agreger des scores de QUALITE par moteur serait une erreur : PubMed
    # accumulerait de mauvaises notes sur les sujets non medicaux, finirait
    # par descendre durablement, et serait absent le jour ou une question de
    # sante publique arrive. On appauvrirait les sources sans jamais le
    # voir - on ne sait pas ce qu'on n'a pas trouve.
    # La disponibilite, elle, est independante du sujet : un moteur suspendu
    # l'est pour tout le monde. Elle est donc transferable sans risque.
    # Ne pas reintroduire de scoring de pertinence par moteur ici.

    def mark_failure(self, name: str, reason: str = ""):
        """Signale qu'un moteur n'a pas repondu (suspension, timeout, vide)."""
        key = self._canonical(name)
        if not key:
            return
        self._failures[key] = self._clock()
        logger.debug(f"Moteur '{key}' ecarte temporairement ({reason})")

    def mark_success(self, name: str):
        """Un moteur qui repond de nouveau est rehabilite immediatement."""
        key = self._canonical(name)
        if key:
            self._failures.pop(key, None)

    def _is_available(self, name: str) -> bool:
        ts = self._failures.get(name)
        if ts is None:
            return True
        if self._clock() - ts > FAILURE_COOLDOWN_SECONDS:
            del self._failures[name]  # rehabilitation apres cooldown
            return True
        return False

    def _canonical(self, name: str) -> Optional[str]:
        key = str(name).strip().lower()
        return next((real for real in self._engines if real.lower() == key), None)

    def axis_of(self, engine_name: str) -> str:
        """Axe d'un moteur, deduit de ses categories SearXNG."""
        meta = self._engines.get(engine_name, {})
        for cat in meta.get("categories", []):
            if cat in CATEGORY_TO_AXIS:
                return CATEGORY_TO_AXIS[cat]
        return DEFAULT_AXIS

    def cascade(self, per_axis: int = 3, axes: Optional[List[str]] = None) -> List[str]:
        """
        Selection par defaut : les `per_axis` meilleurs moteurs DISPONIBLES
        de chaque axe.

        Garantit l'exhaustivite (chaque axe reste represente) tout en bornant
        la charge : sans selection, SearXNG interroge tous les moteurs actifs
        de la categorie. La cascade absorbe aussi les suspensions - si le
        premier choix d'un axe est indisponible, le suivant prend sa place,
        la ou une liste figee se serait videe.
        """
        if not self._loaded:
            return []

        by_axis: Dict[str, List[str]] = {}
        for name in self._engines:
            by_axis.setdefault(self.axis_of(name), []).append(name)

        selected: List[str] = []
        for axis, names in by_axis.items():
            if axes and axis not in axes:
                continue
            pref = AXIS_PREFERENCE.get(axis, [])
            def rank(n: str) -> tuple:
                low = n.lower()
                return (pref.index(low) if low in pref else len(pref), low)
            ordered = sorted(names, key=rank)
            kept = [n for n in ordered if self._is_available(n)][:per_axis]
            selected.extend(kept)

        return selected


engine_catalog = EngineCatalog()
