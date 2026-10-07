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
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompt_values import PromptValue
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, Field


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
