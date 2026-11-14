"""Sources requises par l'appelant (sources.required) : extraction, proposition aux sections, bilan.

Une URL requise est extraite AVANT la collecte des sections, par le
gestionnaire d'extraction de la requete (garde SSRF, budget et rejeu compris),
decoupee en passages comme toute autre source, puis proposee comme candidate a
TOUTES les sections ; la selection par pertinence decide ou elle sert. La
demande explicite prime sur domains_whitelist et exclusions.
"""

import logging
import re
from typing import Any

from app.api.models import ExtractionOptions
from app.research.citations import url_key
from app.research.context import ExecutionContext
from app.research.stages.queries import filter_by_constraints
from app.research.stages.services import StageServices

logger = logging.getLogger(__name__)


def required_urls_from_context(context: Any) -> list[str]:
    """URL requises du contexte de l'appelant, dedoublonnees, dans l'ordre."""
    raw = context.get("sources_required") if isinstance(context, dict) else None
    urls: list[str] = []
    for item in raw or []:
        url = str(item).strip()
        if url and url not in urls:
            urls.append(url)
    return urls


async def extract_required_sources(
    svc: StageServices,
    ctx: ExecutionContext,
    context: Any,
    exploration_data: dict[str, Any],
) -> None:
    """Extrait les URL requises et les rend visibles du plan.

    Les passages vont dans ctx.required_chunks (proposes aux sections par
    add_required_chunks) ; titres et extraits rejoignent
    exploration_data["sources"], que lit le plan. Un echec est consigne dans
    ctx.required_failed, jamais leve.
    """
    urls = required_urls_from_context(context)
    ctx.required_urls = urls
    if not urls:
        return

    from app.utils.chunking import document_to_chunk_items

    cfg = ctx.config
    options = ExtractionOptions(
        use_agent=cfg.extraction_use_agent,
        headless=True,
        rss_tier_root_only=cfg.rss_tier_root_only,
        single_llm_pass_enabled=cfg.single_llm_pass_enabled,
    )
    plan_sources: list[dict[str, Any]] = []

    for url in urls:
        # La demande explicite prime : l'URL passe, l'ecart est note.
        if (ctx.domains_whitelist or ctx.domains_exclusions) and not filter_by_constraints(
            [{"url": url}], ctx
        ):
            ctx.diagnostics.note(
                "sources.requise_hors_contraintes",
                url=url,
                whitelist=ctx.domains_whitelist,
                exclusions=ctx.domains_exclusions,
            )

        reason = ""
        try:
            if url in ctx.extraction_cache:
                result = ctx.extraction_cache[url]
            else:
                result = await svc.extractor_manager.extract(
                    url=url, llm_client=svc.llm_client, options=options
                )
                # Seuls les succes sont gardes : une section qui retrouve
                # cette URL retente une extraction en echec transitoire.
                if result.success:
                    ctx.extraction_cache[url] = result
            if result.success and result.content:
                items = document_to_chunk_items(
                    source=url,
                    title=result.title or "",
                    content=result.content,
                    v2=cfg.chunking_v2,
                )
                ctx.required_chunks.extend(items)
                ctx.required_extracted.append(url)
                if url not in ctx.discovered_sources:
                    ctx.discovered_sources.append(url)
                plan_sources.append(
                    {
                        "url": url,
                        "title": result.title or "",
                        "snippet": result.content[: cfg.exploration_snippet_chars],
                        "required": True,
                    }
                )
                ctx.add_step(
                    "webextractor",
                    "extract_required_source",
                    url,
                    {"title": result.title, "content_length": len(result.content)},
                    True,
                )
                continue
            reason = (getattr(result, "error", None) or "contenu vide").strip() or "contenu vide"
        except Exception as exc:  # noqa: BLE001 - l'echec est rendu dans la reponse
            reason = f"{type(exc).__name__}: {exc}"

        logger.warning(f"  Source requise non extraite: {url} ({reason})")
        ctx.required_failed.append({"url": url, "reason": reason})
        ctx.diagnostics.note("sources.requise_echec", url=url, reason=reason)
        ctx.add_step("webextractor", "extract_required_source", url, reason, False)

    if plan_sources:
        # En tete : le plan voit d'abord ce que l'appelant a exige.
        known = {s.get("url") for s in plan_sources}
        exploration_data["sources"] = plan_sources + [
            s for s in exploration_data.get("sources", []) if s.get("url") not in known
        ]


def add_required_chunks(ctx: ExecutionContext, extracted_by_section: dict[str, list[dict]]) -> None:
    """Propose les passages des sources requises a TOUTES les sections.

    Copie par section (le croisement ecrit dans les passages) ; un passage deja
    present dans la section n'est pas duplique.
    """
    if not ctx.required_chunks:
        return
    for name, items in extracted_by_section.items():
        present = {(i.get("source"), i.get("content")) for i in items if isinstance(i, dict)}
        extra = [
            dict(chunk)
            for chunk in ctx.required_chunks
            if (chunk.get("source"), chunk.get("content")) not in present
        ]
        extracted_by_section[name] = list(items) + extra


def apply_required_sources(report: dict[str, Any], ctx: ExecutionContext) -> None:
    """Bilan des sources requises dans le rapport.

    Echec d'extraction : rapport degrade, avec required_sources_failed.
    URL extraite mais absente de la bibliographie : required_sources_uncited,
    sans degrader.
    """
    if not ctx.required_urls:
        return
    if ctx.required_failed:
        report["required_sources_failed"] = [dict(f) for f in ctx.required_failed]
        report["degraded"] = True
    # La bibliographie garde aussi les passages jamais cites : une source est
    # citee quand son numero [n] figure dans le texte du rapport.
    texts = [str(s.get("content") or "") for s in (report.get("sections") or [])]
    texts.append(str(report.get("summary") or ""))
    used_numbers = {int(n) for t in texts for n in re.findall(r"\[(\d+)\]", t)}
    # Comparaison par url_key, comme la deduplication de la bibliographie :
    # ".../page/" requise est citee quand ".../page" l'est.
    numbers: dict[str, set] = {}
    for b in report.get("bibliography") or []:
        if isinstance(b, dict) and b.get("url"):
            numbers.setdefault(url_key(str(b["url"])), set()).add(b.get("id"))
    uncited = [
        u for u in ctx.required_extracted if not numbers.get(url_key(u), set()) & used_numbers
    ]
    if uncited:
        report["required_sources_uncited"] = uncited
        ctx.diagnostics.note("sources.requise_non_citee", urls=uncited)
