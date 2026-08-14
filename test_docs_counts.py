#!/usr/bin/env python3
"""The three counted docs must match a live regeneration of the counts block.

No numbers are stored in this file. The expected block is produced by
build.counts_block(build.actual_counts()) — the same path build.py docs uses.
"""

from __future__ import annotations

import os
import sys

import build

HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))


def block_in(text: str) -> str | None:
    if build.COUNTS_BEGIN not in text or build.COUNTS_END not in text:
        return None
    start = text.index(build.COUNTS_BEGIN)
    end = text.index(build.COUNTS_END) + len(build.COUNTS_END)
    return text[start:end]


def main() -> int:
    expected = build.counts_block(build.actual_counts())
    required = {"README_sk-lint.md", "REPORT.md", "README.md"}
    check(
        "all_three_docs_are_generator_targets",
        required <= set(build.COUNTED_DOCS),
        str(build.COUNTED_DOCS),
    )
    for name in ("README_sk-lint.md", "REPORT.md", "README.md"):
        path = os.path.join(HERE, name)
        if not os.path.isfile(path):
            check(f"{name}_exists", False, "missing")
            continue
        found = block_in(open(path, encoding="utf-8").read())
        check(f"{name}_has_markers", found is not None, "no counts block")
        check(
            f"{name}_matches_live_block",
            found == expected,
            "" if found == expected else f"stale:\n{found}\nexpected:\n{expected}",
        )

    width = max((len(name) for _, name, _ in results), default=10)
    failed = 0
    for status, name, detail in results:
        line = f"  {status}  {name.ljust(width)}"
        if detail and status == FAIL:
            line += f"   {detail}"
        print(line)
        if status == FAIL:
            failed += 1
    print(f"\n  {len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
