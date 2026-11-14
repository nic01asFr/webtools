"""
Client LLM pour OpenAI API.
"""

import asyncio
import json
import logging
import re
from types import SimpleNamespace
from typing import Any

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI, BadRequestError

from .base import (
    STREAM_INTERRUPTED,
    TRUNCATION_REASONS,
    BaseLLMClient,
    LLMClientError,
    LLMEmptyContentError,
    LLMSchemaError,
    LLMTimeoutError,
    LLMTruncatedError,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 5
DEFAULT_ATTEMPT_TIMEOUT = 90.0
# Embeddings : appel court et non bloquant (la selection se replie sur les
# mots-cles en cas d'echec). Avec les valeurs de generation (90 s, 5 reprises),
# un point d'acces d'embeddings qui ne repond pas pouvait retenir une section
# jusqu'a six tentatives de 90 s avant ce repli.
EMBED_ATTEMPT_TIMEOUT = 30.0
EMBED_MAX_RETRIES = 1


async def _close_stream(stream: Any) -> None:
    """Ferme un flux SDK (AsyncStream.close) sans masquer l'erreur en cours."""
    close = getattr(stream, "close", None)
    if close is None:
        return
    try:
        result = close()
        if asyncio.iscoroutine(result):
            await result
    except Exception as e:  # noqa: BLE001 - la fermeture ne doit pas remplacer l'erreur d'origine
        logger.debug(f"Fermeture du flux LLM impossible : {e}")


def _cause_chain(error: BaseException) -> str:
    """
    "Connection error" est le message generique du SDK OpenAI : il masque la
    cause reelle (httpx.ConnectError, ReadTimeout, DNS, RemoteProtocolError...).
    Sans la chaine __cause__, un diagnostic part sur de fausses pistes.
    """
    cause = error.__cause__
    chain = []
    while cause is not None and len(chain) < 4:
        chain.append(f"{type(cause).__name__}: {cause}")
        cause = getattr(cause, "__cause__", None)
    return " <- ".join(chain) if chain else "aucune cause sous-jacente"


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_TAG = re.compile(r"</?think>", re.IGNORECASE)


def strip_reasoning(text: str | None) -> str | None:
    """
    Retire les traces de raisonnement du modele (<think>...</think>).

    Observe en production (qwen3, raisonnement pourtant desactive) : une balise
    </think> isolee apparaissait au milieu d'une phrase du rapport.
    """
    if not text:
        return text
    return _THINK_TAG.sub("", _THINK_BLOCK.sub("", text))


def _closed_client_error(error: BaseException) -> bool:
    """Vrai si l'erreur vient d'un client HTTP ferme ("client has been closed")."""
    seen: BaseException | None = error
    for _ in range(6):
        if seen is None:
            return False
        if "client has been closed" in str(seen):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


class OpenAILLMClient(BaseLLMClient):
    """
    Client pour l'API OpenAI (GPT-4, GPT-4o, etc.)
    """

    supports_structured = True
    supports_embeddings = True
    supports_vision = True

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        model: str = "gpt-4o",
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = DEFAULT_ATTEMPT_TIMEOUT,
        total_timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        stream: bool = False,
    ):
        """
        Initialise le client OpenAI.

        Args:
            api_key: Clé API OpenAI
            base_url: URL de base personnalisée (pour proxies ou providers compatibles)
            model: Nom du modèle OpenAI à utiliser
            max_retries: reessais du SDK par appel
            timeout: delai par tentative, en secondes
            total_timeout: echeance totale par appel, tentatives comprises
                (None : pas d'echeance globale)
            transport: transport httpx injecte (tests sans reseau)
            stream: generation en flux. Le delai par tentative s'applique
                alors a l'inactivite entre deux morceaux et non a toute la
                generation (voir _complete).
        """
        super().__init__(api_key=api_key, base_url=base_url, model=model)
        self.max_retries = max_retries
        self.attempt_timeout = timeout
        self.total_timeout = total_timeout
        self.stream = stream
        self._transport = transport

        # Client OpenAI asynchrone. research_deep enchaine des dizaines
        # d'appels sur plusieurs minutes avec le meme client - un pool de
        # connexions HTTP garde-vivantes (keep-alive) par defaut peut finir
        # par reutiliser une connexion perimee cote serveur/passerelle,
        # provoquant une "Connection error" meme sur un prompt minuscule
        # (observe : echecs avec 0 chunks selectionnes, donc prompt quasi
        # vide - la taille du prompt n'est pas en cause). On force un cycle
        # de vie de connexion court pour eviter la reutilisation de
        # connexions perimees, avec retry pour absorber le reste.
        self._build_client()

    def _build_client(self) -> None:
        """
        (Re)cree le client SDK et son transport HTTP.

        Isole dans une methode car le client doit pouvoir etre reconstruit :
        get_llm_client() renvoie une instance PARTAGEE par tout le service, et
        son transport httpx se retrouvait ferme en cours de pipeline sans que
        notre close() soit jamais appele (tracage en place, 0 appel observe) -
        vraisemblablement a la sortie d'un contexte asynchrone de Playwright /
        browser-use. Tous les appels suivants echouaient alors en
        "Cannot send a request, as the client has been closed", remonte en
        "Connection error" generique par le SDK.
        """
        http_kwargs: dict[str, Any] = {
            "limits": httpx.Limits(
                max_connections=20,
                max_keepalive_connections=5,
                keepalive_expiry=15.0
            ),
            "timeout": self.attempt_timeout,
        }
        if self._transport is not None:
            http_kwargs["transport"] = self._transport
        http_client = httpx.AsyncClient(**http_kwargs)
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            max_retries=self.max_retries,
            timeout=self.attempt_timeout,
            http_client=http_client
        )

    def _ensure_open(self) -> None:
        """
        Reconstruit le client si son transport a ete ferme.

        Auto-reparation plutot que simple detection : sur un client partage,
        une fermeture externe est toujours anormale mais ne doit pas rendre le
        service inutilisable jusqu'au redemarrage. Verifie avant chaque appel
        (cout negligeable : lecture d'un booleen).
        """
        try:
            transport_closed = getattr(self.client, "is_closed", False)
            if callable(transport_closed):
                transport_closed = transport_closed()
            inner = getattr(self.client, "_client", None)
            if inner is not None and getattr(inner, "is_closed", False):
                transport_closed = True
        except Exception:
            transport_closed = False

        if transport_closed:
            logger.warning(
                "Client LLM partage trouve ferme — reconstruction automatique "
                "(fermeture externe anormale, voir _build_client)"
            )
            self._build_client()

    def _prepare_call(self, kwargs: dict[str, Any]) -> tuple[Any, float | None]:
        """
        Extrait de kwargs les options propres au client (max_retries, timeout
        par tentative, total_timeout) et rend (client SDK configure, echeance
        totale). Ces options ne sont pas des parametres de create() : les
        laisser dans kwargs ferait echouer l'appel.
        """
        if "qwen" in self.model.lower() and "extra_body" not in kwargs:
            # Modeles Qwen (SSPCloud et autres deploiements) : desactiver le
            # mode raisonnement pour les appels de structuration/synthese
            # courte - sinon le modele peut epuiser tout son budget de tokens
            # dans sa chaine de pensee interne et ne jamais emettre de contenu
            # final (content: null, finish_reason: length).
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

        total_timeout = kwargs.pop("total_timeout", self.total_timeout)
        options: dict[str, Any] = {}
        if "max_retries" in kwargs:
            options["max_retries"] = kwargs.pop("max_retries")
        if "timeout" in kwargs:
            options["timeout"] = kwargs.pop("timeout")

        self._ensure_open()
        client = self.client.with_options(**options) if options else self.client
        return client, total_timeout

    async def _complete(self, client: Any, **create_kwargs: Any) -> Any:
        """
        Appel chat.completions, en flux si self.stream.

        Sans flux, le delai par tentative (90 s) borne toute la generation.
        Mesure sur le proxy SSPCloud (qwen3-6-35b-moe, novembre 2026) : environ
        10 jetons par seconde, premier jeton en 1,2 s. Un plan structure
        d'environ 2 000 jetons demande donc ~200 s : chaque tentative expirait
        a 90 s et etait relancee depuis zero, le plan n'aboutissait jamais et
        tout le rapport partait sur le plan de repli. En flux, le delai ne
        porte que sur l'inactivite entre deux morceaux ; l'echeance totale
        (total_timeout) reste la garde globale.

        Rend un objet de meme forme qu'une reponse non diffusee :
        choices[0].message.content et choices[0].finish_reason.
        """
        if not self.stream:
            return await client.chat.completions.create(**create_kwargs)

        stream = await client.chat.completions.create(stream=True, **create_kwargs)
        parts: list[str] = []
        finish_reason = None
        seen_choice = False
        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                seen_choice = True
                choice = chunk.choices[0]
                delta = getattr(choice, "delta", None)
                text = getattr(delta, "content", None) if delta is not None else None
                if text:
                    parts.append(text)
                if getattr(choice, "finish_reason", None):
                    finish_reason = choice.finish_reason
        finally:
            # Flux ferme dans tous les cas : sur une annulation (echeance
            # totale, delai global du pipeline) ou une erreur en cours de
            # lecture, la connexion restait ouverte et occupait le pool.
            await _close_stream(stream)
        if not seen_choice:
            return SimpleNamespace(choices=[])
        if parts and finish_reason is None:
            # Flux termine sans raison d'arret alors que du texte est arrive :
            # la connexion a ete fermee en cours de generation, le texte est
            # partiel. Signale comme une coupure (last_truncated vrai) plutot
            # qu'en erreur : c'est le contrat de generate pour une sortie
            # coupee, la redaction relance alors avec un plafond plus haut
            # puis coupe a la derniere phrase complete, une sortie structuree
            # au JSON incomplet leve LLMTruncatedError, et un serveur qui
            # n'enverrait jamais de finish_reason ne fait pas echouer tous
            # les appels.
            logger.warning(
                "Flux LLM termine sans finish_reason (modele %s) : sortie partielle",
                create_kwargs.get("model"),
            )
            finish_reason = STREAM_INTERRUPTED
        message = SimpleNamespace(content="".join(parts) if parts else None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
        )

    async def _call(self, kwargs: dict[str, Any], make: Any) -> Any:
        """
        Prepare puis execute un appel ; le rejoue une fois si le client HTTP a
        ete ferme pendant l'appel.

        Observe en production (12/11/2026) : le client partage etait trouve
        ferme a la fin de chaque tache de l'agent navigateur browser-use, qui
        appelle gc.collect() en se terminant. _ensure_open le reconstruit avant
        chaque appel, mais un appel deja en vol echouait ("Cannot send a
        request, as the client has been closed"), par exemple les embeddings
        d'une section. La cause exacte de la fermeture n'est pas etablie ; le
        rejeu unique rend le service insensible a elle.
        """
        saved = dict(kwargs)
        client, total_timeout = self._prepare_call(kwargs)
        try:
            return await self._with_deadline(make(client, kwargs), total_timeout)
        except Exception as e:
            if not _closed_client_error(e):
                raise
            logger.warning("Client LLM ferme pendant l'appel : reconstruction et rejeu unique")
            self._build_client()
            retry_kwargs = dict(saved)
            client, total_timeout = self._prepare_call(retry_kwargs)
            return await self._with_deadline(make(client, retry_kwargs), total_timeout)

    async def _with_deadline(self, awaitable: Any, total_timeout: float | None) -> Any:
        """Applique l'echeance totale (retries du SDK compris)."""
        if not total_timeout:
            return await awaitable
        try:
            return await asyncio.wait_for(awaitable, timeout=total_timeout)
        except TimeoutError:
            raise LLMTimeoutError(
                f"Echeance totale de {total_timeout:g}s depassee (modele {self.model})"
            )

    async def generate_with_vision(
        self,
        text: str,
        image_url: str,
        system_prompt: str | None = None,
        **kwargs
    ) -> str:
        """
        Genere une reponse en analysant une image (format vision standard
        OpenAI-compatible - fonctionne avec tout modele multimodal, y
        compris via un endpoint SSPCloud/Albert configure en mode "openai").
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": image_url}}
            ]
        })

        return await self.generate(messages, **kwargs)

    async def generate(
        self,
        messages: list[dict[str, str]],
        **kwargs
    ) -> str:
        """
        Génère une réponse avec OpenAI.

        Args:
            messages: Liste de messages au format {"role": "...", "content": "..."}
            **kwargs: Paramètres additionnels (temperature, max_tokens, etc.),
                plus max_retries, timeout (par tentative) et total_timeout.

        Returns:
            Contenu de la réponse générée. Une sortie coupee par max_tokens
            est rendue telle quelle ; last_finish_reason vaut alors "length".

        Raises:
            LLMClientError: erreur du fournisseur, contenu None ou vide
                (LLMEmptyContentError), echeance totale (LLMTimeoutError)
        """
        self._record_finish_reason(None)
        # Modele par appel (ex. verificateur distinct du redacteur), meme point d'acces.
        model = kwargs.pop("model", None) or self.model
        try:
            response = await self._call(
                kwargs,
                lambda client, kw: self._complete(client, model=model, messages=messages, **kw),
            )

            if not response.choices:
                raise LLMClientError("Réponse inattendue de l'API OpenAI")

            choice = response.choices[0]
            finish_reason = getattr(choice, "finish_reason", None)
            content = strip_reasoning(choice.message.content)
            self._record_finish_reason(finish_reason)
            if content is None or not content.strip():
                raise LLMEmptyContentError(
                    f"Contenu vide rendu par le modele {self.model} "
                    f"(finish_reason={finish_reason})"
                )
            return content

        except LLMClientError as e:
            logger.error(f"Echec generation OpenAI: {type(e).__name__}: {e}")
            raise
        except Exception as e:
            detail = _cause_chain(e)
            logger.error(
                f"Echec generation OpenAI: {type(e).__name__}: {e} | cause: {detail}"
            )
            raise LLMClientError(
                f"Erreur lors de la génération OpenAI: {e!s} [cause: {detail}]"
            ) from e

    async def embed(self, texts: list, model: str = "qwen3-embedding-8b") -> list:
        """
        Genere des embeddings (vecteurs) pour une liste de textes, via
        l'endpoint /embeddings compatible OpenAI. Utilise pour un scoring
        de pertinence par similarite semantique reelle plutot que par
        correspondance de mots-cles (qui rate synonymes/reformulations).
        """
        try:
            response = await self._call(
                {"timeout": EMBED_ATTEMPT_TIMEOUT, "max_retries": EMBED_MAX_RETRIES},
                lambda client, kw: client.embeddings.create(model=model, input=texts),
            )
        except LLMClientError:
            raise
        except Exception as e:
            raise LLMClientError(
                f"Erreur lors des embeddings OpenAI: {e!s} [cause: {_cause_chain(e)}]"
            ) from e
        return [item.embedding for item in response.data]

    async def generate_structured(
        self,
        messages: list,
        schema: dict,
        schema_name: str = "response",
        **kwargs
    ) -> dict:
        """
        Genere une reponse dont le format JSON est garanti par le serveur
        (response_format=json_schema, strict), plutot que d'esperer qu'un
        prompt texte suffise et de parser le resultat a la main (recherche
        de la premiere accolade, comptage de profondeur...). Cette derniere
        approche est fragile : un LLM peut ajouter du texte avant/apres le
        JSON, changer legerement la structure d'une generation a l'autre,
        ou etre coupe en cours de generation - toutes choses qui cassent un
        parsing manuel silencieusement.

        Args:
            messages: messages de la conversation
            schema: JSON Schema (properties, required, additionalProperties: false)
            schema_name: nom du schema (requis par l'API)

        Returns:
            dict deja parse et conforme au schema

        Raises:
            LLMTruncatedError: sortie coupee par max_tokens (augmenter max_tokens)
            LLMSchemaError: schema refuse par le serveur, ou sortie non conforme
            LLMEmptyContentError: contenu None ou vide
            LLMTimeoutError: echeance totale depassee
            LLMClientError: toute autre erreur du fournisseur
        """
        self._record_finish_reason(None)
        model = kwargs.pop("model", None) or self.model
        try:
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }
            response = await self._call(
                kwargs,
                lambda client, kw: self._complete(
                    client, model=model, messages=messages, response_format=response_format, **kw
                ),
            )
        except LLMClientError:
            raise
        except BadRequestError as e:
            # Le proxy refuse le schema (ou la requete) : sans cette trace, le
            # refus est indiscernable d'une panne reseau.
            logger.error(
                "Schema '%s' refuse par le serveur (HTTP 400) : %s", schema_name, e
            )
            raise LLMSchemaError(
                f"Schema '{schema_name}' refuse par le serveur: {e}"
            ) from e
        except Exception as e:
            detail = _cause_chain(e)
            logger.error(
                f"Echec sortie structuree OpenAI: {type(e).__name__}: {e} | cause: {detail}"
            )
            raise LLMClientError(
                f"Erreur lors de la sortie structurée OpenAI: {e!s} [cause: {detail}]"
            ) from e

        if not response.choices:
            raise LLMClientError("Réponse inattendue de l'API OpenAI (sortie structurée)")

        choice = response.choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        content = strip_reasoning(choice.message.content)
        self._record_finish_reason(finish_reason)
        truncated = finish_reason in TRUNCATION_REASONS

        if content is None or not content.strip():
            if truncated:
                raise LLMTruncatedError(
                    f"Sortie structuree '{schema_name}' vide, coupee par max_tokens "
                    f"(modele {self.model})"
                )
            raise LLMEmptyContentError(
                f"Contenu vide pour la sortie structuree '{schema_name}' "
                f"(finish_reason={finish_reason})"
            )

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            if truncated:
                raise LLMTruncatedError(
                    f"Sortie structuree '{schema_name}' coupee par max_tokens "
                    f"(JSON incomplet: {e}) - augmenter max_tokens"
                ) from e
            raise LLMSchemaError(
                f"Sortie structuree '{schema_name}' invalide malgre le schema force: {e}"
            ) from e

    def get_langchain_wrapper(self) -> BaseChatModel:
        """
        Retourne un wrapper LangChain pour browser-use.

        OpenAI est nativement supporté par LangChain via ChatOpenAI. La cle et
        l'URL sont passees en arguments : aucune ecriture dans os.environ.

        Returns:
            ChatOpenAI compatible avec browser-use
        """
        kwargs = {
            "api_key": self.api_key,
            "model": self.model
        }

        if self.base_url:
            kwargs["base_url"] = self.base_url

        return ChatOpenAI(**kwargs)

    async def _aclose(self) -> None:
        """Ferme le client SDK et son pool HTTP."""
        await self.client.close()
