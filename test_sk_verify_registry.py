#!/usr/bin/env python3
"""Every sk_verify.RULES entry is bound to a fixture or a declared exemption.

Mirrors test_sk_lint.py's every_rule_has_a_red_fixture, against the run
corpus. Exemptions live on sk_verify.UNCOVERED_OK and are themselves
asserted: if a loadable run starts tripping an exempted rule, the
exemption is stale.
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

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))


def main() -> int:
    check("RULES_is_enumerable", isinstance(V.RULES, dict) and len(V.RULES) > 0)
    check(
        "UNCOVERED_OK_keys_are_in_RULES",
        set(V.UNCOVERED_OK) <= set(V.RULES),
        f"orphan exemptions: {sorted(set(V.UNCOVERED_OK) - set(V.RULES))}",
    )

    key_doc = yaml.safe_load(open(KEY, encoding="utf-8"))
    registry, by_artifact = A.load_registry(SCHEMAS)
    ledger = (
        [json.loads(line) for line in open(LEDGER, encoding="utf-8") if line.strip()]
        if os.path.exists(LEDGER)
        else None
    )

    tripped: dict[str, list[str]] = {rid: [] for rid in V.RULES}
    for name in sorted(os.listdir(RUNS)):
        run_dir = os.path.join(RUNS, name)
        if not os.path.isfile(os.path.join(run_dir, "run.json")):
            continue
        try:
            run = V.Run(run_dir)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        vs = V.verify_run(run, key_doc, KEY, registry, by_artifact, ledger, SCHEMAS)
        for item in vs:
            if item.rule in tripped:
                tripped[item.rule].append(name)

    for rid in sorted(V.RULES):
        bound = tripped[rid]
        exempt = rid in V.UNCOVERED_OK
        if exempt:
            check(
                f"{rid}_exemption_still_untripped",
                not bound,
                f"exemption stale; now trips on {bound}",
            )
            check(
                f"{rid}_exemption_has_reason",
                bool(V.UNCOVERED_OK[rid].strip()),
                "exemption has no written reason",
            )
        else:
            check(
                f"{rid}_bound_to_a_run",
                bool(bound),
                "no run trips this rule and it is not in UNCOVERED_OK",
            )

    undeclared = sorted(
        rid for rid, bound in tripped.items() if not bound and rid not in V.UNCOVERED_OK
    )
    check(
        "every_rule_bound_or_declared",
        not undeclared,
        f"undeclared gaps: {undeclared}",
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
    if V.UNCOVERED_OK:
        print("\n  declared coverage gaps:")
        for rid, why in sorted(V.UNCOVERED_OK.items()):
            print(f"    {rid}  {why}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
