"""
Interface de base pour les clients LLM.
Permet l'abstraction des différents providers (Albert, OpenAI, Anthropic, etc.)
"""

import logging
from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)


class LLMClientError(Exception):
    """Exception levée en cas d'erreur avec un client LLM."""


class LLMEmptyContentError(LLMClientError):
    """Le modele a repondu sans contenu (None ou vide)."""


class LLMTruncatedError(LLMClientError):
    """Sortie structuree coupee par max_tokens : le JSON est inutilisable."""


class LLMSchemaError(LLMClientError):
    """Schema refuse par le serveur, ou sortie non conforme au schema."""


class LLMTimeoutError(LLMClientError):
    """Echeance totale de l'appel depassee (toutes tentatives comprises)."""


# Raison d'arret du dernier appel, par contexte asynchrone (tache) et non par
# instance : l'instance est partagee par tout le processus, un attribut simple
# serait ecrase par les appels concurrents.
_last_finish_reason: ContextVar[str | None] = ContextVar(
    "llm_last_finish_reason", default=None
)

# Raison d'arret posee par le client quand un flux se termine avec du contenu
# mais sans finish_reason (connexion fermee en cours de generation) : le texte
# est partiel, a traiter comme une sortie coupee.
STREAM_INTERRUPTED = "interrupted"

# Valeurs normalisees de finish_reason qui signalent une sortie coupee.
TRUNCATION_REASONS = frozenset({"length", STREAM_INTERRUPTED})


@dataclass(frozen=True)
class GenerationResult:
    """Resultat enrichi d'une generation : texte et raison d'arret."""

    text: str
    finish_reason: str | None

    @property
    def truncated(self) -> bool:
        return self.finish_reason in TRUNCATION_REASONS


class BaseLLMClient(ABC):
    """
    Interface abstraite pour tous les clients LLM.
    Chaque provider (Albert, OpenAI, Anthropic) doit implémenter cette interface.

    Contrat commun :
    - generate rend un texte non vide ou leve LLMClientError (contenu None ou
      vide compris).
    - Une sortie coupee par max_tokens N'EST PAS une exception dans generate
      (une section presque complete reste exploitable) : l'information est
      disponible via `last_finish_reason` / `last_truncated` (propres au
      contexte asynchrone courant) ou via `generate_result` qui rend un
      GenerationResult.
    - Les capacites optionnelles sont declarees par les attributs supports_*.
    """

    supports_structured: bool = False
    supports_embeddings: bool = False
    supports_vision: bool = False

    def __init__(self, api_key: str, base_url: str | None = None, model: str = ""):
        """
        Initialise le client LLM.

        Args:
            api_key: Clé API pour le provider
            base_url: URL de base pour l'API (optionnel)
            model: Nom du modèle à utiliser
        """
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        # Echeance totale par appel en secondes (None = pas d'echeance globale).
        self.total_timeout: float | None = None
        # Vrai pour l'instance rendue par get_llm_client() : close() est alors
        # sans effet, le cycle de vie appartient a l'application.
        self._shared = False

    @property
    def last_finish_reason(self) -> str | None:
        """Raison d'arret du dernier appel dans ce contexte asynchrone."""
        return _last_finish_reason.get()

    @property
    def last_truncated(self) -> bool:
        """Vrai si le dernier appel de ce contexte a ete coupe par max_tokens."""
        return _last_finish_reason.get() in TRUNCATION_REASONS

    def _record_finish_reason(self, reason: str | None) -> None:
        _last_finish_reason.set(reason)
        if reason in TRUNCATION_REASONS:
            logger.warning(
                "Sortie LLM coupee (modele %s, finish_reason=%s)",
                self.model, reason,
            )

    @abstractmethod
    async def generate(
        self,
        messages: list[dict[str, str]],
        **kwargs
    ) -> str:
        """
        Génère une réponse à partir d'une liste de messages.

        Args:
            messages: Liste de messages au format {"role": "user/assistant/system", "content": "..."}
            **kwargs: Paramètres additionnels spécifiques au provider

        Returns:
            Contenu de la réponse générée (jamais None ni vide)

        Raises:
            LLMClientError: erreur du fournisseur, contenu vide, echeance
        """

    async def generate_result(
        self,
        messages: list[dict[str, str]],
        **kwargs
    ) -> GenerationResult:
        """Comme generate, mais rend aussi la raison d'arret."""
        _last_finish_reason.set(None)
        text = await self.generate(messages, **kwargs)
        return GenerationResult(text=text, finish_reason=_last_finish_reason.get())

    @abstractmethod
    def get_langchain_wrapper(self) -> BaseChatModel:
        """
        Retourne un wrapper compatible LangChain pour browser-use.

        Browser-use nécessite un modèle compatible LangChain (BaseChatModel).
        Cette méthode retourne un wrapper qui adapte le client LLM actuel
        pour être utilisé avec browser-use.

        Returns:
            Instance de BaseChatModel compatible avec browser-use
        """

    @abstractmethod
    async def _aclose(self) -> None:
        """Libere reellement les connexions du client."""

    async def close(self) -> None:
        """
        Ferme proprement les connexions du client.

        Sans effet sur l'instance partagee de get_llm_client() : de nombreux
        appelants la recuperent puis appellent close(), et la fermer casserait
        tous les autres. Utiliser close_llm_client() (ou force_close) pour la
        fermeture reelle a l'arret du service.
        """
        if self._shared:
            logger.debug("close() ignore : client LLM partage")
            return
        await self._aclose()

    async def force_close(self) -> None:
        """Ferme reellement le client, meme partage."""
        await self._aclose()
