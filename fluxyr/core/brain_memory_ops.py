"""Memory consolidation and diagnostic operations for SyntheticBrain.

These functions are intentionally extracted from ``brain.py`` to keep that
module under the 300-line limit while preserving the full public API via
delegation methods on :class:`SyntheticBrain`.
"""

import logging
import uuid
from typing import Any

from fluxyr.core.brain_memory_helpers import clean_large_files_from_memory
from fluxyr.core.memory.episodic import EpisodicMemory
from fluxyr.core.memory.implicit import ImplicitMemory
from fluxyr.core.memory.semantic import SemanticMemory
from fluxyr.core.memory.short_term import ShortTermMemory
from fluxyr.core.utils.exceptions import BrainMemoryError

logger = logging.getLogger(__name__)

# `get_episodic_memory_content` takes a top_k and has no count-only form; a
# bound this far above any real episodic store is a total in practice, and
# reaching for one is cheaper than adding a method to MemoryManager for a
# diagnostic.
_ALL_EPISODIC = 1_000_000


def save_memory_fn(
    semantic: SemanticMemory,
    episodic: EpisodicMemory,
    memory_type: str,
    content: str,
    key: str | None = None,
    importance: float = 0.5,
    metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """Execute the built-in ``save_in_memory`` tool call.

    Args:
        semantic: Brain's semantic memory instance.
        episodic: Brain's episodic memory instance.
        memory_type: ``"semantic"`` or ``"episodic"``.
        content: Content to store.
        key: Semantic memory key (generated if omitted for semantic type).
        importance: Priority level (0.0–1.0).
        metadata: Optional metadata dict.

    Returns:
        ``(result_dict, mode)`` where mode is always ``"continue"``.
    """
    try:
        if memory_type == "semantic":
            if not key:
                key = f"semantic_{uuid.uuid4()}"
            memory_id = semantic.add(key, content, importance, metadata)
            return (
                {
                    "status": "added",
                    "memory_id": memory_id,
                    "memory_type": "semantic",
                    "key": key,
                    "message": f"Added to semantic memory: '{key}'",
                },
                "continue",
            )
        elif memory_type == "episodic":
            memory_id = episodic.add(content, importance, metadata)
            return (
                {
                    "status": "added",
                    "memory_id": memory_id,
                    "memory_type": "episodic",
                    "message": "Added to episodic memory",
                },
                "continue",
            )
        else:
            return {
                "status": "error",
                "message": f"Invalid memory type: {memory_type}",
            }, "continue"
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Error saving to memory: {exc!s}",
        }, "continue"


def observe_pattern_fn(
    implicit: ImplicitMemory,
    pattern_key: str,
    observation: str,
) -> tuple[dict[str, Any], str]:
    """Execute the built-in ``observe_pattern`` tool call.

    Args:
        implicit: Brain's implicit memory instance.
        pattern_key: The pattern category.
        observation: The specific observation to record.

    Returns:
        ``(result_dict, mode)`` where mode is always ``"continue"``.
    """
    try:
        implicit.observe(pattern_key, observation)
        return (
            {
                "status": "observed",
                "pattern_key": pattern_key,
                "message": f"Observation recorded for pattern '{pattern_key}'",
            },
            "continue",
        )
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Error recording observation: {exc!s}",
        }, "continue"


def consolidate_memories(
    short_term: ShortTermMemory,
    semantic: SemanticMemory,
    episodic: EpisodicMemory,
    memory_manager: Any,
    large_file_threshold_mb: float,
) -> None:
    """Trigger memory consolidation across all memory systems.

    Args:
        short_term: Brain's short-term memory.
        semantic: Brain's semantic memory.
        episodic: Brain's episodic memory.
        memory_manager: Brain's :class:`~memory.manager.MemoryManager`.
        large_file_threshold_mb: File size threshold for cleaning.

    Raises:
        BrainMemoryError: On any failure during consolidation.
    """
    try:
        files_removed = clean_large_files_from_memory(
            short_term.get_all(), large_file_threshold_mb
        )
        if files_removed > 0:
            logger.debug(
                "Cleaned %d large files before memory consolidation", files_removed
            )

        memory_manager.analyze_conversation(short_term.get_all())
        semantic.consolidate()
        episodic.consolidate()
    except Exception as exc:
        raise BrainMemoryError(f"Error during memory consolidation: {exc!s}") from exc


def memory_counts(
    short_term: ShortTermMemory,
    memory_manager: Any,
) -> dict[str, int]:
    """Sizes of every memory system. Counts ONLY — never contents.

    REPLACES ``debug_memory_log``, which emitted ten-plus DEBUG records per
    call, several of them with embedded newlines (so one record became several
    lines of the stream), and each of them carrying the memory CONTENT itself:
    semantic values, episodic text and short-term messages are all conversation
    material, which Part VI.1 forbids in a log under any level. The question
    that dump was actually asked to answer — *is memory growing, and where* — is
    four integers, and four integers are queryable where a formatted dump is
    not.

    `implicit` counts distinct patterns rather than observations, matching what
    ``get_implicit_patterns`` returns; `short_term` is the message count, whose
    ceiling is ``short_term.capacity``.
    """
    return {
        "semantic": len(memory_manager.get_semantic_memory_content()),
        "episodic": len(memory_manager.get_episodic_memory_content(_ALL_EPISODIC)),
        "implicit": len(memory_manager.get_implicit_patterns()),
        "short_term": len(short_term.get_all()),
    }
