"""Token usage returned by the installation adapter during distillation."""

from dataclasses import dataclass


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str
    request_id: str | None
    requested_model: str | None = None
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
