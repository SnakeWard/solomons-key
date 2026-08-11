#!/usr/bin/env python3
"""Turn ordinary CI results into Solomon's Key evidence and a gate decision.

Supported inputs:
    sk_adapt.py junit results.xml --gate unit_tests_gate --program pytest --out artifacts/
    sk_adapt.py sarif scan.sarif --gate security_gate --program bandit --out artifacts/
    sk_adapt.py exit-code 0 --gate build_gate --program make --out artifacts/

The decision is derived from the input: JUnit failures/errors, SARIF error-level
results, and non-zero exit codes all produce a failing decision and exit 1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "1.0.0"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def program_token(program: str) -> str:
    try:
        return shlex.split(program, posix=os.name != "nt")[0]
    except (ValueError, IndexError):
        return program.strip().split()[0] if program.strip() else ""


def resolve_program(program: str) -> str | None:
    token = program_token(program)
    if not token:
        return None
    candidate = os.path.abspath(os.path.expanduser(token))
    return candidate if os.path.isfile(candidate) else shutil.which(token)


def envelope(
    artifact_id: str,
    body: dict[str, Any],
    *,
    run_id: str,
    program: str,
    program_hash: str | None,
    input_hash: str,
    input_path: str | None,
    route_id: str | None,
    argv: list[str],
) -> dict[str, Any]:
    artifact = {
        "artifact_id": artifact_id,
        "artifact_status": "validated",
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "pass_id": run_id,
        "timestamp": timestamp(),
        "produced_by_role": "build",
        "produced_by_actor": "user",
        "route_id": route_id,
        "required_gate": "artifact_requirement_gate",
        "ledger_ref": "pending",
        "claims_final_authority": False,
        "evidence_source": "program",
        "produced_by_program": {"name": program, "argv": argv},
        "input_sha256": input_hash,
        "body": body,
    }
    if program_hash:
        artifact["produced_by_program"]["sha256"] = program_hash
    if input_path:
        artifact["input_path"] = input_path
    return artifact


def write_json(path: str, value: Any) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(value, fh, indent=2, sort_keys=True)
        fh.write("\n")


def int_attr(node: ET.Element, name: str) -> int:
    try:
        return int(node.attrib.get(name, "0"))
    except ValueError:
        return 0


def read_junit(path: str) -> tuple[bool, str, list[dict[str, str]]]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag.endswith("testsuite") else [
        node for node in root.iter() if node.tag.endswith("testsuite")
    ]
    failures = sum(int_attr(suite, "failures") for suite in suites)
    errors = sum(int_attr(suite, "errors") for suite in suites)
    failed = failures + errors > 0
    findings = []
    if failed:
        findings.append({
            "rule": "junit",
            "severity": "high",
            "message": f"JUnit reports {failures} failure(s) and {errors} error(s)",
        })
    return failed, f"JUnit: {failures} failure(s), {errors} error(s)", findings


def read_sarif(path: str) -> tuple[bool, str, list[dict[str, str]]]:
    doc = json.load(open(path, encoding="utf-8"))
    results = [
        result
        for run in doc.get("runs") or []
        for result in run.get("results") or []
        if isinstance(result, dict)
    ]
    errors = [result for result in results if result.get("level", "warning") == "error"]
    findings = []
    for result in errors:
        message = result.get("message") or {}
        findings.append({
            "rule": str(result.get("ruleId") or "sarif"),
            "severity": "high",
            "message": str(message.get("text") or message.get("markdown") or "SARIF error-level result"),
        })
    return bool(errors), f"SARIF: {len(errors)} error-level result(s)", findings


def read_exit_code(value: str) -> tuple[bool, str, list[dict[str, str]], str]:
    try:
        code = int(value)
    except ValueError as exc:
        raise ValueError(f"exit-code must be an integer, got {value!r}") from exc
    failed = code != 0
    findings = []
    if failed:
        findings.append({
            "rule": "exit-code",
            "severity": "high",
            "message": f"program exited with status {code}",
        })
    digest = sha256_bytes((value + "\n").encode("utf-8"))
    return failed, f"Exit code: {code}", findings, digest


def adapt(args: argparse.Namespace) -> int:
    if args.format == "junit":
        failed, summary, findings = read_junit(args.input)
        source_hash = sha256_file(args.input)
        layer = "junit"
        source_arg = os.path.abspath(args.input)
        input_path = source_arg
    elif args.format == "sarif":
        failed, summary, findings = read_sarif(args.input)
        source_hash = sha256_file(args.input)
        layer = "sarif"
        source_arg = os.path.abspath(args.input)
        input_path = source_arg
    else:
        failed, summary, findings, source_hash = read_exit_code(args.input)
        layer = "exit-code"
        source_arg = args.input
        input_path = None

    os.makedirs(args.out, exist_ok=True)
    argv = [args.format, source_arg, "--gate", args.gate, "--program", args.program]
    program_name = program_token(args.program)
    program_path = resolve_program(args.program)
    program_hash = sha256_file(program_path) if program_path else None
    if not program_hash:
        print(
            f"sk-adapt: warning: --program {args.program!r} did not resolve on PATH; "
            "the artifacts will fail RUN17 because the producing binary is unidentified",
            file=sys.stderr,
        )
    evidence = envelope(
        "validation_report_artifact",
        {
            "layers_run": [layer],
            "layers_skipped": [],
            "result": "fail" if failed else "pass",
            "validator_sha256": source_hash,
            "findings": findings,
        },
        run_id=args.run_id,
        program=program_name or args.program,
        program_hash=program_hash,
        input_hash=source_hash,
        input_path=input_path,
        route_id=args.route,
        argv=argv,
    )
    evidence_name = f"evidence_{args.gate}.json"
    evidence_path = os.path.join(args.out, evidence_name)
    write_json(evidence_path, evidence)

    evidence_hash = sha256_file(evidence_path)
    decision = envelope(
        "gate_decision_artifact",
        {
            "gate_id": args.gate,
            "decision": "fail" if failed else "pass",
            "enforcement_class": "automatic",
            "evidence_refs": [{
                "artifact_id": "validation_report_artifact",
                "path": evidence_name,
                "sha256": evidence_hash,
            }],
        },
        run_id=args.run_id,
        program=program_name or args.program,
        program_hash=program_hash,
        input_hash=source_hash,
        input_path=input_path,
        route_id=args.route,
        argv=argv,
    )
    decision_path = os.path.join(args.out, f"gate_{args.gate}.json")
    write_json(decision_path, decision)

    print(f"sk-adapt {args.format}: {'FAIL' if failed else 'PASS'}")
    print(f"  {summary}")
    print(f"  evidence  {evidence_path}")
    print(f"  decision  {decision_path}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sk-adapt", description=__doc__)
    sub = ap.add_subparsers(dest="format", required=True)
    for name in ("junit", "sarif", "exit-code"):
        p = sub.add_parser(name)
        p.add_argument("input", help="result file, or integer for exit-code")
        p.add_argument("--gate", required=True)
        p.add_argument("--program", required=True)
        p.add_argument("--run-id", default="RUN_adapt_0001")
        p.add_argument("--route", default=None)
        p.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        return adapt(args)
    except (OSError, ValueError, ET.ParseError, json.JSONDecodeError) as exc:
        print(f"sk-adapt: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
