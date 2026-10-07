"""Main SyntheticBrain cognitive architecture implementation."""

import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

from fluxyr_agent.core.adapters.base import AIProviderAdapter
from fluxyr_agent.core.adapters.openai import OpenAIAdapter
from fluxyr_agent.core.brain_memory_helpers import clean_large_files_from_memory
from fluxyr_agent.core.brain_memory_ops import (
    consolidate_memories,
    memory_counts,
    observe_pattern_fn,
    save_memory_fn,
)
from fluxyr_agent.core.brain_prompt import (
    build_enhanced_prompt,
    generate_default_system_prompt,
)
from fluxyr_agent.core.brain_tool_batch import execute_tool_batch_concurrent
from fluxyr_agent.core.brain_tool_dispatch import (
    park_on_all_waits,
    run_tools_sequentially,
    should_run_concurrent,
)
from fluxyr_agent.core.brain_tool_executor import build_memory_tool, build_observe_tool
from fluxyr_agent.core.brain_tool_schema import get_tool_schema
from fluxyr_agent.core.call_id_normalize import ensure_call_ids
from fluxyr_agent.core.content.content_item import ContentItem, TextContent
from fluxyr_agent.core.content.user_message import UserMessage
from fluxyr_agent.core.effect_bookkeeping import (
    load_effect_bookkeeping,
    restore_effect_occurrences,
    save_effect_state,
    stamp_step_effect_coords,
    undo_not_executed_occurrence_hints,
)
from fluxyr_agent.core.memory.episodic import EpisodicMemory
from fluxyr_agent.core.memory.implicit import ImplicitMemory
from fluxyr_agent.core.memory.manager import MemoryManager
from fluxyr_agent.core.memory.semantic import SemanticMemory
from fluxyr_agent.core.memory.short_term import ShortTermMemory
from fluxyr_agent.core.memory_tools import memory_tools
from fluxyr_agent.core.tools.tool_response import ToolDefinition, ToolResponse
from fluxyr_agent.core.utils.enums import BrainState
from fluxyr_agent.core.utils.exceptions import (
    BrainMemoryError,
    ProviderError,
    StateError,
)
from fluxyr_agent.core.utils.token_usage import TokenUsage
from fluxyr_agent.core.wire_sanitizer import sanitize_state_for_wire
from fluxyr_agent.logging import get_logger, scope

logger = logging.getLogger(__name__)
# structlog alongside the stdlib logger above: a catalogue event carries
# KWARGS, and `logging.Logger.debug` accepts none — the rest of this module
# is still %-style and converts in Phase 6+.
log = get_logger(__name__)


# The keys restore() needs before it can identify a saved brain at all. A
# state without them is not a brain — see is_restorable_state's docstring.
_IDENTITY_KEYS = ("provider", "model")


def is_restorable_state(state: dict[str, Any] | None) -> bool:
    """True when ``state`` carries enough identity for ``restore`` to use it.

    A state that fails this check provably holds no conversation. ``save()``
    always writes both identity keys, so the only writer that can produce a
    state without them is the status stamp — which is applied to an empty
    document. That is what makes it safe for a caller to treat a non-restorable
    state as "no saved state", where treating a REAL state that way would
    silently discard the conversation.
    """
    return bool(state) and all(key in state for key in _IDENTITY_KEYS)


class SyntheticBrain:
    """Main cognitive architecture with multi-layered memory systems."""

    # Class-level default so a brain reconstructed without __init__ (tests build
    # bare instances via __new__ to probe save()) still serialises the flag.
    _analysis_pending: bool = False
    # Same reason, for work item C's (#870) occurrence-hint map — __init__
    # shadows it with a fresh per-instance dict before step() can mutate it.
    # See effect_bookkeeping.py for the full contract on both this and
    # _effect_scope below.
    _effect_occurrences: dict[str, int] = {}
    # The scope identity itself (§3.0) — a plain, JSON-safe dict or None,
    # never a resolved tuple; built into ToolExecutionContext.effect_scope by
    # the CALLER, never derived from this attribute at call time.
    _effect_scope: dict[str, Any] | None = None

    def __init__(
        self,
        provider_adapter: AIProviderAdapter,
        semantic_memory: dict[str, Any] | None = None,
        tools: list[ToolDefinition] | None = None,
        system_prompt: str | None = None,
        stable_system_prefix: str | None = None,
        short_term_capacity: int = 10,
        episodic_capacity: int = 100,
        large_file_threshold_mb: float = 5.0,
        stream_callback: Callable[[Any, Any], None] | None = None,
        tool_lifecycle_callback: Callable[[dict[str, Any]], None] | None = None,
        max_iterations: int = 10,
        quota_check: Callable[[int], str | None] | None = None,
        effect_scope: dict[str, Any] | None = None,
        checkpoint: Callable | None = None,
        automatic_memory: bool = True,
        memory_changed: Callable | None = None,
    ):
        """Initialize the synthetic brain.

        Args:
            provider_adapter: Adapter for the AI provider (OpenAI, Anthropic).
            semantic_memory: Initial semantic memory (role, capabilities, etc.).
            tools: List of tool definitions with function references.
            system_prompt: General instructions for the brain.
            stable_system_prefix: Optional cacheable prefix of ``system_prompt``.
                When set, ``build_enhanced_prompt`` returns Anthropic cache-control
                blocks instead of a plain string.
            short_term_capacity: Maximum messages in short-term memory.
            episodic_capacity: Maximum episodes in episodic memory.
            large_file_threshold_mb: Per-file size threshold for auto-cleanup.
            stream_callback: Optional callback for streaming responses.
            tool_lifecycle_callback: Optional callback fired before/after each tool execution.
            max_iterations: Maximum number of tool-call loop iterations per step.
        """
        self._automatic_memory = automatic_memory
        self._memory_changed = memory_changed
        self._checkpoint = checkpoint
        self._provider_adapter = provider_adapter
        self._stable_system_prefix = stable_system_prefix
        self._short_term = ShortTermMemory(capacity=short_term_capacity)
        self._semantic = SemanticMemory()
        self._episodic = EpisodicMemory(capacity=episodic_capacity)
        self._implicit = ImplicitMemory()
        self._memory_manager = MemoryManager(
            semantic_memory=self._semantic,
            episodic_memory=self._episodic,
            implicit_memory=self._implicit,
            provider_adapter=self._provider_adapter,
        )
        # tools may be a static list OR a zero-arg factory, invoked fresh each
        # iteration (brain_tool_bridge re-queries tools after build_skill mutates them).
        self._tools: list[dict[str, Any]] | Callable[[], list[dict[str, Any]]] = (
            tools or []
        )
        self._system_prompt = system_prompt or generate_default_system_prompt()
        self._short_term.add({"role": "system", "content": self._system_prompt})
        self._current_state = BrainState.READY
        self._stream_callback = stream_callback
        self._tool_lifecycle_callback = tool_lifecycle_callback
        self._quota_check = quota_check
        self._last_quota_stop_kind: str | None = None
        # What the LAST `step()` cost (for the caller's `*.finished` event) —
        # these loop locals are otherwise unreachable from outside step().
        self.last_step_iterations = 0
        self.last_step_tool_calls = 0
        self._pending_tools: list[dict[str, Any]] = []
        self._turn_seq = (
            0  # A0: session-wide call_id counter — see call_id_normalize.py
        )
        # Work item C (#870), reset by the caller whenever the scope changes —
        # see effect_bookkeeping.py for the full contract on both attributes.
        self._effect_occurrences: dict[str, int] = {}
        self._effect_scope: dict[str, Any] | None = effect_scope
        self._large_file_threshold_mb = large_file_threshold_mb
        self._max_iterations = max_iterations
        # Owed memory analysis for the turn's opening message — set on a new
        # message, cleared once analyze_conversation runs. Survives a park/resume
        # round trip (save()/restore()) so a parked step defers it to the next
        # settling step (which may be the resume call itself, message=None).
        self._analysis_pending = False

        if semantic_memory:
            for key, value in semantic_memory.items():
                self._memory_manager.save_to_semantic_memory(key, value, importance=0.8)

    # ------------------------------------------------------------------
    # Internal helpers (bound callables for built-in memory tools)
    # ------------------------------------------------------------------

    def _save_memory_function(
        self,
        memory_type: str,
        content: str,
        key: str | None = None,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        result = save_memory_fn(
            self._semantic,
            self._episodic,
            memory_type,
            content,
            key,
            importance,
            metadata,
        )
        if result[0].get("status") == "added" and self._memory_changed:
            self._memory_changed()
        return result

    def _observe_pattern_function(
        self, pattern_key: str, observation: str
    ) -> tuple[dict[str, Any], str]:
        result = observe_pattern_fn(self._implicit, pattern_key, observation)
        if self._memory_changed:
            self._memory_changed()
        return result

    def _all_tools(self) -> list[dict[str, Any]]:
        base = self._tools() if callable(self._tools) else self._tools
        return (
            base
            + memory_tools(self)
            + [
                build_memory_tool(self._save_memory_function),
                build_observe_tool(self._observe_pattern_function),
            ]
        )

    # ------------------------------------------------------------------
    # Public API: step
    # ------------------------------------------------------------------

    def step(
        self,
        message: str | UserMessage | list[ContentItem] | None = None,
        tool_outputs: list[ToolResponse] | None = None,
    ) -> tuple[Any, BrainState, list[dict[str, Any]], TokenUsage]:
        """Execute one step of thinking and return the response.

        Args:
            message: User message (text, UserMessage, or content items).
            tool_outputs: Tool responses from a previous WAITING step.

        Returns:
            Tuple of (response, state, tools_called, token_usage).
        """
        self._current_state = BrainState.RUNNING
        self._turn_seq += 1
        self._last_quota_stop_kind = None
        # How the loop ENDED, read back by whichever surface owns the turn —
        # an event name must be a literal at the call site, so the brain
        # records the fact and the surface (chat/routine) names the event.
        self.last_step_finish_reason = "unknown"
        # last_step_parks: the N-park list; last_step_park is its FIRST entry (back-compat).
        self.last_step_parks: list[dict] = []
        self.last_step_park: dict | None = None
        token_usage = TokenUsage(provider=self._provider_adapter.get_provider_name())

        if message is not None:
            if isinstance(message, str):
                message = UserMessage([TextContent(message)])
            elif isinstance(message, list):
                message = UserMessage(message)
            self._short_term.add(message.to_dict())
            # Owed regardless of how this step ends — if it parks, the flag
            # carries the debt forward to whichever later step settles READY.
            self._analysis_pending = True

        if tool_outputs:
            for tr in tool_outputs:
                self._short_term.add(
                    {
                        "role": "tool",
                        "name": tr.tool_name,
                        "content": json.dumps(tr.result),
                        "tool_call_id": tr.call_id,
                    }
                )

        files_removed = clean_large_files_from_memory(
            self._short_term.get_all(), self._large_file_threshold_mb
        )
        if files_removed > 0:
            logger.debug(
                "Cleaned %d large files from memory to reduce token usage",
                files_removed,
            )

        should_wait = False
        last_response: Any = None
        last_tools_called: list[dict[str, Any]] = []
        iterations = 0
        self.last_step_iterations = 0
        self.last_step_tool_calls = 0

        try:
            while not should_wait:
                iterations += 1
                # scope() restores the surrounding snapshot on exit, so anything
                # bound while handling turn N is gone before turn N+1 starts.
                with scope(turn=iterations):
                    # Stamped INSIDE the loop, not after it: `step()` raises
                    # ProviderError on any adapter failure, and the failure
                    # event needs to say how far the turn got.
                    self.last_step_iterations = iterations
                    if iterations > self._max_iterations:
                        logger.warning(
                            "SyntheticBrain: max iterations (%d) reached in step()",
                            self._max_iterations,
                        )
                        # Rides out on `chat.turn.finished.finish_reason` so the
                        # exhaustion RATE is a query, not a WARNING to notice.
                        self.last_step_finish_reason = "max_iterations"
                        break
                    if self._quota_check is not None:
                        _stop_kind = self._quota_check(iterations)
                        if _stop_kind is not None:
                            self._last_quota_stop_kind = _stop_kind
                            self._current_state = BrainState.READY
                            self.last_step_finish_reason = "quota_stopped"
                            should_wait = True
                            break
                    enhanced_prompt = build_enhanced_prompt(
                        self._system_prompt,
                        self._memory_manager,
                        stable_prefix=self._stable_system_prefix,
                    )
                    stream_mode = self._stream_callback is not None

                    response, tools_called, step_usage = (
                        self._provider_adapter.execute_step_with_usage(
                            messages=self._short_term.get_all(),
                            system_prompt=enhanced_prompt,
                            tools=get_tool_schema(self._all_tools()),
                            stream=stream_mode,
                            stream_callback=self._stream_callback,
                        )
                    )
                    if self._quota_check is not None and self._quota_check(iterations):
                        self._current_state = BrainState.READY
                        self.last_step_finish_reason = "quota_stopped"
                        should_wait = True
                        break
                    token_usage = token_usage + step_usage
                    # Turn-global count before this batch: A0's call_id seed
                    # (see call_id_normalize.py) and the dispatchers' base_index.
                    base_index = self.last_step_tool_calls
                    ensure_call_ids(
                        response,
                        tools_called,
                        step_seq=self._turn_seq,
                        base_index=base_index,
                    )

                    self._short_term.add(response)
                    last_response = response
                    last_tools_called = tools_called
                    self.last_step_tool_calls += len(tools_called or ())

                    if not tools_called:
                        self._current_state = BrainState.READY
                        self.last_step_finish_reason = "end_turn"
                        should_wait = True
                        continue

                    if self._checkpoint:
                        self._checkpoint(self.save())
                    all_tools = self._all_tools()

                    stamp_step_effect_coords(
                        tools_called,
                        all_tools,
                        self._effect_occurrences,
                        step_seq=self._turn_seq,
                        turn_seq=iterations,
                        base_index=base_index,
                    )

                    # Concurrent: 2+ tools, all parallel-eligible. Sequential:
                    # everything else (preserves parking / PUA semantics).
                    parallel = should_run_concurrent(tools_called, all_tools)
                    batch_id = str(uuid.uuid4())
                    callback = self._tool_lifecycle_callback

                    def batch_event(
                        event,
                        callback=callback,
                        batch_id=batch_id,
                        parallel=parallel,
                        batch_size=len(tools_called),
                    ):
                        if callback:
                            callback(
                                {
                                    **event,
                                    "batch_id": batch_id,
                                    "parallel": parallel,
                                    "batch_size": batch_size,
                                }
                            )

                    batch_event(
                        {
                            "event": "tool_batch_start",
                            "calls": [
                                {
                                    "tool_call_id": t.get("call_id"),
                                    "tool_name": t.get("name"),
                                }
                                for t in tools_called
                            ],
                        }
                    )
                    if parallel:
                        tool_responses, has_wait = execute_tool_batch_concurrent(
                            tools_called,
                            all_tools,
                            batch_event,
                            base_index=base_index,
                        )
                        # Concurrent path never suspends (every call already ran).
                        park = (
                            park_on_all_waits(tool_responses, tools_called)
                            if has_wait
                            else None
                        )
                    else:
                        # Sequential path applies A1's run/suspend rule after a park.
                        tool_responses, park = run_tools_sequentially(
                            tools_called,
                            all_tools,
                            batch_event,
                            base_index=base_index,
                        )
                        if park is not None:
                            undo_not_executed_occurrence_hints(
                                self._effect_occurrences,
                                park["pending_tools"],
                            )

                    batch_event(
                        {
                            "event": "tool_batch_end",
                            "status": "waiting" if park else "completed",
                        }
                    )
                    if park is not None:
                        self._current_state = BrainState.WAITING
                        self.last_step_finish_reason = "parked"
                        self.last_step_parks = park["parks"]
                        self.last_step_park = park["parks"][0]
                        self._pending_tools = park["pending_tools"]
                        should_wait = True
                        break

                    # A3: nothing parked — safe to fold every result into memory now.
                    if tool_responses:
                        for tr in tool_responses:
                            self._short_term.add(
                                {
                                    "role": "tool",
                                    "name": tr.tool_name,
                                    "content": json.dumps(tr.result),
                                    "tool_call_id": tr.call_id,
                                }
                            )

                    if self._checkpoint:
                        self._checkpoint(self.save())

            # Run the owed analysis for every non-WAITING outcome — a parked
            # step leaves _analysis_pending set so the NEXT settling step runs
            # it once (even if that's the resume itself), instead of delaying
            # this step's finish/PUA events with a slow analysis call.
            if (
                self._automatic_memory
                and self._analysis_pending
                and self._current_state != BrainState.WAITING
                and not self._last_quota_stop_kind
            ):
                mem_usage = self._memory_manager.analyze_conversation(
                    self._short_term.get_all()
                )
                token_usage = token_usage + mem_usage
                self._analysis_pending = False

            # Clear on every non-WAITING exit — a decided cycle must never look abandoned.
            if self._current_state != BrainState.WAITING:
                self._pending_tools = []

            return last_response, self._current_state, last_tools_called, token_usage

        except Exception as exc:
            self._current_state = BrainState.ERROR
            self.last_step_finish_reason = "error"
            raise ProviderError(f"Error executing step: {exc!s}") from exc

    # ------------------------------------------------------------------
    # State serialization
    # ------------------------------------------------------------------

    def save(self) -> dict[str, Any]:
        """Serialise the current brain state to a plain dictionary."""
        state: dict[str, Any] = {
            "provider": self._provider_adapter.get_provider_name(),
            "model": self._provider_adapter.get_model_name(),
            "system_prompt": self._system_prompt,
            "current_state": self._current_state.value,
            "pending_tools": self._pending_tools,
            "turn_seq": self._turn_seq,
            "short_term": self._short_term.to_dict(),
            "semantic": self._semantic.to_dict(),
            "episodic": self._episodic.to_dict(),
            "implicit": self._implicit.to_dict(),
            "tools_schema": get_tool_schema(
                self._tools() if callable(self._tools) else self._tools
            ),
            "max_iterations": self._max_iterations,
            "analysis_pending": self._analysis_pending,
        }
        save_effect_state(state, self._effect_occurrences, self._effect_scope)
        # Persist adapter max_tokens so a restored brain cannot silently revert to a
        # smaller default.  Only stored when the adapter exposes a max_tokens property.
        adapter_max_tokens = getattr(self._provider_adapter, "max_tokens", None)
        if adapter_max_tokens is not None:
            state["adapter_max_tokens"] = adapter_max_tokens
        return state

    @classmethod
    def from_saved_state(
        cls,
        state: dict[str, Any],
        tools: list[ToolDefinition],
        stream_callback: Callable[[Any, Any], None] | None = None,
        tool_lifecycle_callback: Callable[[dict[str, Any]], None] | None = None,
        quota_check: Callable[[int], str | None] | None = None,
    ) -> "SyntheticBrain":
        """Reconstruct a brain from a previously saved state dictionary.

        NOTE: this path constructs the adapter WITHOUT a usage_callback — the
        billing seam is intentionally not reachable here. Callers that need usage
        capture (chat/routine) build the brain via AIEngine.create_*_brain, which
        passes usage_callback into the adapter constructor directly.
        """
        provider = state["provider"]
        model = state["model"]

        if provider == "openai":
            adapter: AIProviderAdapter = OpenAIAdapter(model=model)
        elif provider == "anthropic":
            from fluxyr_agent.core.adapters.anthropic import AnthropicAdapter

            # Restore persisted max_tokens (default 64000 for old states predating
            # this field) — exceeds the Anthropic SDK's non-streaming threshold
            # (~21333); from_saved_state has no callers today, but a future
            # non-streaming caller must pass an explicit lower value.
            saved_max_tokens: int = state.get("adapter_max_tokens", 64000)
            adapter = AnthropicAdapter(model=model, max_tokens=saved_max_tokens)
        else:
            raise ValueError(f"Unsupported provider: {provider}")

        capacity = state.get("short_term", {}).get("capacity", 10)
        brain = cls(
            provider_adapter=adapter,
            system_prompt=state["system_prompt"],
            tools=tools,
            short_term_capacity=capacity,
            stream_callback=stream_callback,
            tool_lifecycle_callback=tool_lifecycle_callback,
            max_iterations=state.get("max_iterations", 10),
            quota_check=quota_check,
        )
        brain._short_term = ShortTermMemory.from_dict(state["short_term"])
        brain._short_term.capacity = capacity
        brain._semantic = SemanticMemory.from_dict(state["semantic"])
        brain._episodic = EpisodicMemory.from_dict(state["episodic"])
        brain._implicit = ImplicitMemory.from_dict(state["implicit"])
        brain._memory_manager = MemoryManager(
            semantic_memory=brain._semantic,
            episodic_memory=brain._episodic,
            implicit_memory=brain._implicit,
            provider_adapter=adapter,
        )
        brain._current_state = BrainState(state["current_state"])
        brain._pending_tools = state["pending_tools"]
        brain._analysis_pending = state.get("analysis_pending", False)
        brain._turn_seq = state.get("turn_seq", 0)  # old rows predate A0 — no migration
        brain._effect_occurrences, brain._effect_scope = load_effect_bookkeeping(state)
        return brain

    def restore(self, state: dict[str, Any], allow_model_change: bool = False) -> None:
        """Restore brain state from a dictionary, validating provider/model match.

        Note: ``system_prompt`` is intentionally NOT restored from the saved
        state.  The caller sets the correct mode-specific prompt via ``__init__``
        before calling ``restore()``, and that prompt must take precedence so
        that mode transitions (e.g. normal → workbench) are honoured correctly.
        After restoring ``_short_term`` the in-memory system message is updated
        to match ``self._system_prompt`` so the two stay consistent.

        Args:
            state: The saved state dict from ``save()``.
            allow_model_change: When True, a provider or model mismatch is not an
                error — the conversation is carried over to the new model and the
                adapter is rebound. Set this when the user has deliberately
                switched model on an existing session.

                Both checks must be relaxed together. The provider check runs
                FIRST, so relaxing only the model check would still raise on
                every cross-wire switch (Anthropic ↔ OpenRouter), which is the
                common case.

        Raises:
            StateError: On a provider/model mismatch when allow_model_change is
                False. Callers must NOT swallow this into a fresh brain — that
                looks like success while silently discarding the entire
                conversation memory.
        """
        if not is_restorable_state(state):
            # Was a bare KeyError, which is how a failed turn used to poison a
            # session permanently: the status stamp left {'current_state': ...}
            # behind, and every later turn died here with no indication of why.
            missing = sorted(set(_IDENTITY_KEYS) - set(state or {}))
            raise StateError(
                f"Saved state is not restorable: missing {missing}. It carries "
                "no conversation — callers should treat it as absent rather "
                "than restore it."
            )

        saved_provider = state["provider"]
        saved_model = state["model"]
        current_provider = self._provider_adapter.get_provider_name()
        current_model = self._provider_adapter.get_model_name()

        provider_changed = saved_provider != current_provider
        model_changed = saved_model != current_model

        if (provider_changed or model_changed) and not allow_model_change:
            if provider_changed:
                raise StateError(
                    f"Provider mismatch: saved state is for {saved_provider}, "
                    f"but brain is using {current_provider}"
                )
            raise StateError(
                f"Model mismatch: saved state is for {saved_model}, "
                f"but brain is using {current_model}"
            )

        if provider_changed:
            # Cross-wire switch. The stored assistant turns are in the OLD
            # wire's shape and cannot be replayed to the new one — see
            # sanitize_for_wire for exactly what breaks.
            state = sanitize_state_for_wire(state, target_provider=current_provider)
            logger.info(
                "brain.restore: carrying conversation across wires %s -> %s",
                saved_provider,
                current_provider,
            )
        elif model_changed:
            logger.info(
                "brain.restore: carrying conversation across models %s -> %s",
                saved_model,
                current_model,
            )

        self._current_state = BrainState(state["current_state"])
        self._pending_tools = state["pending_tools"]
        self._analysis_pending = state.get("analysis_pending", False)
        self._turn_seq = state.get("turn_seq", 0)  # old rows predate A0 — no migration
        # `_effect_scope` deliberately NOT re-read here — see
        # restore_effect_occurrences's docstring for the asymmetry.
        self._effect_occurrences = restore_effect_occurrences(state)
        self._max_iterations = state.get("max_iterations", self._max_iterations)
        current_capacity = self._short_term.capacity
        self._short_term = ShortTermMemory.from_dict(state["short_term"])
        self._short_term.capacity = current_capacity
        for msg in self._short_term.get_all():
            if msg.get("role") == "system":
                msg["content"] = self._system_prompt
                break
        self._semantic = SemanticMemory.from_dict(state["semantic"])
        self._episodic = EpisodicMemory.from_dict(state["episodic"])
        self._implicit = ImplicitMemory.from_dict(state["implicit"])
        self._memory_manager = MemoryManager(
            semantic_memory=self._semantic,
            episodic_memory=self._episodic,
            implicit_memory=self._implicit,
            provider_adapter=self._provider_adapter,
        )

    # ------------------------------------------------------------------
    # Memory access & maintenance
    # ------------------------------------------------------------------

    def get_semantic_memories(self) -> dict[str, Any]:
        return self._memory_manager.get_semantic_memory_content()

    def get_episodic_memories(self, top_k: int = 10) -> list[dict[str, Any]]:
        return self._memory_manager.get_episodic_memory_content(top_k)

    def get_implicit_patterns(self) -> dict[str, Any]:
        return self._memory_manager.get_implicit_patterns()

    def consolidate_memories(self) -> None:
        """Trigger memory consolidation across all memory systems."""
        self._current_state = BrainState.CONSOLIDATING
        try:
            consolidate_memories(
                self._short_term,
                self._semantic,
                self._episodic,
                self._memory_manager,
                self._large_file_threshold_mb,
            )
            self._current_state = BrainState.READY
        except BrainMemoryError:
            self._current_state = BrainState.ERROR
            raise

    def debug_memory(self) -> None:
        """Emit one `ai.brain.memory_loaded` with the size of each memory system.

        The `top_k`/`title` parameters are gone with the dump they formatted:
        both existed to shape a multi-line human-readable block, and neither has
        a meaning for a single structured event.
        """
        log.debug(
            "ai.brain.memory_loaded",
            memory_counts=memory_counts(self._short_term, self._memory_manager),
        )
