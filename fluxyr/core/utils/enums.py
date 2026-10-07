"""Enumerations used in the SyntheticBrain system."""

import enum


class BrainState(enum.Enum):
    """States for the SyntheticBrain"""

    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    ERROR = "ERROR"
    CONSOLIDATING = "CONSOLIDATING"
