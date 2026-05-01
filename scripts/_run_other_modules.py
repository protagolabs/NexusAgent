#!/usr/bin/env python3
"""
Run replay+QA for the non-SN test data (Memory / Awareness / cross-module).

Picks the right BenchmarkConfig YAML per file based on `target_modules`:
  - memory only          → memory_isolation.yaml         (fast replay)
  - awareness only       → awareness_isolation.yaml      (fast replay)
  - social_network only  → sn_isolation.yaml             (agent-loop replay)
  - multi-module         → full_integration.yaml         (agent-loop replay)

Uses the same SQLite backend as the MCP server (matches dev-local.sh).

Usage:
    DATABASE_URL="sqlite:////home/ulss/.narranexus/nexus.db" \\
    SQLITE_PROXY_URL="http://localhost:8100" \\
    CONVERSATION_DUMP_ENABLED=1 \\
    .venv/bin/python -u scripts/_run_other_modules.py

If you want to wait for the current SN batch to finish first, prefix:
    while pgrep -f "_run_interleaved.py" > /dev/null; do sleep 60; done && \\
    DATABASE_URL=... .venv/bin/python -u scripts/_run_other_modules.py
"""

import os
import sys
import asyncio
import json
import re
import time
from pathlib import Path

# Match dev-local.sh: SQLite + Proxy
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

# Reuse the same eval user as the SN run (already has providers registered).
USER_ID = "user_bench_v4"
AGENT_PREFIX = "agent_other_"
DATA_DIR = _ROOT / "benchmark" / "generated_seed_data"
CONFIG_DIR = _ROOT / "benchmark" / "test_configs"
RESULTS_DIR = _ROOT / "data" / "seed_eval_results"


# (file, config_name)
CASES = [
    ("20260417T071541Z_art_memory.json",         "memory_isolation.yaml"),
    ("20260417T072411Z_preference_alignment.json", "awareness_isolation.yaml"),
    ("20260417T072104Z_identity_network.json",   "sn_isolation.yaml"),
    ("20260417T071757Z_career_background.json",  "full_integration.yaml"),
    ("20260417T072527Z_project_followup.json",   "full_integration.yaml"),
    ("art_memory.json",                          "memory_isolation.yaml"),
]


def _agent_id(sample_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", sample_id)[:50]
    return f"{AGENT_PREFIX}{safe}"


async def ensure_provider_for_user(user_id: str):
    """Ensure user_providers + user_slots are registered for `user_id`."""
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.settings import settings

    db = await get_db_client()
    slots = await db.get("user_slots", filters={"user_id": user_id})
    if slots:
        return
    print(f"[Setup] Registering providers for {user_id}", flush=True)
    now = "2026-05-01 00:00:00"
    await db.insert("user_providers", {
        "provider_id": f"prov_{user_id}_claude", "user_id": user_id,
        "name": f"{user_id} Claude", "source": "user", "protocol": "anthropic",
        "auth_type": "api_key", "api_key": settings.anthropic_api_key or "",
        "base_url": settings.anthropic_base_url or "",
        "models": json.dumps([settings.anthropic_model or "claude-sonnet-4-20250514"]),
        "linked_group": "", "is_active": 1, "updated_at": now,
    })
    await db.insert("user_providers", {
        "provider_id": f"prov_{user_id}_openai", "user_id": user_id,
        "name": f"{user_id} OpenAI", "source": "user", "protocol": "openai",
        "auth_type": "api_key", "api_key": settings.openai_api_key or "",
        "base_url": "",
        "models": json.dumps(["gpt-4o", settings.openai_embedding_model or "text-embedding-3-small"]),
        "linked_group": "", "is_active": 1, "updated_at": now,
    })
    for slot, prov, model in [
        ("agent",      f"prov_{user_id}_claude", settings.anthropic_model or "claude-sonnet-4-20250514"),
        ("helper_llm", f"prov_{user_id}_openai", "gpt-4o"),
        ("embedding",  f"prov_{user_id}_openai", settings.openai_embedding_model or "text-embedding-3-small"),
    ]:
        await db.insert("user_slots", {
            "user_id": user_id, "slot_name": slot, "provider_id": prov,
            "model": model, "updated_at": now,
        })


async def replay_one(session, agent_id: str, bench_config: BenchmarkConfig):
    """Run replay for one session under the given BenchmarkConfig."""
    config = ReplayConfig(
        agent_id=agent_id, user_id=USER_ID,
        agent_name=session.session_id, user_name="OtherModule Eval User",
        inter_turn_delay=0.3,
        test_config=bench_config,
    )
    engine = ReplayEngine(config)
    await engine.setup()
    return await engine.replay([session])


async def qa_one(agent_id: str, qa_items: list, bench_config: BenchmarkConfig) -> list:
    """Run all QA questions for one agent under the given config."""
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
    await ensure_provider_for_user(USER_ID)

    all_results: dict = {}
    t_total = time.monotonic()

    for case_idx, (filename, config_name) in enumerate(CASES, 1):
        file_path = DATA_DIR / filename
        config_path = CONFIG_DIR / config_name

        if not file_path.exists():
            print(f"[{case_idx}/{len(CASES)}] SKIP — {filename} not found", flush=True)
            continue

        # Load test data
        adapter = SeedDataAdapter(str(file_path))
        sessions = adapter.load()
        if not sessions:
            print(f"[{case_idx}/{len(CASES)}] SKIP — no sessions in {filename}", flush=True)
            continue

        session = sessions[0]
        sample_id = session.metadata.get("sample_id", session.session_id)
        agent_id = _agent_id(sample_id)
        targets = session.metadata.get("target_modules", [])

        # Load BenchmarkConfig from yaml
        bench_config = BenchmarkConfig.from_yaml(str(config_path))

        print(f"\n{'=' * 80}", flush=True)
        print(f"[{case_idx}/{len(CASES)}] {sample_id}", flush=True)
        print(f"  targets={targets}  config={config_name}", flush=True)
        print(f"  agent={agent_id}  rounds={len(session.rounds)}  use_agent_loop={bench_config.use_agent_loop}", flush=True)
        print(f"{'=' * 80}", flush=True)

        # Phase 1: Replay
        print(f"\n  [Replay] Starting {len(session.rounds)} rounds "
              f"(mode={'agent-loop' if bench_config.use_agent_loop else 'fast'})...", flush=True)
        t0 = time.monotonic()
        try:
            stats = await replay_one(session, agent_id, bench_config)
            print(f"  [Replay] Done in {time.monotonic() - t0:.0f}s: {stats}", flush=True)
        except Exception as exc:
            print(f"  [Replay] FAILED: {exc}", flush=True)
            continue

        await asyncio.sleep(3)

        # Phase 2: QA
        qa_items = adapter.get_qa_items_by_sample().get(sample_id, [])
        if not qa_items:
            print(f"  [QA] No QA items, skipping", flush=True)
            continue

        print(f"\n  [QA] Running {len(qa_items)} questions"
              f" (skip_modules={bench_config.qa_skip_modules()},"
              f" skip_narrative={bench_config.skip_narrative_prompt()})...", flush=True)
        results = await qa_one(agent_id, qa_items, bench_config)
        all_results[sample_id] = {
            "targets": targets,
            "config": config_name,
            "results": results,
        }

        ok = sum(1 for r in results if r["status"] == "OK")
        empty = sum(1 for r in results if r["status"] == "EMPTY")
        print(f"\n  [QA] {ok}/{len(results)} OK ({empty} EMPTY)", flush=True)

    # Save aggregated results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = RESULTS_DIR / "other_modules.json"
    out_file.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")

    # Summary
    elapsed = time.monotonic() - t_total
    print(f"\n\n{'=' * 80}", flush=True)
    print(f"OTHER MODULES BENCHMARK COMPLETE — {elapsed:.0f}s", flush=True)
    print(f"{'=' * 80}", flush=True)
    grand_ok = grand_empty = grand_total = 0
    for sample_id, data in all_results.items():
        results = data["results"]
        ok = sum(1 for r in results if r["status"] == "OK")
        empty = sum(1 for r in results if r["status"] == "EMPTY")
        total = len(results)
        targets = data["targets"]
        sid = sample_id.split("_2026")[0] if "_2026" in sample_id else sample_id
        print(f"  {sid:35s} targets={targets!s:50s} {ok}/{total} OK ({empty} EMPTY)", flush=True)
        grand_ok += ok
        grand_empty += empty
        grand_total += total
    if grand_total:
        rate = f"{grand_ok / grand_total * 100:.0f}%"
        print(f"  {'TOTAL':35s} {' ' * 50}     {grand_ok}/{grand_total} OK ({rate})", flush=True)
    print(f"\nResults saved to {out_file}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
