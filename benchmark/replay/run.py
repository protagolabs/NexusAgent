#!/usr/bin/env python3
"""
Unified replay entry point.

Usage:
    # LoCoMo replay (all dialogs)
    python -m benchmark.replay.run locomo \
        --topics benchmark/locomo_eval/data/locomo_topics.json \
        --perspective melanie \
        --agent-id my_agent

    # LoCoMo replay (single dialog)
    python -m benchmark.replay.run locomo \
        --topics benchmark/locomo_eval/data/locomo_topics.json \
        --perspective melanie \
        --dialog-index 0

    # Custom adapter (implement DataAdapter, register here)
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

# Ensure NexusAgent src is importable
_SCRIPT_DIR = Path(__file__).resolve().parent
_NEXUS_SRC = _SCRIPT_DIR.parent.parent / "src"
if str(_NEXUS_SRC) not in sys.path:
    sys.path.insert(0, str(_NEXUS_SRC))

from benchmark.replay import ReplayConfig, ReplayEngine
from benchmark.replay.adapters import LoCoMoAdapter


def _build_locomo_config(args) -> ReplayConfig:
    """Derive agent_id / user_id from LoCoMo adapter metadata."""
    adapter = LoCoMoAdapter(
        topics_path=args.topics,
        perspective=args.perspective,
        dialog_indices=[args.dialog_index] if args.dialog_index is not None else None,
    )
    sessions = adapter.load()
    if not sessions:
        print("No sessions loaded. Check --topics and --perspective.")
        sys.exit(1)

    # Derive IDs
    meta = sessions[0].metadata
    safe_u = re.sub(r"\W+", "_", meta["user_name"].lower())
    if args.agent_id:
        agent_id = args.agent_id
    else:
        safe_a = re.sub(r"\W+", "_", meta["agent_name"].lower())
        agent_id = f"agent_locomo_d{meta['dialog_idx']}_{safe_a}"
    user_id = args.user_id or f"user_locomo_{safe_u}"

    config = ReplayConfig(
        agent_id=agent_id,
        user_id=user_id,
        agent_name=meta["agent_name"],
        user_name=meta["user_name"],
        inter_turn_delay=args.delay,
        dump_file=args.dump_file,
    )
    return config, sessions


async def cmd_locomo(args):
    config, sessions = _build_locomo_config(args)
    engine = ReplayEngine(config)
    await engine.setup()
    stats = await engine.replay(sessions)
    print(f"\nDone: {stats}")


def main():
    parser = argparse.ArgumentParser(description="Pluggable replay engine")
    sub = parser.add_subparsers(dest="adapter")

    # -- locomo --
    p = sub.add_parser("locomo", help="Replay LoCoMo dataset")
    p.add_argument("--topics", required=True, help="Path to topic-split JSON")
    p.add_argument("--perspective", required=True, help="Which speaker is the agent")
    p.add_argument("--dialog-index", type=int, default=None, help="Single dialog (0-based)")
    p.add_argument("--agent-id", default=None, help="Override agent_id")
    p.add_argument("--user-id", default=None, help="Override user_id")
    p.add_argument("--delay", type=float, default=0.3, help="Inter-turn delay (seconds)")
    p.add_argument("--dump-file", default=None, help="Save intermediate data to JSON")

    args = parser.parse_args()
    if not args.adapter:
        parser.print_help()
        return

    dispatch = {
        "locomo": cmd_locomo,
    }
    asyncio.run(dispatch[args.adapter](args))


if __name__ == "__main__":
    main()
