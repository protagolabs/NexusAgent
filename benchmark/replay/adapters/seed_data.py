"""
Seed-data adapter for social_network_issue_cases (and similar generated benchmarks).

Each JSON file contains one test case with:
  - sample_id
  - dialogue_rounds: [{turn_index, user_message, agent_reply}, ...]
  - qa_items: [{question, tester_hint_answer, target_modules, evidence_turns}, ...]

The adapter converts dialogue_rounds into a single ReplaySession per file.
QA items are exposed separately for the QA evaluation phase.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models import DataAdapter, ReplayRound, ReplaySession


class SeedDataAdapter(DataAdapter):
    """
    Adapter for generated seed-data benchmark files.

    Each JSON file becomes one ReplaySession.  When a directory is given,
    all ``*.json`` files inside it are loaded.
    """

    def __init__(self, path: str, file_filter: Optional[str] = None):
        """
        Args:
            path: Path to a single JSON file or a directory of JSON files.
            file_filter: Optional substring filter on filenames (e.g. "sn_entity_dedup").
        """
        self._path = Path(path)
        self._file_filter = file_filter
        self._raw: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # DataAdapter interface
    # ------------------------------------------------------------------

    def load(self) -> List[ReplaySession]:
        self._raw = self._load_raw_files()
        return [self._to_session(item) for item in self._raw]

    # ------------------------------------------------------------------
    # QA helpers (not part of the DataAdapter interface)
    # ------------------------------------------------------------------

    def get_qa_items(self) -> List[Dict[str, Any]]:
        """Return all QA items across loaded files, tagged with sample_id."""
        out: List[Dict[str, Any]] = []
        for item in self._raw:
            sample_id = item.get("sample_id", "unknown")
            for qa in item.get("qa_items", []):
                out.append({**qa, "sample_id": sample_id})
        return out

    def get_qa_items_by_sample(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return QA items grouped by sample_id."""
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for item in self._raw:
            sample_id = item.get("sample_id", "unknown")
            grouped[sample_id] = item.get("qa_items", [])
        return grouped

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_raw_files(self) -> List[Dict[str, Any]]:
        if self._path.is_file():
            files = [self._path]
        elif self._path.is_dir():
            files = sorted(self._path.glob("*.json"))
        else:
            raise FileNotFoundError(f"Path not found: {self._path}")

        if self._file_filter:
            files = [f for f in files if self._file_filter in f.name]

        raw = []
        for f in files:
            data = json.loads(f.read_text("utf-8"))
            # Carry the source filename for traceability
            data.setdefault("_source_file", f.name)
            raw.append(data)
        return raw

    @staticmethod
    def _to_session(item: Dict[str, Any]) -> ReplaySession:
        sample_id = item.get("sample_id", "unknown")
        rounds: List[ReplayRound] = []

        for turn in item.get("dialogue_rounds", []):
            user_msg = turn.get("user_message", "")
            agent_reply = turn.get("agent_reply", "")
            if not user_msg and not agent_reply:
                continue
            rounds.append(ReplayRound(
                user_input=user_msg,
                agent_response=agent_reply,
                metadata={
                    "turn_index": turn.get("turn_index"),
                    "sample_id": sample_id,
                },
            ))

        return ReplaySession(
            session_id=sample_id,
            rounds=rounds,
            metadata={
                "sample_id": sample_id,
                "source_file": item.get("_source_file", ""),
                "target_modules": item.get("target_modules", []),
                "num_qa_items": len(item.get("qa_items", [])),
            },
        )
