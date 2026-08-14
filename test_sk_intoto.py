#!/usr/bin/env python3
"""Fixtures for the optional in-toto exporter / consumer.

Does not import sk_emit or sk_verify. Existing RUN/SEM/SK outcomes are
out of scope. This suite only checks interchange: emit a statement from
an existing run, consume a valid one, reject a bad one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

from solomons_key.in_toto import (
    PREDICATE_TYPE,
    STATEMENT_TYPE,
    consume_path,
    consume_statement,
    emit_statement,
)

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "solomons_key", "in_toto", "fixtures")
GOOD_RUN = os.path.join(HERE, "runs", "good")
RUN06 = os.path.join(HERE, "runs", "RUN06_gate_bypassed")
PY = sys.executable

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))


def main() -> int:
    good = emit_statement(GOOD_RUN)
    check(
        "emit_good_has_statement_types",
        good.get("_type") == STATEMENT_TYPE
        and good.get("predicateType") == PREDICATE_TYPE,
        f"_type={good.get('_type')!r} predicateType={good.get('predicateType')!r}",
    )
    predicate = good.get("predicate") or {}
    check(
        "emit_good_carries_computed_or_asserted",
        predicate.get("computed_or_asserted") in ("computed", "asserted", "mixed")
        and predicate.get("evidence_source") in ("program", "attestation", "mixed"),
        str(predicate.get("computed_or_asserted")),
    )
    check(
        "emit_good_carries_gate_results",
        isinstance(predicate.get("gates"), list) and len(predicate["gates"]) >= 1,
        f"gates={len(predicate.get('gates') or [])}",
    )
    check(
        "emit_good_carries_producer_hashes",
        all(
            isinstance(item.get("sha256"), str) and len(item["sha256"]) == 64
            for item in predicate.get("producers") or []
        )
        and len(predicate.get("producers") or []) >= 1,
        str(predicate.get("producers")),
    )
    check(
        "emit_good_references_run_and_ledger",
        (predicate.get("run") or {}).get("run_id")
        and (predicate.get("ledger") or {}).get("entry_hashes"),
        str(predicate.get("run")),
    )
    check(
        "emit_good_includes_source_boundary_gate",
        any(
            gate.get("gate_id") == "source_boundary_gate"
            for gate in predicate.get("gates") or []
        ),
        [gate.get("gate_id") for gate in predicate.get("gates") or []],
    )
    consumed = consume_statement(good)
    check("consume_accepts_emitted_good", consumed.ok, "; ".join(consumed.errors))

    run06 = emit_statement(RUN06)
    run06_gates = {
        gate.get("gate_id") for gate in (run06.get("predicate") or {}).get("gates") or []
    }
    check(
        "emit_run06_omits_bypassed_gate",
        "source_boundary_gate" not in run06_gates and len(run06_gates) >= 1,
        sorted(run06_gates),
    )
    consumed06 = consume_statement(run06)
    check(
        "consume_accepts_emitted_run06_structure",
        consumed06.ok,
        "; ".join(consumed06.errors),
    )

    committed_good = os.path.join(FIXTURES, "from_runs_good.json")
    committed_run06 = os.path.join(FIXTURES, "from_run06.json")
    check(
        "committed_good_fixture_validates",
        consume_path(committed_good).ok,
        "; ".join(consume_path(committed_good).errors),
    )
    check(
        "committed_run06_fixture_validates",
        consume_path(committed_run06).ok,
        "; ".join(consume_path(committed_run06).errors),
    )
    with open(committed_good, encoding="utf-8") as handle:
        saved = json.load(handle)
    check(
        "committed_good_matches_live_emit",
        saved == good,
        "committed fixture drifted from emit_statement(runs/good)",
    )

    missing = consume_path(os.path.join(FIXTURES, "missing_predicate_type.json"))
    check(
        "reject_missing_predicate_type",
        not missing.ok and any("predicateType" in item for item in missing.errors),
        missing.errors,
    )
    wrong = consume_path(os.path.join(FIXTURES, "wrong_predicate_type.json"))
    check(
        "reject_wrong_predicate_type",
        not wrong.ok and any("predicateType" in item for item in wrong.errors),
        wrong.errors,
    )
    incomplete = consume_path(os.path.join(FIXTURES, "incomplete_predicate.json"))
    check(
        "reject_incomplete_predicate",
        not incomplete.ok,
        incomplete.errors,
    )
    malformed = consume_path(os.path.join(FIXTURES, "malformed.json"))
    check(
        "reject_malformed_json",
        not malformed.ok and any("malformed JSON" in item for item in malformed.errors),
        malformed.errors,
    )

    with tempfile.TemporaryDirectory(prefix="sk-intoto-") as temp:
        out = os.path.join(temp, "statement.json")
        proc = subprocess.run(
            [PY, "-m", "solomons_key.in_toto", "emit", GOOD_RUN, "-o", out],
            cwd=HERE,
            capture_output=True,
            text=True,
        )
        check(
            "cli_emit_exit_0",
            proc.returncode == 0 and os.path.isfile(out),
            proc.stderr,
        )
        proc = subprocess.run(
            [PY, "-m", "solomons_key.in_toto", "consume", out],
            cwd=HERE,
            capture_output=True,
            text=True,
        )
        check("cli_consume_good_exit_0", proc.returncode == 0, proc.stderr)
        proc = subprocess.run(
            [
                PY, "-m", "solomons_key.in_toto", "consume",
                os.path.join(FIXTURES, "malformed.json"),
            ],
            cwd=HERE,
            capture_output=True,
            text=True,
        )
        check("cli_consume_bad_exit_1", proc.returncode == 1, proc.stdout)

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
