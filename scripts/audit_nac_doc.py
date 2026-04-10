"""
@file_name: audit_nac_doc.py
@author: NexusAgent
@date: 2026-04-09
@description: Layer 3 soft staleness detector for .nac_doc/mirror/.

Walks all mirror mds, reads `last_verified` from frontmatter, and uses
`git log --since=<last_verified> -- <code_file>` to detect whether the
corresponding code file has been touched since the md was last human-verified.

Does NOT block commits — only reports a todo list of stale mds. Stubs
(`stub: true` in frontmatter) are skipped (they have a separate backlog).

Run from repo root:
    uv run python -m scripts.audit_nac_doc
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from scripts import nac_doc_lib


@dataclass(frozen=True)
class StaleEntry:
    rel_md: str
    rel_code: str
    last_verified: str
    commits_since: int


def audit() -> list[StaleEntry]:
    """Scan mirror mds and return a list of stale entries."""
    mirror = nac_doc_lib.mirror_root()
    root = nac_doc_lib.repo_root()
    if not mirror.exists():
        return []

    stale: list[StaleEntry] = []
    for md in sorted(mirror.rglob("*.md")):
        if md.name == "_overview.md":
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _ = nac_doc_lib.parse_frontmatter(text)
        if fm.get("stub", "false").lower() == "true":
            continue
        last_verified = fm.get("last_verified")
        code_file = fm.get("code_file")
        if not last_verified or not code_file:
            continue
        code_path = root / code_file
        if not code_path.exists():
            continue
        commits = _commits_since(root, last_verified, code_path)
        if commits > 0:
            stale.append(
                StaleEntry(
                    rel_md=md.relative_to(root).as_posix(),
                    rel_code=code_file,
                    last_verified=last_verified,
                    commits_since=commits,
                )
            )
    return stale


def _commits_since(root: Path, since: str, path: Path) -> int:
    """Return the number of commits touching `path` strictly after `since`."""
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                f"--since={since} 23:59:59",
                "--pretty=format:%H",
                "--",
                str(path.relative_to(root)),
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return 0
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return len(lines)


def main() -> int:
    stale = audit()
    if not stale:
        print("[audit_nac_doc] All mirror mds are up-to-date.")
        return 0
    print(f"[audit_nac_doc] {len(stale)} stale mirror md(s):")
    for s in stale:
        print(f"  - {s.rel_md}")
        print(f"      code: {s.rel_code}")
        print(f"      last_verified: {s.last_verified}   commits since: {s.commits_since}")
    return 0  # non-blocking


if __name__ == "__main__":
    raise SystemExit(main())
