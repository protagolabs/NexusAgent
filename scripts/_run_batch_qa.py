#!/usr/bin/env python3
"""
Batch QA runner for the first 5 seed-data cases.
Assumes replay has already been done.

Usage:
    .venv/bin/python scripts/_run_batch_qa.py
"""

import os, sys, asyncio, json, time, re
from pathlib import Path

# Match dev-local.sh: use the same SQLite DB + proxy as run.sh / MCP servers
_db_dir = Path.home() / ".narranexus"
_db_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_dir / 'nexus.db'}")
os.environ.setdefault("SQLITE_PROXY_URL", "http://localhost:8100")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from benchmark.replay.adapters import SeedDataAdapter
from xyz_agent_context.agent_runtime import AgentRuntime
from xyz_agent_context.schema import WorkingSource, ProgressMessage

CASES = [
    "20260421T035124Z_sn_keyword_semantic_duplicate_01",
    "20260421T035340Z_sn_keyword_semantic_duplicate_02",
    # 20260421T035523Z has 0 QA items, skip
    "20260421T040537Z_sn_keyword_semantic_duplicate_03",
    "20260421T040651Z_sn_keyword_cap_01",
]

SKIP_MODULES = {"ChatModule", "MemoryModule"}


def _agent_id(sample_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", sample_id)[:60]
    return f"agent_{safe}"


async def run_qa_for_case(case_filter: str, all_results: dict):
    adapter = SeedDataAdapter(
        str(_ROOT / "benchmark/generated_seed_data/social_network_issue_cases"),
        file_filter=case_filter,
    )
    adapter.load()
    qa_by_sample = adapter.get_qa_items_by_sample()

    for sample_id, qa_items in qa_by_sample.items():
        if not qa_items:
            print(f"[SKIP] {sample_id}: no QA items")
            continue

        agent_id = _agent_id(sample_id)
        user_id = "user_seed_eval"
        print(f"\n{'='*70}")
        print(f"[QA] {sample_id}  agent={agent_id}  questions={len(qa_items)}")
        print(f"{'='*70}")

        results = []
        async with AgentRuntime() as runtime:
            for qi, qa in enumerate(qa_items, 1):
                question = qa["question"]
                hint = qa.get("tester_hint_answer", "")
                print(f"\n  Q{qi}: {question[:90]}")
                print(f"  Hint: {hint[:90]}")

                t0 = time.monotonic()
                agent_answer = ""
                try:
                    async for msg in runtime.run(
                        agent_id=agent_id,
                        user_id=user_id,
                        input_content=question,
                        working_source=WorkingSource.CHAT,
                        read_only=True,
                        skip_modules=SKIP_MODULES,
                        skip_narrative_prompt=True,
                    ):
                        if isinstance(msg, ProgressMessage):
                            tool_name = (getattr(msg, "details", None) or {}).get("tool_name", "")
                            if tool_name.endswith("send_message_to_user_directly"):
                                content = msg.details.get("arguments", {}).get("content", "")
                                if content:
                                    agent_answer += content
                except Exception as exc:
                    agent_answer = f"[ERROR] {exc}"

                elapsed = time.monotonic() - t0
                short = agent_answer[:150].replace("\n", " ")
                status = "OK" if agent_answer and not agent_answer.startswith("[ERROR]") else "EMPTY" if not agent_answer else "ERR"
                print(f"  [{status}] ({elapsed:.0f}s) {short}...")

                results.append({
                    "sample_id": sample_id,
                    "question": question,
                    "hint": hint,
                    "agent_answer": agent_answer,
                    "elapsed_s": round(elapsed, 1),
                    "status": status,
                })

        all_results[sample_id] = results


async def main():
    all_results = {}
    t0 = time.monotonic()

    for case in CASES:
        await run_qa_for_case(case, all_results)

    elapsed = time.monotonic() - t0

    # Save
    out_dir = _ROOT / "data" / "seed_eval_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "batch_qa_sn_only.json"
    out_file.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")

    # Summary table
    print(f"\n\n{'='*70}")
    print(f"BATCH QA SUMMARY  ({elapsed:.0f}s total)")
    print(f"{'='*70}")
    print(f"{'Case':55s} {'OK':>3} {'EMPTY':>5} {'ERR':>4} {'Total':>5}")
    print("-" * 75)
    grand_ok = grand_empty = grand_err = grand_total = 0
    for sid, results in all_results.items():
        ok = sum(1 for r in results if r["status"] == "OK")
        empty = sum(1 for r in results if r["status"] == "EMPTY")
        err = sum(1 for r in results if r["status"] == "ERR")
        total = len(results)
        issue = sid.split("_20")[0].replace("sn_", "")
        print(f"  {issue:53s} {ok:3d} {empty:5d} {err:4d} {total:5d}")
        grand_ok += ok; grand_empty += empty; grand_err += err; grand_total += total
    print("-" * 75)
    print(f"  {'TOTAL':53s} {grand_ok:3d} {grand_empty:5d} {grand_err:4d} {grand_total:5d}")
    print(f"\nResults saved to {out_file}")


if __name__ == "__main__":
    asyncio.run(main())
