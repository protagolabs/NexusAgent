"""
LoCoMo dataset adapter.

Reads topic-split LoCoMo data (output of split_by_topic.py) and converts
it into ReplaySessions that the generic ReplayEngine can consume.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..models import DataAdapter, ReplayRound, ReplaySession


class LoCoMoAdapter(DataAdapter):
    """
    Adapter for LoCoMo benchmark data.

    Expects topic-split JSON (from split_by_topic.py) with structure:
        [
            {
                "speakers": ["Melanie", "Caroline"],
                "topics": [
                    {
                        "topic_id": "...",
                        "topic_summary": "...",
                        "cleaned_turns": [
                            {"speaker": "Melanie", "text": "...", "dia_id": "..."},
                            ...
                        ]
                    },
                    ...
                ]
            },
            ...
        ]
    """

    def __init__(
        self,
        topics_path: str,
        perspective: str,
        dialog_indices: Optional[List[int]] = None,
    ):
        """
        Args:
            topics_path: Path to topic-split JSON file.
            perspective: Which speaker is the agent (e.g. "melanie").
            dialog_indices: Which dialogs to load (0-based). None = all.
        """
        self.topics_path = Path(topics_path)
        self.perspective = perspective
        self.dialog_indices = dialog_indices

    def load(self) -> List[ReplaySession]:
        with open(self.topics_path, "r", encoding="utf-8") as f:
            topics_data = json.load(f)

        indices = self.dialog_indices or list(range(len(topics_data)))
        sessions = []

        for idx in indices:
            if idx >= len(topics_data):
                continue
            dialog = topics_data[idx]
            session = self._convert_dialog(dialog, idx)
            if session.rounds:
                sessions.append(session)

        return sessions

    def _convert_dialog(self, dialog: Dict, dialog_idx: int) -> ReplaySession:
        speakers = dialog.get("speakers", [])
        agent_name, user_name = self._resolve_speakers(speakers)

        all_rounds: List[ReplayRound] = []

        for topic in dialog.get("topics", []):
            topic_id = topic.get("topic_id", "")
            topic_summary = topic.get("topic_summary", "")
            cleaned = topic.get("cleaned_turns", [])
            if not cleaned:
                continue

            paired = _pair_turns(cleaned, agent_name)
            for user_input, agent_response, dia_id in paired:
                if not user_input and not agent_response:
                    continue
                all_rounds.append(ReplayRound(
                    user_input=user_input,
                    agent_response=agent_response,
                    metadata={
                        "topic_id": topic_id,
                        "topic_summary": topic_summary,
                        "dia_id": dia_id,
                        "dialog_idx": dialog_idx,
                    },
                ))

        return ReplaySession(
            session_id=f"locomo_d{dialog_idx}",
            rounds=all_rounds,
            metadata={
                "dialog_idx": dialog_idx,
                "agent_name": agent_name,
                "user_name": user_name,
                "num_topics": len(dialog.get("topics", [])),
            },
        )

    def _resolve_speakers(self, speakers: List[str]) -> Tuple[str, str]:
        """Return (agent_name, user_name) based on perspective."""
        if len(speakers) < 2:
            raise ValueError(f"Expected 2 speakers, got {speakers}")
        sp_a, sp_b = speakers[0], speakers[1]
        if self.perspective.lower() == sp_a.lower():
            return sp_a, sp_b
        elif self.perspective.lower() == sp_b.lower():
            return sp_b, sp_a
        else:
            raise ValueError(
                f"perspective '{self.perspective}' doesn't match speakers {speakers}"
            )


def _pair_turns(
    cleaned_turns: List[Dict],
    agent_speaker: str,
) -> List[Tuple[str, str, Optional[str]]]:
    """
    Pair consecutive user/agent turns into (user_input, agent_response, first_dia_id).
    Consecutive same-role turns are concatenated with newlines.
    """
    rounds: List[Tuple[str, str, Optional[str]]] = []
    user_buf: List[str] = []
    agent_buf: List[str] = []
    first_dia_id: Optional[str] = None

    def flush():
        nonlocal user_buf, agent_buf, first_dia_id
        if user_buf or agent_buf:
            rounds.append((
                "\n".join(user_buf),
                "\n".join(agent_buf),
                first_dia_id,
            ))
            user_buf = []
            agent_buf = []
            first_dia_id = None

    for turn in cleaned_turns:
        is_agent = turn["speaker"].lower() == agent_speaker.lower()
        dia_id = turn.get("dia_id")

        if is_agent:
            if user_buf and agent_buf:
                flush()
            agent_buf.append(turn["text"])
            if first_dia_id is None:
                first_dia_id = dia_id
        else:
            if agent_buf and user_buf:
                flush()
            user_buf.append(turn["text"])
            if first_dia_id is None:
                first_dia_id = dia_id

    flush()
    return rounds
