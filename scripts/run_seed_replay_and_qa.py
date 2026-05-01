#!/usr/bin/env python3
"""
Replay generated seed-data dialogues, then run QA in read-only mode.

Phase 1 — Replay:  Each JSON file gets its own agent.  The dialogue rounds
                    are fed through ReplayEngine (Narrative + Event + Hooks).
Phase 2 — QA:      For each agent, the qa_items from the same JSON are sent
                    through AgentRuntime.run(read_only=True).

Usage:
    # Replay + QA on all social_network_issue_cases
    .venv/bin/python scripts/run_seed_replay_and_qa.py \
        --data-dir benchmark/generated_seed_data/social_network_issue_cases

    # Only replay (skip QA)
    .venv/bin/python scripts/run_seed_replay_and_qa.py \
        --data-dir benchmark/generated_seed_data/social_network_issue_cases \
        --replay-only

    # Only QA (assumes replay was already done)
    .venv/bin/python scripts/run_seed_replay_and_qa.py \
        --data-dir benchmark/generated_seed_data/social_network_issue_cases \
        --qa-only

    # Filter by filename substring
    .venv/bin/python scripts/run_seed_replay_and_qa.py \
        --data-dir benchmark/generated_seed_data/social_network_issue_cases \
        --filter sn_entity_dedup

Environment:
    CONVERSATION_DUMP_ENABLED=1   Enable dump capture during QA (optional)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time

from pathlib import Path

os.environ.setdefault("CONVERSATION_DUMP_ENABLED", "1")

# Match dev-local.sh: use the same SQLite DB + proxy as run.sh / MCP servers
_db_dir = Path.home() / ".narranexus"
_db_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_dir / 'nexus.db'}")
os.environ.setdefault("SQLITE_PROXY_URL", "http://localhost:8100")
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
# Make benchmark package importable
sys.path.insert(0, str(_ROOT))

USER_ID_PREFIX = "user_seed_eval"


def _agent_id_from_sample(sample_id: str) -> str:
    """Derive a stable agent_id from a sample_id."""
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", sample_id)[:60]
    return f"agent_{safe}"


# =========================================================================
# Phase 1 — Replay
# =========================================================================

async def run_replay(
    data_dir: str,
    file_filter: str | None = None,
    inter_turn_delay: float = 0.1,
    dump_dir: str | None = None,
    use_agent_loop: bool = False,
    bench_config: Any = None,  # Optional BenchmarkConfig
) -> Dict[str, Any]:
    """Replay all seed-data files.  Returns {sample_id: stats}."""
    from benchmark.replay import ReplayConfig, ReplayEngine
    from benchmark.replay.adapters import SeedDataAdapter

    adapter = SeedDataAdapter(data_dir, file_filter=file_filter)
    sessions = adapter.load()
    if not sessions:
        print("No sessions loaded. Check --data-dir and --filter.")
        return {}

    mode = "agent-loop" if use_agent_loop else "fast"
    print(f"[Replay] Loaded {len(sessions)} sessions (mode={mode})")
    all_stats: Dict[str, Any] = {}

    for session in sessions:
        sample_id = session.metadata.get("sample_id", session.session_id)
        agent_id = _agent_id_from_sample(sample_id)
        user_id = USER_ID_PREFIX

        print(f"\n{'='*70}")
        print(f"[Replay] {sample_id}  agent={agent_id}  rounds={len(session.rounds)}")
        print(f"{'='*70}")

        dump_file = None
        if dump_dir:
            Path(dump_dir).mkdir(parents=True, exist_ok=True)
            dump_file = str(Path(dump_dir) / f"{sample_id}_replay.json")

        config = ReplayConfig(
            agent_id=agent_id,
            user_id=user_id,
            agent_name=sample_id,
            user_name="seed_eval_user",
            inter_turn_delay=inter_turn_delay,
            dump_file=dump_file,
            use_agent_loop=use_agent_loop,
            test_config=bench_config,
        )

        engine = ReplayEngine(config)
        await engine.setup()
        stats = await engine.replay([session])
        all_stats[sample_id] = stats
        print(f"[Replay] {sample_id} done: {stats}")

    return all_stats


# =========================================================================
# Phase 2 — QA (read-only)
# =========================================================================

def _inject_llm_config_from_env():
    """
    Pre-set LLM config from environment variables / settings so that
    AgentRuntime does not need user_providers rows in the database.
    This is a benchmark convenience — production uses per-user DB config.
    """
    from xyz_agent_context.agent_framework.api_config import (
        set_user_config,
        _load_from_settings,
    )
    claude, openai_cfg, embedding = _load_from_settings()
    set_user_config(claude, openai_cfg, embedding)


async def run_qa(
    data_dir: str,
    file_filter: str | None = None,
    results_dir: str | None = None,
    skip_modules: set | None = None,
    skip_narrative_prompt: bool = False,
) -> Dict[str, List[Dict[str, Any]]]:
    """Run QA questions in read-only mode.  Returns {sample_id: [result]}."""
    from benchmark.replay.adapters import SeedDataAdapter
    from xyz_agent_context.agent_runtime import AgentRuntime
    from xyz_agent_context.schema import WorkingSource, AgentTextDelta, ProgressMessage

    adapter = SeedDataAdapter(data_dir, file_filter=file_filter)
    adapter.load()  # populates internal _raw
    qa_by_sample = adapter.get_qa_items_by_sample()

    if not qa_by_sample:
        print("No QA items found.")
        return {}

    total_q = sum(len(qs) for qs in qa_by_sample.values())
    print(f"\n[QA] {total_q} questions across {len(qa_by_sample)} samples")

    all_results: Dict[str, List[Dict[str, Any]]] = {}

    for sample_id, qa_items in qa_by_sample.items():
        agent_id = _agent_id_from_sample(sample_id)
        user_id = USER_ID_PREFIX
        results: List[Dict[str, Any]] = []

        print(f"\n{'='*70}")
        print(f"[QA] {sample_id}  agent={agent_id}  questions={len(qa_items)}")
        print(f"{'='*70}")

        async with AgentRuntime() as runtime:
            for qi, qa in enumerate(qa_items, 1):
                question = qa["question"]
                hint = qa.get("tester_hint_answer", "")
                print(f"\n  [{qi}/{len(qa_items)}] Q: {question[:100]}")
                print(f"       Hint: {hint[:100]}")

                # Inject LLM config from env before each run so
                # get_agent_owner_llm_configs doesn't need DB rows.
                _inject_llm_config_from_env()

                t0 = time.monotonic()
                agent_answer = ""
                try:
                    async for msg in runtime.run(
                        agent_id=agent_id,
                        user_id=user_id,
                        input_content=question,
                        working_source=WorkingSource.CHAT,
                        read_only=True,
                        skip_modules=skip_modules,
                        skip_narrative_prompt=skip_narrative_prompt,
                    ):
                        # Agent delivers replies via send_message_to_user_directly
                        # MCP tool.  Text deltas are just thinking / not user-visible.
                        # Tool name may be prefixed (mcp__ChatModule__send_message...)
                        # so use endswith() matching — same as ChatModule does.
                        if isinstance(msg, ProgressMessage):
                            tool_name = (getattr(msg, "details", None) or {}).get("tool_name", "")
                            if tool_name.endswith("send_message_to_user_directly"):
                                args = msg.details.get("arguments", {})
                                content = args.get("content", "")
                                if content:
                                    agent_answer += content
                except Exception as exc:
                    agent_answer = f"[ERROR] {exc}"
                    print(f"       ERROR: {exc}")

                elapsed = time.monotonic() - t0
                print(f"       A: {agent_answer[:120]}{'...' if len(agent_answer) > 120 else ''}")
                print(f"       ({elapsed:.1f}s)")

                results.append({
                    "sample_id": sample_id,
                    "question": question,
                    "tester_hint_answer": hint,
                    "agent_answer": agent_answer,
                    "target_modules": qa.get("target_modules", []),
                    "evidence_turns": qa.get("evidence_turns", []),
                    "elapsed_s": round(elapsed, 1),
                })

        all_results[sample_id] = results

    # Save results
    if results_dir:
        out_dir = Path(results_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "qa_results.json"
        out_file.write_text(
            json.dumps(all_results, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\n[QA] Results saved to {out_file}")

    return all_results


# =========================================================================
# Main
# =========================================================================

async def main():
    parser = argparse.ArgumentParser(
        description="Replay seed-data dialogues then run QA in read-only mode"
    )
    parser.add_argument(
        "--data-dir", required=True,
        help="Directory containing seed-data JSON files",
    )
    parser.add_argument(
        "--filter", default=None, dest="file_filter",
        help="Substring filter on filenames (e.g. sn_entity_dedup)",
    )
    parser.add_argument(
        "--replay-only", action="store_true",
        help="Only run replay phase (skip QA)",
    )
    parser.add_argument(
        "--qa-only", action="store_true",
        help="Only run QA phase (assumes replay was already done)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.1,
        help="Inter-turn delay during replay (seconds)",
    )
    parser.add_argument(
        "--results-dir", default=None,
        help="Directory to save QA results JSON (default: data/seed_eval_results/)",
    )
    parser.add_argument(
        "--dump-dir", default=None,
        help="Directory to save replay dump files",
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to a BenchmarkConfig YAML (e.g. benchmark/test_configs/sn_isolation.yaml). "
             "Drives use_agent_loop, qa skip modules, and narrative prompt skip. "
             "Overrides individual flags below if both are given.",
    )
    parser.add_argument(
        "--use-agent-loop", action="store_true",
        help="(Legacy) Run full AgentRuntime during replay. Use --config instead.",
    )
    parser.add_argument(
        "--skip-modules", default=None,
        help="(Legacy) Comma-separated module class names to exclude from QA context. "
             "Use --config instead.",
    )
    parser.add_argument(
        "--skip-narrative-prompt", action="store_true",
        help="(Legacy) Skip narrative summary in QA system prompt. Use --config instead.",
    )

    args = parser.parse_args()

    # Load BenchmarkConfig from YAML if provided
    bench_config = None
    if args.config:
        from benchmark.replay.test_config import BenchmarkConfig
        bench_config = BenchmarkConfig.from_yaml(args.config)
        # Override legacy flags from config
        args.use_agent_loop = bench_config.use_agent_loop
        args.skip_narrative_prompt = bench_config.skip_narrative_prompt()
        skip_set = bench_config.qa_skip_modules()
        args.skip_modules = ",".join(sorted(skip_set)) if skip_set else None
        print(f"[Config] Loaded {args.config}", flush=True)
        print(f"[Config]   use_agent_loop={args.use_agent_loop}", flush=True)
        print(f"[Config]   skip_narrative_prompt={args.skip_narrative_prompt}", flush=True)
        print(f"[Config]   qa_skip_modules={args.skip_modules}", flush=True)
        print(f"[Config]   replay_off_modules={bench_config.replay_off_modules()}", flush=True)

    results_dir = args.results_dir or str(
        _ROOT / "data" / "seed_eval_results"
    )

    t_total = time.monotonic()

    # Phase 1: Replay
    if not args.qa_only:
        print("\n" + "=" * 70)
        print("PHASE 1: REPLAY")
        print("=" * 70)
        t0 = time.monotonic()
        replay_stats = await run_replay(
            data_dir=args.data_dir,
            file_filter=args.file_filter,
            inter_turn_delay=args.delay,
            dump_dir=args.dump_dir,
            use_agent_loop=args.use_agent_loop,
            bench_config=bench_config,
        )
        print(f"\n[Replay] Total time: {time.monotonic() - t0:.1f}s")
        print(f"[Replay] Replayed {len(replay_stats)} samples")

        # Wait for background hooks to settle before QA
        if not args.replay_only:
            print("\nWaiting 10s for background hooks to settle...")
            await asyncio.sleep(10)

    # Phase 2: QA
    if not args.replay_only:
        print("\n" + "=" * 70)
        print("PHASE 2: QA (read-only)")
        print("=" * 70)
        t0 = time.monotonic()
        skip_modules = set(args.skip_modules.split(",")) if args.skip_modules else None
        qa_results = await run_qa(
            data_dir=args.data_dir,
            file_filter=args.file_filter,
            results_dir=results_dir,
            skip_modules=skip_modules,
            skip_narrative_prompt=args.skip_narrative_prompt,
        )
        print(f"\n[QA] Total time: {time.monotonic() - t0:.1f}s")

        # Summary
        total_q = sum(len(qs) for qs in qa_results.values())
        errors = sum(
            1 for qs in qa_results.values()
            for q in qs if q["agent_answer"].startswith("[ERROR]")
        )
        print(f"[QA] {total_q} questions, {errors} errors")

    print(f"\nTotal elapsed: {time.monotonic() - t_total:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
