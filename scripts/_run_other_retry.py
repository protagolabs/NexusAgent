#!/usr/bin/env python3
"""
Re-run only the 3 cases that failed previously due to the
build_module_instructions / skip_modules bug.

Cases:
  - art_memory (30 rounds)         → memory_isolation
  - preference_alignment           → awareness_isolation
  - art_memory (short 5 rounds)    → memory_isolation
"""

import os
import sys
import asyncio
import json
import re
import time
from pathlib import Path

_db_dir = Path.home() / ".narranexus"
_db_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_dir / 'nexus.db'}")
os.environ.setdefault("SQLITE_PROXY_URL", "http://localhost:8100")
os.environ.setdefault("CONVERSATION_DUMP_ENABLED", "1")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from benchmark.replay import ReplayConfig, ReplayEngine
from benchmark.replay.adapters import SeedDataAdapter
from benchmark.replay.test_config import BenchmarkConfig
from xyz_agent_context.agent_runtime import AgentRuntime
from xyz_agent_context.schema import WorkingSource, ProgressMessage

USER_ID = "user_bench_v4"
AGENT_PREFIX = "agent_other_"
DATA_DIR = _ROOT / "benchmark" / "generated_seed_data"
CONFIG_DIR = _ROOT / "benchmark" / "test_configs"
RESULTS_DIR = _ROOT / "data" / "seed_eval_results"


CASES = [
    ("20260417T071541Z_art_memory.json",         "memory_isolation.yaml"),
    ("20260417T072411Z_preference_alignment.json", "awareness_isolation.yaml"),
    ("art_memory.json",                          "memory_isolation.yaml"),
]


def _agent_id(sample_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", sample_id)[:50]
    return f"{AGENT_PREFIX}{safe}"


async def cleanup_failed_agents():
    """Drop agents/instances/events from the previous failed run."""
    from xyz_agent_context.utils.db_factory import get_db_client
    db = await get_db_client()
    for filename, _ in CASES:
        adapter = SeedDataAdapter(str(DATA_DIR / filename))
        sessions = adapter.load()
        if not sessions:
            continue
        sample_id = sessions[0].metadata.get("sample_id", sessions[0].session_id)
        aid = _agent_id(sample_id)
        await db.delete("agents", {"agent_id": aid})
        await db.delete("module_instances", {"agent_id": aid})
        await db.delete("events", {"agent_id": aid})
        await db.delete("narratives", {"agent_id": aid})
        print(f"[Cleanup] {aid}", flush=True)


async def replay_one(session, agent_id: str, bench_config: BenchmarkConfig):
    config = ReplayConfig(
        agent_id=agent_id, user_id=USER_ID,
        agent_name=session.session_id, user_name="Retry Eval User",
        inter_turn_delay=0.3,
        test_config=bench_config,
    )
    engine = ReplayEngine(config)
    await engine.setup()
    return await engine.replay([session])


async def qa_one(agent_id: str, qa_items: list, bench_config: BenchmarkConfig) -> list:
    skip_modules = bench_config.qa_skip_modules() or None
    skip_narrative = bench_config.skip_narrative_prompt()

    results = []
    async with AgentRuntime() as runtime:
        for qi, qa in enumerate(qa_items, 1):
            question = qa["question"]
            hint = qa.get("tester_hint_answer", "")

            t0 = time.monotonic()
            agent_answer = ""
            try:
                async for msg in runtime.run(
                    agent_id=agent_id, user_id=USER_ID,
                    input_content=question,
                    working_source=WorkingSource.CHAT,
                    read_only=True,
                    skip_modules=skip_modules,
                    skip_narrative_prompt=skip_narrative,
                ):
                    if isinstance(msg, ProgressMessage):
                        tn = (getattr(msg, "details", None) or {}).get("tool_name", "")
                        if tn.endswith("send_message_to_user_directly"):
                            c = msg.details.get("arguments", {}).get("content", "")
                            if c:
                                agent_answer += c
            except Exception as exc:
                agent_answer = f"[ERROR] {exc}"

            elapsed = time.monotonic() - t0
            status = (
                "OK" if agent_answer and not agent_answer.startswith("[ERROR]")
                else "EMPTY" if not agent_answer else "ERR"
            )
            short = agent_answer[:120].replace("\n", " ") if agent_answer else "(empty)"
            print(f"    Q{qi} [{status}] ({elapsed:.0f}s) {short}", flush=True)

            results.append({
                "question": question, "hint": hint,
                "agent_answer": agent_answer,
                "elapsed_s": round(elapsed, 1),
                "status": status,
            })
    return results


async def main():
    await cleanup_failed_agents()

    all_results: dict = {}
    t_total = time.monotonic()

    for case_idx, (filename, config_name) in enumerate(CASES, 1):
        file_path = DATA_DIR / filename
        config_path = CONFIG_DIR / config_name

        adapter = SeedDataAdapter(str(file_path))
        sessions = adapter.load()
        session = sessions[0]
        sample_id = session.metadata.get("sample_id", session.session_id)
        agent_id = _agent_id(sample_id)
        targets = session.metadata.get("target_modules", [])
        bench_config = BenchmarkConfig.from_yaml(str(config_path))

        print(f"\n{'=' * 80}", flush=True)
        print(f"[{case_idx}/{len(CASES)}] {sample_id}", flush=True)
        print(f"  targets={targets}  config={config_name}", flush=True)
        print(f"  agent={agent_id}  rounds={len(session.rounds)}  use_agent_loop={bench_config.use_agent_loop}", flush=True)
        print(f"{'=' * 80}", flush=True)

        # Phase 1
        print(f"\n  [Replay] Starting {len(session.rounds)} rounds (mode=fast)...", flush=True)
        t0 = time.monotonic()
        try:
            stats = await replay_one(session, agent_id, bench_config)
            print(f"  [Replay] Done in {time.monotonic() - t0:.0f}s: {stats}", flush=True)
        except Exception as exc:
            print(f"  [Replay] FAILED: {exc}", flush=True)
            continue

        await asyncio.sleep(3)

        # Phase 2
        qa_items = adapter.get_qa_items_by_sample().get(sample_id, [])
        if not qa_items:
            continue

        print(f"\n  [QA] Running {len(qa_items)} questions"
              f" (skip={bench_config.qa_skip_modules()}, no_narrative={bench_config.skip_narrative_prompt()})...", flush=True)
        results = await qa_one(agent_id, qa_items, bench_config)
        all_results[sample_id] = {"targets": targets, "config": config_name, "results": results}

        ok = sum(1 for r in results if r["status"] == "OK")
        empty = sum(1 for r in results if r["status"] == "EMPTY")
        print(f"\n  [QA] {ok}/{len(results)} OK ({empty} EMPTY)", flush=True)

    # Save and print summary
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = RESULTS_DIR / "other_modules_retry.json"
    out_file.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")

    elapsed = time.monotonic() - t_total
    print(f"\n\n{'=' * 80}", flush=True)
    print(f"RETRY COMPLETE — {elapsed:.0f}s", flush=True)
    print(f"{'=' * 80}", flush=True)
    grand_ok = grand_total = 0
    for sample_id, data in all_results.items():
        results = data["results"]
        ok = sum(1 for r in results if r["status"] == "OK")
        total = len(results)
        sid = sample_id[:50]
        print(f"  {sid:55s} {ok}/{total} OK", flush=True)
        grand_ok += ok
        grand_total += total
    if grand_total:
        rate = f"{grand_ok / grand_total * 100:.0f}%"
        print(f"  {'TOTAL':55s} {grand_ok}/{grand_total} OK ({rate})", flush=True)
    print(f"\nResults saved to {out_file}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
