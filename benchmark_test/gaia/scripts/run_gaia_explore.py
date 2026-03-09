"""
GAIA Exploration Benchmark Runner for NexusMind
=================================================

Enhanced version of run_gaia.py for exploration experiments.
Adds: --indices, --validate, --experiment, --output-name flags,
timestamp logging per question, log file finder, and experiment-aware reporting.

Usage:
    # Run specific indices with answer validation
    python run_gaia_explore.py --indices 22,25,108 --split validation \
        --agent-id agent_2be97fdd0b81 --validate \
        --experiment exp1_validator --output-name agent_A_results

    # Run regression retry experiment
    python run_gaia_explore.py --indices 19,20,21,33,40 --split validation \
        --agent-id agent_2be97fdd0b81 \
        --experiment exp2_retry --output-name agent_A_results

    # Dry run a single question
    python run_gaia_explore.py --indices 157 --split validation \
        --agent-id agent_2be97fdd0b81 --dry-run
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
import websockets
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AGENT_ID = "agent_2be97fdd0b81"
USER_ID = "hongyitest"
WS_URL = "ws://localhost:8000/ws/agent/run"

# Paths (relative to this script's parent dir = benchmark_test/gaia/)
SCRIPT_DIR = Path(__file__).resolve().parent
GAIA_DIR = SCRIPT_DIR.parent
RESULTS_DIR = GAIA_DIR / "validation_explore_results"
LOGS_DIR = GAIA_DIR.parent.parent / "logs"

# Timeouts
WS_CONNECT_TIMEOUT = 30        # seconds to wait for WS connection
WS_RESPONSE_TIMEOUT = 1200      # seconds to wait for full agent response (20 min)
WS_MESSAGE_TIMEOUT = 120       # seconds to wait for any single WS message

# ---------------------------------------------------------------------------
# Prompt Template
# ---------------------------------------------------------------------------

def build_user_prompt(question: str, file_path: Optional[str] = None) -> str:
    """
    Construct the user prompt with question and optional file reference.

    NOTE: The system prompt (answer format rules, strategy, tool guidance)
    is already configured on the agent side. This function only builds
    the per-question user message.
    """
    parts = []

    if file_path:
        parts.append(f"[Attached file: {file_path}]")
        parts.append("")

    parts.append(question)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------

def load_metadata(split: str) -> list[dict]:
    """Load GAIA metadata for a given split."""
    metadata_path = GAIA_DIR / split / "metadata.json"
    if not metadata_path.exists():
        print(f"ERROR: Metadata file not found: {metadata_path}")
        sys.exit(1)
    with open(metadata_path) as f:
        return json.load(f)


def get_file_path(split: str, file_name: str) -> Optional[str]:
    """Get absolute path to an attached file, or None if no file."""
    if not file_name:
        return None
    fpath = GAIA_DIR / split / file_name
    if fpath.exists():
        return str(fpath.resolve())
    return None


# ---------------------------------------------------------------------------
# Answer Extraction & Scoring
# ---------------------------------------------------------------------------

def extract_final_answer(response: str) -> Optional[str]:
    """Extract the answer after 'FINAL ANSWER:' from the agent's response."""
    # Try multiple patterns (case insensitive)
    patterns = [
        r"FINAL ANSWER:\s*(.+?)(?:\n|$)",
        r"Final Answer:\s*(.+?)(?:\n|$)",
        r"final answer:\s*(.+?)(?:\n|$)",
        r"\*\*FINAL ANSWER:\*\*\s*(.+?)(?:\n|$)",
        r"\*\*FINAL ANSWER\*\*:\s*(.+?)(?:\n|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, response, re.IGNORECASE)
        if match:
            answer = match.group(1).strip()
            # Clean up common formatting artifacts
            answer = answer.strip("`\"'*")
            return answer
    return None


def normalize_answer(answer: str) -> str:
    """Normalize an answer string for comparison."""
    if answer is None:
        return ""
    s = answer.strip().lower()
    # Remove trailing period
    s = s.rstrip(".")
    # Normalize whitespace
    s = re.sub(r"\s+", " ", s)
    return s


def score_answer(predicted: Optional[str], ground_truth: str) -> bool:
    """Check if predicted answer matches ground truth (GAIA exact match)."""
    if predicted is None:
        return False
    return normalize_answer(predicted) == normalize_answer(ground_truth)


# ---------------------------------------------------------------------------
# Answer Validation (LLM-based)
# ---------------------------------------------------------------------------

def validate_answer(question: str, response_text: str, extracted_answer: str) -> str:
    """
    Use GPT-4o to normalize/validate the extracted answer against the question requirements.

    Reads the question carefully to understand expected format, then re-extracts
    the answer from the response in the correct format.
    """
    from openai import OpenAI
    from dotenv import load_dotenv

    # Load API key from project .env
    load_dotenv(GAIA_DIR.parent.parent / ".env")
    client = OpenAI()

    prompt = f"""You are an answer validation assistant for the GAIA benchmark.

The GAIA benchmark uses EXACT string matching to score answers. Your job is to
strip the extracted answer down to the minimal, clean form that matches what a
human grader would write as the ground truth.

Given the question, agent response, and extracted answer, return ONLY the
cleaned answer — nothing else. No explanation, no quotes, no markdown.

CRITICAL formatting rules (GAIA-specific):
- ALWAYS strip currency symbols ($, €, £, ¥). "Express in USD" means the number only, e.g. 89706.00 NOT $89706.00
- ALWAYS strip thousands separators. Return 89706.00 not 89,706.00
- ALWAYS strip units (kg, miles, cm, etc.) unless the question explicitly says "include the unit in your answer"
- ALWAYS strip trailing zeros ONLY beyond what the question asks. If it says "two decimal places", keep .00
- For percentages: return just the number unless the question says "include the % sign"
- For names: return just the name, no titles (Dr., Mr., etc.) unless the question asks for them
- For comma-separated lists: alphabetical order, separated by ", " (comma-space)
- Remove all markdown formatting, quotes, backticks, asterisks, extra whitespace
- If the answer is a single word or short phrase, return it in its simplest form
- When in doubt, return the SHORTER / SIMPLER form — GAIA ground truths are minimal

Question: {question}

Agent's full response (last 2000 chars):
{response_text[-2000:]}

Extracted answer: {extracted_answer}

Return ONLY the cleaned answer:"""

    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0,
        )
        validated = resp.choices[0].message.content.strip()
        # Clean up any quotes or markdown the model might add
        validated = validated.strip("`\"'*")
        return validated
    except Exception as e:
        print(f"  WARNING: Validation failed: {e}")
        return extracted_answer


# ---------------------------------------------------------------------------
# Log File Finder
# ---------------------------------------------------------------------------

def find_relevant_log(start_time: str, end_time: str, logs_dir: Path) -> Optional[str]:
    """
    Find log file whose modification time overlaps with question execution window.

    Log filenames follow patterns like:
        agent_XXXX_YYYYMMDD_HHMMSS.log
        agent_XXXX_YYYYMMDD_HHMMSS.log.zip

    Returns path of the most relevant log, or None.
    """
    if not logs_dir.exists():
        return None

    try:
        start_dt = datetime.fromisoformat(start_time)
        end_dt = datetime.fromisoformat(end_time)
    except (ValueError, TypeError):
        return None

    candidates = []
    for log_file in logs_dir.iterdir():
        if not log_file.name.endswith((".log", ".log.zip")):
            continue

        # Try to parse timestamp from filename: agent_XXX_YYYYMMDD_HHMMSS.log
        parts = log_file.stem.replace(".log", "").split("_")
        if len(parts) >= 4:
            try:
                date_str = parts[-2]  # YYYYMMDD
                time_str = parts[-1]  # HHMMSS
                log_dt = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")

                # Check if log timestamp falls within execution window (with 5 min buffer)
                from datetime import timedelta
                buffer = timedelta(minutes=5)
                if (start_dt - buffer) <= log_dt <= (end_dt + buffer):
                    candidates.append((log_file, abs((log_dt - start_dt).total_seconds())))
            except ValueError:
                continue

    if not candidates:
        # Fallback: check file modification time
        for log_file in logs_dir.iterdir():
            if not log_file.name.endswith((".log", ".log.zip")):
                continue
            mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
            from datetime import timedelta
            buffer = timedelta(minutes=5)
            if (start_dt - buffer) <= mtime <= (end_dt + buffer):
                candidates.append((log_file, abs((mtime - start_dt).total_seconds())))

    if candidates:
        # Return the closest match
        candidates.sort(key=lambda x: x[1])
        return str(candidates[0][0])

    return None


# ---------------------------------------------------------------------------
# Failure Detection
# ---------------------------------------------------------------------------

class FailureType:
    """Enum-like class for failure categories."""
    SUCCESS = "success"
    NO_RESPONSE = "no_response"              # Agent returned empty
    NO_FINAL_ANSWER = "no_final_answer"      # Agent responded but no FINAL ANSWER
    WS_CONNECTION_ERROR = "ws_connection_error"
    WS_TIMEOUT = "ws_timeout"                # Timed out waiting for response
    SERVER_ERROR_500 = "server_error_500"     # Backend returned 500
    AGENT_REFUSED = "agent_refused"           # Claude refused to answer
    AGENT_ERROR = "agent_error"              # Agent runtime error
    UNKNOWN_ERROR = "unknown_error"


def detect_failure(
    response_text: str,
    error_messages: list[str],
    ws_error: Optional[str] = None,
) -> tuple[str, str]:
    """
    Detect and classify failures in the agent response.

    Returns:
        (failure_type, failure_detail)
    """
    # WebSocket level errors (only if we have NO response data)
    if ws_error and not response_text.strip():
        if "timeout" in ws_error.lower():
            return FailureType.WS_TIMEOUT, ws_error
        if "refused" in ws_error.lower():
            return FailureType.WS_CONNECTION_ERROR, ws_error
        return FailureType.WS_CONNECTION_ERROR, ws_error

    # Server errors from the runtime
    for err in error_messages:
        if "500" in err or "Internal Server Error" in err:
            return FailureType.SERVER_ERROR_500, err
        if "error" in err.lower():
            return FailureType.AGENT_ERROR, err

    # Empty response
    if not response_text or not response_text.strip():
        return FailureType.NO_RESPONSE, "Agent returned empty response"

    # Agent refused to answer (Claude safety filters etc.)
    refusal_indicators = [
        "I cannot",
        "I can't",
        "I'm not able to",
        "I apologize, but I",
        "I'm unable to",
        "sorry, but I can't",
        "against my guidelines",
        "I must decline",
    ]
    lower_resp = response_text.lower()
    for indicator in refusal_indicators:
        if indicator.lower() in lower_resp and "FINAL ANSWER" not in response_text:
            return FailureType.AGENT_REFUSED, f"Refusal detected: '{indicator}'"

    # No FINAL ANSWER in the response
    if extract_final_answer(response_text) is None:
        return FailureType.NO_FINAL_ANSWER, "Response present but no FINAL ANSWER found"

    return FailureType.SUCCESS, ""


# ---------------------------------------------------------------------------
# WebSocket Communication
# ---------------------------------------------------------------------------

async def send_question_to_agent(
    question_prompt: str,
    agent_id: str = AGENT_ID,
    user_id: str = USER_ID,
) -> dict:
    """
    Send a question to the NexusMind agent via WebSocket and collect the response.

    Returns:
        dict with keys:
            - response_text: full concatenated agent text
            - thinking_text: agent thinking (if any)
            - tool_calls: list of tool call dicts
            - error_messages: list of error strings
            - ws_error: WebSocket-level error string or None
            - raw_messages: all raw WS messages
            - duration_seconds: how long the request took
    """
    result = {
        "response_text": "",
        "thinking_text": "",
        "tool_calls": [],
        "error_messages": [],
        "ws_error": None,
        "raw_messages": [],
        "duration_seconds": 0,
        "final_output": None,  # From progress step 3.5, the authoritative full text
        "mcp_response": None,  # From send_message_to_user MCP tool (the actual user-facing answer)
    }

    start_time = time.time()

    try:
        async with websockets.connect(
            WS_URL,
            open_timeout=WS_CONNECT_TIMEOUT,
            close_timeout=10,
            max_size=50 * 1024 * 1024,  # 50MB to match agent buffer
        ) as ws:
            # Send request
            request = {
                "agent_id": agent_id,
                "user_id": user_id,
                "input_content": question_prompt,
                "working_source": "chat",
            }
            await ws.send(json.dumps(request))

            # Collect responses
            while True:
                try:
                    raw = await asyncio.wait_for(
                        ws.recv(),
                        timeout=WS_MESSAGE_TIMEOUT,
                    )
                    msg = json.loads(raw)
                    result["raw_messages"].append(msg)

                    msg_type = msg.get("type", "")

                    if msg_type == "agent_response":
                        delta = msg.get("delta", "")
                        result["response_text"] += delta

                    elif msg_type == "agent_thinking":
                        result["thinking_text"] += msg.get("thinking_content", "")

                    elif msg_type == "tool_call":
                        result["tool_calls"].append({
                            "tool_name": msg.get("tool_name", ""),
                            "tool_input": msg.get("tool_input", {}),
                            "tool_output": msg.get("tool_output", ""),
                        })

                    elif msg_type == "error":
                        result["error_messages"].append(
                            msg.get("error_message", str(msg))
                        )

                    elif msg_type == "complete":
                        break

                    elif msg_type == "progress":
                        details = msg.get("details") or {}
                        tool_name = details.get("tool_name", "")

                        # Capture tool calls from progress messages
                        if tool_name:
                            tool_entry = {
                                "tool_name": tool_name,
                                "arguments": details.get("arguments", {}),
                            }
                            # Capture the agent's user-facing message
                            if "send_message_to_user" in tool_name:
                                content = tool_entry["arguments"].get("content", "")
                                if content:
                                    result["mcp_response"] = content
                            result["tool_calls"].append(tool_entry)

                        # Tool output (step 3.4.x without tool_name = output)
                        display = details.get("display") or {}
                        if display.get("output") and result["tool_calls"]:
                            result["tool_calls"][-1]["output"] = display["output"]

                        # Step 3.5 carries the fully-assembled final output
                        fo = details.get("final_output")
                        if fo:
                            result["final_output"] = fo

                except asyncio.TimeoutError:
                    result["ws_error"] = (
                        f"Timeout: no message received for {WS_MESSAGE_TIMEOUT}s"
                    )
                    break
                except websockets.exceptions.ConnectionClosedOK:
                    # Server closed gracefully — this is normal after execution completes.
                    # If we already collected response data, treat as success.
                    break
                except websockets.exceptions.ConnectionClosedError as e:
                    # Abnormal close mid-stream
                    if not result["response_text"]:
                        result["ws_error"] = f"WebSocket closed unexpectedly: {e}"
                    break

    except websockets.exceptions.ConnectionClosedOK:
        # Graceful close at outer level — fine if we have data
        if not result["response_text"]:
            result["ws_error"] = "WebSocket closed before any response data"
    except websockets.exceptions.ConnectionClosedError as e:
        result["ws_error"] = f"WebSocket closed unexpectedly: {e}"
    except ConnectionRefusedError:
        result["ws_error"] = "Connection refused — is the backend running on port 8000?"
    except asyncio.TimeoutError:
        result["ws_error"] = f"Connection timeout after {WS_CONNECT_TIMEOUT}s"
    except Exception as e:
        result["ws_error"] = f"{type(e).__name__}: {e}"

    result["duration_seconds"] = round(time.time() - start_time, 2)
    return result


# ---------------------------------------------------------------------------
# Single Task Runner
# ---------------------------------------------------------------------------

async def run_single_task(
    task: dict,
    task_index: int,
    split: str,
    dry_run: bool = False,
    use_validate: bool = False,
    experiment: str = "default",
    agent_id_label: str = "",
) -> dict:
    """Run a single GAIA task and return the result."""
    task_id = task.get("task_id", f"unknown_{task_index}")
    question = task.get("Question", "")
    level = task.get("Level", "?")
    file_name = task.get("file_name", "")
    ground_truth = task.get("Final answer", "")

    # Build prompt
    file_path = get_file_path(split, file_name)
    user_prompt = build_user_prompt(question, file_path)

    print(f"\n{'='*70}")
    print(f"Task [{task_index}] | UUID: {task_id} | Level: {level} | Split: {split}")
    print(f"Experiment: {experiment} | Agent: {AGENT_ID}")
    print(f"Question: {question[:120]}{'...' if len(question) > 120 else ''}")
    if file_name:
        print(f"File: {file_name} {'(FOUND)' if file_path else '(MISSING!)'}")
    if ground_truth:
        print(f"Ground truth: {ground_truth}")
    print(f"{'='*70}")

    if dry_run:
        print("\n--- USER PROMPT ---")
        print(user_prompt)
        print("--- END PROMPT ---\n")
        return {
            "task_id": task_id,
            "task_index": task_index,
            "level": level,
            "split": split,
            "experiment": experiment,
            "agent_id": AGENT_ID,
            "status": "dry_run",
        }

    # Timestamp: start
    start_time_iso = datetime.now().isoformat()

    # Send to agent
    print("Sending to agent...")
    ws_result = await send_question_to_agent(user_prompt)

    # Timestamp: end
    end_time_iso = datetime.now().isoformat()

    # Priority: MCP send_message (the actual user-facing answer) > final_output > streamed deltas
    response_text = (
        ws_result["mcp_response"]
        or ws_result["final_output"]
        or ws_result["response_text"]
    )

    # Extract answer
    predicted = extract_final_answer(response_text)

    # Detect failures
    failure_type, failure_detail = detect_failure(
        response_text,
        ws_result["error_messages"],
        ws_result["ws_error"],
    )

    # Score (only for validation split which has ground truth)
    correct = None
    if split == "validation" and ground_truth:
        correct = score_answer(predicted, ground_truth)

    # Build result
    result = {
        "task_id": task_id,
        "task_index": task_index,
        "level": level,
        "split": split,
        "experiment": experiment,
        "agent_id": AGENT_ID,
        "question": question,
        "file_name": file_name or None,
        "ground_truth": ground_truth or None,
        "predicted_answer": predicted,
        "correct": correct,
        "failure_type": failure_type,
        "failure_detail": failure_detail,
        "response_text": response_text,
        "thinking_text": ws_result["thinking_text"],
        "tool_calls": ws_result["tool_calls"],
        "error_messages": ws_result["error_messages"],
        "duration_seconds": ws_result["duration_seconds"],
        "start_time": start_time_iso,
        "end_time": end_time_iso,
        "timestamp": datetime.now().isoformat(),
    }

    # Answer validation via LLM
    if use_validate and predicted:
        print("  Validating answer with GPT-4o...")
        validated = validate_answer(question, response_text, predicted)
        result["validated_answer"] = validated
        if split == "validation" and ground_truth:
            result["validated_correct"] = score_answer(validated, ground_truth)
        print(f"  Original:  {predicted}")
        print(f"  Validated: {validated}")
        if result.get("validated_correct") is not None:
            v_icon = "V-correct" if result["validated_correct"] else "V-wrong"
            print(f"  Validation result: {v_icon}")

    # Log file finder for failures
    if failure_type not in (FailureType.SUCCESS,):
        relevant_log = find_relevant_log(start_time_iso, end_time_iso, LOGS_DIR)
        if relevant_log:
            result["relevant_log_file"] = relevant_log
            print(f"  Relevant log: {relevant_log}")

    # Print summary
    status_icon = "?" if correct is None else ("Y" if correct else "N")
    if failure_type != FailureType.SUCCESS:
        status_icon = "!"
        print(f"  FAILURE [{failure_type}]: {failure_detail}")

    print(f"  Predicted: {predicted}")
    if ground_truth:
        print(f"  Expected:  {ground_truth}")
    print(f"  Result: {status_icon} | Time: {ws_result['duration_seconds']}s "
          f"| Tools: {len(ws_result['tool_calls'])}")

    return result


# ---------------------------------------------------------------------------
# Batch Runner
# ---------------------------------------------------------------------------

async def run_batch(
    tasks: list[dict],
    indices: list[int],
    split: str,
    dry_run: bool = False,
    use_validate: bool = False,
    experiment: str = "default",
) -> list[dict]:
    """Run a batch of tasks sequentially."""
    results = []
    for idx in indices:
        if idx < 0 or idx >= len(tasks):
            print(f"WARNING: Task index {idx} out of range (0-{len(tasks)-1}), skipping")
            continue

        result = await run_single_task(
            tasks[idx], idx, split, dry_run,
            use_validate=use_validate,
            experiment=experiment,
        )
        results.append(result)

        # Small delay between requests to avoid overwhelming the server
        if not dry_run and idx != indices[-1]:
            await asyncio.sleep(2)

    return results


def print_summary(results: list[dict], use_validate: bool = False) -> None:
    """Print a summary of batch results with experiment-aware reporting."""
    if not results:
        return

    total = len(results)
    scored = [r for r in results if r.get("correct") is not None]
    correct = sum(1 for r in scored if r["correct"])
    failures = [r for r in results if r.get("failure_type") != FailureType.SUCCESS]

    experiment = results[0].get("experiment", "default") if results else "default"

    print(f"\n{'='*70}")
    print(f"SUMMARY — Experiment: {experiment}")
    print(f"{'='*70}")
    print(f"Total tasks:     {total}")

    if scored:
        print(f"Scored:          {len(scored)}")
        print(f"Correct:         {correct}/{len(scored)} ({100*correct/len(scored):.1f}%)")

        # Per-level breakdown
        for level in sorted(set(r["level"] for r in scored)):
            level_results = [r for r in scored if r["level"] == level]
            level_correct = sum(1 for r in level_results if r["correct"])
            print(f"  Level {level}:      {level_correct}/{len(level_results)} "
                  f"({100*level_correct/len(level_results):.1f}%)")

    # Validation comparison (if --validate was used)
    if use_validate:
        validated_scored = [r for r in results if r.get("validated_correct") is not None]
        if validated_scored:
            v_correct = sum(1 for r in validated_scored if r["validated_correct"])
            print(f"\n--- Validation Comparison ---")
            print(f"Original correct:  {correct}/{len(scored)}")
            print(f"Validated correct: {v_correct}/{len(validated_scored)}")

            # Per-question comparison
            print(f"\nPer-question details:")
            for r in results:
                idx = r.get("task_index", "?")
                orig = r.get("predicted_answer", "N/A")
                val = r.get("validated_answer", "N/A")
                gt = r.get("ground_truth", "N/A")
                orig_ok = r.get("correct", False)
                val_ok = r.get("validated_correct", None)

                change = ""
                if val_ok is not None and orig_ok != val_ok:
                    change = " << IMPROVED" if val_ok else " << REGRESSED"

                print(f"  [{idx}] orig='{orig}' | val='{val}' | gt='{gt}' "
                      f"| orig_ok={orig_ok} | val_ok={val_ok}{change}")

    if failures:
        print(f"\nFailures:        {len(failures)}")
        # Group by failure type
        failure_counts = {}
        for r in failures:
            ft = r["failure_type"]
            failure_counts[ft] = failure_counts.get(ft, 0) + 1
        for ft, count in sorted(failure_counts.items()):
            print(f"  {ft}: {count}")

    # Timing
    durations = [r["duration_seconds"] for r in results if r.get("duration_seconds")]
    if durations:
        print(f"\nTiming:")
        print(f"  Total:   {sum(durations):.1f}s")
        print(f"  Average: {sum(durations)/len(durations):.1f}s")
        print(f"  Min:     {min(durations):.1f}s")
        print(f"  Max:     {max(durations):.1f}s")

    print(f"{'='*70}\n")


def save_results(results: list[dict], experiment: str, output_name: str) -> str:
    """Save results to a JSON file in the experiment subfolder."""
    output_dir = RESULTS_DIR / experiment
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{output_name}.json"
    filepath = output_dir / filename

    # Summary stats
    scored = [r for r in results if r.get("correct") is not None]
    correct = sum(1 for r in scored if r["correct"])
    validated_scored = [r for r in results if r.get("validated_correct") is not None]
    validated_correct = sum(1 for r in validated_scored if r["validated_correct"])

    output = {
        "metadata": {
            "agent_id": AGENT_ID,
            "user_id": USER_ID,
            "experiment": experiment,
            "output_name": output_name,
            "split": results[0]["split"] if results else "unknown",
            "timestamp": datetime.now().isoformat(),
            "total_tasks": len(results),
            "scored": len(scored),
            "correct": correct,
            "validated_scored": len(validated_scored),
            "validated_correct": validated_correct,
            "indices": [r["task_index"] for r in results],
        },
        "results": results,
    }

    with open(filepath, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Results saved to: {filepath}")
    return str(filepath)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="GAIA Exploration Benchmark Runner for NexusMind",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Experiment 1: Answer Validator
  python run_gaia_explore.py --indices 22,25,108,116,157,158,159 \\
    --split validation --agent-id agent_2be97fdd0b81 --validate \\
    --experiment exp1_validator --output-name agent_A_results

  # Experiment 2: Regression Retry
  python run_gaia_explore.py --indices 19,20,21,33,40,49,52,54,57,78,120,133,136 \\
    --split validation --agent-id agent_2be97fdd0b81 \\
    --experiment exp2_retry --output-name agent_A_results

  # Experiment 3: No-Answer Rerun
  python run_gaia_explore.py --indices 12,31,34,60,62,63,102,164 \\
    --split validation --agent-id agent_2be97fdd0b81 \\
    --experiment exp3_rerun --output-name agent_A_results

  # Dry run
  python run_gaia_explore.py --indices 157 --split validation \\
    --agent-id agent_2be97fdd0b81 --dry-run
        """,
    )

    # Task selection (mutually exclusive)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--indices", type=str,
        help="Comma-separated task indices (e.g. 22,25,108)",
    )
    group.add_argument("--task-id", type=int, help="Run a single task by index")
    group.add_argument("--all", action="store_true", help="Run all tasks")
    group.add_argument(
        "--start", type=int,
        help="Start index for a range (use with --end)",
    )

    parser.add_argument("--end", type=int, help="End index (exclusive) for a range")
    parser.add_argument(
        "--split",
        choices=["validation", "test"],
        required=True,
        help="Dataset split to use",
    )
    parser.add_argument(
        "--level",
        type=int,
        choices=[1, 2, 3],
        help="Filter by GAIA level",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print prompts without sending to agent",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Use GPT-4o to normalize/validate extracted answers",
    )
    parser.add_argument(
        "--experiment",
        type=str,
        default="default",
        help="Experiment subfolder name (e.g. exp1_validator, exp2_retry, exp3_rerun)",
    )
    parser.add_argument(
        "--output-name",
        type=str,
        help="Output filename without .json (e.g. agent_A_results)",
    )
    parser.add_argument(
        "--agent-id",
        type=str,
        default=AGENT_ID,
        help=f"Agent ID (default: {AGENT_ID})",
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default=USER_ID,
        help=f"User ID (default: {USER_ID})",
    )
    parser.add_argument(
        "--ws-url",
        type=str,
        default=WS_URL,
        help=f"WebSocket URL (default: {WS_URL})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=WS_MESSAGE_TIMEOUT,
        help=f"Per-message timeout in seconds (default: {WS_MESSAGE_TIMEOUT})",
    )

    return parser.parse_args()


async def main():
    args = parse_args()

    # Override globals if custom args provided
    global AGENT_ID, USER_ID, WS_URL, WS_MESSAGE_TIMEOUT
    AGENT_ID = args.agent_id
    USER_ID = args.user_id
    WS_URL = args.ws_url
    WS_MESSAGE_TIMEOUT = args.timeout

    # Load data
    tasks = load_metadata(args.split)
    print(f"Loaded {len(tasks)} tasks from {args.split} split")

    # Filter by level if specified
    if args.level:
        level_indices = [i for i, t in enumerate(tasks) if str(t.get("Level")) == str(args.level)]
        print(f"Filtered to {len(level_indices)} Level {args.level} tasks")
    else:
        level_indices = None

    # Determine which tasks to run
    if args.indices is not None:
        indices = [int(x.strip()) for x in args.indices.split(",")]
        if level_indices:
            indices = [i for i in indices if i in level_indices]
    elif args.task_id is not None:
        indices = [args.task_id]
    elif args.all:
        indices = level_indices if level_indices else list(range(len(tasks)))
    elif args.start is not None:
        end = args.end if args.end is not None else args.start + 1
        indices = list(range(args.start, end))
        if level_indices:
            indices = [i for i in indices if i in level_indices]
    else:
        print("ERROR: No task selection specified")
        sys.exit(1)

    if not indices:
        print("No tasks to run after filtering.")
        sys.exit(0)

    # Determine output name
    output_name = args.output_name
    if not output_name:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        idx_str = ",".join(str(i) for i in indices[:5])
        if len(indices) > 5:
            idx_str += f"...({len(indices)} total)"
        output_name = f"explore_{args.experiment}_{timestamp}"

    print(f"\n{'='*70}")
    print(f"GAIA Exploration Runner")
    print(f"{'='*70}")
    print(f"Experiment:  {args.experiment}")
    print(f"Agent:       {AGENT_ID}")
    print(f"Validate:    {args.validate}")
    print(f"Tasks:       {len(indices)} — {indices}")
    print(f"Output:      validation_explore_results/{args.experiment}/{output_name}.json")
    print(f"WebSocket:   {WS_URL}")
    print(f"{'='*70}")

    # Run
    results = await run_batch(
        tasks, indices, args.split, args.dry_run,
        use_validate=args.validate,
        experiment=args.experiment,
    )

    # Save & summarize
    if not args.dry_run and results:
        save_results(results, args.experiment, output_name)
        print_summary(results, use_validate=args.validate)


if __name__ == "__main__":
    asyncio.run(main())
