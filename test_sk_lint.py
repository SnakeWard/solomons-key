#!/usr/bin/env python3
"""
test_sk_lint.py — the judge.

Three claims are under test:

  1. The green baseline lints clean (no ERROR findings, exit 0).
  2. Every red fixture trips the rule it is bound to in manifest.json.
  3. Every rule in sk_lint.RULES has at least one red fixture.

Claim 3 is the one that matters most. Without it, rules can accumulate that
have never been shown a violation, which is exactly the ceremony problem this
tool exists to solve.

No pytest dependency. Run directly:  python3 test_sk_lint.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile

import yaml

import sk_lint

HERE = os.path.dirname(os.path.abspath(__file__))
GREEN = os.path.join(HERE, "key.repaired.yaml")
CORPUS = os.path.join(HERE, "redcorpus")

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))


def load(path: str) -> dict:
    return yaml.safe_load(open(path, encoding="utf-8"))


def main() -> int:
    # --- 1. green baseline -------------------------------------------
    if not os.path.exists(GREEN):
        print(f"missing green baseline: {GREEN}", file=sys.stderr)
        return 3
    findings = sk_lint.lint(load(GREEN))
    errors = [f for f in findings if f.severity == sk_lint.ERROR]
    check(
        "green_baseline_has_no_errors",
        not errors,
        "" if not errors else f"{len(errors)} error(s): {[f.rule for f in errors]}",
    )
    with contextlib.redirect_stdout(io.StringIO()):
        rc = sk_lint.main([GREEN, "--json"])
    check("green_baseline_exit_zero", rc == 0, "" if rc == 0 else f"exit {rc}")

    with tempfile.TemporaryDirectory() as td:
        repaired = os.path.join(td, "key.repaired.yaml")
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "repair_pass.py"),
             os.path.join(HERE, "key.yaml"), repaired],
            capture_output=True,
            text=True,
        )
        repaired_bytes = open(repaired, "rb").read() if os.path.isfile(repaired) else b""
        portable_repair = proc.returncode == 0 and b"\r\n" not in repaired_bytes
        check(
            "repair_output_uses_canonical_lf",
            portable_repair,
            "" if portable_repair else
            (proc.stderr.strip() or "repair output contains CRLF bytes"),
        )

    # --- 2. red fixtures ---------------------------------------------
    manifest_path = os.path.join(CORPUS, "manifest.json")
    if not os.path.exists(manifest_path):
        print(f"missing red corpus manifest: {manifest_path}", file=sys.stderr)
        return 3
    manifest = json.load(open(manifest_path, encoding="utf-8"))

    covered: set[str] = set()
    for case in manifest["cases"]:
        rule_id, fixture = case["rule"], case["fixture"]
        path = os.path.join(CORPUS, fixture)
        covered.add(rule_id)
        try:
            doc = load(path)
        except Exception as exc:
            check(f"{rule_id}_fixture_parses", False, str(exc))
            continue
        tripped = {f.rule for f in sk_lint.lint(doc)}
        check(
            f"{rule_id}_trips_on_{fixture}",
            rule_id in tripped,
            "" if rule_id in tripped else f"rule silent; tripped instead: {sorted(tripped)}",
        )

    # --- 3. rule coverage --------------------------------------------
    uncovered = sorted(set(sk_lint.RULES) - covered)
    check(
        "every_rule_has_a_red_fixture",
        not uncovered,
        "" if not uncovered else f"no fixture for: {uncovered}",
    )

    # --- report -------------------------------------------------------
    width = max(len(n) for _, n, _ in results)
    for status, name, detail in results:
        line = f"  {status}  {name.ljust(width)}"
        if detail:
            line += f"   {detail}"
        print(line)

    failed = sum(1 for s, _, _ in results if s == FAIL)
    print(f"\n  {len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
