#!/usr/bin/env python3
"""Merge two tau2-bench simulation JSON files.

Combines the `tasks` and `simulations` arrays from two files,
deduplicating by task id (later file wins on conflict).
The `info` block is taken from the first file and `timestamp`
is set to the current time.

Usage:
    python merge_simulations.py file1.json file2.json -o merged.json
"""

import argparse
import json
from datetime import datetime
from pathlib import Path


def merge(files: list[Path], output: Path) -> None:
    base = None
    tasks_by_id: dict[str, dict] = {}
    sims_by_key: dict[tuple[str, int], dict] = {}

    for path in files:
        with open(path) as f:
            data = json.load(f)

        if base is None:
            base = data

        for task in data.get("tasks", []):
            tasks_by_id[task["id"]] = task

        for sim in data.get("simulations", []):
            key = (sim["task_id"], sim.get("trial", 0))
            sims_by_key[key] = sim

    merged_tasks = sorted(tasks_by_id.values(), key=lambda t: int(t["id"]))
    merged_sims = sorted(sims_by_key.values(), key=lambda s: (int(s["task_id"]), s.get("trial", 0)))

    result = {
        "timestamp": datetime.now().isoformat(),
        "info": base["info"],
        "tasks": merged_tasks,
        "simulations": merged_sims,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"Merged {len(files)} files -> {output}")
    print(f"  tasks:       {len(merged_tasks)}")
    print(f"  simulations: {len(merged_sims)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge tau2-bench simulation JSON files")
    parser.add_argument("files", nargs="+", type=Path, help="Input JSON files to merge")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output merged JSON file")
    args = parser.parse_args()

    merge(args.files, args.output)


if __name__ == "__main__":
    main()
