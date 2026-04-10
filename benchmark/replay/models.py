"""
Replay data models and adapter interface.

ReplayRound  — one user-agent exchange
ReplaySession — an ordered sequence of rounds (e.g. one dialog / one conversation)
ReplayConfig — runtime configuration for the replay engine
DataAdapter  — ABC that every dataset adapter must implement
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ReplayRound:
    """One turn of conversation: user says something, agent responds."""
    user_input: str
    agent_response: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplaySession:
    """A sequence of rounds forming a logical conversation unit (e.g. one dialog)."""
    session_id: str
    rounds: List[ReplayRound]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplayConfig:
    """Runtime configuration for ReplayEngine."""
    agent_id: str
    user_id: str
    agent_name: str = ""
    user_name: str = ""
    inter_turn_delay: float = 0.3
    dump_file: Optional[str] = None


class DataAdapter(ABC):
    """Abstract adapter that converts a dataset into ReplaySessions."""

    @abstractmethod
    def load(self) -> List[ReplaySession]:
        """Load and return replay sessions from the dataset source."""
        ...
