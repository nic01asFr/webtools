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

import logging
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

# Profils de repli. Ils ne CONTRAIGNENT pas le choix du LLM : ils servent
# quand il ne se prononce pas, quand sa selection est vide apres
# validation, ou quand il prefere un raccourci a une enumeration.
# Les noms sont valides contre le catalogue reel au moment de l'usage :
# un moteur absent de la configuration est simplement ignore.
FALLBACK_PROFILES: Dict[str, List[str]] = {
    "general": ["google", "duckduckgo", "bing", "brave", "startpage", "wikipedia"],
    "academic": ["google scholar", "semantic scholar", "arxiv", "pubmed",
                 "crossref", "openairedatasets", "wikipedia"],
    "news": ["google news", "bing news", "reuters", "yahoo news", "duckduckgo"],
    "technical": ["github", "stackexchange", "pypi", "arch linux wiki",
                  "docker hub", "duckduckgo"],
    "reference": ["wikipedia", "wikidata", "wikibooks", "duckduckgo", "google"],
}

DEFAULT_PROFILE = "general"


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
    "generaliste": ["google", "duckduckgo", "bing", "brave", "startpage",
                    "google cse", "wikipedia", "mojeek", "qwant"],
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

    def __init__(self):
        self._engines: Dict[str, dict] = {}
        self._loaded = False
        # moteur -> horodatage du dernier echec (voir mark_failure)
        self._failures: Dict[str, float] = {}

    async def load(self, base_url: str, timeout: float = 10.0) -> bool:
        """Interroge /config. Best-effort : un echec laisse le catalogue vide,
        et l'appelant retombe sur le comportement sans selection."""
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{base_url.rstrip('/')}/config")
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(f"Catalogue de moteurs indisponible ({e}) — selection desactivee")
            self._loaded = False
            return False

        for eng in data.get("engines", []):
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

    @property
    def available(self) -> bool:
        return self._loaded

    def validate(self, names: Optional[List[str]]) -> List[str]:
        """Ne garde que les moteurs reellement presents. Un nom inconnu est
        ecarte sans erreur : le LLM peut se tromper, cela ne doit pas faire
        echouer une recherche."""
        if not names or not self._loaded:
            return []
        valid, unknown = [], []
        for n in names:
            key = str(n).strip().lower()
            match = next((real for real in self._engines if real.lower() == key), None)
            (valid.append(match) if match else unknown.append(n))
        if unknown:
            logger.debug(f"Moteurs inconnus ignores : {unknown}")
        return valid

    def resolve(
        self,
        engines: Optional[List[str]] = None,
        profile: Optional[str] = None,
    ) -> List[str]:
        """
        Selection finale, par ordre de priorite :
          1. moteurs explicites choisis par le LLM (valides)
          2. profil de repli demande
          3. profil general
        """
        chosen = self.validate(engines)
        if chosen:
            return chosen

        if profile:
            chosen = self.validate(FALLBACK_PROFILES.get(profile.lower().strip(), []))
            if chosen:
                return chosen

        return self.validate(FALLBACK_PROFILES[DEFAULT_PROFILE])

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
        import time
        key = self._canonical(name)
        if not key:
            return
        self._failures[key] = time.time()
        logger.debug(f"Moteur '{key}' ecarte temporairement ({reason})")

    def mark_success(self, name: str):
        """Un moteur qui repond de nouveau est rehabilite immediatement."""
        key = self._canonical(name)
        if key:
            self._failures.pop(key, None)

    def _is_available(self, name: str) -> bool:
        import time
        ts = self._failures.get(name)
        if ts is None:
            return True
        if time.time() - ts > FAILURE_COOLDOWN_SECONDS:
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

    def prompt_listing(self, max_per_category: int = 6) -> str:
        """
        Liste compacte pour le prompt du plan, groupee par categorie.

        Bornee volontairement : le prompt du plan porte deja la structure, les
        objectifs, les questions cles et les requetes. Y deverser 40 noms
        bruts risquerait de degrader le reste - un LLM qui fait cinq choses
        correctement en fait rarement sept.
        """
        if not self._loaded:
            return ""
        by_cat: Dict[str, List[str]] = {}
        for name, meta in self._engines.items():
            for cat in meta["categories"]:
                by_cat.setdefault(cat, []).append(name)
        lines = []
        for cat in sorted(by_cat):
            names = sorted(by_cat[cat])[:max_per_category]
            lines.append(f"  {cat}: {', '.join(names)}")
        return "\n".join(lines)


engine_catalog = EngineCatalog()
