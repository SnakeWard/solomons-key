#!/usr/bin/env python3
"""Derive a small Solomon's Key contract from an existing CI workflow.

The only governance question this tool asks is whether each decision came from
a program or a person. Setup steps are not gates; decision steps become either
automatic or attested gates in a three-section contract.

Examples:
    sk_init.py --repo . --out project.key.yaml
    sk_init.py --from-ci .github/workflows/ci.yml --out project.key.yaml \
        --automatic "Unit tests=pytest" --attested "Manual sign-off"
    sk_init.py --demo-run --key project.key.yaml --out runs/first
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_VERSION = "1.0.0"
SETUP_WORDS = {
    "checkout", "install", "setup", "set up", "configure", "configuration",
    "cache", "restore", "dependencies", "dependency",
}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def slug(value: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return out or "step"


def find_ci(repo: str) -> str | None:
    root = os.path.abspath(repo)
    workflow_dir = os.path.join(root, ".github", "workflows")
    if os.path.isdir(workflow_dir):
        choices = sorted(
            str(p) for pattern in ("*.yml", "*.yaml")
            for p in Path(workflow_dir).glob(pattern)
        )
        if choices:
            return choices[0]
    for name in (".gitlab-ci.yml", "azure-pipelines.yml", "azure-pipelines.yaml"):
        candidate = os.path.join(root, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def read_steps(path: str) -> list[dict[str, str]]:
    try:
        doc = yaml.safe_load(open(path, encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read CI workflow {path}: {exc}") from exc

    steps: list[dict[str, str]] = []
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if isinstance(jobs, dict):
        for job_name, job in jobs.items():
            for index, step in enumerate((job or {}).get("steps") or [], 1):
                if not isinstance(step, dict) or not (step.get("run") or step.get("uses")):
                    continue
                command = str(step.get("run") or step.get("uses")).strip()
                label = str(step.get("name") or f"{job_name} step {index}").strip()
                steps.append({"name": label, "command": command})
    else:
        # Basic GitLab/Azure-shaped fallback: top-level mappings with script(s).
        ignored = {"stages", "variables", "workflow", "default", "include", "trigger"}
        for name, body in doc.items() if isinstance(doc, dict) else []:
            if name in ignored or not isinstance(body, dict):
                continue
            script = body.get("script")
            if isinstance(script, list):
                command = " && ".join(str(x) for x in script)
            elif isinstance(script, str):
                command = script
            else:
                continue
            steps.append({"name": str(name), "command": command})
    if not steps:
        raise ValueError(f"no executable CI steps found in {path}")
    return steps


def is_setup_step(step: dict[str, str]) -> bool:
    name = step["name"].lower()
    return any(word in name for word in SETUP_WORDS)


def parse_automatic(values: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in values:
        name, sep, program = raw.partition("=")
        if not sep or not name.strip() or not program.strip():
            raise ValueError(f"--automatic must be 'Step name=program', got {raw!r}")
        out[name.strip().casefold()] = program.strip()
    return out


def classify_steps(
    steps: list[dict[str, str]], automatic_values: list[str], attested_values: list[str]
) -> list[dict[str, str]]:
    automatic = parse_automatic(automatic_values)
    attested = {
        value.strip().casefold(): value.strip()
        for value in attested_values if value.strip()
    }
    by_name = {step["name"].casefold(): step for step in steps}
    unknown_automatic = sorted(set(automatic) - set(by_name))
    if unknown_automatic:
        raise ValueError(
            f"automatic classification names not found in CI workflow: {unknown_automatic}"
        )
    synthetic_attested = [attested[key] for key in attested.keys() - by_name.keys()]

    classified: list[dict[str, str]] = []
    if automatic or attested:
        for step in steps:
            key = step["name"].casefold()
            if key in automatic:
                classified.append({**step, "class": "automatic", "program": automatic[key]})
            elif key in attested:
                classified.append({**step, "class": "attested"})
            elif not is_setup_step(step):
                print(f"sk-init: leaving unclassified CI step outside the contract: {step['name']}",
                      file=sys.stderr)
        for name in synthetic_attested:
            classified.append({
                "name": name,
                "command": "human attestation",
                "class": "attested",
            })
    else:
        if not sys.stdin.isatty():
            raise ValueError(
                "non-interactive use requires --automatic and --attested classifications"
            )
        for step in steps:
            if is_setup_step(step):
                continue
            print(f"\n{step['name']}\n  {step['command']}")
            answer = input("Did a program decide this, or did a person? [program/person] ").strip().lower()
            if answer.startswith("p") and answer != "person":
                program = input("Program name or executable path: ").strip()
                if not program:
                    raise ValueError(f"no program supplied for {step['name']}")
                classified.append({**step, "class": "automatic", "program": program})
            elif answer in {"person", "human", "attested"}:
                classified.append({**step, "class": "attested"})
            else:
                raise ValueError(f"expected program or person for {step['name']}")

    classes = {step["class"] for step in classified}
    if not classified:
        raise ValueError("no decision steps were classified")
    if {"automatic", "attested"} - classes:
        raise ValueError(
            "a derived contract must include at least one automatic decision and one "
            "attested decision; marking every step alike does not ask the governance question"
        )
    return classified


def contract_for(classified: list[dict[str, str]]) -> dict[str, Any]:
    gates = []
    gate_ids: list[str] = []
    seen: set[str] = set()
    for step in classified:
        base = f"{slug(step['name'])}_gate"
        gate_id = base
        suffix = 2
        while gate_id in seen:
            gate_id = f"{base}_{suffix}"
            suffix += 1
        seen.add(gate_id)
        gate_ids.append(gate_id)
        gate: dict[str, Any] = {
            "gate_id": gate_id,
            "enforcement_class": step["class"],
            "required_evidence": ["validation_report_artifact"],
            "failure_response": "block",
        }
        if step["class"] == "automatic":
            gate["program"] = step["program"]
        gates.append(gate)

    return {
        "lot": {
            "routes": [{
                "route_id": "ci",
                "required_gates": gate_ids,
                "required_artifacts": ["gate_decision_artifact"],
            }],
        },
        "gates": {"gate_entries": gates},
        "artifacts": {
            "artifact_entries": [
                {"artifact_id": "gate_decision_artifact", "produced_by_role": "build"},
                {"artifact_id": "validation_report_artifact", "produced_by_role": "build"},
            ],
        },
    }


def program_token(command: str) -> str:
    try:
        return shlex.split(command, posix=os.name != "nt")[0]
    except (ValueError, IndexError):
        return command.strip().split()[0] if command.strip() else ""


def resolve_program(command: str) -> str | None:
    token = program_token(command)
    if not token:
        return None
    expanded = os.path.abspath(os.path.expanduser(token))
    if os.path.isfile(expanded):
        return expanded
    return shutil.which(token)


def guard_trust_root(path: str, force: bool) -> None:
    if not os.path.exists(path):
        return
    existing = [
        line.rstrip("\r\n")
        for line in open(path, encoding="utf-8")
        if line.strip() and not line.lstrip().startswith("#")
    ]
    inventory = "\n".join(f"    {line}" for line in existing) or "    (no active entries)"
    message = (
        f"trusted-programs file already exists: {path}\n"
        f"overwriting it would discard these trust decisions:\n{inventory}"
    )
    if not force:
        raise ValueError(
            message
            + "\nrefusing to replace the trust root; use --force or choose a distinct "
              "path with --trusted-out"
        )
    print(f"sk-init: --force replacing trust root\n{message}", file=sys.stderr)


def write_allowlist(path: str, classified: list[dict[str, str]]) -> None:
    init_hash = sha256_file(__file__)
    lines = [
        "# Generated by sk_init.py. Pin this file out of band before treating a run as evidence.",
        f"{init_hash}  sk_init.py",
    ]
    seen = {"sk_init.py"}
    for step in classified:
        if step["class"] != "automatic":
            continue
        declared = step["program"]
        name = program_token(declared)
        if name in seen:
            continue
        seen.add(name)
        resolved = resolve_program(declared)
        if resolved:
            lines.append(f"{sha256_file(resolved)}  {name}")
        else:
            lines.append(f"# UNRESOLVED {name} — install it and rerun sk_init before real evidence is accepted")
            print(
                f"sk-init: warning: automatic program {name!r} was not found on PATH; "
                "RUN17 will reject its evidence until it is pinned",
                file=sys.stderr,
            )
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def envelope(
    artifact_id: str,
    run_id: str,
    body: dict[str, Any],
    *,
    evidence_source: str,
    program_hash: str | None = None,
) -> dict[str, Any]:
    art: dict[str, Any] = {
        "artifact_id": artifact_id,
        "artifact_status": "validated",
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "pass_id": run_id,
        "timestamp": timestamp(),
        "produced_by_role": "build",
        "produced_by_actor": "user",
        "route_id": "ci",
        "required_gate": "artifact_requirement_gate",
        "ledger_ref": "pending",
        "claims_final_authority": False,
        "evidence_source": evidence_source,
        "body": body,
    }
    if evidence_source == "program":
        art["produced_by_program"] = {
            "name": "sk_init.py",
            "sha256": program_hash or sha256_file(__file__),
            "argv": ["sk_init.py", "demo-run"],
        }
    return art


def write_json(path: str, value: Any) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(value, fh, indent=2, sort_keys=True)
        fh.write("\n")


def write_demo_run(key_path: str, out_dir: str, allowlist_path: str | None = None) -> str:
    key_path = os.path.abspath(key_path)
    key_doc = yaml.safe_load(open(key_path, encoding="utf-8")) or {}
    routes = (key_doc.get("lot") or {}).get("routes") or []
    if not routes:
        raise ValueError("contract has no route for the demonstration run")
    route = routes[0]
    route_id = route.get("route_id") or "ci"
    gates = {
        gate["gate_id"]: gate
        for gate in (key_doc.get("gates") or {}).get("gate_entries") or []
    }

    os.makedirs(out_dir, exist_ok=False)
    artifacts_dir = os.path.join(out_dir, "artifacts")
    os.makedirs(artifacts_dir)
    run_id = "RUN_first_0001"
    init_hash = sha256_file(__file__)

    validation = envelope(
        "validation_report_artifact",
        run_id,
        {
            "layers_run": ["fixture_generation"],
            "layers_skipped": [],
            "result": "pass",
            "validator_sha256": init_hash,
            "findings": [],
        },
        evidence_source="program",
        program_hash=init_hash,
    )
    validation["route_id"] = route_id
    validation_path = os.path.join(artifacts_dir, "validation_report_artifact.json")
    write_json(validation_path, validation)
    validation_digest = sha256_file(validation_path)

    for gate_id in route.get("required_gates") or []:
        gate = gates.get(gate_id)
        if gate is None:
            raise ValueError(f"route requires undefined gate {gate_id}")
        enforcement = gate.get("enforcement_class")
        body: dict[str, Any] = {
            "gate_id": gate_id,
            "decision": "pass",
            "enforcement_class": enforcement,
            "evidence_refs": [{
                "artifact_id": "validation_report_artifact",
                "path": "validation_report_artifact.json",
                "sha256": validation_digest,
            }],
        }
        source = "program" if enforcement == "automatic" else "attestation"
        if source == "attestation":
            body["attestation"] = {
                "attested_by": "user",
                "statement": "Fixture attestation generated for the bypass demonstration; not build evidence.",
            }
        decision = envelope(
            "gate_decision_artifact", run_id, body,
            evidence_source=source,
            program_hash=init_hash,
        )
        decision["route_id"] = route_id
        write_json(os.path.join(artifacts_dir, f"gate_{gate_id}.json"), decision)

    if allowlist_path is None:
        candidate = os.path.join(os.path.dirname(key_path), "TRUSTED_PROGRAMS.sha256")
        allowlist_path = candidate if os.path.isfile(candidate) else None
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "key_file": key_path.replace("\\", "/"),
        "key_sha256": sha256_file(key_path),
        "task_frame_id": "derived_first_run",
        "selected_route_id": route_id,
        "actor": "sk_init fixture generator",
        "result": "pass",
        "fixture": True,
    }
    if allowlist_path:
        manifest["trusted_programs_file"] = os.path.abspath(allowlist_path).replace("\\", "/")
    write_json(os.path.join(out_dir, "run.json"), manifest)
    return os.path.abspath(out_dir)


def next_demo_run_path(out_path: str) -> str:
    """Choose a fresh demonstration-run directory without replacing a record."""
    runs_dir = os.path.join(os.path.dirname(out_path), "runs")
    candidate = os.path.join(runs_dir, "first")
    suffix = 2
    while os.path.exists(candidate):
        candidate = os.path.join(runs_dir, f"first-{suffix}")
        suffix += 1
    return candidate


def create(args: argparse.Namespace) -> int:
    ci_path = args.from_ci
    if args.repo:
        ci_path = find_ci(args.repo)
        if ci_path is None:
            raise ValueError(f"no supported CI workflow found under {os.path.abspath(args.repo)}")
    if not ci_path:
        raise ValueError("pass --repo or --from-ci")

    classified = classify_steps(read_steps(ci_path), args.automatic, args.attested)
    contract = contract_for(classified)
    out_path = os.path.abspath(args.out)
    allowlist = os.path.abspath(
        args.trusted_out
        or os.path.join(os.path.dirname(out_path), "TRUSTED_PROGRAMS.sha256")
    )
    guard_trust_root(allowlist, args.force)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        yaml.safe_dump(contract, fh, sort_keys=False, allow_unicode=True)

    write_allowlist(allowlist, classified)
    first_run = next_demo_run_path(out_path)
    write_demo_run(out_path, first_run, allowlist)

    first_gate = contract["lot"]["routes"][0]["required_gates"][0]
    print(f"sk-init: wrote contract {out_path}")
    print(f"sk-init: wrote trusted-programs allowlist {allowlist}")
    print(f"sk-init: wrote demonstration run {first_run}")
    print("\nTry removing a gate decision and re-running sk_verify:")
    print(f"    rm {os.path.join(first_run, 'artifacts', f'gate_{first_gate}.json')}")
    print(f"    python sk_verify.py {first_run} --key {out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sk-init", description=__doc__)
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--repo", help="repository whose CI workflow should be discovered")
    source.add_argument("--from-ci", help="CI workflow to derive from")
    ap.add_argument("--out", required=True, help="contract path, or run directory with --demo-run")
    ap.add_argument("--automatic", action="append", default=[], metavar="STEP=PROGRAM")
    ap.add_argument("--attested", action="append", default=[], metavar="STEP")
    ap.add_argument("--trusted-out", help="trusted-programs path (default: beside --out)")
    ap.add_argument("--force", action="store_true", help="replace an existing trusted-programs file")
    ap.add_argument("--demo-run", action="store_true", help="write a demonstration run for --key")
    ap.add_argument("--key", help="derived contract used by --demo-run")
    args = ap.parse_args(argv)

    try:
        if args.demo_run:
            if not args.key:
                raise ValueError("--demo-run requires --key")
            path = write_demo_run(args.key, args.out)
            print(f"sk-init: wrote demonstration run {path}")
            return 0
        return create(args)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"sk-init: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
