"""Selection des passages : score de pertinence, scoring semantique et budget de caracteres."""

import logging
from typing import Dict, List, Optional

from app.research.context import ExecutionContext, cosine_similarity
from app.research.pipeline_config import PipelineConfig
from app.research.stages.services import StageServices


logger = logging.getLogger(__name__)


def score_relevance(
    svc: StageServices,
    content: str,
    query: str,
    structured_data: Optional[Dict] = None,
    config: PipelineConfig | None = None,
) -> float:
    """
    Score la pertinence d'un chunk de contenu par rapport à la requête.

    Args:
        content: Contenu textuel à scorer
        query: Requête utilisateur
        structured_data: Données structurées extraites (boost le score)

    Returns:
        Score de pertinence (0-100)
    """
    if not content:
        return 0.0

    cfg = config if config is not None else svc.config
    score = 0.0
    content_lower = content.lower()
    query_lower = query.lower()

    # 1. Keyword matching (max 40 points)
    query_words = set(query_lower.split())
    query_words = {w for w in query_words if len(w) > 3}  # Mots > 3 chars

    if query_words:
        matched_words = sum(1 for word in query_words if word in content_lower)
        # La normalisation par len(query_words) rendait le score
        # INVERSEMENT PROPORTIONNEL a la precision de la demande : sur une
        # consigne de 350 caracteres (~50 mots significatifs), il fallait
        # qu'un chunk contienne 44% de ces mots pour franchir le seuil de
        # 35. Un chunk valide plafonnait donc a 30 points (20 longueur +
        # 10 chiffres) et etait systematiquement rejete — sections vides.
        # Ca passait inapercu sur des sujets courts type "SearXNG moteur".
        # On sature desormais sur un nombre de correspondances ABSOLU :
        # 8 mots distincts retrouves valent le maximum, quelle que soit la
        # longueur de la consigne.
        keyword_score = min(
            cfg.selection_keyword_max_points,
            (matched_words / cfg.selection_keyword_match_saturation)
            * cfg.selection_keyword_max_points,
        )
        score += keyword_score

    # 2. Structured data boost (max 30 points)
    if structured_data:
        num_count = len(structured_data.get("numerical", []))
        temp_count = len(structured_data.get("temporal", []))
        ent_count = len(structured_data.get("entities", []))

        # Plus de données structurées = plus pertinent
        struct_score = min(
            cfg.selection_structured_max_points,
            (num_count * cfg.selection_structured_numerical_weight)
            + (temp_count * cfg.selection_structured_temporal_weight)
            + (ent_count * cfg.selection_structured_entity_weight),
        )
        score += struct_score

    # 3. Content length quality (max 20 points)
    # Ni trop court (< 500 chars) ni trop long (> 5000 chars)
    content_len = len(content)
    len_min = cfg.selection_length_ideal_min_chars
    len_max = cfg.selection_length_ideal_max_chars
    len_points = cfg.selection_length_max_points
    if len_min <= content_len <= len_max:
        length_score = len_points
    elif content_len < len_min:
        length_score = (content_len / len_min) * len_points
    else:  # > len_max
        length_score = max(
            cfg.selection_length_floor_points,
            len_points - ((content_len - len_max) / cfg.selection_length_decay_chars),
        )
    score += length_score

    # 4. Presence of numbers/dates (max 10 points)
    import re

    numbers = re.findall(r"\d+(?:[.,]\d+)?", content)
    dates = re.findall(r"\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4}", content)

    data_score = min(
        cfg.selection_numbers_max_points,
        len(numbers) + len(dates) * cfg.selection_dates_weight,
    )
    score += data_score

    return min(100.0, score)


async def semantic_chunk_selection(
    svc: StageServices,
    all_data: List[Dict],
    query: str,
    max_chars: int | None = None,
    depth: str = "moderate",
    min_score_threshold: float = None,
    context: Optional["ExecutionContext"] = None,
    section: Optional[str] = None,
) -> List[Dict]:
    """
    Sélectionne intelligemment les chunks les plus pertinents selon la profondeur demandée.

    APPROCHE QUALITATIVE:
    - Au lieu de fixer une longueur arbitraire, on adapte les critères de sélection
    - Profondeur "light": peu de chunks, seulement les plus pertinents
    - Profondeur "deep": plus de chunks, exploration large

    Args:
        all_data: Toutes les données collectées
        query: Requête utilisateur pour contexte
        max_chars: Budget maximum de caractères (selection_default_max_chars si None)
        depth: "light" | "moderate" | "deep" - Profondeur d'analyse attendue
        min_score_threshold: Score minimum pour inclure un chunk (auto si None)
        context: contexte de la requete (cache d'embeddings, diagnostics,
            configuration) ; sans contexte, cache local et aucune note

    Returns:
        Liste triée des chunks les plus pertinents
    """
    cfg = context.config if context is not None else svc.config
    if max_chars is None:
        max_chars = cfg.selection_default_max_chars

    # Filtrer les items valides d'abord (necessaire pour le batch d'embeddings)
    valid_items = [item for item in all_data if isinstance(item, dict) and item.get("content")]

    # Scoring semantique par embeddings, en complement du scoring
    # mots-cles existant : capture les synonymes/reformulations qu'un
    # simple "le mot exact apparait-il dans le texte" ne peut pas voir.
    # Repli silencieux sur le scoring mots-cles seul si l'appel
    # d'embedding echoue (jamais bloquant pour la selection).
    semantic_scores = {}
    embedding_cache = context.embedding_cache if context is not None else {}
    if not svc.llm_client.supports_embeddings:
        if valid_items and context is not None:
            context.diagnostics.note(
                "selection.repli_mots_cles",
                etape="selection",
                raison="embeddings non supportes",
            )
    elif valid_items:
        try:
            from app.research.embedding_cache import embed_with_cache

            vectors = await embed_with_cache(
                svc.llm_client,
                embedding_cache,
                [query] + [item["content"] for item in valid_items],
            )
            query_vec = vectors[0]
            for item, vec in zip(valid_items, vectors[1:]):
                semantic_scores[id(item)] = cosine_similarity(query_vec, vec)
            logger.info(
                f"  🧠 Scoring sémantique par embeddings : {len(semantic_scores)} chunks scorés"
            )
        except Exception as e:
            logger.warning(
                f"Embeddings indisponibles pour la selection semantique, repli sur mots-cles seuls: {e}"
            )
            if context is not None:
                context.diagnostics.note(
                    "selection.repli_mots_cles",
                    etape="selection",
                    raison=f"embeddings en erreur : {e}",
                )

    scored_items = []

    for item in valid_items:
        content = item.get("content", "")

        # Score mots-cles + donnees structurees (existant)
        structured_data = item.get("structured_data")
        keyword_score = score_relevance(svc, content, query, structured_data, config=cfg)

        # Score semantique (0-1 -> 0-100) vs la requete
        sem = semantic_scores.get(id(item))

        # Bonus de corroboration : un chunk confirme par des sources
        # independantes (calcule dans _cross_reference_data, cf.
        # corroboration_count) merite de remonter, meme si son score
        # de pertinence brut est un peu plus faible - c'est le meme
        # principe que le croisement de sources en fact-checking.
        corrob = min(item.get("corroboration_count", 0), cfg.selection_corroboration_cap) / float(
            cfg.selection_corroboration_cap
        )  # plafonne a selection_corroboration_cap sources

        if sem is not None:
            # Densite factuelle : 4e composante, en BONUS additif plutot
            # qu'en redistribution des poids.
            #
            # Le scoring de pertinence mesure si un fragment PARLE DU BON
            # SUJET ; il ne mesure pas s'il contient des faits
            # VERIFIABLES. Un paragraphe d'introduction qui reformule le
            # sujet score tres bien (mots-cles + similarite) sans porter
            # aucune donnee citable ; un tableau statistique score moins
            # bien alors qu'il porte l'information qu'on veut citer.
            # C'est une cause plausible du taux de citations validees de
            # 61% : on retenait de la prose generale, et le modele
            # completait les chiffres manquants de memoire.
            #
            # EN BONUS et non en poids : un contenu explicatif legitime
            # (ex. le fonctionnement du borrow checker de Rust) ne
            # contient aucun chiffre et ne doit pas etre penalise. La
            # densite fait REMONTER les fragments citables, elle
            # n'ecarte personne.
            # Plafonne a 10 points sur 100 : assez pour departager deux
            # fragments de pertinence voisine, trop peu pour faire
            # remonter un fragment hors sujet.
            fact_bonus = 0.0
            try:
                from app.utils.factual_density import factual_density

                fact_bonus = (
                    factual_density(item.get("content", ""))["score"] * cfg.selection_fact_bonus_max
                )
            except Exception as e:
                if context is not None:
                    context.diagnostics.note(
                        "selection.densite_factuelle_indisponible", erreur=str(e)
                    )

            score = (
                (keyword_score * cfg.selection_weight_keyword)
                + (sem * 100 * cfg.selection_weight_semantic)
                + (corrob * 100 * cfg.selection_weight_corroboration)
                + fact_bonus
            )
        else:
            score = (keyword_score * cfg.selection_weight_keyword_only) + (
                corrob * 100 * cfg.selection_weight_corroboration
            )

        scored_items.append({"score": score, "item": item, "content_length": len(content)})

    # Trier par score décroissant
    scored_items.sort(key=lambda x: x["score"], reverse=True)

    # Adapter seuil et stratégie selon profondeur
    #
    # Ces cibles ont ete calibrees quand un "chunk" etait un DOCUMENT
    # entier tronque a 3 000 caracteres. Depuis le decoupage, un chunk
    # est un fragment de ~2 000 caracteres : il en faut plusieurs pour
    # couvrir un meme document, et les anciennes valeurs plafonnaient
    # mecaniquement la matiere (4 chunks x 2 000 = 4 000 chars, quel que
    # soit le budget max_chars accorde).
    # Doublees pour que max_chars redevienne la contrainte effective.
    if min_score_threshold is None:
        if depth == "light":
            min_score_threshold = cfg.selection_min_score_light
            max_chunks_target = cfg.selection_max_chunks_light
        elif depth == "deep":
            min_score_threshold = cfg.selection_min_score_deep
            max_chunks_target = cfg.selection_max_chunks_deep
        else:  # moderate
            min_score_threshold = cfg.selection_min_score_moderate
            max_chunks_target = cfg.selection_max_chunks_moderate
    else:
        max_chunks_target = cfg.selection_max_chunks_explicit_threshold

    # Sélectionner les meilleurs chunks
    selected = []
    total_chars = 0
    chunks_selected = 0
    max_per_source = (
        context.config.max_chunks_per_source if context is not None else 0
    )  # 0 sans contexte : pas de plafond
    per_source: Dict[str, int] = {}

    for scored in scored_items:
        # Arrêter si on a atteint le nombre cible ET que le score diminue trop
        if chunks_selected >= max_chunks_target and scored["score"] < min_score_threshold:
            break

        # Filtrer les chunks avec score trop faible
        if scored["score"] < min_score_threshold:
            continue

        item = scored["item"]
        content_len = scored["content_length"]

        # Plafond par source (0 = aucun) : un seul document long ne doit
        # pas remplir tout le budget.
        if max_per_source > 0:
            source_key = str(item.get("source", ""))
            if per_source.get(source_key, 0) >= max_per_source:
                continue
            per_source[source_key] = per_source.get(source_key, 0) + 1

        # Vérifier si on peut ajouter ce chunk
        if total_chars + content_len > max_chars:
            # Pour profondeur deep, essayer de prendre au moins un extrait
            if depth == "deep":
                remaining = max_chars - total_chars
                if remaining > cfg.selection_deep_min_remaining_chars:  # budget restant suffisant
                    # Truncate ce chunk pour fit
                    item_copy = item.copy()
                    item_copy["content"] = item["content"][:remaining]
                    selected.append(item_copy)
                    total_chars += remaining
                    chunks_selected += 1
            break

        selected.append(item)
        total_chars += content_len
        chunks_selected += 1

    # DEGRADATION GRACIEUSE : si AUCUN chunk ne franchit le seuil alors que
    # des chunks valides existent, on retient quand meme les meilleurs.
    # Rediger une section sur des sources moyennement pertinentes vaut
    # toujours mieux que rendre une section vide — et un rapport vide
    # sortait jusqu'ici avec success=true. Le seuil sert a classer, il ne
    # doit pas pouvoir aneantir la production.
    if not selected and scored_items:
        fallback_n = min(cfg.selection_fallback_chunks, len(scored_items))
        for scored in scored_items[:fallback_n]:
            selected.append(scored["item"])
            total_chars += scored["content_length"]
        logger.warning(
            f"  🛟 Repli: {fallback_n} chunk(s) retenus malgré un score sous "
            f"le seuil — une section vide serait pire qu'une section imparfaite"
        )
        # Visible dans la reponse : mesure du 12/11/2026, 10 des 24 sections
        # passees par ce repli ont ete refusees par le modele (passages hors
        # sujet), contre 2 sur 28 apres une selection normale.
        if context is not None:
            context.diagnostics.note(
                "selection.repli_sous_seuil",
                section=section,
                meilleur=round(max(s["score"] for s in scored_items), 1),
                seuil=min_score_threshold,
                passages=fallback_n,
            )

    # Le log precedent calculait la moyenne sur scored_items[:len(selected)] :
    # quand rien n'est selectionne, la tranche est vide et il affichait
    # "score moyen: 0.0" — laissant croire que les chunks scoraient zero,
    # alors qu'ils scoraient simplement sous le seuil. Diagnostic fausse.
    # On affiche desormais le meilleur score REELLEMENT obtenu et le seuil
    # applique : c'est ce couple qui explique un rejet.
    best_score = max((s["score"] for s in scored_items), default=0.0)
    mean_selected = (
        sum(s["score"] for s in scored_items[: len(selected)]) / len(selected) if selected else 0.0
    )
    logger.info(
        f"  📊 Sélection intelligente: {len(selected)}/{len(all_data)} chunks, "
        f"{total_chars:,} chars (retenus: {mean_selected:.1f}/100, "
        f"meilleur disponible: {best_score:.1f}, seuil: {min_score_threshold:.0f})"
    )
    if not selected and scored_items:
        logger.warning(
            f"  ⚠️  Aucun chunk retenu alors que {len(scored_items)} étaient "
            f"disponibles (meilleur: {best_score:.1f} < seuil {min_score_threshold:.0f})"
        )

    return selected
