"""Token usage tracking module."""


class TokenUsage:
    """Track token usage for model calls"""

    def __init__(
        self, input_tokens: int = 0, output_tokens: int = 0, provider: str = "unknown"
    ):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.provider = provider

    @property
    def total_tokens(self) -> int:
        """Calculate total tokens (input + output)"""
        return self.input_tokens + self.output_tokens

    def __str__(self) -> str:
        return f"TokenUsage({self.provider}): {self.input_tokens} input, {self.output_tokens} output, {self.total_tokens} total"

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        """Allow adding token usage objects together"""
        if not isinstance(other, TokenUsage):
            return NotImplemented

        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            provider=self.provider if self.provider == other.provider else "mixed",
        )
