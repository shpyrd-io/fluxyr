"""
# SyntheticBrain - Advanced Cognitive Architecture for AI Systems

This implementation provides a sophisticated cognitive architecture inspired by human memory systems,
designed to give AI systems more human-like memory organization and reasoning capabilities.

## Key Features:

### 1. Multi-layered Memory System
- **Short-Term Memory**: Temporary storage of current context with limited capacity
- **Explicit Semantic Long-Term Memory**: Factual knowledge and conceptual information
- **Explicit Episodic Long-Term Memory**: Autobiographical events and specific experiences
- **Implicit Long-Term Memory**: Procedural knowledge and patterns that influence behavior

### 2. Memory Processing
- Automatic memory consolidation at the end of each interaction
- Importance-based memory retention and pruning
- Context-aware memory retrieval
- Dynamic prompt construction based on relevant memories

### 3. Adapter Pattern
- Abstract interface for different AI providers (OpenAI, Anthropic)
- Provider-specific implementations handle API differences

### 4. Tool Execution Control
- `continue`: Tool response is fed back to the model and processing continues automatically
- `wait`: Tool requires external processing, engine pauses and returns control

### 5. State Management
- Save/restore conversation and memory state at any point
- Token usage tracking for each interaction
"""

# Import the main components for easy access
from fluxyr.core.adapters.openai import OpenAIAdapter
from fluxyr.core.brain import SyntheticBrain
from fluxyr.core.content.content_item import (
    ContentItem,
    FileContent,
    ImageContent,
    TextContent,
)
from fluxyr.core.content.user_message import UserMessage
from fluxyr.core.memory.manager import MemoryManager
from fluxyr.core.tools.tool_response import ToolDefinition, ToolResponse
from fluxyr.core.utils.enums import BrainState
from fluxyr.core.utils.exceptions import (
    AIEngineError,
    BrainMemoryError,
    ProviderError,
    StateError,
    ToolError,
)
from fluxyr.core.utils.token_usage import TokenUsage

__version__ = "0.1.0"
