"""Etape 2.3 : croisement des sources (corroboration inter-sections par embeddings)."""

import logging
from typing import Dict, List

from app.research.context import ExecutionContext, cosine_similarity, extract_domain
from app.research.stages.services import StageServices


logger = logging.getLogger(__name__)


async def cross_reference_data(
    svc: StageServices, section_name: str, new_data: List[Dict], context: ExecutionContext
) -> List[Dict]:
    """
    PHASE 2.3: Croisement avec données existantes - a deux niveaux.

    1. Niveau source : meme URL deja citee ailleurs (signal faible -
       c'est la meme source, pas une confirmation independante).
    2. Niveau contenu : similarite semantique entre le contenu de CE
       chunk et des chunks precedents venant d'un DOMAINE DIFFERENT
       (signal fort - deux redactions independantes disent la meme
       chose = corroboration reelle). Une similarite extreme (>0.92)
       est au contraire suspecte (reprise verbatim d'un communique,
       pas une confirmation independante) et n'est pas comptee.
    """
    logger.info(f"       🔗 Croisement données pour '{section_name}'")

    CORROBORATION_MIN = context.config.corroboration_min_similarity
    CORROBORATION_MAX = (
        context.config.corroboration_max_similarity
    )  # au-dela : probable reprise verbatim, pas une vraie confirmation

    enriched = []

    # Embeddings des nouveaux chunks, avec cache partage (evite un
    # recalcul si _semantic_chunk_selection traite le meme contenu ensuite)
    new_embeddings = {}
    if not svc.llm_client.supports_embeddings:
        if new_data:
            context.diagnostics.note(
                "selection.repli_mots_cles",
                etape="croisement",
                section=section_name,
                raison="embeddings non supportes",
            )
    elif new_data:
        try:
            from app.research.embedding_cache import embed_with_cache

            vectors = await embed_with_cache(
                svc.llm_client,
                context.embedding_cache,
                [dp.get("content", "") for dp in new_data],
            )
            for dp, vec in zip(new_data, vectors):
                new_embeddings[id(dp)] = vec
        except Exception as e:
            logger.warning(f"       Embeddings indisponibles pour le croisement semantique: {e}")
            context.diagnostics.note(
                "selection.repli_mots_cles",
                etape="croisement",
                section=section_name,
                raison=f"embeddings en erreur : {e}",
            )

    for data_point in new_data:
        enriched_point = data_point.copy()
        enriched_point["cross_references"] = []
        source_domain = extract_domain(data_point.get("source", ""))
        vec = new_embeddings.get(id(data_point))

        # Niveau 1 : meme URL reutilisee dans une autre section
        for other_section, section_content in context.final_content["sections"].items():
            if other_section == section_name:
                continue
            for existing_data in section_content.get("raw_data", []):
                if isinstance(existing_data, dict) and existing_data.get(
                    "source"
                ) == data_point.get("source"):
                    enriched_point["cross_references"].append(
                        {
                            "section": other_section,
                            "type": "meme_source",
                            "note": "Même source utilisée",
                        }
                    )

        # Niveau 2 : corroboration semantique par une source independante
        # (domaine different, contenu similaire mais pas identique)
        corroborating_domains = set()
        if vec:
            for prior in context.all_extracted_chunks:
                if prior["domain"] == source_domain or not prior.get("embedding"):
                    continue
                sim = cosine_similarity(vec, prior["embedding"])
                if CORROBORATION_MIN <= sim <= CORROBORATION_MAX:
                    corroborating_domains.add(prior["domain"])
                    enriched_point["cross_references"].append(
                        {
                            "section": prior["section"],
                            "type": "corroboration_semantique",
                            "note": f"Confirmé indépendamment par {prior['domain']} (similarité {sim:.2f})",
                        }
                    )

        enriched_point["corroboration_count"] = len(corroborating_domains)
        enriched.append(enriched_point)

        # Alimente l'historique global pour les corroborations futures
        context.all_extracted_chunks.append(
            {
                "domain": source_domain,
                "section": section_name,
                "embedding": vec,
            }
        )

    cross_ref_count = sum(1 for e in enriched if e.get("cross_references"))
    corroborated_count = sum(1 for e in enriched if e.get("corroboration_count", 0) > 0)
    if cross_ref_count > 0:
        logger.info(
            f"       → {cross_ref_count} données avec croisements, {corroborated_count} corroborées par une source indépendante"
        )

    return enriched
