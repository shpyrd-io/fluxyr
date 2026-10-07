"""TechDoc uses the installation's configured adapter, with no gateway or billing."""

import uuid
from contextvars import ContextVar

from ...providers import response_text
from ...streaming import field
from ...usage import bind_usage
from .llm_types import LLMResult

adapter_factory = ContextVar("techdoc_adapter_factory", default=None)
activity_emitter = ContextVar("techdoc_activity_emitter", default=None)


class TechdocLLM:
    chunk_words = 16000
    max_input_tokens = 100000
    max_output_tokens = 16000

    def __init__(self, factory, emit=None):
        self.factory = factory
        self.emit = emit or (lambda *_: None)

    def complete(self, *, system, user, max_tokens, **_):
        adapter = self.factory()
        adapter._max_tokens = min(max_tokens, self.max_output_tokens)
        scope = {"activity_scope": str(uuid.uuid4()), "tool_name": "tech_doc"}
        bind_usage(
            adapter,
            self.emit,
            lambda: {
                **scope,
                "model_call_id": scope["activity_scope"],
                "source": "techdoc",
            },
        )

        def streaming(event, *_):
            if field(event, "type") == "content_block_delta":
                delta = field(event, "delta")
                text = (
                    field(delta, "text")
                    or field(delta, "thinking")
                    or field(delta, "partial_json")
                )
                if text:
                    self.emit("substream_delta", {**scope, "characters": len(text)})

        self.emit("substream_start", scope)
        try:
            response, _, usage = adapter.execute_step_with_usage(
                messages=[{"role": "user", "content": user}],
                system_prompt=system,
                stream=True,
                stream_callback=streaming,
            )
        finally:
            self.emit("substream_end", scope)
        return LLMResult(
            text=response_text(response),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            model=adapter.get_model_name(),
            provider=adapter.get_provider_name(),
            request_id=None,
        )


def get_llm():
    factory = adapter_factory.get()
    if factory is None:
        raise RuntimeError("TechDoc requires an installation adapter factory")
    return TechdocLLM(factory, activity_emitter.get())
