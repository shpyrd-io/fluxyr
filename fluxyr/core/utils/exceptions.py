"""Custom exceptions for the SyntheticBrain system."""


class AIEngineError(Exception):
    """Base exception for AIEngine errors"""


class ProviderError(AIEngineError):
    """Error from AI provider"""


class ToolError(AIEngineError):
    """Error during tool execution"""


class StateError(AIEngineError):
    """Error during state operations"""


class BrainMemoryError(AIEngineError):
    """Error during memory operations"""
