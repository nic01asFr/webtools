"""Etape 2.4 : redaction d'une section a partir des passages retenus."""

import json
import logging
from typing import Dict, List

from app.research.context import ExecutionContext
from app.research.stages.services import StageServices
from app.research.stages.crossref import cross_reference_data
from app.research.stages.queries import build_search_query
from app.research.stages.select import semantic_chunk_selection

logger = logging.getLogger(__name__)


async def synthesize_single_section(
    svc: StageServices,
    query: str,
    section_name: str,
    section_config: Dict,
    enriched_data: List[Dict],
    context: ExecutionContext,
) -> str:
    """
    PHASE 2.4: Synthèse d'une section unique.

    Génère le contenu rédigé de la section en utilisant:
    - Les données enrichies
    - Les objectifs de la section
    - La profondeur cible
    """
    logger.info(f"       ✍️  Synthèse section '{section_name}'")

    depth = section_config.get("depth", "moderate")
    cfg = context.config
    words_target = section_config.get("words_target", cfg.synthesis_default_words_target)
    objectives = section_config.get("objectives", [])
    key_questions = section_config.get("key_questions", [])

    # Ajustement local et leger de l'ambition de CETTE section, au
    # regard du materiau reellement disponible - le plan reste globalement
    # fige (pas de replanification complete), mais n'est pas rigide au
    # point d'imposer un objectif de mots deconnecte de ce qui a ete
    # trouve. Sans ca, une section pauvre en sources produit un contenu
    # dilue/generique pour atteindre un objectif arbitraire, ou echoue.
    n_sources = len(enriched_data)
    if n_sources == 0:
        words_target = min(words_target, cfg.synthesis_no_source_words_cap)
        logger.info(
            f"       ⚠️  Aucune source pour '{section_name}', objectif réduit à {words_target} mots"
        )
    elif (
        n_sources <= cfg.synthesis_few_sources_max
        and words_target > cfg.synthesis_few_sources_words_threshold
    ):
        original_target = words_target
        words_target = max(
            cfg.synthesis_few_sources_words_floor,
            words_target // cfg.synthesis_few_sources_words_divisor,
        )
        depth = "light"  # coherence : ne pas demander 6-10 paragraphes "deep" sur 250 mots
        logger.info(
            f"       ⚠️  Peu de sources ({n_sources}) pour '{section_name}', objectif ajusté {original_target}→{words_target} mots, profondeur réduite"
        )

    # Sélection sémantique adaptée
    # Requete bornee plutot que la consigne brute concatenee : le scoring
    # compare les mots de la requete au contenu des chunks, une consigne de
    # 350 caracteres noie les termes discriminants dans du remplissage.
    scoring_query = build_search_query(
        svc,
        query=query,
        section_name=section_name,
        search_terms=(section_config or {}).get("search_terms"),
    )
    selected_data = await semantic_chunk_selection(
        svc,
        all_data=enriched_data,
        query=scoring_query,
        # Budget de MATIERE, independant de la longueur du texte a
        # ecrire. Auparavant words_target * 10 : une section de 300 mots
        # ne recevait que 3 000 caracteres de sources — indexer la
        # matiere premiere sur la taille du produit fini n'a aucun sens,
        # et c'est ce qui affamait les sections courtes.
        # 16 000 caracteres : assez pour porter plusieurs passages
        # substantiels de documents differents, tout en restant loin du
        # point ou le contexte se degrade (Chroma, juillet 2025 : la
        # performance baisse quand le contexte s'allonge). Verifie par
        # ailleurs : ce client accepte des prompts de 30 000 caracteres
        # sans difficulte.
        max_chars=cfg.synthesis_source_budget_chars,
        depth=depth,
        context=context,
    )

    # Charge utile du bloc DONNEES DISPONIBLES. Par defaut les passages
    # complets (cross_references, metadata, compteurs compris) ; en mode
    # minimal, seulement ce que la redaction cite.
    if context.config.prompt_payload_minimal:
        prompt_payload = [
            {
                "source": d.get("source", ""),
                "title": d.get("title", ""),
                "content": d.get("content", ""),
                "corroboration_count": d.get("corroboration_count", 0),
            }
            for d in selected_data
        ]
    else:
        prompt_payload = selected_data

    # Construire le prompt de synthèse
    depth_instructions = {
        "light": "Rédige 2-3 paragraphes concis allant à l'essentiel.",
        "moderate": "Rédige 4-6 paragraphes équilibrés et informatifs.",
        "deep": "Rédige 6-10 paragraphes détaillés explorant tous les aspects.",
    }.get(depth, "Rédige un contenu équilibré.")

    # Apercu des sections DEJA REDIGEES (celles qui precedent dans la
    # boucle sequentielle) - sans nouvel appel LLM, juste les premiers
    # caracteres du contenu deja ecrit. Objectif : que la redaction
    # de CETTE section evite proactivement la redondance avec ce qui
    # a deja ete dit, plutot que de compter uniquement sur la Phase 3
    # (detection de redondance) pour la corriger apres coup - priorite
    # a la coherence construite, pas seulement patchee en aval.
    already_written = []
    for other_name, other_data in context.final_content["sections"].items():
        other_content = other_data.get("content", "")
        if other_content and "[Erreur" not in other_content:
            already_written.append(
                f'- "{other_name}": {other_content[: cfg.synthesis_previous_sections_chars].strip()}...'
            )

    already_written_block = ""
    if already_written:
        already_written_block = f"""
SECTIONS DÉJÀ RÉDIGÉES DANS CE RAPPORT (pour éviter les répétitions, construis sur ce qui a déjà été dit plutôt que de le reformuler) :
{chr(10).join(already_written[: cfg.synthesis_previous_sections_max])}
"""

    # Langue de redaction : la meme que celle des recherches, decidee par
    # detection sur la consigne. Sans cette consigne, le modele redigeait
    # systematiquement en francais, y compris sur une consigne anglaise —
    # or le respect de la consigne est l'un des quatre criteres notes par
    # RACE : un rapport dans la mauvaise langue echoue sur ce critere
    # quelle que soit la qualite du fond.
    _lang = context.search_language or "all"
    _lang_names = {
        "fr": "français",
        "en": "anglais",
        "es": "espagnol",
        "de": "allemand",
        "it": "italien",
        "pt": "portugais",
    }
    if _lang in _lang_names:
        lang_instruction = (
            f"\n⚠️ LANGUE : rédige INTÉGRALEMENT en {_lang_names[_lang]}, "
            f"la langue de la demande. Les sources peuvent être dans "
            f"d'autres langues : traduis ce que tu en reprends.\n"
        )
    else:
        lang_instruction = "\n⚠️ LANGUE : rédige dans la langue de la demande ci-dessous.\n"

    prompt = f"""{lang_instruction}
Rédige la section "{section_name}" pour répondre à: "{query}"

OBJECTIFS DE CETTE SECTION:
{chr(10).join(f"- {obj}" for obj in objectives)}

QUESTIONS CLÉS À EXPLORER:
{chr(10).join(f"- {q}" for q in key_questions)}

INSTRUCTIONS:
{depth_instructions}
{already_written_block}
DONNÉES DISPONIBLES ({len(selected_data)} sources):
{json.dumps(prompt_payload, indent=2, ensure_ascii=False)}

RÈGLES:
1. CITATIONS — chaque affirmation factuelle porte sa source, en fin de
   phrase, au format [SOURCE:url_complète] (crochet fermant OBLIGATOIRE).
   Une section de 400 mots doit contenir plusieurs citations, pas une seule
   en conclusion. Un paragraphe sans citation n'est pas verifiable : soit
   il reformule une source et doit la citer, soit il n'apporte rien.
   Copie l'URL exactement telle qu'elle apparaît dans les données, sans la
   raccourcir ni l'abréger.
2. La longueur est une CONSÉQUENCE de la matière disponible, jamais un
   objectif. Écris ce que les sources permettent d'établir, puis arrête-toi.
   Mieux vaut une section dense et courte qu'une section étirée pour
   atteindre un volume.
3. Structure et PRÉSENTATION DES DONNÉES (critère noté explicitement) :
   La prose reste le mode principal, mais mets en forme ce qui se lit mal en
   paragraphe :
   - un TABLEAU markdown dès que tu compares 3 éléments ou plus sur les mêmes
     critères, ou que tu alignes des séries chiffrées (années, montants,
     parts) — c'est plus clair et plus vérifiable qu'une énumération noyée
     dans une phrase ;
   - une LISTE courte pour des éléments réellement parallèles (conditions,
     étapes, facteurs) ;
   - un SOUS-TITRE (###) quand la section couvre deux angles nettement
     distincts.
   Ne fabrique pas de tableau pour du contenu qui n'en est pas : deux
   chiffres dans une phrase restent une phrase.

   EXEMPLE de ce qui est attendu quand tu compares trois éléments :

   Les trois langages divergent nettement sur la gestion mémoire [SOURCE:url].

   | Critère | Rust | Go | C++ |
   |---|---|---|---|
   | Sûreté mémoire | garantie à la compilation | ramasse-miettes | manuelle |
   | Latence | prévisible | pauses GC | prévisible |
   | Maturité | écosystème jeune | stable | très mature |

   Cette différence explique pourquoi Rust est retenu pour les systèmes
   temps réel [SOURCE:url].

   Le tableau ne remplace pas l'analyse : il la précède ou la suit.
4. Ton: informatif, précis, fluide
5. Ne répète pas ce qui est déjà couvert par les autres sections listées ci-dessus

FIDÉLITÉ DES DONNÉES CHIFFRÉES (règle prioritaire) :
Tout chiffre, pourcentage, date ou montant doit être RECOPIÉ EXACTEMENT
depuis la source, avec son année de référence. Ne reformule jamais une
donnée de mémoire, ne l'arrondis pas, ne l'harmonise pas entre sources.
  ✗ "en 2020, la proportion atteignait 28,7%"  alors que la source dit
  ✓ "en 2021, la proportion était de 28,9% [SOURCE:url]"
Si deux sources donnent des valeurs différentes, cite les deux avec leurs
années respectives plutôt que d'en fabriquer une moyenne.
Si tu n'es pas certain du chiffre exact, écris la tendance sans chiffre —
une affirmation vague et vraie vaut mieux qu'un chiffre précis et faux.
Donne toujours les chiffres avec leur unité et leur année, dans la phrase
elle-même — pas en note ni en aparté.

Rédige uniquement le contenu (pas de titre de section, pas de métadonnées).
"""

    # Diagnostic : une synthese qui rend 13-17 mots au lieu de ~400 vient
    # soit d'un prompt sans donnees exploitables, soit d'un refus du
    # modele. Journaliser la taille reelle du prompt et le volume de
    # donnees transmis permet de trancher sans relancer un run complet.
    # Pas de re-limitation ici : semantic_chunk_selection a deja applique
    # max_chars ET max_chunks_target. Un plafond supplementaire annulerait
    # silencieusement le budget accorde — c'est exactement le defaut
    # corrige plus haut (max_chunks_target=4 plafonnait a 4 000 chars
    # quel que soit max_chars).
    _payload_chars = sum(len(str(d.get("content", ""))) for d in selected_data)

    # REFUS DE REDIGER SANS SOURCE.
    #
    # Sans donnees, le LLM redigeait quand meme depuis ses connaissances
    # internes : des rapports de 1 200 a 1 500 mots sortaient avec ZERO
    # citation et zero entree de bibliographie. C'est exactement
    # l'hallucination que tout le pipeline (corroboration, scoring,
    # citations [SOURCE:]) existe pour empecher - un rapport non source
    # est pire qu'une section absente, parce qu'il est indiscernable d'un
    # rapport documente pour qui le lit.
    #
    # L'ajustement d'ambition (objectif reduit a 150 mots) attenuait le
    # symptome sans traiter la cause. On refuse desormais franchement, et
    # la section est marquee pour que l'echec remonte au lieu d'etre
    # invisible.
    if not selected_data or _payload_chars < cfg.synthesis_min_payload_chars:
        logger.warning(
            f"       ⛔ Section '{section_name}' non rédigée : "
            f"{len(selected_data)} source(s), {_payload_chars} chars de données — "
            f"rédiger sans matière produirait du contenu non sourcé"
        )
        return (
            "[SECTION_SANS_SOURCE] Aucune donnée exploitable n'a pu être collectée "
            "pour cette section."
        )

    logger.info(
        f"       📝 Prompt synthèse '{section_name}': {len(prompt)} chars, "
        f"{len(selected_data)} source(s), {_payload_chars} chars de données, "
        f"objectif {words_target} mots (max_tokens={int(words_target * cfg.synthesis_tokens_per_word)})"
    )

    # Reessai avec attente progressive : la synthese est le seul endroit du
    # pipeline ou un echec LLM est irrattrapable (sans texte, pas de
    # section). Les "Connection error" observes sur SSPCloud sont
    # intermittents et souvent resolus au 2e essai ; sans reessai, une
    # coupure de quelques secondes vidait TOUT le rapport - 5 sections
    # perdues d'un coup, pour un rapport rendu avec success=true.
    last_error = None
    max_attempts = cfg.synthesis_max_attempts
    for attempt in range(max_attempts):
        try:
            response = await svc.llm_client.generate(
                [{"role": "user", "content": prompt}],
                # 4.5 jetons/mot et non 2.5.
                #
                # Le ratio 2.5 coupait les sections en pleine phrase :
                # 30% des rapports du run v9 se terminaient sur un mot
                # tronque, contre 0% sur la reference. Mesure directe :
                # des sections de 576, 635, 913 et 1003 mots pour un
                # budget de 1000 a 1375 jetons.
                #
                # Deux causes cumulees : le francais consomme plus de
                # jetons par mot que l'anglais (accents, elisions), et
                # depuis que le budget de sources est passe de 3 000 a
                # 16 000 caracteres le modele a bien plus de matiere,
                # donc redige plus long que words_target.
                # words_target reste un OBJECTIF indicatif ; max_tokens
                # doit etre un plafond de securite, pas une contrainte
                # qui mutile la derniere phrase.
                max_tokens=int(words_target * cfg.synthesis_tokens_per_word),
                temperature=cfg.synthesis_temperature,
            )
            if svc.llm_client.last_truncated:
                context.diagnostics.note(
                    "selection.synthese_tronquee",
                    section=section_name,
                    max_tokens=int(words_target * cfg.synthesis_tokens_per_word),
                )
            if response and response.strip():
                # Controle de fidelite numerique : signale les chiffres
                # ecrits qui n'apparaissent dans aucune source de la
                # section. Diagnostic FACT : les citations invalides ne
                # viennent pas d'hallucination mais de derive a la
                # reformulation (28,7% en 2020 ecrit pour 28,9% en 2021).
                # On journalise sans corriger : un chiffre peut etre
                # legitimement calcule sans figurer tel quel.
                try:
                    from app.utils.numeric_fidelity import check_numeric_fidelity

                    _src_text = " ".join(str(d.get("content", "")) for d in selected_data)
                    _fid = check_numeric_fidelity(response, _src_text)
                    if _fid.get("unmatched_count"):
                        logger.warning(
                            f"       🔢 '{section_name}': {_fid['unmatched_count']}/"
                            f"{_fid['numbers_written']} chiffre(s) absent(s) des sources "
                            f"(fidélité {_fid['fidelity_rate']}) — {_fid['unmatched'][:5]}"
                        )
                        context.diagnostics.note(
                            "selection.chiffres_absents",
                            section=section_name,
                            nombre=_fid["unmatched_count"],
                            ecrits=_fid["numbers_written"],
                        )
                except Exception as e:
                    logger.debug(f"Contrôle de fidélité non appliqué : {e}")
                    context.diagnostics.note(
                        "selection.controle_fidelite_indisponible",
                        section=section_name,
                        erreur=str(e),
                    )

                # Un refus du modele (passages hors sujet) n'est pas du
                # contenu : la section suit le chemin des sections sans
                # source, retiree du rapport et signalee (app/research/refusal.py).
                from app.research.refusal import is_refusal

                if is_refusal(response):
                    logger.warning(
                        f"       ⛔ Section '{section_name}' : le modele refuse de rediger "
                        f"sur les passages retenus — traitee comme sans source"
                    )
                    context.diagnostics.note("selection.refus_du_modele", section=section_name)
                    return (
                        "[SECTION_SANS_SOURCE] Les passages retenus ne traitent pas de "
                        "cette section (refus du modele)."
                    )

                if cfg.deep_verify_sections:
                    response = await verify_section(
                        svc, context, section_name, prompt, response, selected_data,
                        max_tokens=int(words_target * cfg.synthesis_tokens_per_word),
                        temperature=cfg.synthesis_temperature,
                    )

                word_count = len(response.split())
                if word_count < 50 and words_target >= 150:
                    logger.warning(
                        f"       ⚠️  Réponse anormalement courte ({word_count} mots "
                        f"pour {words_target} demandés) — brut: {response.strip()[:300]!r}"
                    )
                logger.info(f"       → {word_count} mots générés")
                return response.strip()
            last_error = "reponse vide"
        except Exception as e:
            last_error = e

        if attempt < max_attempts - 1:
            delay = cfg.synthesis_retry_backoff_base**attempt  # 1s, 2s par defaut
            logger.warning(
                f"       ⏳ Synthèse '{section_name}' échouée "
                f"({last_error}) — nouvel essai dans {delay}s"
            )
            await svc.sleep(delay)

    # Echec definitif : marqueur reconnaissable en aval (permet de
    # compter les sections perdues et de ne pas rendre un rapport vide
    # avec success=true, cf. final_assembly).
    logger.error(
        f"       ❌ Synthèse '{section_name}' abandonnée après {max_attempts} essais: {last_error}"
    )
    return f"[SYNTHESE_ECHOUEE] Section '{section_name}' non générée ({last_error})."


async def verify_section(
    svc: StageServices,
    context: ExecutionContext,
    section_name: str,
    prompt: str,
    response: str,
    selected_data: List[Dict],
    max_tokens: int,
    temperature: float,
) -> str:
    """Verifie chaque phrase de la section contre les passages transmis au
    modele ; reecrit une fois la section, critique jointe, si une phrase est
    partielle, contredite ou non soutenue (C89, drapeau deep_verify_sections).

    Une verification ou une reecriture en echec, vide ou refusee garde la
    section telle quelle : la verification ne doit jamais faire perdre une
    section.
    """
    from app.research.answer_check import FAULTY, check_answer, correction_request
    from app.research.refusal import is_refusal

    by_url: Dict[str, List[str]] = {}
    for d in selected_data:
        url = str(d.get("source") or "")
        if url:
            by_url.setdefault(url, []).append(str(d.get("content") or ""))
    sources = [(url, "\n[...]\n".join(parts)) for url, parts in by_url.items()]
    try:
        verdicts = await check_answer(svc.llm_client, response, sources, label_style="url")
    except Exception as e:
        logger.warning(f"       Vérification de '{section_name}' impossible : {e}")
        context.diagnostics.note(
            "selection.verification_impossible", section=section_name, erreur=type(e).__name__
        )
        return response
    faulty = [v for v in verdicts if v.verdict in FAULTY]
    context.diagnostics.note(
        "selection.verification", section=section_name, phrases=len(verdicts), fautives=len(faulty)
    )
    if not faulty:
        return response
    try:
        corrected = await svc.llm_client.generate(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": response},
                {"role": "user", "content": correction_request(faulty, "[SOURCE:url]")},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except Exception as e:
        logger.warning(f"       Réécriture de '{section_name}' impossible : {e}")
        context.diagnostics.note(
            "selection.reecriture_impossible", section=section_name, erreur=type(e).__name__
        )
        return response
    if not corrected or not corrected.strip() or is_refusal(corrected):
        context.diagnostics.note("selection.reecriture_rejetee", section=section_name)
        return response
    logger.info(f"       ✔ Section '{section_name}' réécrite : {len(faulty)} phrase(s) fautive(s)")
    context.diagnostics.note("selection.section_reecrite", section=section_name, fautives=len(faulty))
    return corrected.strip()


async def write_sections(
    svc: StageServices,
    ctx: ExecutionContext,
    query: str,
    section_targets: Dict[str, Dict],
    extracted_by_section: Dict[str, List[Dict]],
) -> None:
    """Etapes 2.3 et 2.4 : croise puis redige chaque section, dans l'ordre du plan.

    Le contenu et les donnees de chaque section sont ecrits dans
    ctx.final_content["sections"].
    """
    # Étape 2.3+2.4 (croisement + synthèse) : sequentiel, car le
    # croisement doit voir les sections precedentes deja finalisees.
    for section_name, section_config in section_targets.items():
        logger.info(f"\n  ✍️  Section (croisement+synthèse): {section_name}")
        extracted_data = extracted_by_section.get(section_name, [])

        enriched_data = await cross_reference_data(
            svc, section_name=section_name, new_data=extracted_data, context=ctx
        )
        logger.info(f"     ✓ {len(enriched_data)} données enrichies")

        section_content = await synthesize_single_section(
            svc,
            query=query,
            section_name=section_name,
            section_config=section_config,
            enriched_data=enriched_data,
            context=ctx,
        )
        logger.info(f"     ✓ Contenu généré: {len(section_content.split())} mots")

        # Stocker dans le canvas. setdefault plutot qu'un acces direct :
        # le nom de section utilise ici peut diverger legerement (ponctuation,
        # espaces) de celui pose par initialize_sections() plus tot dans le
        # pipeline, provoquant sinon un KeyError qui fait echouer tout le
        # rapport alors que le contenu de CETTE section a bien ete genere.
        ctx.final_content["sections"].setdefault(
            section_name,
            {"raw_data": [], "content": "", "metadata": {"data_count": 0, "sources": []}},
        )
        ctx.final_content["sections"][section_name]["content"] = section_content
        ctx.final_content["sections"][section_name]["raw_data"] = enriched_data
