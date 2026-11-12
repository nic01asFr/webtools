"""Cache d'embeddings partage par le croisement et la selection de passages.

La cle est l'empreinte sha256 du contenu ENTIER : deux passages qui ne
different qu'apres leurs 100 premiers caracteres ne se confondent pas, et un
meme passage n'est envoye qu'une fois au service d'embeddings (C37).
"""

import hashlib
from typing import Any

# Longueur de texte transmise au service d'embeddings par passage.
EMBED_CHARS = 2000


def content_key(content: str) -> str:
    """Empreinte sha256 du contenu entier (cle du cache)."""
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


async def embed_with_cache(llm_client: Any, cache: dict[str, list], texts: list[str]) -> list[list]:
    """Vecteurs de `texts`, dans l'ordre ; seuls les textes absents du cache
    sont envoyes (une fois chacun) a llm_client.embed.

    Leve l'exception du client si l'appel echoue : l'appelant decide du repli.
    """
    keys = [content_key(t) for t in texts]
    missing: dict[str, str] = {}
    for key, text in zip(keys, texts):
        if key not in cache and key not in missing:
            missing[key] = text[:EMBED_CHARS]
    if missing:
        vectors = await llm_client.embed(list(missing.values()))
        for key, vec in zip(missing, vectors):
            cache[key] = vec
    return [cache[key] for key in keys]
