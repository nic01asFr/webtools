"""Cache d'embeddings partage par le croisement et la selection de passages.

La cle est l'empreinte sha256 du contenu ENTIER : deux passages qui ne
different qu'apres leurs 100 premiers caracteres ne se confondent pas, et un
meme passage n'est envoye qu'une fois au service d'embeddings (C37).

Les textes partent par lots bornes, et l'echec du service est memorise pour
le reste de la requete : un service lent coutait jusqu'a ~9 min sur un seul
lot de tous les passages (delai x reprises), puis la selection repayait la
meme chose apres le croisement.
"""

import hashlib
from typing import Any

# Longueur de texte transmise au service d'embeddings par passage.
EMBED_CHARS = 2000

# Textes envoyes par appel au service d'embeddings.
EMBED_BATCH_SIZE = 64


class EmbeddingsUnavailableError(Exception):
    """Le service d'embeddings a deja echoue pendant cette requete."""


class EmbeddingCache(dict):
    """Cache {empreinte: vecteur} d'une requete, avec memoire de l'echec.

    `failure` : message du premier echec du service, None tant qu'il repond.
    `diagnostics` : journal des degradations de la requete (optionnel).
    """

    def __init__(self, *args: Any, diagnostics: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.failure: str | None = None
        self.diagnostics = diagnostics


def content_key(content: str) -> str:
    """Empreinte sha256 du contenu entier (cle du cache)."""
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def _note(cache: dict, event: str, **details: Any) -> None:
    diagnostics = getattr(cache, "diagnostics", None)
    if diagnostics is not None:
        diagnostics.note(event, **details)


async def embed_with_cache(llm_client: Any, cache: dict[str, list], texts: list[str]) -> list[list]:
    """Vecteurs de `texts`, dans l'ordre ; seuls les textes absents du cache
    sont envoyes (une fois chacun) a llm_client.embed, par lots de
    EMBED_BATCH_SIZE au plus.

    Leve l'exception du client si l'appel echoue : l'appelant decide du repli.
    Avec un EmbeddingCache, l'echec est memorise : les appels suivants de la
    meme requete levent EmbeddingsUnavailableError sans solliciter le service.
    """
    keys = [content_key(t) for t in texts]
    missing: dict[str, str] = {}
    for key, text in zip(keys, texts):
        if key not in cache and key not in missing:
            missing[key] = text[:EMBED_CHARS]
    if missing:
        failure = getattr(cache, "failure", None)
        if failure is not None:
            _note(cache, "selection.embeddings_echec_memorise", textes=len(missing))
            raise EmbeddingsUnavailableError(
                f"service d'embeddings en echec plus tot dans la requete : {failure}"
            )
        pending = list(missing.items())
        for start in range(0, len(pending), EMBED_BATCH_SIZE):
            batch = pending[start : start + EMBED_BATCH_SIZE]
            try:
                vectors = await llm_client.embed([text for _key, text in batch])
            except Exception as exc:
                if isinstance(cache, EmbeddingCache):
                    cache.failure = str(exc) or type(exc).__name__
                    _note(
                        cache,
                        "selection.embeddings_echec",
                        lot=start // EMBED_BATCH_SIZE + 1,
                        lots=-(-len(pending) // EMBED_BATCH_SIZE),
                        error=cache.failure,
                    )
                raise
            # Les lots deja recus restent en cache : seul le reste manque.
            for (key, _text), vec in zip(batch, vectors):
                cache[key] = vec
    return [cache[key] for key in keys]
