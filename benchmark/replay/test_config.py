"""
Benchmark test configuration.

Minimal config: each context source has ONE role, plus one top-level toggle.

Sources
-------
Module class names (e.g. "ChatModule", "MemoryModule",
"SocialNetworkModule") map to actual modules.

Special keys for non-module context sources:
  "Narrative" — narrative summary in the QA system prompt.

Roles
-----
  "active" (default):
      Replay: this module's hook runs, writes its data normally.
      QA:     its `hook_data_gathering` is called → injects context
              into the QA system prompt, so the agent can use it.

  "no_qa":
      Replay: same as "active" — hook still runs and writes data.
      QA:     `hook_data_gathering` is SKIPPED → context is NOT injected.
      Use this to *isolate* the module under test: other modules still
      build up state during replay (as in real production), but their
      knowledge is hidden from the QA so the answer must come from the
      target module alone.

  "off":
      Replay: hook is SKIPPED entirely — no data written.
      QA:     no context injection (same as "no_qa").
      Use this when you want a clean ablation — verify the system works
      *without* this module ever touching the data.

      For "Narrative" specifically: a narrative is always selected
      during replay because every Event binds to one. "off" is treated
      the same as "no_qa" (only the QA prompt injection is suppressed).

Top-level
---------
  use_agent_loop: drive replay through the full AgentRuntime (LLM + MCP).
      ON by default. Switch off only when you don't need to test the
      agent's *active* behavior — pure hook-only writes are enough.

Phase semantics
---------------
- Replay phase always writes (events, narratives, hooks). Not configurable.
- QA phase is always read-only. Not configurable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Literal, Union


Role = Literal["active", "no_qa", "off"]

NARRATIVE_KEY = "Narrative"


@dataclass
class BenchmarkConfig:
    """Top-level benchmark configuration.

    Examples
    --------
    SN-only QA isolation::

        BenchmarkConfig(
            sources={
                "ChatModule": "no_qa",
                "MemoryModule": "no_qa",
                "Narrative": "no_qa",
            },
        )

    Memory-only, no agent loop::

        BenchmarkConfig(
            sources={
                "SocialNetworkModule": "off",
                "ChatModule": "no_qa",
                "Narrative": "no_qa",
            },
            use_agent_loop=False,
        )

    Full integration test (everything on)::

        BenchmarkConfig()
    """

    # Per-source role map. Sources not listed default to "active".
    sources: Dict[str, Role] = field(default_factory=dict)

    # Drive replay through full AgentRuntime (LLM + MCP).
    use_agent_loop: bool = True

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def role_of(self, name: str) -> Role:
        return self.sources.get(name, "active")

    def replay_off_modules(self) -> set[str]:
        """Module class names whose hooks should be skipped in replay."""
        return {
            name for name, role in self.sources.items()
            if role == "off" and name != NARRATIVE_KEY
        }

    def qa_skip_modules(self) -> set[str]:
        """Module class names whose hook_data_gathering is skipped in QA."""
        return {
            name for name, role in self.sources.items()
            if role in ("no_qa", "off") and name != NARRATIVE_KEY
        }

    def skip_narrative_prompt(self) -> bool:
        """Whether the QA system prompt should omit narrative summary."""
        return self.role_of(NARRATIVE_KEY) in ("no_qa", "off")

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: Union[str, Path]) -> "BenchmarkConfig":
        """Load a config from a YAML file.

        Expected YAML structure::

            sources:
              ChatModule: no_qa
              MemoryModule: no_qa
              Narrative: no_qa
            use_agent_loop: true
        """
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            data: Dict[str, Any] = yaml.safe_load(f) or {}

        # YAML quirk: bare `on` / `off` parse to True / False. We want
        # them as the role strings "on" / "off", so coerce bools back.
        raw_sources = data.get("sources") or {}
        sources: Dict[str, Role] = {}
        for k, v in raw_sources.items():
            if v is True:
                sources[k] = "active"   # `on` / `yes` / `true` → "active"
            elif v is False:
                sources[k] = "off"      # `off` / `no` / `false` → "off"
            else:
                sources[k] = v          # already a role string

        return cls(
            sources=sources,
            use_agent_loop=bool(data.get("use_agent_loop", True)),
        )
