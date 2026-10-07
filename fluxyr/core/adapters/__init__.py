"""Adapters for different AI providers.

This package implements the adapter pattern to provide a consistent interface
for working with different AI providers:
- OpenAI (GPT models)
- Anthropic (Claude models)
- Other providers can be added by implementing the AIProviderAdapter interface
"""

from fluxyr.core.adapters.anthropic import AnthropicAdapter
from fluxyr.core.adapters.base import AIProviderAdapter
from fluxyr.core.adapters.openai import OpenAIAdapter
