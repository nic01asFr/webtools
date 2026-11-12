"""Faux client LLM : reponses scenarisees, appels enregistres."""

import hashlib
import re
from dataclasses import dataclass, field
from re import Pattern
from typing import Any

from app.core.llm.base import BaseLLMClient, LLMClientError
from langchain_core.language_models import BaseChatModel


class FakeLLMError(LLMClientError):
    """Appel non scenarise, ou erreur injectee par un scenario."""


@dataclass
class LLMCall:
    """Un appel recu par le faux LLM."""

    kind: str  # "generate" | "structured" | "embed"
    messages: list[dict[str, Any]] = field(default_factory=list)
    prompt: str = ""
    schema_name: str | None = None
    schema: dict | None = None
    kwargs: dict[str, Any] = field(default_factory=dict)
    texts: list[str] = field(default_factory=list)


# Une reponse scenarisee est : une valeur (str pour generate, dict pour
# generate_structured), une exception (levee), une fonction(LLMCall) -> valeur
# (ou exception, levee), ou une liste de ces elements (consommee dans l'ordre
# a chaque appel ; le dernier element se repete).
Response = Any


def _resolve(response: Response, call: LLMCall, state: dict[str, int], key: str) -> Any:
    if isinstance(response, list):
        index = min(state.get(key, 0), len(response) - 1)
        state[key] = state.get(key, 0) + 1
        response = response[index]
    if callable(response) and not isinstance(response, type):
        response = response(call)
    if isinstance(response, BaseException):
        raise response
    return response


def _as_pattern(pattern: str | Pattern[str]) -> Pattern[str]:
    return pattern if isinstance(pattern, re.Pattern) else re.compile(pattern, re.DOTALL)


class FakeLLM(BaseLLMClient):
    """Double de OpenAILLMClient, sans reseau.

    Memes methodes et signatures que le client reel : generate,
    generate_structured, embed, close.

    Scenarisation
    - on_structured(schema_name, reponse) : reponse a generate_structured
      pour ce nom de schema (argument schema_name, ex. "research_plan").
    - on_text(motif, reponse) : reponse a generate pour le premier motif
      (regex, re.DOTALL, re.search) qui correspond au prompt (contenu de tous
      les messages joint par des sauts de ligne). Ordre d'enregistrement.
    Un appel sans scenario est enregistre dans `unmatched` puis leve
    FakeLLMError (le pipeline avale souvent l'exception : verifier
    `unmatched` en fin de test).

    Enregistrement : `calls` (tous les appels), `unmatched`, calls_for().
    """

    # Capacites declarees comme le client OpenAI reel (lot V1-7).
    supports_structured = True
    supports_embeddings = True
    supports_vision = False

    def __init__(self, embed_dim: int = 16) -> None:
        super().__init__(api_key="fake", base_url=None, model="fake-llm")
        self.embed_dim = embed_dim
        self.calls: list[LLMCall] = []
        self.unmatched: list[LLMCall] = []
        self._structured: dict[str, Response] = {}
        self._text: list[tuple[Pattern[str], Response]] = []
        self._cursor: dict[str, int] = {}

    def on_structured(self, schema_name: str, response: Response) -> "FakeLLM":
        self._structured[schema_name] = response
        return self

    def on_text(self, pattern: str | Pattern[str], response: Response) -> "FakeLLM":
        self._text.append((_as_pattern(pattern), response))
        return self

    def calls_for(
        self,
        kind: str | None = None,
        schema_name: str | None = None,
        pattern: str | Pattern[str] | None = None,
    ) -> list[LLMCall]:
        """Appels enregistres, filtres par type, nom de schema ou motif de prompt."""
        regex = _as_pattern(pattern) if pattern is not None else None
        return [
            c
            for c in self.calls
            if (kind is None or c.kind == kind)
            and (schema_name is None or c.schema_name == schema_name)
            and (regex is None or regex.search(c.prompt))
        ]

    @staticmethod
    def _prompt_of(messages: list[dict[str, Any]]) -> str:
        return "\n".join(str(m.get("content", "")) for m in messages)

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        call = LLMCall("generate", list(messages), self._prompt_of(messages), kwargs=dict(kwargs))
        self.calls.append(call)
        for index, (regex, response) in enumerate(self._text):
            if regex.search(call.prompt):
                return _resolve(response, call, self._cursor, f"text:{index}")
        self.unmatched.append(call)
        raise FakeLLMError(f"generate non scenarise : {call.prompt[:80]!r}")

    async def generate_structured(
        self,
        messages: list,
        schema: dict,
        schema_name: str = "response",
        **kwargs: Any,
    ) -> dict:
        call = LLMCall(
            "structured",
            list(messages),
            self._prompt_of(messages),
            schema_name=schema_name,
            schema=schema,
            kwargs=dict(kwargs),
        )
        self.calls.append(call)
        if schema_name in self._structured:
            return _resolve(
                self._structured[schema_name], call, self._cursor, f"schema:{schema_name}"
            )
        self.unmatched.append(call)
        raise FakeLLMError(f"generate_structured non scenarise : schema {schema_name!r}")

    async def embed(self, texts: list, model: str = "qwen3-embedding-8b") -> list:
        """Vecteurs deterministes (hash du texte) : meme texte, meme vecteur ;
        textes differents, vecteurs quasi orthogonaux."""
        self.calls.append(LLMCall("embed", texts=list(texts), kwargs={"model": model}))
        vectors = []
        for text in texts:
            digest = hashlib.sha256(str(text).encode("utf-8")).digest()
            raw = (digest * (self.embed_dim // len(digest) + 1))[: self.embed_dim]
            vectors.append([(b - 127.5) / 127.5 for b in raw])
        return vectors

    def get_langchain_wrapper(self) -> BaseChatModel:
        raise NotImplementedError("FakeLLM n'expose pas de wrapper LangChain")

    async def _aclose(self) -> None:
        return None
