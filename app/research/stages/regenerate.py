"""Regeneration d'une section faible avec les donnees de la recherche ciblee."""

import json
import logging
from typing import Any, Dict

from app.research.context import ExecutionContext
from app.research.stages.services import StageServices


logger = logging.getLogger(__name__)


async def regenerate_section(
    svc: StageServices,
    query: str,
    section_title: str,
    report: Dict[str, Any],
    enrichment_data: Dict[str, Any],
    ctx: ExecutionContext,
) -> Dict[str, Any]:
    """Régénère une section avec les données enrichies."""

    # Récupérer la section actuelle
    current_section = None
    for section in report.get("sections", []):
        if section.get("title") == section_title:
            current_section = section
            break

    if not current_section:
        return None

    if ctx.config.regenerate_v2:
        return await regenerate_section_v2(
            svc, query, section_title, report, current_section, enrichment_data, ctx
        )

    # Données d'enrichissement
    enrichment_content = json.dumps(
        enrichment_data.get("sources", []), indent=2, ensure_ascii=False
    )[: ctx.config.enrichment_data_chars]

    prompt = f"""Améliore cette section de rapport avec les nouvelles données collectées:

REQUÊTE GLOBALE: "{query}"

SECTION À AMÉLIORER: {section_title}

CONTENU ACTUEL:
{current_section.get("content", "")}

NOUVELLES DONNÉES COLLECTÉES:
{enrichment_content}

MANQUES IDENTIFIÉS: {", ".join(enrichment_data.get("missing", []))}

Ta mission:
1. ENRICHIR le contenu existant avec les nouvelles données
2. AJOUTER des chiffres précis, dates, noms trouvés
3. MAINTENIR la cohérence avec le reste du rapport
4. CITER les nouvelles sources utilisées

Retourne JSON:
{{
  "title": "{section_title}",
  "content": "Contenu enrichi (2-5 paragraphes avec détails factuels)",
  "sources": [{{"url": "...", "title": "..."}}]
}}

IMPORTANT: Le contenu doit être PLUS détaillé et factuel que l'original.
"""

    try:
        response = await svc.llm_client.generate(
            [{"role": "user", "content": prompt}],
            max_tokens=ctx.config.enrichment_regenerate_max_tokens,
            temperature=ctx.config.enrichment_regenerate_temperature,
        )

        # Parser JSON
        start_idx = response.find("{")
        if start_idx != -1:
            bracket_depth = 0
            for i, char in enumerate(response[start_idx:], start=start_idx):
                if char == "{":
                    bracket_depth += 1
                elif char == "}":
                    bracket_depth -= 1
                    if bracket_depth == 0:
                        result = json.loads(response[start_idx : i + 1])
                        logger.info(f"    ✅ Section enrichie: {section_title}")
                        return result
    except Exception as e:
        logger.error(f"Erreur régénération section: {e}")
        ctx.diagnostics.note(
            "enrichissement.regeneration_echouee", title=section_title, error=repr(e)
        )

    # Fallback: retourner la section actuelle
    return current_section


async def regenerate_section_v2(
    svc: StageServices,
    query: str,
    section_title: str,
    report: Dict[str, Any],
    current_section: Dict[str, Any],
    enrichment_data: Dict[str, Any],
    ctx: ExecutionContext,
) -> Dict[str, Any]:
    """Regeneration de section (drapeau regenerate_v2, C43).

    Au moment de l'enrichissement les sections portent des [n] (la
    conversion a eu lieu dans _final_assembly) : on les remet en
    [SOURCE:url] avant l'appel, le modele les conserve, et la conversion
    finale reprend les memes numeros (bibliographie existante conservee).
    Toute erreur ou perte de citation rend la section d'origine.
    """
    from app.research.enrichment_utils import (
        cited_urls,
        count_citations,
        numbered_to_source,
        strip_unknown_citations,
    )

    lang = ctx.search_language or "all"
    bibliography = report.get("bibliography") or []
    current_content = current_section.get("content", "") or ""
    current_with_urls = numbered_to_source(current_content, bibliography)
    original_urls = cited_urls(current_with_urls)

    sources = enrichment_data.get("sources", [])
    allowed_urls = set(original_urls)
    for item in sources:
        if isinstance(item, dict):
            for key in ("url", "source"):
                if isinstance(item.get(key), str):
                    allowed_urls.add(item[key])
    enrichment_content = json.dumps(sources, indent=2, ensure_ascii=False)

    if lang and lang != "all":
        language_rule = f"Redige en langue '{lang}' (code ISO), comme le reste du rapport."
    else:
        language_rule = "Redige dans la meme langue que le CONTENU ACTUEL."

    prompt = f"""Ameliore cette section de rapport avec les nouvelles donnees collectees.

REQUETE GLOBALE: "{query}"

SECTION A AMELIORER: {section_title}

CONTENU ACTUEL:
{current_with_urls}

NOUVELLES DONNEES COLLECTEES:
{enrichment_content}

MANQUES IDENTIFIES: {", ".join(enrichment_data.get("missing", []))}

Consignes:
1. {language_rule}
2. Enrichis le contenu actuel avec les nouvelles donnees (chiffres, dates, noms).
3. Conserve chaque citation [SOURCE:url] du contenu actuel, avec la meme URL.
4. Cite les nouvelles donnees au format [SOURCE:url], avec une URL presente dans les donnees ci-dessus. N'invente aucune URL.
5. Aucun nombre de paragraphes n'est impose : garde la longueur et la structure que le contenu exige.
"""
    schema = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": (
                    "Texte complet de la section enrichie, citations au format [SOURCE:url]."
                ),
            }
        },
        "required": ["content"],
        "additionalProperties": False,
    }

    def _unchanged(reason: str, **details: Any) -> Dict[str, Any]:
        ctx.diagnostics.note(
            "enrichissement.regeneration_v2_repli", title=section_title, raison=reason, **details
        )
        return current_section

    if not getattr(svc.llm_client, "supports_structured", False):
        return _unchanged("sortie_structuree_indisponible")

    try:
        result = await svc.llm_client.generate_structured(
            [{"role": "user", "content": prompt}],
            schema=schema,
            schema_name="section_regeneree",
            max_tokens=ctx.config.enrichment_regenerate_v2_max_tokens,
            temperature=ctx.config.enrichment_regenerate_temperature,
        )
    except Exception as e:
        logger.error(f"Erreur régénération section (v2): {e}")
        return _unchanged("erreur_llm", error=repr(e))

    new_content = result.get("content") if isinstance(result, dict) else None
    if not isinstance(new_content, str) or not new_content.strip():
        return _unchanged("contenu_vide")

    new_content, invented = strip_unknown_citations(new_content, allowed_urls)
    if invented:
        ctx.diagnostics.note(
            "enrichissement.citations_inventees_retirees",
            title=section_title,
            urls=invented,
        )

    lost = original_urls - cited_urls(new_content)
    if lost:
        return _unchanged("citations_perdues", urls=sorted(lost))

    updated = dict(current_section)
    updated["content"] = new_content.strip()
    metadata = dict(current_section.get("metadata") or {})
    if "word_count" in metadata:
        metadata["word_count"] = len(updated["content"].split())
    updated["metadata"] = metadata
    ctx.diagnostics.note(
        "enrichissement.regeneration_v2",
        title=section_title,
        data_chars=len(enrichment_content),
        citations_before=count_citations(current_with_urls),
        citations_after=count_citations(new_content),
    )
    logger.info(f"    Section enrichie (v2): {section_title}")
    return updated
