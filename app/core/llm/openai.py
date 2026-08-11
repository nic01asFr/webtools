"""
Client LLM pour OpenAI API.
"""

import json
import logging
from typing import List, Dict, Any, Optional
from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from openai import AsyncOpenAI

from .base import BaseLLMClient, LLMClientError

logger = logging.getLogger(__name__)


class OpenAILLMClient(BaseLLMClient):
    """
    Client pour l'API OpenAI (GPT-4, GPT-4o, etc.)
    """

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: str = "gpt-4o"
    ):
        """
        Initialise le client OpenAI.

        Args:
            api_key: Clé API OpenAI
            base_url: URL de base personnalisée (pour proxies ou providers compatibles)
            model: Nom du modèle OpenAI à utiliser
        """
        super().__init__(api_key=api_key, base_url=base_url, model=model)

        # Client OpenAI asynchrone. research_deep enchaine des dizaines
        # d'appels sur plusieurs minutes avec le meme client - un pool de
        # connexions HTTP garde-vivantes (keep-alive) par defaut peut finir
        # par reutiliser une connexion perimee cote serveur/passerelle,
        # provoquant une "Connection error" meme sur un prompt minuscule
        # (observe : echecs avec 0 chunks selectionnes, donc prompt quasi
        # vide - la taille du prompt n'est pas en cause). On force un cycle
        # de vie de connexion court pour eviter la reutilisation de
        # connexions perimees, avec retry pour absorber le reste.
        import httpx
        http_client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=5,
                keepalive_expiry=15.0
            ),
            timeout=90.0
        )
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            max_retries=5,
            timeout=90.0,
            http_client=http_client
        )

    async def generate_with_vision(
        self,
        text: str,
        image_url: str,
        system_prompt: Optional[str] = None,
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
        messages: List[Dict[str, str]],
        **kwargs
    ) -> str:
        """
        Génère une réponse avec OpenAI.

        Args:
            messages: Liste de messages au format {"role": "...", "content": "..."}
            **kwargs: Paramètres additionnels (temperature, max_tokens, etc.)

        Returns:
            Contenu de la réponse générée
        """
        try:
            # Modeles Qwen (SSPCloud et autres deploiements) : desactiver le
            # mode raisonnement pour les appels de structuration/synthese
            # courte - sinon le modele peut epuiser tout son budget de tokens
            # dans sa chaine de pensee interne et ne jamais emettre de contenu
            # final (content: null, finish_reason: length).
            if "qwen" in self.model.lower() and "extra_body" not in kwargs:
                kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

            # max_retries n'est pas un parametre de create() mais du client -
            # le surcharger par appel necessite with_options() plutot que de
            # le laisser filtrer dans kwargs (qui ferait echouer create()).
            client = self.client
            if "max_retries" in kwargs:
                client = self.client.with_options(max_retries=kwargs.pop("max_retries"))

            response = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                **kwargs
            )

            if response.choices and len(response.choices) > 0:
                return response.choices[0].message.content
            else:
                raise LLMClientError("Réponse inattendue de l'API OpenAI")

        except Exception as e:
            # "Connection error" est le message generique du SDK OpenAI : il
            # masque la cause reelle (httpx.ConnectError, ReadTimeout, DNS,
            # RemoteProtocolError...). Sans la chaine __cause__, un diagnostic
            # part sur de fausses pistes - teste isolement, ce client passait
            # 15 appels enchaines et 30 000 caracteres de prompt sans un echec.
            cause = e.__cause__
            chain = []
            while cause is not None and len(chain) < 4:
                chain.append(f"{type(cause).__name__}: {cause}")
                cause = getattr(cause, "__cause__", None)
            detail = " <- ".join(chain) if chain else "aucune cause sous-jacente"
            logger.error(
                f"Echec generation OpenAI: {type(e).__name__}: {e} | cause: {detail}"
            )
            raise LLMClientError(f"Erreur lors de la génération OpenAI: {str(e)} [cause: {detail}]")

    async def embed(self, texts: list, model: str = "qwen3-embedding-8b") -> list:
        """
        Genere des embeddings (vecteurs) pour une liste de textes, via
        l'endpoint /embeddings compatible OpenAI. Utilise pour un scoring
        de pertinence par similarite semantique reelle plutot que par
        correspondance de mots-cles (qui rate synonymes/reformulations).
        """
        response = await self.client.embeddings.create(model=model, input=texts)
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
        """
        if "qwen" in self.model.lower() and "extra_body" not in kwargs:
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

        client = self.client
        if "max_retries" in kwargs:
            client = self.client.with_options(max_retries=kwargs.pop("max_retries"))

        response = await client.chat.completions.create(
            model=self.model,
            messages=messages,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema
                }
            },
            **kwargs
        )

        if not response.choices or len(response.choices) == 0:
            raise LLMClientError("Réponse inattendue de l'API OpenAI (sortie structurée)")

        content = response.choices[0].message.content
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMClientError(f"Sortie structuree invalide malgre le schema force: {e}")

    def get_langchain_wrapper(self) -> BaseChatModel:
        """
        Retourne un wrapper LangChain pour browser-use.

        OpenAI est nativement supporté par LangChain via ChatOpenAI.

        Returns:
            ChatOpenAI compatible avec browser-use
        """
        import os
        # browser-use valide la presence de OPENAI_API_KEY dans l'environnement
        # independamment des kwargs passes au client - on la fixe explicitement
        # pour que la config ne depende pas d'une variable d'environnement externe.
        os.environ["OPENAI_API_KEY"] = self.api_key
        if self.base_url:
            os.environ["OPENAI_BASE_URL"] = self.base_url

        kwargs = {
            "api_key": self.api_key,
            "model": self.model
        }

        if self.base_url:
            kwargs["base_url"] = self.base_url

        return ChatOpenAI(**kwargs)

    async def close(self):
        """
        Ferme le client OpenAI.

        ATTENTION : get_llm_client() renvoie une instance PARTAGEE par tout le
        service. Fermer ce client le rend inutilisable pour tous les appels
        suivants du processus ("Cannot send a request, as the client has been
        closed", remonte en generique "Connection error" par le SDK).
        Le cycle de vie appartient a l'application, pas a un appelant isole.
        La trace ci-dessous identifie l'appelant en cas de fermeture
        inattendue.
        """
        import traceback
        caller = "".join(traceback.format_stack()[-4:-1]).strip()
        logger.warning(f"Fermeture du client LLM PARTAGE demandee par:\n{caller}")
        await self.client.close()
