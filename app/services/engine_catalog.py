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


class EngineCatalog:
    """Catalogue des moteurs reellement disponibles, decouvert a l'execution."""

    def __init__(self):
        self._engines: Dict[str, dict] = {}
        self._loaded = False

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
