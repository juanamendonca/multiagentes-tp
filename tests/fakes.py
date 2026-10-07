"""Fakes compartidos para tests unitarios (sin red).

``ScriptedChatModel`` es un chat model de LangChain que devuelve respuestas guionadas en
orden y registra cada llamada:

- ``invoke(...)`` devuelve el siguiente elemento de ``responses`` como ``AIMessage``
  (si es ``str``) o tal cual (si ya es un ``BaseMessage``).
- ``with_structured_output(Schema)`` devuelve un runnable que, en cada llamada, toma el
  siguiente elemento de ``responses``: si es una instancia de ``BaseModel`` la devuelve, si es
  ``dict`` la valida con ``Schema``. Un elemento que es una ``Exception`` se lanza.
- ``calls`` es la lista de prompts recibidos (texto plano, mensajes unidos por ``\\n``),
  así que ``len(model.calls)`` es la cantidad de llamadas y se puede verificar el contenido
  de cada prompt (p. ej. que un reintento incluya el feedback de error).

Si se agotan las respuestas, se lanza ``AssertionError`` (más llamadas de las esperadas).
Las llamadas resueltas desde la caché de LangChain no llegan a ``_generate`` y no se cuentan.

Iteración 4: ``FakeEmbeddings`` (determinísticos, dimensión configurable, cuenta lotes),
``FakeVectorStore`` (``VectorStore`` en memoria con prefijos y fallas inyectables) y
``FakePineconeClient``/``FakePineconeIndex`` (forma del SDK ``pinecone``, para probar
``ensure_index`` y ``PineconeVectorStore`` sin red).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterator, Mapping, Sequence
from types import SimpleNamespace
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompt_values import PromptValue
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, Field

from services.vectorstore import Match, VectorRecord


def _prompt_text(value: LanguageModelInput | Sequence[BaseMessage]) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, PromptValue):
        return value.to_string()
    if isinstance(value, BaseMessage):
        return value.text
    return "\n".join(m.text if isinstance(m, BaseMessage) else str(m) for m in value)


class ScriptedChatModel(BaseChatModel):
    """Chat model fake con respuestas guionadas y contador de llamadas."""

    responses: list[Any] = Field(default_factory=list)
    calls: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        # Estable entre llamadas para que la clave de caché no dependa de `calls`.
        return {"model": "scripted-fake"}

    def _next_response(self, prompt: str) -> Any:
        self.calls.append(prompt)
        index = len(self.calls) - 1
        if index >= len(self.responses):
            raise AssertionError(
                f"ScriptedChatModel: llamada #{index + 1} sin respuesta guionada "
                f"({len(self.responses)} disponibles)"
            )
        response = self.responses[index]
        if isinstance(response, Exception):
            raise response
        return response

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        response = self._next_response(_prompt_text(messages))
        message = response if isinstance(response, BaseMessage) else AIMessage(content=str(response))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def with_structured_output(  # type: ignore[override]
        self, schema: type[BaseModel], **kwargs: Any
    ) -> Runnable[LanguageModelInput, BaseModel]:
        def run(value: LanguageModelInput) -> BaseModel:
            response = self._next_response(_prompt_text(value))
            if isinstance(response, BaseModel):
                return response
            return schema.model_validate(response)

        return RunnableLambda(run, name=f"ScriptedStructured[{schema.__name__}]")


# --- Embeddings y vector store (Iteración 4) -----------------------------------------------------


class FakeEmbeddings(Embeddings):
    """Embeddings determinísticos sin red: el mismo texto da siempre el mismo vector unitario.

    ``calls`` guarda cada lote recibido por ``embed_documents`` (``len(calls)`` = llamadas) y
    ``query_calls`` los textos de ``embed_query``.
    """

    def __init__(self, dimension: int = 8) -> None:
        self.dimension = dimension
        self.calls: list[list[str]] = []
        self.query_calls: list[str] = []

    def vector(self, text: str) -> list[float]:
        values: list[float] = []
        counter = 0
        while len(values) < self.dimension:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            values.extend((b - 127.5) / 127.5 for b in digest)
            counter += 1
        values = values[: self.dimension]
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return self.vector(text)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class FakeVectorStore:
    """``VectorStore`` en memoria (ver ``services.vectorstore.VectorStore``).

    - ``vectors``: ``id -> VectorRecord`` (un upsert con el mismo id reemplaza, como Pinecone).
    - ``fail_on_upsert_call``: número (1-based) de llamada a ``upsert`` que lanza
      ``RuntimeError`` (falla inyectada para FR-029); ``fail_on_ids`` hace fallar cualquier
      upsert que incluya alguno de esos ids.
    - ``upsert_calls`` / ``delete_calls`` / ``query_calls``: registro de llamadas.
    """

    def __init__(self, fail_on_upsert_call: int | None = None, fail_on_ids: Sequence[str] = ()) -> None:
        self.vectors: dict[str, VectorRecord] = {}
        self.fail_on_upsert_call = fail_on_upsert_call
        self.fail_on_ids = set(fail_on_ids)
        self.upsert_calls: list[list[str]] = []
        self.delete_calls: list[list[str]] = []
        self.query_calls: list[int] = []

    def upsert(self, vectors: Sequence[VectorRecord]) -> None:
        ids = [v.id for v in vectors]
        self.upsert_calls.append(ids)
        if self.fail_on_upsert_call == len(self.upsert_calls) or self.fail_on_ids & set(ids):
            raise RuntimeError("Pinecone fake: falla inyectada en upsert")
        for v in vectors:
            self.vectors[v.id] = v

    def list_ids(self, prefix: str) -> list[str]:
        return sorted(i for i in self.vectors if i.startswith(prefix))

    def delete(self, ids: Sequence[str]) -> None:
        self.delete_calls.append(list(ids))
        for i in ids:
            self.vectors.pop(i, None)

    def query(self, vector: Sequence[float], top_k: int) -> list[Match]:
        self.query_calls.append(top_k)
        scored = [
            Match(id=v.id, score=_cosine(vector, v.values), metadata=dict(v.metadata))
            for v in self.vectors.values()
        ]
        scored.sort(key=lambda m: (-m.score, m.id))
        return scored[:top_k]

    def count(self) -> int:
        return len(self.vectors)


class FakePineconeIndex:
    """Índice con la forma del SDK ``pinecone`` (``upsert``/``list``/``delete``/``query``/
    ``describe_index_stats``) para probar ``PineconeVectorStore`` sin red."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}
        self.upsert_calls = 0

    def upsert(self, *, vectors: Sequence[Mapping[str, Any]], namespace: str = "", **_: Any) -> None:
        self.upsert_calls += 1
        for v in vectors:
            self.data[v["id"]] = dict(v)

    def list(self, *, prefix: str | None = None, namespace: str = "", **_: Any) -> Iterator[Any]:
        ids = sorted(i for i in self.data if i.startswith(prefix or ""))
        for start in range(0, len(ids), 2):  # páginas de 2 para probar la paginación
            yield SimpleNamespace(vectors=[SimpleNamespace(id=i) for i in ids[start : start + 2]])

    def delete(self, *, ids: Sequence[str], namespace: str = "", **_: Any) -> None:
        for i in ids:
            self.data.pop(i, None)

    def query(self, *, vector: Sequence[float], top_k: int, include_metadata: bool = False, **_: Any) -> Any:
        matches = [
            SimpleNamespace(
                id=i, score=_cosine(vector, v["values"]), metadata=v["metadata"] if include_metadata else None
            )
            for i, v in self.data.items()
        ]
        matches.sort(key=lambda m: -m.score)
        return SimpleNamespace(matches=matches[:top_k])

    def describe_index_stats(self, **_: Any) -> Any:
        return SimpleNamespace(total_vector_count=len(self.data))


class FakePineconeClient:
    """Plano de control fake del SDK: ``has_index``, ``describe_index``, ``create_index``,
    ``Index``. ``indexes`` = ``name -> {"dimension", "metric"}``; ``created`` registra las
    creaciones e ``index_objects`` los ``FakePineconeIndex`` entregados."""

    def __init__(self, indexes: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self.indexes: dict[str, dict[str, Any]] = {k: dict(v) for k, v in (indexes or {}).items()}
        self.created: list[dict[str, Any]] = []
        self.index_objects: dict[str, FakePineconeIndex] = {}

    def has_index(self, name: str) -> bool:
        return name in self.indexes

    def describe_index(self, name: str) -> Any:
        info = self.indexes[name]
        return SimpleNamespace(name=name, host=f"{name}.fake", status=SimpleNamespace(ready=True), **info)

    def create_index(self, *, name: str, dimension: int, metric: str, spec: Any, **kwargs: Any) -> Any:
        self.created.append({"name": name, "dimension": dimension, "metric": metric, "spec": spec, **kwargs})
        self.indexes[name] = {"dimension": dimension, "metric": metric}
        return self.describe_index(name)

    def Index(self, name: str = "", host: str = "", **_: Any) -> FakePineconeIndex:  # noqa: N802 (API del SDK)
        return self.index_objects.setdefault(name, FakePineconeIndex())
