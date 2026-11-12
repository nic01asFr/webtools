"""Etape 2.2 : extraction du contenu des sources d'une section."""

import logging
from typing import Dict, List

from app.api.models import ExtractionOptions
from app.research.context import ExecutionContext
from app.research.stages.services import StageServices


logger = logging.getLogger(__name__)


async def section_extraction_phase(
    svc: StageServices, section_name: str, section_data: Dict, context: ExecutionContext
) -> List[Dict]:
    """
    PHASE 2.2: Extraction du contenu des sources découvertes.

    Retourne liste de contenus extraits et nettoyés.
    """
    logger.info(f"       📥 Extraction sources pour '{section_name}'")

    sources = section_data.get("sources", [])
    urls = [s["url"] for s in sources]

    if not urls:
        return []

    # Utiliser webextractor - reutilise l'instance partagee de
    # l'orchestrateur (svc.extractor_manager) plutot que d'en recreer
    # une nouvelle a chaque section, ce qui permettra un futur cache
    # au niveau du manager de beneficier a toutes les sections/requetes.
    try:
        extractor_manager = svc.extractor_manager
        # extract_images, extract_links et clean_html etaient passes ici
        # sans exister dans ExtractionOptions : ignores en silence, puis
        # refuses depuis que le modele interdit les champs inconnus (C60).
        cfg = context.config
        options = ExtractionOptions(
            use_agent=True,
            headless=True,
            rss_tier_root_only=cfg.rss_tier_root_only,
            single_llm_pass_enabled=cfg.single_llm_pass_enabled,
        )

        # Extraire chaque URL séquentiellement, en reutilisant le cache
        # partage si cette URL a deja ete extraite pour une autre section
        # (frequent : un meme article pertinent pour plusieurs sections).
        extracted_data = []
        # Plafond d'URL extraites : la valeur demandee par la requete
        # (V3-3) prime ; sinon, avec le drapeau, il suit la profondeur deja
        # appliquee a la recherche (3, 5 ou 8) ; sinon 5 (evite le timeout).
        if cfg.max_sources_per_section:
            url_cap = cfg.max_sources_per_section
        elif cfg.extraction_cap_follows_depth:
            url_cap = len(urls)
        else:
            url_cap = cfg.extraction_url_cap_default
        for url in urls[:url_cap]:
            try:
                if url in context.extraction_cache:
                    result = context.extraction_cache[url]
                    logger.info(f"       ♻️  Reutilisation cache extraction pour {url}")
                else:
                    result = await extractor_manager.extract(
                        url=url, llm_client=svc.llm_client, options=options
                    )
                    context.extraction_cache[url] = result

                if result.success and result.content:
                    # DECOUPAGE au lieu de TRONCATURE.
                    #
                    # Auparavant : result.content[:3000]. Un article de
                    # recherche de 100 000 caracteres perdait 97% de son
                    # contenu DES L'EXTRACTION, avant tout scoring — et le
                    # chiffre precis cite par le rapport se trouvait
                    # souvent dans la partie jetee. C'est la cause
                    # mecanique du taux de citations valides de 61% : le
                    # modele voyait le debut d'un article et completait le
                    # reste avec ce qu'il savait du sujet.
                    #
                    # Desormais le document entier est decoupe en chunks
                    # qui se chevauchent ; la selection semantique
                    # (_semantic_chunk_selection) choisit ensuite les plus
                    # pertinents. La TOTALITE du document est donc
                    # candidate, quelle que soit sa taille.
                    from app.utils.chunking import document_to_chunk_items

                    _items = document_to_chunk_items(
                        source=url,
                        title=result.title or "",
                        content=result.content,
                        v2=cfg.chunking_v2,
                    )
                    extracted_data.extend(_items)
                    if len(_items) > 1:
                        logger.info(
                            f"          ✂️  {url[:55]} : {len(result.content):,} chars "
                            f"→ {len(_items)} chunks"
                        )

                    # Tracker l'extraction
                    if url not in context.discovered_sources:
                        context.discovered_sources.append(url)
                    context.add_step(
                        "webextractor",
                        f"extract_content_for_{section_name}",
                        url,
                        {"title": result.title, "content_length": len(result.content)},
                        True,
                    )
                else:
                    context.add_step(
                        "webextractor", f"extract_content_for_{section_name}", url, None, False
                    )
            except Exception as url_error:
                logger.warning(f"       ⚠️ Extraction failed for {url}: {url_error}")
                context.diagnostics.note(
                    "collecte.extraction_url_echec",
                    section=section_name,
                    url=url,
                    error=str(url_error),
                )
                context.add_step(
                    "webextractor",
                    f"extract_content_for_{section_name}",
                    url,
                    str(url_error),
                    False,
                )
                continue

        logger.info(f"       → {len(extracted_data)}/{len(urls)} sources extraites")
        return extracted_data

    except Exception as e:
        logger.error(f"       ❌ Erreur extraction: {e}")
        context.diagnostics.note(
            "collecte.extraction_section_echec", section=section_name, error=str(e)
        )
        return []
