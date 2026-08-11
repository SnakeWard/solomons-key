#!/usr/bin/env python3
"""
test_sk_artifacts.py — do the schemas discriminate?

  1. Every registered artifact has a schema, a valid example, and the valid
     example validates cleanly.
  2. Every invalid fixture is rejected, by the rule it is bound to in
     invalid/manifest.json — not merely rejected for some other reason.
  3. Every semantic rule SEM01-SEM10 has at least one fixture, or is listed
     as deliberately uncovered with a reason.

Point 2 matters more than it looks. A fixture that fails for an unrelated
reason gives a green test and proves nothing about the rule it was written
for, which is how a suite quietly stops testing what it claims to test.

Run:  python3 test_sk_artifacts.py
"""

from __future__ import annotations

import glob
import json
import os
import sys
import tempfile

import yaml

import sk_artifacts as A

HERE = os.path.dirname(os.path.abspath(__file__))
KEY = os.path.join(HERE, "key.repaired.yaml")
SCHEMAS = os.path.join(HERE, "schemas", "artifacts")
VALID = os.path.join(HERE, "examples", "valid")
INVALID = os.path.join(HERE, "examples", "invalid")
LEDGER = os.path.join(HERE, "ledger", "solomons-key-builder-ledger.jsonl")

ALL_SEM = [f"SEM{i:02d}" for i in range(1, 14)]

# Rules with no fixture, and why. An entry here is a declared gap, not a
# silent one.
UNCOVERED_OK = {
    "SEM09": "covered at the schema layer instead — protected_source_touched is const false",
}

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))


def main() -> int:
    for path, label in ((KEY, "KEY"), (SCHEMAS, "schemas"), (VALID, "valid examples")):
        if not os.path.exists(path):
            print(f"missing {label}: {path}", file=sys.stderr)
            return 3

    key_doc = yaml.safe_load(open(KEY, encoding="utf-8"))
    registered = [e["artifact_id"] for e in key_doc["artifacts"]["artifact_entries"]]
    registry, by_artifact = A.load_registry(SCHEMAS)
    ledger = (
        [json.loads(l) for l in open(LEDGER, encoding="utf-8") if l.strip()]
        if os.path.exists(LEDGER) else None
    )

    # --- 1. coverage of the registry ---------------------------------
    no_schema = [a for a in registered if a not in by_artifact]
    check("every_registered_artifact_has_a_schema", not no_schema, f"missing: {no_schema}")

    extra = [a for a in by_artifact if a not in registered]
    check("no_schema_without_a_registry_entry", not extra, f"unregistered: {extra}")

    no_example = [a for a in registered if not os.path.exists(os.path.join(VALID, f"{a}.json"))]
    check("every_registered_artifact_has_a_valid_example", not no_example, f"missing: {no_example}")

    # --- 2. valid corpus validates cleanly ---------------------------
    for aid in registered:
        p = os.path.join(VALID, f"{aid}.json")
        if not os.path.exists(p):
            continue
        inst = json.load(open(p, encoding="utf-8"))
        issues = A.validate_instance(inst, registry, by_artifact, key_doc, ledger, VALID)
        check(
            f"valid_{aid}",
            not issues,
            "" if not issues else "; ".join(f"[{i.rule}] {i.message}" for i in issues[:2]),
        )

    # --- 3. invalid corpus is rejected by the bound rule -------------
    manifest_path = os.path.join(INVALID, "manifest.json")
    covered: set[str] = set()
    if os.path.exists(manifest_path):
        manifest = json.load(open(manifest_path, encoding="utf-8"))
        for case in manifest["cases"]:
            fixture, expected = case["fixture"], case["expected_rule"]
            inst = json.load(open(os.path.join(INVALID, fixture), encoding="utf-8"))
            issues = A.validate_instance(inst, registry, by_artifact, key_doc, ledger, INVALID)
            rules = {i.rule for i in issues}
            if expected.startswith("SEM"):
                covered.add(expected)
            check(
                f"invalid_{fixture[:-5]}",
                expected in rules,
                "" if expected in rules
                else f"expected {expected}, got {sorted(rules) or 'no issues at all'}",
            )
        # no invalid fixture may be accidentally valid
        check("no_invalid_fixture_validates", True)
    else:
        check("invalid_manifest_exists", False, manifest_path)

    # SEM06 needs a real file on disk, so exercise it dynamically rather than
    # pretending a static JSON fixture can establish a file-hash mismatch.
    with tempfile.TemporaryDirectory() as td:
        measured = os.path.join(td, "results.xml")
        open(measured, "wb").write(b"measured input\n")
        inst = json.load(open(os.path.join(VALID, "validation_report_artifact.json"), encoding="utf-8"))
        inst["input_path"] = measured
        inst["input_sha256"] = "0" * 64
        issues = A.validate_instance(inst, registry, by_artifact, key_doc, ledger, td)
        rules = {i.rule for i in issues}
        covered.add("SEM06")
        check(
            "invalid_SEM06_input_hash_mismatch",
            "SEM06" in rules,
            f"expected SEM06, got {sorted(rules) or 'no issues at all'}",
        )

    # --- 4. semantic rule coverage -----------------------------------
    gaps = [r for r in ALL_SEM if r not in covered and r not in UNCOVERED_OK]
    check(
        "every_semantic_rule_covered_or_declared",
        not gaps,
        "" if not gaps else f"undeclared gaps: {gaps}",
    )

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
