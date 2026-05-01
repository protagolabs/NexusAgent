#!/usr/bin/env python3
"""
Interleaved replay+QA: for each case, replay all rounds via full AgentRuntime
(agent can call MCP tools), then immediately run QA and print results.

Uses fresh agent/user IDs (agent_loop_* / user_loop_eval) to avoid mixing
with fast-replay data.

Usage:
    CONVERSATION_DUMP_ENABLED=1 .venv/bin/python scripts/_run_interleaved.py
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
USER_ID = "user_bench_v4"
AGENT_PREFIX = "agent_v4_"
SKIP_MODULES = {"ChatModule", "MemoryModule"}


def _agent_id(sample_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", sample_id)[:50]
    return f"{AGENT_PREFIX}{safe}"


async def ensure_provider(user_id: str):
    """Register LLM providers for the eval user if not exists."""
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.settings import settings
    db = await get_db_client()
    existing = await db.get("user_providers", filters={"user_id": user_id})
    if existing:
        return
    now = "2026-04-24 00:00:00.000000"
    await db.insert("user_providers", {
        "provider_id": "prov_loop_claude", "user_id": user_id,
        "name": "Loop Claude", "source": "user", "protocol": "anthropic",
        "auth_type": "api_key", "api_key": settings.anthropic_api_key or "",
        "base_url": settings.anthropic_base_url or "",
        "models": json.dumps([settings.anthropic_model or "claude-sonnet-4-20250514"]),
        "linked_group": "", "is_active": 1, "updated_at": now,
    })
    await db.insert("user_providers", {
        "provider_id": "prov_loop_openai", "user_id": user_id,
        "name": "Loop OpenAI", "source": "user", "protocol": "openai",
        "auth_type": "api_key", "api_key": settings.openai_api_key or "",
        "base_url": "",
        "models": json.dumps(["gpt-4o", settings.openai_embedding_model or "text-embedding-3-small"]),
        "linked_group": "", "is_active": 1, "updated_at": now,
    })
    for slot, prov, model in [
        ("agent", "prov_loop_claude", settings.anthropic_model or "claude-sonnet-4-20250514"),
        ("helper_llm", "prov_loop_openai", "gpt-4o"),
        ("embedding", "prov_loop_openai", settings.openai_embedding_model or "text-embedding-3-small"),
    ]:
        await db.insert("user_slots", {
            "user_id": user_id, "slot_name": slot, "provider_id": prov,
            "model": model, "updated_at": now,
        })
    print(f"[Setup] Registered LLM providers for {user_id}", flush=True)


async def replay_one(session, agent_id: str):
    """Full agent-loop replay for one session."""
    config = ReplayConfig(
        agent_id=agent_id, user_id=USER_ID,
        agent_name=session.session_id, user_name="Loop Eval User",
        inter_turn_delay=0.5,
        use_agent_loop=True,
    )
    engine = ReplayEngine(config)
    await engine.setup()
    stats = await engine.replay([session])
    return stats


async def qa_one(agent_id: str, qa_items: list) -> list:
    """Run QA questions for one agent in SN-only mode."""
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
            short = agent_answer[:120].replace("\n", " ") if agent_answer else "(empty)"
            print(f"    Q{qi} [{status}] ({elapsed:.0f}s) {short}", flush=True)
            print(f"       Hint: {hint[:120]}", flush=True)

            results.append({
                "question": question, "hint": hint,
                "agent_answer": agent_answer, "elapsed_s": round(elapsed, 1),
                "status": status,
            })
    return results


async def main():
    await ensure_provider(USER_ID)

    adapter = SeedDataAdapter(DATA_DIR)
    sessions = adapter.load()
    qa_by_sample = adapter.get_qa_items_by_sample()

    all_results = {}
    t_total = time.monotonic()
    case_num = 0

    for session in sessions:
        sample_id = session.metadata.get("sample_id", session.session_id)
        qa_items = qa_by_sample.get(sample_id, [])
        agent_id = _agent_id(sample_id)
        issue = sample_id.split("_20")[0].replace("sn_", "") if "_20" in sample_id else sample_id

        case_num += 1
        print(f"\n{'='*80}", flush=True)
        print(f"[{case_num}/22] {issue}", flush=True)
        print(f"  agent={agent_id}  rounds={len(session.rounds)}  qa={len(qa_items)}", flush=True)
        print(f"{'='*80}", flush=True)

        # Phase 1: Replay
        print(f"\n  [Replay] Starting {len(session.rounds)} rounds (agent-loop mode)...", flush=True)
        t0 = time.monotonic()
        stats = await replay_one(session, agent_id)
        replay_elapsed = time.monotonic() - t0
        print(f"  [Replay] Done in {replay_elapsed:.0f}s: {stats}", flush=True)

        # Wait for background hooks
        await asyncio.sleep(5)

        # Phase 2: QA
        if qa_items:
            print(f"\n  [QA] Running {len(qa_items)} questions (SN-only)...", flush=True)
            results = await qa_one(agent_id, qa_items)
            all_results[sample_id] = results

            ok = sum(1 for r in results if r["status"] == "OK")
            empty = sum(1 for r in results if r["status"] == "EMPTY")
            print(f"\n  [QA] Results: {ok} OK, {empty} EMPTY / {len(results)} total", flush=True)
        else:
            print(f"\n  [QA] No QA items, skipping", flush=True)

    # Save all results
    out_dir = _ROOT / "data" / "seed_eval_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "interleaved_loop_sn_only.json"
    out_file.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")

    # Summary
    elapsed = time.monotonic() - t_total
    print(f"\n\n{'='*80}", flush=True)
    print(f"COMPLETE — {elapsed:.0f}s total", flush=True)
    print(f"{'='*80}", flush=True)
    grand_ok = grand_empty = grand_total = 0
    for sid, results in all_results.items():
        issue = sid.split("_20")[0].replace("sn_", "")
        ok = sum(1 for r in results if r["status"] == "OK")
        empty = sum(1 for r in results if r["status"] == "EMPTY")
        total = len(results)
        print(f"  {issue:45s} {ok}/{total} OK", flush=True)
        grand_ok += ok; grand_empty += empty; grand_total += total
    print(f"  {'TOTAL':45s} {grand_ok}/{grand_total} OK ({grand_ok/grand_total*100:.0f}%)" if grand_total else "", flush=True)
    print(f"\nResults: {out_file}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
