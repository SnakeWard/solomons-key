#!/usr/bin/env python3
"""Argv paths must not leak drive or prefix into generated or reported bytes."""

from __future__ import annotations

import os
import sys

import yaml

import gen_runs as G
import sk_verify as V

HERE = os.path.dirname(os.path.abspath(__file__))
KEY = os.path.join(HERE, "key.repaired.yaml")
LEDGER = os.path.join(HERE, "ledger", "solomons-key-builder-ledger.jsonl")

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))


def main() -> int:
    key_doc = yaml.safe_load(open(KEY, encoding="utf-8"))
    head = "0" * 64

    m_rel, _ = G.build_good(key_doc, "key.repaired.yaml", r"ledger\solomons-key-builder-ledger.jsonl", head)
    m_abs, _ = G.build_good(key_doc, KEY, LEDGER, head)
    other_drive_key = os.path.join("C:\\", "not-the-repo", "key.repaired.yaml")
    m_out = {"key_file": G.manifest_path(other_drive_key)}

    check("relative_key_is_posix_repo_path", m_rel["key_file"] == "key.repaired.yaml", m_rel["key_file"])
    check(
        "relative_ledger_is_posix_repo_path",
        m_rel["ledger_file"] == "ledger/solomons-key-builder-ledger.jsonl",
        m_rel["ledger_file"],
    )
    check("absolute_key_collapses_to_same", m_abs["key_file"] == m_rel["key_file"], m_abs["key_file"])
    check("absolute_ledger_collapses_to_same", m_abs["ledger_file"] == m_rel["ledger_file"], m_abs["ledger_file"])
    check("no_backslash_in_manifest_paths", "\\" not in m_abs["key_file"] + m_abs["ledger_file"])
    check("outside_repo_uses_basename", m_out["key_file"] == "key.repaired.yaml", m_out["key_file"])

    in_repo = V.report_path(os.path.join(HERE, "runs", "good"))
    deep = V.report_path(os.path.join("C:\\", "deep", "nested", "path", "T3a", "good"))
    shallow = V.report_path(os.path.join("C:\\", "T3b", "good"))
    check("in_repo_run_dir_is_relative", in_repo == "runs/good", in_repo)
    check("outside_run_dirs_share_basename", deep == shallow == "good", f"{deep!r} vs {shallow!r}")

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
