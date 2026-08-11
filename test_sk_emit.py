#!/usr/bin/env python3
"""
test_sk_emit.py — the acceptance gate for the emitter pass.

WRITTEN BEFORE THE WORK. This file is expected to FAIL until sk_emit.py exists
and satisfies every claim below. That ordering is the point: enforcement
precedes content, and a gate written after the thing it judges is a gate shaped
around what was built rather than what was required.

What it enforces, and why each one matters:

  A. Every required emitter exists and is callable.
  B. Emitters are DETERMINISTIC. Run twice on identical input, get identical
     output modulo timestamp. This is the single check a hand-written artifact
     cannot pass, because a model asked the same question twice does not
     produce byte-identical prose. Determinism is what distinguishes a
     measurement from a claim.
  C. Emitted artifacts declare evidence_source: program and name themselves in
     produced_by_program with their own real file hash.
  D. Emitted artifacts validate against their registered schemas.
  E. Emitters DETECT rather than assert: given a tampered input, the emitter
     must report the failure rather than emit a passing artifact. An emitter
     that always emits `pass` is decoration.
  F. A full emitted run passes sk-verify with zero critical and zero error.

Run:  python3 test_sk_emit.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
EMIT = os.path.join(HERE, "sk_emit.py")
KEY = os.path.join(HERE, "key.repaired.yaml")
SCHEMAS = os.path.join(HERE, "schemas", "artifacts")
LEDGER = os.path.join(HERE, "ledger", "solomons-key-builder-ledger.jsonl")

# Emitters required by this pass, scoped to the four gates protocol_build_route
# requires. Not the full set — a smaller tranche that actually runs beats a
# complete one that does not.
REQUIRED_EMITTERS = [
    "inventory",    # source_inventory_artifact
    "boundary",     # source_boundary_lock_artifact
    "taskframe",    # task_frame_artifact + task_frame_validation_artifact
    "route",        # lot_route_artifact
    "validate",     # validation_report_artifact
    "telemetry",    # telemetry_trace_artifact
    "gate",         # gate_decision_artifact (automatic gates only)
]

# Fields allowed to differ between two runs of the same emitter on the same
# input. Everything else must be byte-identical.
NONDETERMINISTIC_FIELDS = {"timestamp", "run_id"}

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results: list[tuple[str, str, str]] = []


def check(name: str, status: str, detail: str = "") -> None:
    results.append((status, name, detail))


def ok(name: str, cond: bool, detail: str = "") -> None:
    check(name, PASS if cond else FAIL, detail)


def emit(*args, cwd: str | None = None) -> tuple[int, str, str]:
    p = subprocess.run(
        [sys.executable, EMIT, *args],
        capture_output=True, text=True, cwd=cwd or HERE,
    )
    return p.returncode, p.stdout, p.stderr


def strip_nondet(d):
    if isinstance(d, dict):
        return {k: strip_nondet(v) for k, v in d.items() if k not in NONDETERMINISTIC_FIELDS}
    if isinstance(d, list):
        return [strip_nondet(x) for x in d]
    return d


def main() -> int:
    if not os.path.exists(EMIT):
        check("sk_emit_exists", FAIL, f"{EMIT} does not exist — this pass has not been built")
        report()
        return 1
    check("sk_emit_exists", PASS)

    # --- A. every emitter is present and callable --------------------
    rc, out, err = emit("--list")
    ok("emit_list_works", rc == 0, err.strip()[:120])
    for name in REQUIRED_EMITTERS:
        ok(f"emitter_{name}_declared", name in out, f"'{name}' not in --list output")

    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "src")
        os.makedirs(src)
        for i, content in enumerate(["alpha\n", "beta\n", "gamma\n"]):
            open(os.path.join(src, f"file_{i}.txt"), "w").write(content)

        out1 = os.path.join(td, "a")
        out2 = os.path.join(td, "b")

        # --- B. determinism ------------------------------------------
        rc1, _, e1 = emit("inventory", "--source", src, "--out", out1)
        rc2, _, e2 = emit("inventory", "--source", src, "--out", out2)
        ok("inventory_runs", rc1 == 0 and rc2 == 0, (e1 or e2).strip()[:160])

        f1 = os.path.join(out1, "source_inventory_artifact.json")
        f2 = os.path.join(out2, "source_inventory_artifact.json")
        if os.path.exists(f1) and os.path.exists(f2):
            a1 = strip_nondet(json.load(open(f1)))
            a2 = strip_nondet(json.load(open(f2)))
            ok("inventory_is_deterministic", a1 == a2,
               "two runs on identical input produced different artifacts")

            # --- C. self-identification ------------------------------
            art = json.load(open(f1))
            ok("declares_evidence_source_program",
               art.get("evidence_source") == "program",
               f"got {art.get('evidence_source')!r}")
            prog = art.get("produced_by_program") or {}
            ok("names_producing_program", bool(prog.get("name")), "produced_by_program.name missing")
            real = hashlib.sha256(open(EMIT, "rb").read()).hexdigest()
            ok("program_hash_is_real", prog.get("sha256") == real,
               f"artifact records {str(prog.get('sha256'))[:12]}…, sk_emit.py is {real[:12]}… — "
               "the hash must be computed, not written in")

            # --- E. detection, not assertion -------------------------
            open(os.path.join(src, "file_0.txt"), "w").write("TAMPERED\n")
            out3 = os.path.join(td, "c")
            emit("inventory", "--source", src, "--out", out3)
            f3 = os.path.join(out3, "source_inventory_artifact.json")
            if os.path.exists(f3):
                a3 = json.load(open(f3))
                ok("inventory_reflects_change",
                   (a3.get("body") or {}).get("inventory_sha256")
                   != (art.get("body") or {}).get("inventory_sha256"),
                   "source changed but the inventory hash did not — the emitter is not reading the files")

                rc4, _, _ = emit("boundary", "--baseline", f1, "--current", f3,
                                 "--out", os.path.join(td, "d"))
                fb = os.path.join(td, "d", "source_boundary_lock_artifact.json")
                if os.path.exists(fb):
                    ab = json.load(open(fb))
                    ok("boundary_detects_modification",
                       (ab.get("body") or {}).get("unmodified") is False,
                       "source was modified and the boundary lock still reports unmodified")
                    ok("boundary_exits_nonzero_on_violation", rc4 != 0,
                       "a detected boundary violation must fail the process, not just be noted")
                else:
                    check("boundary_detects_modification", FAIL, "no boundary artifact emitted")
                    check("boundary_exits_nonzero_on_violation", FAIL, "no boundary artifact emitted")
            else:
                check("inventory_reflects_change", FAIL, "no artifact on second inventory run")
        else:
            for n in ("inventory_is_deterministic", "declares_evidence_source_program",
                      "names_producing_program", "program_hash_is_real",
                      "inventory_reflects_change", "boundary_detects_modification"):
                check(n, FAIL, "inventory emitter produced no artifact")

    # --- D + F. a full emitted run passes sk-verify -------------------
    with tempfile.TemporaryDirectory() as td:
        rundir = os.path.join(td, "run")
        rc, out, err = emit("run", "--key", KEY, "--route", "protocol_build_route",
                            "--ledger", LEDGER, "--out", rundir)
        ok("full_run_emits", rc == 0, err.strip()[:200])
        if os.path.isdir(rundir):
            p = subprocess.run(
                [sys.executable, os.path.join(HERE, "sk_verify.py"), rundir,
                 "--key", KEY, "--schemas", SCHEMAS, "--ledger", LEDGER],
                capture_output=True, text=True, cwd=HERE,
            )
            ok("emitted_run_passes_sk_verify", p.returncode == 0,
               p.stdout.strip().splitlines()[-1] if p.stdout else p.stderr[:160])
        else:
            check("emitted_run_passes_sk_verify", FAIL, "no run directory emitted")

    report()
    return 1 if any(s == FAIL for s, _, _ in results) else 0


def report() -> None:
    width = max((len(n) for _, n, _ in results), default=10)
    for status, name, detail in results:
        line = f"  {status}  {name.ljust(width)}"
        if detail and status != PASS:
            line += f"   {detail}"
        print(line)
    failed = sum(1 for s, _, _ in results if s == FAIL)
    print(f"\n  {len(results) - failed} passed, {failed} failed")
    if failed:
        print("\n  This test is the acceptance gate for the emitter pass.")
        print("  It is expected to fail until sk_emit.py is built to spec.")


if __name__ == "__main__":
    sys.exit(main())
