#!/usr/bin/env python3
"""
Run 3 QA per category (5 × 3 = 15) against agent_locomo_d0_melanie with
CONVERSATION_DUMP_ENABLED=1 to validate the conversation-dump pipeline.

Usage:
    CONVERSATION_DUMP_ENABLED=1 .venv/bin/python scripts/run_locomo_qa_with_dump.py
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

os.environ.setdefault("CONVERSATION_DUMP_ENABLED", "1")

QA_FILE = _ROOT / "benchmark/locomo_eval/results/qa_final3_d0.json"
AGENT_ID = "agent_locomo_d0_melanie"
USER_ID = "user_locomo_caroline"
PER_CATEGORY = int(os.environ.get("PER_CATEGORY", "3"))
SEED = 42


def pick_questions():
    data = json.loads(QA_FILE.read_text("utf-8"))
    by_cat = defaultdict(list)
    for q in data["results"]:
        by_cat[q.get("category")].append(q)
    random.seed(SEED)
    picked = []
    for cat in sorted(by_cat.keys()):
        pool = by_cat[cat]
        sample = random.sample(pool, min(PER_CATEGORY, len(pool)))
        for q in sample:
            picked.append({
                "category": cat,
                "question": q["question"],
                "gold": q.get("answer"),
            })
    return picked


async def main():
    questions = pick_questions()
    print(f"Selected {len(questions)} questions "
          f"({PER_CATEGORY}/category × 5 = {PER_CATEGORY*5})")
    for i, q in enumerate(questions, 1):
        print(f"  [{i:2d}] cat={q['category']}  {q['question'][:80]}")

    from xyz_agent_context.agent_runtime import AgentRuntime
    from xyz_agent_context.schema import WorkingSource

    dump_dirs = []
    errors = []

    async with AgentRuntime() as runtime:
        for i, q in enumerate(questions, 1):
            print(f"\n--- [{i}/{len(questions)}] cat={q['category']} ---")
            print(f"Q: {q['question']}")
            print(f"Gold: {q['gold']}")
            t0 = time.monotonic()
            dump_dir = None
            try:
                async for msg in runtime.run(
                    agent_id=AGENT_ID,
                    user_id=USER_ID,
                    input_content=q["question"],
                    working_source=WorkingSource.CHAT,
                ):
                    # We don't need to print every progress message. Capture the
                    # dump dir from the runtime if the service exposes it.
                    pass
                # Dump service lives on runtime via contextvar during the run.
                # Grab it from the most recent dir under data/conversation_dumps.
            except Exception as exc:
                errors.append((q["question"], repr(exc)))
                print(f"  ERROR: {exc}")
            elapsed = time.monotonic() - t0
            print(f"  elapsed: {elapsed:.1f}s")

    print(f"\n{'='*60}")
    print(f"Completed. Errors: {len(errors)}/{len(questions)}")
    for q, err in errors:
        print(f"  - {q[:60]}: {err}")

    # List newly created dump dirs for this agent
    base = _ROOT / "data" / "conversation_dumps" / AGENT_ID / USER_ID
    if base.exists():
        all_dirs = sorted(base.rglob("*_evt_*"))
        # Keep only dirs (not files)
        all_dirs = [p for p in all_dirs if p.is_dir()]
        recent = [p for p in all_dirs if p.stat().st_mtime > time.time() - 3600]
        print(f"\nRecent dump directories under {base}:")
        for p in recent[-20:]:
            rel = p.relative_to(_ROOT)
            manifest = p / "manifest.json"
            status = "?"
            calls = "?"
            if manifest.exists():
                try:
                    m = json.loads(manifest.read_text("utf-8"))
                    status = m.get("status", "?")
                    calls = m.get("llm", {}).get("call_count", "?")
                except Exception:
                    pass
            print(f"  {rel}  status={status}  llm_calls={calls}")


if __name__ == "__main__":
    asyncio.run(main())
