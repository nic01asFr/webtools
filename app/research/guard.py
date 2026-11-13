"""Contrat commun des etapes qui reecrivent un texte deja verifie (C93).

Constat du 13/11/2026 : la verification des sections (deep_verify_sections)
a lieu a la redaction, mais une etape en aval pouvait ensuite reecrire la
section sans controle. L'enrichissement demandait d'"ajouter des chiffres
precis" sur 3 000 caracteres de donnees, et ce texte remplacait la section
verifiee. Toute etape qui reecrit une section passe donc par ce contrat :
la nouvelle version n'est gardee que si aucune de ses phrases n'est
contredite ou non soutenue par les sources disponibles ; sinon la version
precedente reste.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping

from app.research.answer_check import check_answer
from app.research.context import ExecutionContext

logger = logging.getLogger(__name__)

# Verdicts qui font refuser une reecriture. "partielle" ne suffit pas : la
# version precedente peut en contenir autant.
REJECTING = ("contredite", "non_soutenue")


def sources_from(*groups: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """(url, texte) par page, a partir de passages {"source"|"url", "content"}."""
    by_url: dict[str, list[str]] = {}
    for group in groups:
        for item in group or []:
            if not isinstance(item, Mapping):
                continue
            url = str(item.get("source") or item.get("url") or "").strip()
            content = str(item.get("content") or "").strip()
            if url and content:
                by_url.setdefault(url, []).append(content)
    return [(url, "\n[...]\n".join(parts)) for url, parts in by_url.items()]


async def accept_rewrite(
    llm_client: Any,
    ctx: ExecutionContext,
    where: str,
    rewritten: str,
    sources: list[tuple[str, str]],
) -> bool:
    """Vrai si le texte reecrit (citations [SOURCE:url]) peut remplacer l'ancien.

    Sans verification active (deep_verify_sections faux), le contrat ne
    s'applique pas. Une verification impossible refuse la reecriture : le
    texte deja verifie est plus sur qu'un texte qui ne l'est pas.
    """
    if not ctx.config.deep_verify_sections:
        return True
    if not sources:
        ctx.diagnostics.note("verification.reecriture_refusee", where=where, raison="sans_source")
        return False
    try:
        verdicts = await check_answer(
            llm_client, rewritten, sources, label_style="url", model=ctx.config.verification_model
        )
    except Exception as e:
        logger.warning(f"Reecriture de '{where}' non verifiee, version precedente gardee : {e}")
        ctx.diagnostics.note(
            "verification.reecriture_refusee", where=where, raison="verification_impossible",
            erreur=type(e).__name__,
        )
        return False
    bad = [v for v in verdicts if v.verdict in REJECTING]
    if bad:
        ctx.diagnostics.note(
            "verification.reecriture_refusee", where=where, raison="phrases_fautives",
            fautives=len(bad), phrases=len(verdicts),
        )
        return False
    ctx.diagnostics.note("verification.reecriture_acceptee", where=where, phrases=len(verdicts))
    return True
