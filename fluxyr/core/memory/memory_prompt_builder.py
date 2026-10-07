"""Builds memory-context strings for inclusion in system prompts."""

from fluxyr.core.memory.episodic import EpisodicMemory
from fluxyr.core.memory.implicit import ImplicitMemory
from fluxyr.core.memory.semantic import SemanticMemory


def build_memory_prompt(
    semantic: SemanticMemory,
    episodic: EpisodicMemory,
    implicit: ImplicitMemory,
) -> str:
    """Build a human-readable memory context block for the system prompt.

    Args:
        semantic: The brain's semantic memory instance.
        episodic: The brain's episodic memory instance.
        implicit: The brain's implicit memory instance.

    Returns:
        A multi-line string ready to append to the system prompt.
    """
    memory_prompt = "\n\nMEMORY CONTEXT:\n"

    semantic_memory = semantic.get_all()
    if semantic_memory:
        memory_prompt += "\nSEMANTIC MEMORY (Important Facts):\n"
        for key, value in semantic_memory.items():
            if key != "role":  # Skip internal role key
                memory_prompt += f"- {key}: {value}\n"

    episodic_memories = episodic.get_all()
    if episodic_memories:
        memory_prompt += "\nRECENT INTERACTIONS AND EXPERIENCES:\n"
        for memory in episodic_memories[:25]:
            memory_prompt += f"- {memory['content']}\n"

    implicit_patterns = implicit.get_all_patterns()
    if implicit_patterns:
        memory_prompt += "\nOBSERVED PATTERNS:\n"
        for pattern, observations in implicit_patterns.items():
            if observations:
                top = sorted(observations.items(), key=lambda x: x[1], reverse=True)[:2]
                memory_prompt += f"- {pattern}: {', '.join(o[0] for o in top)}\n"

    return memory_prompt
