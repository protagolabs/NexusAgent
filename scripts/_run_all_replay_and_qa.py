#!/usr/bin/env python3
"""
Full batch: replay ALL 22 seed-data cases then QA all (SN-only mode).
Skips replay for agents that already exist in DB.

Usage:
    .venv/bin/python scripts/_run_all_replay_and_qa.py
"""

import os, sys, asyncio, re, json, time
from pathlib import Path

os.environ.setdefault("CONVERSATION_DUMP_ENABLED", "1")

# Match dev-local.sh: use the same SQLite DB + proxy as run.sh / MCP servers
_db_dir = Path.home() / ".narranexus"
_db_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_dir / 'nexus.db'}")
os.environ.setdefault("SQLITE_PROXY_URL", "http://localhost:8100")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from benchmark.replay import ReplayConfig, ReplayEngine
from benchmark.replay.adapters import SeedDataAdapter
from xyz_agent_context.agent_runtime import AgentRuntime
from xyz_agent_context.schema import WorkingSource, ProgressMessage

DATA_DIR = str(_ROOT / "benchmark/generated_seed_data/social_network_issue_cases")
SKIP_MODULES = {"ChatModule", "MemoryModule"}
USER_ID = "user_seed_eval"


def _agent_id(sample_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", sample_id)[:60]
    return f"agent_{safe}"


async def _agent_exists(agent_id: str) -> bool:
    from xyz_agent_context.utils.db_factory import get_db_client
    db = await get_db_client()
    row = await db.get_one("agents", {"agent_id": agent_id})
    return row is not None


# =========================================================================
# Phase 1: Replay
# =========================================================================

async def replay_all():
    adapter = SeedDataAdapter(DATA_DIR)
    sessions = adapter.load()
    print(f"[Replay] {len(sessions)} sessions to process", flush=True)

    done = 0
    skipped = 0
    t0 = time.monotonic()

    for session in sessions:
        sample_id = session.metadata.get("sample_id", session.session_id)
        agent_id = _agent_id(sample_id)

        if await _agent_exists(agent_id):
            skipped += 1
            print(f"[SKIP]   {sample_id} (agent exists)", flush=True)
            continue

        print(f"[REPLAY] {sample_id} rounds={len(session.rounds)}", flush=True)
        rt0 = time.monotonic()

        config = ReplayConfig(
            agent_id=agent_id, user_id=USER_ID,
            agent_name=sample_id, user_name="seed_eval_user",
            inter_turn_delay=0.1,
        )
        engine = ReplayEngine(config)
        await engine.setup()
        stats = await engine.replay([session])
        done += 1
        print(f"[DONE]   {sample_id} ({time.monotonic()-rt0:.0f}s) {stats}", flush=True)

    elapsed = time.monotonic() - t0
    print(f"\n[Replay] Done: {done} replayed, {skipped} skipped, {elapsed:.0f}s total", flush=True)


# =========================================================================
# Phase 2: QA (SN-only)
# =========================================================================

async def qa_all() -> dict:
    adapter = SeedDataAdapter(DATA_DIR)
    adapter.load()
    qa_by_sample = adapter.get_qa_items_by_sample()

    # Filter out samples with no QA items
    qa_by_sample = {k: v for k, v in qa_by_sample.items() if v}
    total_q = sum(len(v) for v in qa_by_sample.values())
    print(f"\n[QA] {total_q} questions across {len(qa_by_sample)} samples", flush=True)

    all_results = {}
    q_done = 0

    for sample_id, qa_items in qa_by_sample.items():
        agent_id = _agent_id(sample_id)
        issue = sample_id.split("_20")[0].replace("sn_", "") if "_20" in sample_id else sample_id

        print(f"\n--- {issue} ({len(qa_items)} Qs) ---", flush=True)

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
                        skip_modules=SKIP_MODULES,
                        skip_narrative_prompt=True,
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
                status = "OK" if agent_answer and not agent_answer.startswith("[ERROR]") else "EMPTY" if not agent_answer else "ERR"
                q_done += 1
                short = agent_answer[:100].replace("\n", " ") if agent_answer else "(empty)"
                print(f"  Q{qi} [{status}] ({elapsed:.0f}s) {short}", flush=True)

                results.append({
                    "sample_id": sample_id,
                    "question": question,
                    "hint": hint,
                    "agent_answer": agent_answer,
                    "elapsed_s": round(elapsed, 1),
                    "status": status,
                    "issue": issue,
                })

        all_results[sample_id] = results

    return all_results


def print_summary(all_results: dict):
    print(f"\n\n{'='*80}")
    print("FULL BATCH RESULTS — SN-ONLY MODE (ChatModule + MemoryModule + Narrative skipped)")
    print(f"{'='*80}")

    # Group by issue type
    by_issue = {}
    for sid, results in all_results.items():
        if not results:
            continue
        issue = results[0].get("issue", "?")
        by_issue.setdefault(issue, []).extend(results)

    print(f"\n{'Issue Type':45s} {'OK':>3} {'EMPTY':>5} {'ERR':>4} {'Total':>5} {'Rate':>6}")
    print("-" * 72)
    grand_ok = grand_empty = grand_err = grand_total = 0
    for issue in sorted(by_issue.keys()):
        results = by_issue[issue]
        ok = sum(1 for r in results if r["status"] == "OK")
        empty = sum(1 for r in results if r["status"] == "EMPTY")
        err = sum(1 for r in results if r["status"] == "ERR")
        total = len(results)
        rate = f"{ok/total*100:.0f}%" if total else "-"
        print(f"  {issue:43s} {ok:3d} {empty:5d} {err:4d} {total:5d} {rate:>6s}")
        grand_ok += ok; grand_empty += empty; grand_err += err; grand_total += total
    print("-" * 72)
    rate = f"{grand_ok/grand_total*100:.0f}%" if grand_total else "-"
    print(f"  {'TOTAL':43s} {grand_ok:3d} {grand_empty:5d} {grand_err:4d} {grand_total:5d} {rate:>6s}")


async def main():
    t_total = time.monotonic()

    # Phase 1
    print("=" * 80)
    print("PHASE 1: REPLAY (skip existing agents)")
    print("=" * 80, flush=True)
    await replay_all()

    # Wait for hooks
    print("\nWaiting 15s for background hooks...", flush=True)
    await asyncio.sleep(15)

    # Phase 2
    print("\n" + "=" * 80)
    print("PHASE 2: QA (SN-only, read-only)")
    print("=" * 80, flush=True)
    all_results = await qa_all()

    # Save
    out_dir = _ROOT / "data" / "seed_eval_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "all_22_sn_only.json"
    out_file.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults saved to {out_file}", flush=True)

    print_summary(all_results)
    print(f"\nTotal elapsed: {time.monotonic()-t_total:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
