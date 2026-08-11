#!/usr/bin/env python3
"""
test_sk_verify.py — does the judge convict?

  1. The conforming run conforms. No critical, no error.
  2. Every violating run is rejected by the rule it is bound to.
  3. RUN06 and RUN12 are asserted at CRITICAL severity specifically, because
     they carry gate_bypass_attempt and test_bypass_attempt — the two failures
     the KEY classifies critical and no gate can detect.
  4. Every RUN rule has at least one violating run, or a declared reason it
     does not.

Run:  python3 test_sk_verify.py
"""

from __future__ import annotations

import json
import os
import sys

import yaml

import sk_artifacts as A
import sk_verify as V

HERE = os.path.dirname(os.path.abspath(__file__))
KEY = os.path.join(HERE, "key.repaired.yaml")
SCHEMAS = os.path.join(HERE, "schemas", "artifacts")
RUNS = os.path.join(HERE, "runs")
LEDGER = os.path.join(HERE, "ledger", "solomons-key-builder-ledger.jsonl")

ALL_RULES = [f"RUN{i:02d}" for i in range(1, 17)]

# Rules with no run fixture, and why. Declared gaps, never silent ones.
UNCOVERED_OK = {
    "RUN01": "malformed run.json is a load failure, covered by the CLI's exit-3 path",
}

BYPASS_RULES = {"RUN06", "RUN12"}

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))


def run_verify(run_dir: str, key_doc, registry, by_artifact, ledger):
    run = V.Run(run_dir)
    return V.verify_run(run, key_doc, KEY, registry, by_artifact, ledger, SCHEMAS)


def main() -> int:
    for p, label in ((KEY, "KEY"), (SCHEMAS, "schemas"), (RUNS, "runs")):
        if not os.path.exists(p):
            print(f"missing {label}: {p}", file=sys.stderr)
            return 3

    key_doc = yaml.safe_load(open(KEY, encoding="utf-8"))
    registry, by_artifact = A.load_registry(SCHEMAS)
    ledger = (
        [json.loads(l) for l in open(LEDGER, encoding="utf-8") if l.strip()]
        if os.path.exists(LEDGER) else None
    )

    # --- 1. the conforming run conforms ------------------------------
    good = run_verify(os.path.join(RUNS, "good"), key_doc, registry, by_artifact, ledger)
    blocking = [x for x in good if x.severity in (V.CRITICAL, V.ERROR)]
    check(
        "conforming_run_conforms",
        not blocking,
        "" if not blocking else "; ".join(f"[{x.rule}] {x.message[:90]}" for x in blocking[:3]),
    )

    # --- 2 & 3. violating runs are convicted -------------------------
    manifest = json.load(open(os.path.join(RUNS, "manifest.json"), encoding="utf-8"))
    covered: set[str] = set()
    for case in manifest["cases"]:
        name, expected = case["run"], case["expected_rule"]
        covered.add(expected)
        vs = run_verify(os.path.join(RUNS, name), key_doc, registry, by_artifact, ledger)
        rules = {x.rule for x in vs}
        check(
            f"convicts_{name}",
            expected in rules,
            "" if expected in rules else f"expected {expected}, got {sorted(rules) or 'nothing'}",
        )
        if expected in BYPASS_RULES:
            crit = {x.rule for x in vs if x.severity == V.CRITICAL}
            check(
                f"{name}_is_critical",
                expected in crit,
                f"{expected} fired but not at CRITICAL — bypass must not degrade to a warning",
            )
            fids = {x.failure_id for x in vs if x.rule == expected}
            want = "gate_bypass_attempt" if expected == "RUN06" else "test_bypass_attempt"
            check(f"{name}_carries_{want}", want in fids, f"failure_ids seen: {sorted(f for f in fids if f)}")

    # --- 4. rule coverage --------------------------------------------
    gaps = [r for r in ALL_RULES if r not in covered and r not in UNCOVERED_OK]
    check("every_run_rule_covered_or_declared", not gaps, f"undeclared gaps: {gaps}")

    # --- 5. no violating run is accidentally clean -------------------
    clean = []
    for case in manifest["cases"]:
        vs = run_verify(os.path.join(RUNS, case["run"]), key_doc, registry, by_artifact, ledger)
        if not [x for x in vs if x.severity in (V.CRITICAL, V.ERROR)]:
            clean.append(case["run"])
    check("no_violating_run_passes", not clean, f"passed unexpectedly: {clean}")

    # --- report -------------------------------------------------------
    width = max(len(n) for _, n, _ in results)
    for status, name, detail in results:
        line = f"  {status}  {name.ljust(width)}"
        if detail and status == FAIL:
            line += f"   {detail}"
        print(line)
    failed = sum(1 for s, _, _ in results if s == FAIL)
    print(f"\n  {len(results) - failed} passed, {failed} failed")
    if UNCOVERED_OK:
        print("\n  declared coverage gaps:")
        for r, why in sorted(UNCOVERED_OK.items()):
            print(f"    {r}  {why}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
