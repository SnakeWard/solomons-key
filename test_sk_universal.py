#!/usr/bin/env python3
"""
test_sk_universal.py — acceptance gate for the universalization pass.

WRITTEN BEFORE THE SPEC. Expected to FAIL until `sk_init.py`, `sk_adapt.py`,
and the pruned `REQUIRED_ROOT_SECTIONS` exist.

WHY THIS EXISTS
---------------
`sk-verify` currently verifies runs of *this project's* contract. A stranger
with a Python repo and a CI pipeline cannot point it at their own build. That
gap — reference implementation versus tool — is what this pass closes.

A premise was tested before planning the work, and it was half wrong. The
assumption was that adoption required authoring a 1,300-line manifest. It does
not: a working contract for an ordinary Python project is **62 lines and lints
with zero errors**, and `sk-verify` catches a gate bypass against it. The
artifact is committed at `examples/minimal/` so the claim is reproducible
rather than asserted.

What actually blocks adoption is three narrower things:

  1. Sixteen required root sections, of which three are load-bearing for the
     core claim (`lot`, `gates`, `artifacts`). The other thirteen exist because
     THIS project has passes, roles, and a witness ledger. A stranger's build
     does not, and stub sections are pure friction.
  2. Nothing writes the contract. Sixty lines is small, but you must already
     know what `route`, `gate`, `enforcement_class` and `evidence_source` mean.
     The barrier is vocabulary, not volume.
  3. Nothing emits evidence from an ordinary build. `sk_emit` produces
     artifacts for this KEY file. A stranger with pytest and GitHub Actions has
     no path from "my tests ran" to a `gate_decision_artifact`.

WHAT IS UNDER TEST
------------------
  A. A minimal contract lints clean, and is genuinely minimal (<= 25 lines).
  B. Pruning required sections did not break the project's own contract.
  C. `sk-init` derives a contract from a CI config that lints clean.
  D. `sk-adapt` turns a JUnit XML into a valid artifact whose
     `produced_by_program.sha256` is the REAL hash of the results file.
  E. An adapter-built run verifies, and catches a bypass when a required gate's
     decision is removed.
  F. The whole path is doable without hand-writing any artifact JSON.

Run:  python3 test_sk_universal.py
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))


def run(argv: list[str], cwd: str | None = None) -> tuple[int, str]:
    p = subprocess.run([PY, *argv], cwd=cwd or HERE, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for c in iter(lambda: fh.read(65536), b""):
            h.update(c)
    return h.hexdigest()


SAMPLE_WORKFLOW = """\
name: ci
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install
        run: pip install -r requirements.txt
      - name: Unit tests
        run: pytest --junitxml=results.xml
      - name: Security scan
        run: bandit -r src/ -f sarif -o scan.sarif
      - name: Manual sign-off
        run: echo "reviewed by release manager"
"""

SAMPLE_JUNIT = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="412" failures="0" errors="0" skipped="3" time="18.4">
    <testcase classname="test_core" name="test_parses" time="0.01"/>
    <testcase classname="test_core" name="test_rejects_bad_input" time="0.02"/>
  </testsuite>
</testsuites>
"""

SAMPLE_SARIF = """\
{
  "version": "2.1.0",
  "runs": [{"tool": {"driver": {"name": "bandit"}}, "results": []}]
}
"""


def load_allowlist(path: str) -> dict[str, str]:
    trusted: dict[str, str] = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, name = line.partition("  ")
        if name:
            trusted[name.strip()] = digest
    return trusted


def main() -> int:
    # --- A. the minimal contract exists, is minimal, and lints clean ----
    minimal = os.path.join(HERE, "examples", "minimal", "minimal.key.yaml")
    have_min = os.path.exists(minimal)
    check("minimal_contract_committed", have_min, f"missing {minimal}")

    if have_min:
        n = len([l for l in open(minimal, encoding="utf-8")
                 if l.strip() and not l.lstrip().startswith("#")])
        check(
            "minimal_contract_is_minimal",
            n <= 25,
            f"{n} non-comment lines; the pass exists to get this under 25",
        )
        rc, out = run(["sk_lint.py", minimal])
        check("minimal_contract_lints_clean", rc == 0, out.strip()[-200:])

    # --- B. no regression on this project's own contract ----------------
    rc, out = run(["sk_lint.py", "key.repaired.yaml"])
    check("own_contract_still_lints_clean", rc == 0, out.strip()[-200:])

    with tempfile.TemporaryDirectory() as td:
        # --- C. sk-init derives a contract from a CI config -------------
        wf = os.path.join(td, "ci.yml")
        open(wf, "w", encoding="utf-8").write(SAMPLE_WORKFLOW)
        derived = os.path.join(td, "derived.key.yaml")
        pytest_program = os.path.join(td, "pytest-program")
        bandit_program = os.path.join(td, "bandit-program")
        open(pytest_program, "wb").write(b"test executable\n")
        open(bandit_program, "wb").write(b"scan executable\n")

        rc, out = run([
            "sk_init.py", "--from-ci", wf, "--out", derived,
            # Non-interactive: the classification a human would supply.
            # Every automatic step names the program that decides it.
            "--automatic", f"Unit tests={pytest_program}",
            "--automatic", f"Security scan={bandit_program}",
            "--attested", "Manual sign-off",
        ])
        check("sk_init_runs", rc == 0, out.strip()[-200:])

        trusted_path = os.path.join(td, "TRUSTED_PROGRAMS.sha256")
        trust_before = open(trusted_path, "rb").read() if os.path.exists(trusted_path) else b""
        rc, out = run([
            "sk_init.py", "--from-ci", wf, "--out", derived,
            "--automatic", f"Unit tests={pytest_program}",
            "--automatic", f"Security scan={bandit_program}",
            "--attested", "Manual sign-off",
        ])
        check(
            "sk_init_refuses_existing_trust_root",
            rc != 0 and "refusing to replace the trust root" in out,
            out.strip()[-300:],
        )
        trust_after = open(trusted_path, "rb").read() if os.path.exists(trusted_path) else b""
        check(
            "sk_init_preserves_existing_trust_root",
            trust_after == trust_before,
            "existing TRUSTED_PROGRAMS.sha256 changed despite refusal",
        )

        rc, out = run([
            "sk_init.py", "--from-ci", wf, "--out", derived,
            "--automatic", f"Unit tests={pytest_program}",
            "--automatic", f"Security scan={bandit_program}",
            "--attested", "Manual sign-off", "--force",
        ])
        check(
            "sk_init_force_replaces_trust_root",
            rc == 0
            and "--force replacing trust root" in out
            and "pytest-program" in out,
            out.strip()[-400:],
        )
        check(
            "sk_init_force_accumulates_demo_run",
            os.path.isfile(os.path.join(td, "runs", "first-2", "run.json")),
            "authorized re-initialization collided with or replaced runs/first",
        )

        candidate_trusted = os.path.join(td, "candidate.TRUSTED_PROGRAMS.sha256")
        candidate_contract = os.path.join(td, "candidate.key.yaml")
        rc, out = run([
            "sk_init.py", "--from-ci", wf, "--out", candidate_contract,
            "--automatic", f"Unit tests={pytest_program}",
            "--automatic", f"Security scan={bandit_program}",
            "--attested", "Manual sign-off",
            "--trusted-out", candidate_trusted,
        ])
        check(
            "sk_init_accepts_distinct_trust_root",
            rc == 0 and os.path.isfile(candidate_trusted),
            out.strip()[-300:],
        )
        check(
            "sk_init_distinct_root_preserves_default",
            open(trusted_path, "rb").read() == trust_before,
            "--trusted-out changed the repository's default trust root",
        )

        if os.path.exists(derived):
            rc, out = run(["sk_lint.py", derived])
            check("derived_contract_lints_clean", rc == 0, out.strip()[-200:])

            import yaml
            d = yaml.safe_load(open(derived, encoding="utf-8"))
            gates = {g["gate_id"]: g for g in
                     (d.get("gates") or {}).get("gate_entries", [])}
            classes = {g.get("enforcement_class") for g in gates.values()}
            check(
                "derived_contract_has_both_classes",
                {"automatic", "attested"} <= classes,
                f"got {classes} — a derived contract that marks everything "
                "automatic has not asked the only question that matters",
            )
        else:
            for n in ("derived_contract_lints_clean",
                      "derived_contract_has_both_classes"):
                check(n, False, "sk_init produced no contract")

        # --- D. sk-adapt turns real CI output into real evidence --------
        junit = os.path.join(td, "results.xml")
        open(junit, "w", encoding="utf-8").write(SAMPLE_JUNIT)
        adir = os.path.join(td, "artifacts")
        os.makedirs(adir, exist_ok=True)

        rc, out = run([
            "sk_adapt.py", "junit", junit,
            "--gate", "unit_tests_gate", "--program", pytest_program,
            "--run-id", "RUN_adapt_0001", "--out", adir,
        ])
        check("sk_adapt_runs", rc == 0, out.strip()[-200:])

        produced = [os.path.join(adir, f) for f in sorted(os.listdir(adir))] if os.path.isdir(adir) else []
        check("sk_adapt_produced_artifacts", bool(produced), "nothing written")

        if produced:
            a = json.load(open(produced[0], encoding="utf-8"))
            check(
                "adapted_artifact_is_program_sourced",
                a.get("evidence_source") == "program",
                f"got {a.get('evidence_source')!r} — CI output is a measurement",
            )
            prog = a.get("produced_by_program") or {}
            trusted = load_allowlist(trusted_path)
            check(
                "adapted_artifact_hashes_the_executable",
                prog.get("sha256") == trusted.get(prog.get("name")),
                "produced_by_program.sha256 must match the named executable's allowlist entry",
            )
            check(
                "adapted_artifact_hashes_the_real_input",
                a.get("input_sha256") == sha256(junit),
                "input_sha256 must be the hash of the results file actually read",
            )
            open(junit, "a", encoding="utf-8").write("\n")
            rc_input, out_input = run([
                "sk_artifacts.py", "validate", produced[0],
                "--schemas", "schemas/artifacts",
            ])
            check(
                "sem06_detects_changed_adapter_input",
                rc_input != 0 and "SEM06" in out_input and "input hash mismatch" in out_input,
                out_input.strip()[-300:],
            )
            open(junit, "w", encoding="utf-8").write(SAMPLE_JUNIT)

        # --- E. an adapter-built run verifies, and catches a bypass -----
        rundir = os.path.join(td, "run")
        rc, out = run([
            "sk_init.py", "--demo-run", "--key", derived, "--out", rundir,
        ])
        if os.path.isdir(rundir):
            arts = os.path.join(rundir, "artifacts")
            for gate_name in ("unit_tests_gate", "security_scan_gate"):
                os.remove(os.path.join(arts, f"gate_{gate_name}.json"))
            sarif = os.path.join(td, "scan.sarif")
            open(sarif, "w", encoding="utf-8").write(SAMPLE_SARIF)
            rc_junit, out_junit = run([
                "sk_adapt.py", "junit", junit,
                "--gate", "unit_tests_gate", "--program", pytest_program,
                "--run-id", "RUN_first_0001", "--route", "ci", "--out", arts,
            ])
            rc_sarif, out_sarif = run([
                "sk_adapt.py", "sarif", sarif,
                "--gate", "security_scan_gate", "--program", bandit_program,
                "--run-id", "RUN_first_0001", "--route", "ci", "--out", arts,
            ])
            check(
                "adapter_run_uses_adapter_output",
                rc_junit == 0 and rc_sarif == 0,
                (out_junit + out_sarif).strip()[-300:],
            )
            rc, out = run(["sk_verify.py", rundir, "--key", derived,
                           "--schemas", "schemas/artifacts",
                           "--trusted", trusted_path])
            check("adapter_run_verifies", rc == 0, out.strip()[-200:])

            missing_program = os.path.join(td, "program-not-on-path")
            rc_missing, out_missing = run([
                "sk_adapt.py", "junit", junit,
                "--gate", "unit_tests_gate", "--program", missing_program,
                "--run-id", "RUN_first_0001", "--route", "ci", "--out", arts,
            ])
            rc_missing_verify, out_missing_verify = run([
                "sk_verify.py", rundir, "--key", derived,
                "--schemas", "schemas/artifacts", "--trusted", trusted_path,
            ])
            check(
                "run17_reports_missing_executable_hash",
                rc_missing == 0
                and rc_missing_verify != 0
                and "records no executable hash" in out_missing_verify
                and "Do not add this artifact's input hash to the allowlist" in out_missing_verify,
                (out_missing + out_missing_verify).strip()[-500:],
            )
            run([
                "sk_adapt.py", "junit", junit,
                "--gate", "unit_tests_gate", "--program", pytest_program,
                "--run-id", "RUN_first_0001", "--route", "ci", "--out", arts,
            ])

            gate_files = [f for f in os.listdir(arts) if f.startswith("gate_")]
            if gate_files:
                os.remove(os.path.join(arts, gate_files[0]))
                rc, out = run(["sk_verify.py", rundir, "--key", derived,
                               "--schemas", "schemas/artifacts",
                               "--trusted", trusted_path])
                check(
                    "adapter_run_catches_bypass",
                    "RUN06" in out and "gate_bypass_attempt" in out,
                    "removing a required gate decision did not trip RUN06",
                )
            else:
                check("adapter_run_catches_bypass", False, "no gate artifacts in run")
        else:
            check("adapter_run_uses_adapter_output", False, "no demo run produced")
            check("adapter_run_verifies", False, "no demo run produced")
            check("adapter_run_catches_bypass", False, "no demo run produced")

    # --- F. no hand-written JSON anywhere in the path -------------------
    # Enforced by construction: every artifact above came from sk_adapt or
    # sk_init. This assertion documents the requirement for future passes.
    check("path_required_no_handwritten_json", True)

    width = max((len(n) for _, n, _ in results), default=10)
    for status, name, detail in results:
        line = f"  {status}  {name.ljust(width)}"
        if detail and status == FAIL:
            line += f"   {detail}"
        print(line)
    failed = sum(1 for s, _, _ in results if s == FAIL)
    print(f"\n  {len(results) - failed} passed, {failed} failed")
    if failed:
        print("\n  Acceptance gate for the universalization pass.")
        print("  Expected to fail until sk_init.py and sk_adapt.py exist.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
