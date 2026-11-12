"""Client LLM substitut deterministe, pour la verification de bout en bout.

Aucun appel reseau : seul le LLM est remplace, la collecte (SearXNG,
extraction) reste reelle. Les reponses sont derivees du prompt, sans
scenario par test :
- plan : 3 sections dont les requetes viennent des mots de la consigne ;
- synthese : phrases EXTRAITES des donnees du prompt, citees [SOURCE:url]
  avec l'URL exacte fournie ;
- analyse de qualite : score 90 ;
- autres schemas : reponse minimale valide, construite depuis le schema.

Enregistrement : register_stub() (fournisseur "stub" de LLMFactory).
"""

import hashlib
import json
import re
from typing import Any

from app.core.llm.base import BaseLLMClient
from app.core.llm.factory import LLMFactory
from langchain_core.language_models import BaseChatModel

STUB_PROVIDER = "stub"

_STOPWORDS = frozenset(
    ["the", "a", "an", "of", "and", "or", "in", "on", "for", "to", "with", "by", "from", "is", "are", "was", "were", "what", "how", "why", "le", "la", "les", "un", "une", "des", "du", "de", "et", "ou", "en", "sur", "pour", "par", "avec", "dans", "est", "sont", "que", "qui"]
)
_SECTION_ANGLES = (
    ("Overview", "overview"),
    ("Key aspects", "main features"),
    ("Applications and outlook", "applications"),
)


def _prompt_of(messages: list[dict[str, Any]]) -> str:
    return "\n".join(str(m.get("content", "")) for m in messages)


def _query_of(prompt: str) -> str:
    match = re.search(r'(?:CONSIGNE|REQU[^:\n]{0,3}TE)\s*:\s*"(.+?)"\s*$', prompt, re.MULTILINE)
    if match:
        return match.group(1)
    return "general topic"


def _keywords(text: str, limit: int = 5) -> list[str]:
    words = [w for w in re.findall(r"[A-Za-z0-9À-ÿ+#.-]+", text) if len(w) > 2]
    kept = [w for w in words if w.lower() not in _STOPWORDS]
    return (kept or words or ["topic"])[:limit]


def _plan(prompt: str) -> dict[str, Any]:
    query = _query_of(prompt)
    words = _keywords(query)
    base = " ".join(words)
    sections = []
    for title, angle in _SECTION_ANGLES:
        sections.append(
            {
                "name": f"{title}: {' '.join(words[:3])}",
                "words_target": 300,
                "depth": "light",
                "objectives": [f"Present the {angle} of {base}"],
                "key_questions": [f"What are the {angle} of {base}?"],
                "search_terms": [f"{base} {angle}"],
                "source_axis": "generaliste",
            }
        )
    return {
        "complexity_analysis": {
            "overall_score": 2.0,
            "target_length": "standard",
            "estimated_words": 900,
            "justification": "stub",
        },
        "sections": sections,
        "narrative_flow": [
            {
                "from_section": sections[i]["name"],
                "to_section": sections[i + 1]["name"],
                "transition_type": "zoom-in",
                "rationale": "stub",
            }
            for i in range(len(sections) - 1)
        ],
        "search_strategy": {
            "total_sources_needed": 6,
            "sources_per_section": 2,
            "search_depth": "quick",
            "language": "en",
        },
    }


def _neutral(schema: dict[str, Any]) -> Any:
    """Valeur neutre conforme a un fragment de JSON Schema (champs requis)."""
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        props = schema.get("properties", {})
        return {k: _neutral(props[k]) for k in schema.get("required", []) if k in props}
    if kind == "array":
        return []
    if kind == "string":
        return "stub"
    if kind == "integer":
        return int(schema.get("minimum", 0))
    if kind == "number":
        return float(schema.get("minimum", 0))
    if kind == "boolean":
        return False
    return None


def _data_block(prompt: str) -> list[dict[str, Any]]:
    """Tableau JSON qui suit le titre 'DONNEES DISPONIBLES' dans le prompt."""
    match = re.search(r"DONN\S*ES DISPONIBLES[^\n]*\n", prompt)
    if not match:
        return []
    start = prompt.find("[", match.end())
    if start == -1:
        return []
    try:
        value, _ = json.JSONDecoder().raw_decode(prompt[start:])
    except ValueError:
        return []
    return [d for d in value if isinstance(d, dict)] if isinstance(value, list) else []


def _sentences(text: str) -> list[str]:
    clean = re.sub(r"\s+", " ", text)
    out = []
    for raw in re.split(r"(?<=[.!?])\s+", clean):
        sentence = raw.strip()
        if 50 <= len(sentence) <= 300 and sentence[-1] in ".!?" and "[" not in sentence:
            out.append(sentence)
    return out


def _synthesis(prompt: str) -> str:
    parts: list[str] = []
    for item in _data_block(prompt)[:5]:
        url = str(item.get("source", "")).strip()
        if not url:
            continue
        for sentence in _sentences(str(item.get("content", "")))[:2]:
            parts.append(f"{sentence.rstrip('.!? ')} [SOURCE:{url}].")
    if not parts:
        return "No usable source data was provided."
    return " ".join(parts)


class StubLLMClient(BaseLLMClient):
    """Substitut deterministe de client LLM (voir le module)."""

    supports_structured = True
    supports_embeddings = True
    supports_vision = False

    def __init__(
        self,
        api_key: str = "stub",
        base_url: str | None = None,
        model: str = "stub-llm",
        embed_dim: int = 16,
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, model=model)
        self.embed_dim = embed_dim

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        prompt = _prompt_of(messages)
        self._record_finish_reason("stop")
        if "DISPONIBLES" in prompt and "[SOURCE:" in prompt:
            return _synthesis(prompt)
        if '"overall_score"' in prompt and "sections_analysis" in prompt:
            return json.dumps({"overall_score": 90, "sections_analysis": [], "gaps_to_fill": []})
        return "Stub response."

    async def generate_structured(
        self,
        messages: list,
        schema: dict,
        schema_name: str = "response",
        **kwargs: Any,
    ) -> dict:
        self._record_finish_reason("stop")
        prompt = _prompt_of(messages)
        if schema_name == "research_plan":
            return _plan(prompt)
        if schema_name == "coherence_analysis":
            return {"improvements": [], "redundancies": [], "coherence_score": 90}
        if schema_name == "section_regeneree":
            return {"content": "Stub regenerated section."}
        return _neutral(schema)

    async def embed(self, texts: list, model: str = "qwen3-embedding-8b") -> list:
        vectors = []
        for text in texts:
            digest = hashlib.sha256(str(text).encode("utf-8")).digest()
            raw = (digest * (self.embed_dim // len(digest) + 1))[: self.embed_dim]
            vectors.append([(b - 127.5) / 127.5 for b in raw])
        return vectors

    def get_langchain_wrapper(self) -> BaseChatModel:
        raise NotImplementedError("StubLLMClient n'expose pas de wrapper LangChain")

    async def _aclose(self) -> None:
        return None


def register_stub() -> None:
    """Enregistre le fournisseur 'stub' dans LLMFactory."""
    LLMFactory.register(
        STUB_PROVIDER,
        lambda api_key, model, base_url, **options: StubLLMClient(
            api_key=api_key, base_url=base_url, model=model
        ),
    )
