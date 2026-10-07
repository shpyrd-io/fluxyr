"""Strip literal MiniMax <mm:think>...</mm:think> markers from assistant text.

MiniMax (served via OpenRouter) sometimes emits its reasoning as literal
``<mm:think>...</mm:think>`` markers inside the plain assistant-text channel
(``delta.content`` / ``message.content``) instead of using the wire's
dedicated reasoning channel (``delta.reasoning`` / ``reasoning_details`` — see
``openai_stream_translator.extract_reasoning_text``). Only the tag markers
themselves are stripped; the text before, after, and between them is real
assistant text and is left untouched, and wire-marked reasoning blocks are
never touched.

Kept in its own leaf module (no dependency on stream or non-stream state) so
both ``openai_stream_translator.py`` (streaming) and ``openai_execution.py``
(non-streaming) can import it without growing either module past the repo's
file-size budget.
"""

from __future__ import annotations

_MM_THINK_TAGS = ("<mm:think>", "</mm:think>")
_MM_THINK_MAX_TAG_LEN = max(len(tag) for tag in _MM_THINK_TAGS)


def strip_minimax_think_tags(text: str) -> str:
    """Remove literal <mm:think>/</mm:think> markers from a complete string.

    For the non-streaming path (openai_execution.execute_non_stream), where
    the whole response arrives as one string — no carry buffer is needed.
    See MinimaxThinkTagStripper for the streaming equivalent.
    """
    for tag in _MM_THINK_TAGS:
        text = text.replace(tag, "")
    return text


class MinimaxThinkTagStripper:
    """Removes <mm:think>/</mm:think> markers from a streamed sequence of
    text-channel chunks, holding back a short suffix when a tag may be split
    across two chunks.

    Call feed() for every text-channel chunk and flush() once at stream close
    to emit anything still held back — it never completed a tag, so it is
    real text, not part of one.
    """

    def __init__(self) -> None:
        self._carry = ""

    def feed(self, text: str) -> str:
        buf = strip_minimax_think_tags(self._carry + text)

        # Hold back a trailing suffix that could be the START of a tag split
        # across this chunk and the next, so it is not emitted before we know
        # whether the next chunk completes it.
        hold_limit = min(_MM_THINK_MAX_TAG_LEN - 1, len(buf))
        for size in range(hold_limit, 0, -1):
            suffix = buf[-size:]
            if any(tag.startswith(suffix) for tag in _MM_THINK_TAGS):
                self._carry = suffix
                return buf[:-size]

        self._carry = ""
        return buf

    def flush(self) -> str:
        """Return any buffered text that never completed a tag (e.g. stream
        ended mid-fragment). Idempotent — returns '' on subsequent calls."""
        remaining, self._carry = self._carry, ""
        return remaining
