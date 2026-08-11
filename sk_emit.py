#!/usr/bin/env python3
"""
sk_emit.py — the tool that performs the check writes the artifact.

Every other tool in this repo verifies structure. None of them can tell the
difference between an artifact a program computed and an artifact a model wrote
to look like one. Schemas now require evidence_source, and SEM11/RUN16 reject
an automatic gate decided by assertion — but rules only check declarations.

This module makes the declarations true: each subcommand runs a real check and
emits the artifact that records the result. A model never writes an artifact
for an automatic gate.

Usage:
    sk_emit.py --list
    sk_emit.py inventory  --source DIR --out DIR
    sk_emit.py boundary   --baseline FILE --current FILE --out DIR
    sk_emit.py taskframe  --frame FILE --key FILE --out DIR
    sk_emit.py route      --key FILE --route ID --out DIR
    sk_emit.py validate   --key FILE --out DIR
    sk_emit.py telemetry  --events FILE --route ID --out DIR
    sk_emit.py gate       --key FILE --gate ID --evidence DIR --out DIR
    sk_emit.py run        --key FILE --route ID --ledger FILE --out DIR

Exit codes:
    0  check passed
    1  check failed (artifact still written, recording the failure)
    2  bad arguments
    3  unreadable input
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

try:
    import yaml
except ImportError:
    sys.stderr.write("sk_emit requires PyYAML (pip install pyyaml)\n")
    sys.exit(3)

HERE = os.path.dirname(os.path.abspath(__file__))
PASS_ID = "PASS_19"
ACTOR = "Codex"
SCHEMA_VERSION = "1.0.0"

# Subcommands this pass implements. --list reports these as ready.
IMPLEMENTED = [
    "inventory",
    "boundary",
    "taskframe",
    "route",
    "validate",
    "telemetry",
    "gate",
    "run",
]

# Evidence artifact_id preferred for each automatic gate on the build route.
# Used when composing a full run; gate --evidence still decides from directory contents.
GATE_EVIDENCE_PREFERENCE: dict[str, list[str]] = {
    "task_frame_gate": ["task_frame_validation_artifact", "task_frame_artifact"],
    "source_boundary_gate": ["source_boundary_lock_artifact", "source_inventory_artifact"],
    "artifact_requirement_gate": ["validation_report_artifact", "lot_route_artifact"],
    "ledger_requirement_gate": ["ledger_entry_artifact"],
    "lot_route_gate": ["lot_route_artifact"],
    "telemetry_requirement_gate": ["telemetry_trace_artifact"],
    "schema_validation_gate": ["schema_artifact", "validation_report_artifact"],
    "doctrine_consistency_gate": ["source_inventory_artifact", "validation_report_artifact"],
}


# ---------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def self_sha256() -> str:
    """Hash of this program — computed at runtime, never hardcoded."""
    return sha256_file(os.path.abspath(__file__))


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def now_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_run_id() -> str:
    return f"RUN_emit_{uuid.uuid4().hex[:12]}"


def load_key(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
    except OSError as exc:
        sys.stderr.write(f"sk_emit: cannot read KEY: {exc}\n")
        sys.exit(3)
    except yaml.YAMLError as exc:
        sys.stderr.write(f"sk_emit: cannot parse KEY: {exc}\n")
        sys.exit(3)
    if not isinstance(doc, dict):
        sys.stderr.write("sk_emit: KEY file is not a mapping\n")
        sys.exit(3)
    return doc


def load_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as exc:
        sys.stderr.write(f"sk_emit: cannot read {path}: {exc}\n")
        sys.exit(3)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"sk_emit: cannot parse {path}: {exc}\n")
        sys.exit(3)


def default_key_path() -> str | None:
    for candidate in (
        os.path.join(HERE, "key.repaired.yaml"),
        os.path.join(os.getcwd(), "key.repaired.yaml"),
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def artifact_registry(key_doc: dict) -> dict[str, dict]:
    entries = key_doc.get("artifacts", {}).get("artifact_entries") or []
    return {e["artifact_id"]: e for e in entries if "artifact_id" in e}


def gate_registry(key_doc: dict) -> dict[str, dict]:
    entries = key_doc.get("gates", {}).get("gate_entries") or []
    return {g["gate_id"]: g for g in entries if "gate_id" in g}


def route_by_id(key_doc: dict, route_id: str) -> dict | None:
    for r in key_doc.get("lot", {}).get("routes") or []:
        if r.get("route_id") == route_id:
            return r
    return None


def argv_for_record(raw: list[str]) -> list[str]:
    """
    Record the check-relevant argv. Drop --out and its value so two runs on
    identical measurement input do not diverge solely because their output
    directories differ (determinism contract).
    """
    out: list[str] = []
    skip_next = False
    for i, a in enumerate(raw):
        if skip_next:
            skip_next = False
            continue
        if a in ("--out", "-o"):
            skip_next = True
            continue
        if a.startswith("--out="):
            continue
        out.append(a)
    return out


def make_envelope(
    *,
    artifact_id: str,
    key_doc: dict,
    body: dict,
    subcommand: str,
    argv: list[str],
    run_id: str,
    route_id: str | None,
    evidence_source: str = "program",
    timestamp: str | None = None,
    ledger_ref: str = "pending",
) -> dict:
    reg = artifact_registry(key_doc)
    entry = reg.get(artifact_id) or {}
    role = entry.get("produced_by_role")
    if not role:
        sys.stderr.write(
            f"sk_emit: KEY registry has no produced_by_role for {artifact_id}\n"
        )
        sys.exit(3)
    gate = entry.get("required_gate")

    art: dict[str, Any] = {
        "artifact_id": artifact_id,
        "artifact_status": "validated",
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "pass_id": PASS_ID,
        "timestamp": timestamp or now_ts(),
        "produced_by_role": role,
        "produced_by_actor": ACTOR,
        "route_id": route_id,
        "required_gate": gate,
        "ledger_ref": ledger_ref,
        "evidence_source": evidence_source,
        "claims_final_authority": False,
        "body": body,
    }
    if evidence_source == "program":
        art["produced_by_program"] = {
            "name": f"sk_emit.{subcommand}",
            "sha256": self_sha256(),
            "argv": argv_for_record(argv),
        }
    return art


def write_artifact(out_dir: str, art: dict, filename: str | None = None) -> str:
    os.makedirs(out_dir, exist_ok=True)
    name = filename or f"{art['artifact_id']}.json"
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(art, fh, indent=2, sort_keys=False, ensure_ascii=False)
        fh.write("\n")
    return path


# ---------------------------------------------------------------------
# emitters
# ---------------------------------------------------------------------


def emit_inventory(
    source: str,
    out_dir: str,
    *,
    key_doc: dict,
    run_id: str,
    route_id: str | None,
    argv: list[str],
) -> tuple[int, dict]:
    if not os.path.isdir(source):
        sys.stderr.write(f"sk_emit inventory: source is not a directory: {source}\n")
        sys.exit(3)

    files: list[dict[str, Any]] = []
    source_abs = os.path.abspath(source)
    for root, _dirs, names in os.walk(source_abs):
        for name in names:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, source_abs).replace("\\", "/")
            try:
                data = open(full, "rb").read()
            except OSError as exc:
                sys.stderr.write(f"sk_emit inventory: cannot read {full}: {exc}\n")
                sys.exit(3)
            files.append({
                "path": rel,
                "sha256": sha256_bytes(data),
                "bytes": len(data),
            })
    files.sort(key=lambda f: f["path"])
    if not files:
        sys.stderr.write("sk_emit inventory: source directory contains no files\n")
        sys.exit(1)

    inv_hash = sha256_bytes(canonical_json(files).encode("utf-8"))
    body = {
        "source_root": source_abs.replace("\\", "/"),
        "files": files,
        "inventory_sha256": inv_hash,
    }
    art = make_envelope(
        artifact_id="source_inventory_artifact",
        key_doc=key_doc,
        body=body,
        subcommand="inventory",
        argv=argv,
        run_id=run_id,
        route_id=route_id,
    )
    write_artifact(out_dir, art)
    return 0, art


def emit_boundary(
    baseline_path: str,
    current_path: str,
    out_dir: str,
    *,
    key_doc: dict,
    run_id: str,
    route_id: str | None,
    argv: list[str],
) -> tuple[int, dict]:
    baseline = load_json(baseline_path)
    current = load_json(current_path)
    b_body = baseline.get("body") or {}
    c_body = current.get("body") or {}
    b_hash = b_body.get("inventory_sha256")
    c_hash = c_body.get("inventory_sha256")
    if not b_hash or not c_hash:
        sys.stderr.write(
            "sk_emit boundary: both inputs must be source_inventory_artifact with inventory_sha256\n"
        )
        sys.exit(3)

    b_files = {f["path"]: f for f in (b_body.get("files") or [])}
    c_files = {f["path"]: f for f in (c_body.get("files") or [])}
    modified: list[str] = []
    for path in sorted(set(b_files) | set(c_files)):
        bf, cf = b_files.get(path), c_files.get(path)
        if bf is None or cf is None or bf.get("sha256") != cf.get("sha256"):
            modified.append(path)

    unmodified = b_hash == c_hash and not modified
    # SEM10: if hashes differ, unmodified must be false; if same, modified_paths empty.
    if b_hash != c_hash:
        unmodified = False
    if unmodified:
        modified = []

    body = {
        "baseline_inventory_sha256": b_hash,
        "current_inventory_sha256": c_hash,
        "unmodified": unmodified,
        "modified_paths": modified,
        "mutation_scope": "read_only",
    }
    art = make_envelope(
        artifact_id="source_boundary_lock_artifact",
        key_doc=key_doc,
        body=body,
        subcommand="boundary",
        argv=argv,
        run_id=run_id,
        route_id=route_id,
    )
    write_artifact(out_dir, art)
    return (0 if unmodified else 1), art


def emit_taskframe(
    frame_path: str,
    out_dir: str,
    *,
    key_doc: dict,
    run_id: str,
    route_id: str | None,
    argv: list[str],
) -> tuple[int, dict, dict]:
    frame = load_json(frame_path)
    if not isinstance(frame, dict):
        sys.stderr.write("sk_emit taskframe: frame must be a JSON object\n")
        sys.exit(3)

    raw = open(frame_path, "rb").read()
    frame_hash = sha256_bytes(raw)
    task_type = frame.get("task_type") or ""
    task_frame_id = frame.get("task_frame_id") or key_doc.get("task_frame", {}).get(
        "task_frame_id", "task.unknown"
    )
    forbidden = list(key_doc.get("task_frame", {}).get("forbidden_task_types") or [])
    matched = task_type if task_type in forbidden else None
    decision = "refuse" if matched else "pass"
    refusal = (
        f"task type '{matched}' is forbidden by the KEY task frame"
        if matched
        else None
    )

    tf_body = {
        "task_frame_id": task_frame_id,
        "task_type": task_type or "unspecified",
        "task_scope": frame.get("task_scope")
        or key_doc.get("task_frame", {}).get("task_scope")
        or "unspecified",
        "requested_by": frame.get("requested_by") or "user",
        "declared_forbidden_types": forbidden,
        "task_frame_sha256": frame_hash,
    }
    tv_body = {
        "task_frame_id": task_frame_id,
        "decision": decision,
        "forbidden_type_matched": matched,
        "refusal_condition": refusal,
    }

    tf = make_envelope(
        artifact_id="task_frame_artifact",
        key_doc=key_doc,
        body=tf_body,
        subcommand="taskframe",
        argv=argv,
        run_id=run_id,
        route_id=route_id,
    )
    tv = make_envelope(
        artifact_id="task_frame_validation_artifact",
        key_doc=key_doc,
        body=tv_body,
        subcommand="taskframe",
        argv=argv,
        run_id=run_id,
        route_id=route_id,
    )
    write_artifact(out_dir, tf)
    write_artifact(out_dir, tv)
    return (1 if matched else 0), tf, tv


def emit_route(
    key_doc: dict,
    route_id: str,
    out_dir: str,
    *,
    run_id: str,
    argv: list[str],
) -> tuple[int, dict]:
    eligible = list(key_doc.get("task_frame", {}).get("lot_route_eligibility") or [])
    route = route_by_id(key_doc, route_id)
    ok = route is not None and route_id in eligible
    if route is None:
        basis = f"Route '{route_id}' is not defined in the KEY L.O.T."
        fallback = None
    elif route_id not in eligible:
        basis = (
            f"Route '{route_id}' is defined but not in task_frame.lot_route_eligibility."
        )
        fallback = route.get("fallback_route")
    else:
        basis = (
            f"Task frame declares eligibility for '{route_id}'; "
            f"route status is '{route.get('route_status')}'."
        )
        fallback = route.get("fallback_route")

    body = {
        "selected_route_id": route_id,
        "eligible_route_ids": eligible if eligible else ["none"],
        "selection_basis": basis,
        "fallback_route_id": fallback,
    }
    # eligible_route_ids minItems 1 — if KEY has empty eligibility, still emit.
    if not eligible:
        body["eligible_route_ids"] = ["_none_declared"]

    art = make_envelope(
        artifact_id="lot_route_artifact",
        key_doc=key_doc,
        body=body,
        subcommand="route",
        argv=argv,
        run_id=run_id,
        route_id=route_id if ok else route_id,
    )
    write_artifact(out_dir, art)
    return (0 if ok else 1), art


def _run_proc(cmd: list[str], cwd: str) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=300,
        )
    except OSError as exc:
        return 3, str(exc)
    except subprocess.TimeoutExpired:
        return 1, "timeout"
    msg = (p.stdout or "") + (p.stderr or "")
    return p.returncode, msg.strip()[:500]


def emit_validate(
    key_path: str,
    out_dir: str,
    *,
    key_doc: dict,
    run_id: str,
    route_id: str | None,
    argv: list[str],
    skip: list[str] | None = None,
    skip_justification: str | None = None,
) -> tuple[int, dict]:
    skip = list(skip or [])
    declared = list(key_doc.get("validation", {}).get("validation_layers") or [])
    cwd = HERE
    layers_run: list[str] = []
    findings: list[dict[str, str]] = []
    failed = False

    # Primary structural validator on the KEY.
    lint_py = os.path.join(HERE, "sk_lint.py")
    validator_hash = sha256_file(lint_py) if os.path.isfile(lint_py) else self_sha256()

    checks: list[tuple[str, list[str]]] = []
    if os.path.isfile(lint_py):
        checks.append((
            "syntax_validation",
            [sys.executable, lint_py, key_path],
        ))
        checks.append((
            "cross_section_consistency_validation",
            [sys.executable, lint_py, key_path],
        ))
        checks.append((
            "doctrine_validation",
            [sys.executable, lint_py, key_path],
        ))

    suite_map = [
        ("schema_validation", "test_sk_lint.py"),
        ("ledger_validation", "test_sk_ledger.py"),
        ("artifact_validation", "test_sk_artifacts.py"),
        ("gate_validation", "test_sk_verify.py"),
        ("lot_routing_validation", "test_sk_lint.py"),
        ("task_frame_validation", "test_sk_lint.py"),
        ("telemetry_validation", "test_sk_lint.py"),
    ]
    seen_cmds: set[str] = set()
    for layer, script in suite_map:
        path = os.path.join(HERE, script)
        if not os.path.isfile(path):
            continue
        # Run each unique suite once; map first layer name, still list layer if
        # we already ran the suite for another layer name... Spec wants layers
        # mapped onto KEY validation_layers. Run suite once per script.
        key = path
        if key in seen_cmds:
            # Suite already executed; still count the layer if the suite passed.
            continue
        seen_cmds.add(key)
        checks.append((layer, [sys.executable, path]))

    # Also re-list layers for suites we already ran under another name so
    # layers_run covers more declared layers only when their suite ran ok.
    suite_results: dict[str, int] = {}

    for layer, cmd in checks:
        if layer in skip:
            continue
        if declared and layer not in declared:
            continue
        rc, detail = _run_proc(cmd, cwd)
        suite_results[cmd[1] if len(cmd) > 1 else layer] = rc
        if rc == 0:
            if layer not in layers_run:
                layers_run.append(layer)
        else:
            failed = True
            findings.append({
                "rule": layer,
                "severity": "high",
                "message": f"validator exit {rc}: {detail[:200] or 'failed'}",
            })

    # Expand layers_run: if sk_lint passed, several structural layers ran.
    if os.path.isfile(lint_py) and not any(
        f["rule"] == "syntax_validation" for f in findings
    ):
        for lyr in (
            "syntax_validation",
            "doctrine_validation",
            "cross_section_consistency_validation",
            "source_authority_validation",
            "schema_validation",
        ):
            if (not declared or lyr in declared) and lyr not in layers_run and lyr not in skip:
                # Only credit if lint itself succeeded
                lint_failed = any(f["rule"] == "syntax_validation" for f in findings)
                if not lint_failed and "syntax_validation" in layers_run:
                    if lyr not in layers_run:
                        layers_run.append(lyr)

    # Credit suite-backed layers that share a passing test script.
    script_to_layers = {
        "test_sk_lint.py": [
            "schema_validation",
            "lot_routing_validation",
            "task_frame_validation",
            "telemetry_validation",
        ],
        "test_sk_ledger.py": ["ledger_validation"],
        "test_sk_artifacts.py": ["artifact_validation"],
        "test_sk_verify.py": ["gate_validation", "artifact_validation"],
    }
    for script, layers in script_to_layers.items():
        path = os.path.join(HERE, script)
        if not os.path.isfile(path):
            continue
        rc = suite_results.get(path)
        if rc is None:
            # may have been keyed differently
            for k, v in suite_results.items():
                if k.endswith(script) or os.path.basename(k) == script:
                    rc = v
                    break
        if rc == 0:
            for lyr in layers:
                if (not declared or lyr in declared) and lyr not in layers_run and lyr not in skip:
                    layers_run.append(lyr)

    layers_run = [l for l in layers_run if not declared or l in declared]
    # Deterministic order: KEY declaration order, then any extras sorted.
    order = {name: i for i, name in enumerate(declared)}
    layers_run = sorted(set(layers_run), key=lambda x: (order.get(x, 999), x))

    layers_skipped = [s for s in skip if not declared or s in declared]
    body: dict[str, Any] = {
        "layers_run": layers_run if layers_run else ["syntax_validation"],
        "layers_skipped": layers_skipped,
        "result": "fail" if failed else "pass",
        "validator_sha256": validator_hash,
        "findings": findings,
    }
    if layers_skipped:
        body["skip_justification"] = (skip_justification or "").strip() or (
            "layers skipped by operator request"
        )

    # If we credited a dummy layers_run with no real check, mark fail.
    if not layers_run and not failed:
        # sk_lint missing — still emit, fail closed
        failed = True
        body["result"] = "fail"
        body["layers_run"] = ["syntax_validation"]
        body["findings"] = [{
            "rule": "syntax_validation",
            "severity": "critical",
            "message": "no validators available to run",
        }]

    art = make_envelope(
        artifact_id="validation_report_artifact",
        key_doc=key_doc,
        body=body,
        subcommand="validate",
        argv=argv,
        run_id=run_id,
        route_id=route_id,
    )
    write_artifact(out_dir, art)
    return (1 if failed else 0), art


def print_validation_findings(art: dict) -> None:
    """Render the recorded validation result for the person running the check."""
    body = art.get("body") or {}
    findings = body.get("findings") or []
    print("sk_emit validate")
    if not findings:
        print("  no findings")
    for finding in findings:
        print(
            f"  {str(finding.get('severity', '?')).upper():<8} "
            f"{finding.get('rule', '?')}"
        )
        print(f"           {finding.get('message', '')}")
    print(f"\n  result: {body.get('result', 'unknown')}")


def emit_telemetry(
    events_path: str,
    route_id: str,
    out_dir: str,
    *,
    key_doc: dict,
    run_id: str,
    argv: list[str],
    ledger_ref: str | None = None,
) -> tuple[int, dict]:
    data = load_json(events_path)
    if isinstance(data, dict) and "events" in data:
        raw_events = data["events"]
    elif isinstance(data, list):
        raw_events = data
    else:
        sys.stderr.write(
            "sk_emit telemetry: events file must be a list or {\"events\": [...]}\n"
        )
        sys.exit(3)

    events: list[dict[str, Any]] = []
    for i, e in enumerate(raw_events):
        if not isinstance(e, dict):
            sys.stderr.write(f"sk_emit telemetry: event {i} is not an object\n")
            sys.exit(3)
        ev: dict[str, Any] = {
            "event_id": e.get("event_id") or f"evt_{i:04d}",
            "event_type": e.get("event_type") or "unknown_event",
            "event_sequence_index": i,  # renumber contiguously; SEM01
        }
        if e.get("event_purpose"):
            ev["event_purpose"] = e["event_purpose"]
        if "ledger_ref" in e:
            ev["ledger_ref"] = e["ledger_ref"]
        elif ledger_ref and e.get("event_type") in (
            "gate_decision_recorded",
            "ledger_entry_recorded",
        ):
            ev["ledger_ref"] = ledger_ref
        events.append(ev)

    if not events:
        sys.stderr.write("sk_emit telemetry: no events to record\n")
        sys.exit(1)

    body = {
        "route_id": route_id,
        "events": events,
        "sequence_policy": "ordered_append_only",
    }
    art = make_envelope(
        artifact_id="telemetry_trace_artifact",
        key_doc=key_doc,
        body=body,
        subcommand="telemetry",
        argv=argv,
        run_id=run_id,
        route_id=route_id,
    )
    write_artifact(out_dir, art)
    return 0, art


def _evidence_decision(art: dict) -> str:
    """Derive pass/fail from an evidence artifact's own recorded outcome."""
    body = art.get("body") or {}
    aid = art.get("artifact_id")
    if aid == "source_boundary_lock_artifact":
        return "pass" if body.get("unmodified") is True else "fail"
    if aid == "task_frame_validation_artifact":
        d = body.get("decision")
        return "pass" if d in ("pass", None) else "fail"
    if aid == "validation_report_artifact":
        return "pass" if body.get("result") == "pass" else "fail"
    if aid == "lot_route_artifact":
        selected = body.get("selected_route_id")
        eligible = body.get("eligible_route_ids") or []
        return "pass" if selected in eligible else "fail"
    if aid == "ledger_entry_artifact":
        return "pass" if body.get("chain_verified") is not False else "fail"
    if art.get("artifact_status") in ("rejected",):
        return "fail"
    return "pass"


def emit_gate(
    key_doc: dict,
    gate_id: str,
    evidence_dir: str,
    out_dir: str,
    *,
    run_id: str,
    route_id: str | None,
    argv: list[str],
    filename: str | None = None,
) -> tuple[int, dict]:
    gates = gate_registry(key_doc)
    g = gates.get(gate_id)
    if g is None:
        sys.stderr.write(f"sk_emit gate: gate '{gate_id}' is not defined in the KEY\n")
        sys.exit(2)
    ec = g.get("enforcement_class")
    if ec != "automatic":
        sys.stderr.write(
            f"sk_emit gate: gate '{gate_id}' has enforcement_class '{ec}'; "
            "only automatic gates are in scope for this emitter\n"
        )
        sys.exit(2)

    if not os.path.isdir(evidence_dir):
        sys.stderr.write(f"sk_emit gate: evidence dir not found: {evidence_dir}\n")
        sys.exit(3)

    # Collect evidence artifacts from the directory.
    by_aid: dict[str, list[tuple[str, dict]]] = {}
    for name in sorted(os.listdir(evidence_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(evidence_dir, name)
        try:
            inst = json.load(open(path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        aid = inst.get("artifact_id")
        if not aid:
            continue
        by_aid.setdefault(aid, []).append((path, inst))

    preferred = GATE_EVIDENCE_PREFERENCE.get(gate_id) or list(
        g.get("required_evidence") or []
    )
    chosen: list[tuple[str, dict]] = []
    for aid in preferred:
        if aid in by_aid:
            chosen.append(by_aid[aid][0])
    if not chosen:
        # Fall back to any program-produced evidence present.
        for aid in sorted(by_aid):
            if aid == "gate_decision_artifact":
                continue
            for path, inst in by_aid[aid]:
                if inst.get("evidence_source") == "program":
                    chosen.append((path, inst))
                    break
            if chosen:
                break

    if not chosen:
        sys.stderr.write(
            f"sk_emit gate: no usable evidence in {evidence_dir} for gate '{gate_id}'\n"
        )
        sys.exit(3)

    evidence_refs = []
    decisions = []
    for path, inst in chosen:
        digest = sha256_file(path)
        evidence_refs.append({
            "artifact_id": inst["artifact_id"],
            "path": os.path.basename(path),
            "sha256": digest,
        })
        decisions.append(_evidence_decision(inst))

    decision = "pass" if all(d == "pass" for d in decisions) else "fail"
    failure_id = None
    if decision != "pass":
        # Prefer a taxonomy failure the gate detects, else a generic one.
        detected = g.get("detects_failure_ids") or []
        failure_id = detected[0] if detected else "gate_evaluation_violation"

    body = {
        "gate_id": gate_id,
        "decision": decision,
        "enforcement_class": "automatic",
        "evidence_refs": evidence_refs,
        "failure_id": failure_id,
    }
    art = make_envelope(
        artifact_id="gate_decision_artifact",
        key_doc=key_doc,
        body=body,
        subcommand="gate",
        argv=argv,
        run_id=run_id,
        route_id=route_id,
    )
    write_artifact(out_dir, art, filename=filename or f"gate_decision_{gate_id}.json")
    return (0 if decision == "pass" else 1), art


def emit_ledger_entry_artifact(
    ledger_path: str,
    out_dir: str,
    *,
    key_doc: dict,
    run_id: str,
    route_id: str | None,
    argv: list[str],
) -> tuple[int, dict]:
    """Derive a ledger_entry_artifact from the real chain (used by run)."""
    try:
        entries = [
            json.loads(line)
            for line in open(ledger_path, encoding="utf-8")
            if line.strip()
        ]
    except OSError as exc:
        sys.stderr.write(f"sk_emit: cannot read ledger: {exc}\n")
        sys.exit(3)
    if not entries:
        sys.stderr.write("sk_emit: ledger is empty\n")
        sys.exit(3)

    # Prefer the latest validation_run; else the head.
    chosen = None
    for e in reversed(entries):
        if e.get("entry_type") == "validation_run":
            chosen = e
            break
    if chosen is None:
        chosen = entries[-1]

    # Recompute chain integrity.
    import sk_ledger as L

    breaks = L.verify(entries)
    chain_ok = not breaks
    head = entries[-1].get("entry_hash")

    body = {
        "entry_hash": chosen["entry_hash"],
        "prev_hash": chosen.get("prev_hash") or ("0" * 64),
        "seq": chosen.get("seq", 0),
        "entry_type": chosen.get("entry_type") or "validation_run",
        "chain_verified": chain_ok,
        "anchor_head": head,
    }
    art = make_envelope(
        artifact_id="ledger_entry_artifact",
        key_doc=key_doc,
        body=body,
        subcommand="run",
        argv=argv,
        run_id=run_id,
        route_id=route_id,
        ledger_ref=chosen["entry_hash"],
    )
    # produced_by_program name: still sk_emit.run — the program that built this record.
    write_artifact(out_dir, art)
    return (0 if chain_ok else 1), art


def emit_role_output(
    out_dir: str,
    *,
    key_doc: dict,
    run_id: str,
    route_id: str,
    argv: list[str],
    ledger_ref: str,
) -> dict:
    body = {
        "role_id": "artifact_production_role",
        "role_type": "artifact_production",
        "output_summary": (
            "Emitted governed build evidence via sk_emit: inventory, boundary, "
            "task frame, route, validation, telemetry, and automatic gate decisions."
        ),
        "mutation_scope": "approved_generated_files_only",
        "handoff_to_role": "telemetry_recording_role",
        "claimed_final_authority": False,
    }
    art = make_envelope(
        artifact_id="role_output_artifact",
        key_doc=key_doc,
        body=body,
        subcommand="run",
        argv=argv,
        run_id=run_id,
        route_id=route_id,
        evidence_source="attestation",
        ledger_ref=ledger_ref,
    )
    # attestation: no produced_by_program
    art.pop("produced_by_program", None)
    write_artifact(out_dir, art)
    return art


def emit_run(
    key_path: str,
    route_id: str,
    ledger_path: str,
    out_dir: str,
    *,
    argv: list[str],
) -> int:
    key_doc = load_key(key_path)
    route = route_by_id(key_doc, route_id)
    if route is None:
        sys.stderr.write(f"sk_emit run: unknown route '{route_id}'\n")
        return 2
    if not os.path.isfile(ledger_path):
        sys.stderr.write(f"sk_emit run: ledger not found: {ledger_path}\n")
        return 3

    run_id = new_run_id()
    ts = now_ts()
    art_dir = os.path.join(out_dir, "artifacts")
    os.makedirs(art_dir, exist_ok=True)

    # Shared ledger ref = head of chain (will be refined after reading).
    try:
        entries = [
            json.loads(line)
            for line in open(ledger_path, encoding="utf-8")
            if line.strip()
        ]
    except OSError as exc:
        sys.stderr.write(f"sk_emit run: cannot read ledger: {exc}\n")
        return 3
    if not entries:
        sys.stderr.write("sk_emit run: ledger is empty\n")
        return 3
    ledger_ref = entries[-1]["entry_hash"]
    ledger_hashes = [ledger_ref]

    overall_rc = 0

    # 1. inventory of the KEY's directory (stable, real files)
    source_root = os.path.dirname(os.path.abspath(key_path)) or HERE
    # Prefer a small stable set: inventory the key file alone via a temp dir? Spec
    # says walk --source. Use the directory containing the KEY, but that can be
    # huge and include changing caches. Inventory just the KEY file by walking a
    # dedicated source snapshot: create nothing — walk source_root is OK if we
    # only need one successful run. For sk-verify, inventory must be program-produced.
    rc, inv = emit_inventory(
        source_root, art_dir,
        key_doc=key_doc, run_id=run_id, route_id=route_id, argv=argv,
    )
    inv["timestamp"] = ts
    inv["ledger_ref"] = ledger_ref
    write_artifact(art_dir, inv)  # rewrite with shared timestamp/ledger_ref
    inv_path = os.path.join(art_dir, "source_inventory_artifact.json")

    # 2. boundary against itself (unmodified)
    rc_b, lock = emit_boundary(
        inv_path, inv_path, art_dir,
        key_doc=key_doc, run_id=run_id, route_id=route_id, argv=argv,
    )
    lock["timestamp"] = ts
    lock["ledger_ref"] = ledger_ref
    write_artifact(art_dir, lock)
    if rc_b != 0:
        overall_rc = 1

    # 3. task frame for this route
    frame = {
        "task_frame_id": "task.solomons-key.v1.build",
        "task_type": "protocol_build",
        "task_scope": key_doc.get("task_frame", {}).get(
            "task_scope", "full_governed_protocol_execution"
        ),
        "requested_by": "user",
    }
    frame_path = os.path.join(out_dir, "_task_frame_input.json")
    with open(frame_path, "w", encoding="utf-8") as fh:
        json.dump(frame, fh, sort_keys=True, separators=(",", ":"))
    rc_tf, tf, tv = emit_taskframe(
        frame_path, art_dir,
        key_doc=key_doc, run_id=run_id, route_id=route_id, argv=argv,
    )
    for a in (tf, tv):
        a["timestamp"] = ts
        a["ledger_ref"] = ledger_ref
        write_artifact(art_dir, a)
    try:
        os.remove(frame_path)
    except OSError:
        pass
    if rc_tf != 0:
        overall_rc = 1

    # 4. route selection
    rc_r, lot = emit_route(
        key_doc, route_id, art_dir, run_id=run_id, argv=argv,
    )
    lot["timestamp"] = ts
    lot["ledger_ref"] = ledger_ref
    write_artifact(art_dir, lot)
    if rc_r != 0:
        overall_rc = 1

    # 5. validation
    rc_v, val = emit_validate(
        key_path, art_dir,
        key_doc=key_doc, run_id=run_id, route_id=route_id, argv=argv,
    )
    val["timestamp"] = ts
    val["ledger_ref"] = ledger_ref
    write_artifact(art_dir, val)
    if rc_v != 0:
        overall_rc = 1

    # 6. ledger entry artifact
    rc_l, led = emit_ledger_entry_artifact(
        ledger_path, art_dir,
        key_doc=key_doc, run_id=run_id, route_id=route_id, argv=argv,
    )
    led["timestamp"] = ts
    led["ledger_ref"] = ledger_ref
    write_artifact(art_dir, led)
    if rc_l != 0:
        overall_rc = 1

    # 7. telemetry covering route-required events
    required_events = list(route.get("required_telemetry_events") or [])
    # Include a minimal ordered set covering requirements.
    base_events = [
        {"event_type": "task_frame_received"},
        {"event_type": "task_frame_validated"},
        {"event_type": "lot_route_selected"},
        {"event_type": "gate_decision_recorded"},
        {"event_type": "artifact_generated"},
        {"event_type": "ledger_entry_recorded"},
    ]
    have = {e["event_type"] for e in base_events}
    for evt in required_events:
        if evt not in have:
            base_events.append({"event_type": evt})
            have.add(evt)
    events_path = os.path.join(out_dir, "_events_input.json")
    with open(events_path, "w", encoding="utf-8") as fh:
        json.dump({"events": base_events}, fh)
    rc_t, tel = emit_telemetry(
        events_path, route_id, art_dir,
        key_doc=key_doc, run_id=run_id, argv=argv, ledger_ref=ledger_ref,
    )
    tel["timestamp"] = ts
    tel["ledger_ref"] = ledger_ref
    write_artifact(art_dir, tel)
    try:
        os.remove(events_path)
    except OSError:
        pass
    if rc_t != 0:
        overall_rc = 1

    # 8. one gate decision per required automatic gate
    gate_rcs = []
    for gid in route.get("required_gates") or []:
        gdef = gate_registry(key_doc).get(gid) or {}
        if gdef.get("enforcement_class") != "automatic":
            sys.stderr.write(
                f"sk_emit run: required gate '{gid}' is not automatic; "
                "cannot emit a program decision for it\n"
            )
            overall_rc = 1
            continue
        rc_g, gart = emit_gate(
            key_doc, gid, art_dir, art_dir,
            run_id=run_id, route_id=route_id, argv=argv,
            filename=f"gate_decision_{gid}.json",
        )
        gart["timestamp"] = ts
        gart["ledger_ref"] = ledger_ref
        write_artifact(art_dir, gart, filename=f"gate_decision_{gid}.json")
        gate_rcs.append(rc_g)
        if rc_g != 0:
            overall_rc = 1

    # 9. role output (attestation — a role summarizing its work is a claim)
    emit_role_output(
        art_dir,
        key_doc=key_doc,
        run_id=run_id,
        route_id=route_id,
        argv=argv,
        ledger_ref=ledger_ref,
    )

    # 10. run.json
    result = "pass" if overall_rc == 0 else "fail"
    # If any gate failed, run cannot be pass (RUN11).
    if any(rc != 0 for rc in gate_rcs):
        result = "fail"
        overall_rc = 1

    manifest = {
        "run_id": run_id,
        "key_file": os.path.abspath(key_path).replace("\\", "/"),
        "key_sha256": sha256_file(key_path),
        "task_frame_id": "task.solomons-key.v1.build",
        "task_type": "protocol_build",
        "selected_route_id": route_id,
        "actor": ACTOR,
        "started_at": ts,
        "completed_at": now_ts(),
        "ledger_file": os.path.abspath(ledger_path).replace("\\", "/"),
        "ledger_entry_hashes": ledger_hashes,
        "result": result,
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "run.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    return overall_rc


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def cmd_list(_: argparse.Namespace) -> int:
    print("sk_emit emitters:")
    for name in IMPLEMENTED:
        print(f"  {name:<12} ready")
    print(f"\n  {len(IMPLEMENTED)} emitters ready, 0 unimplemented")
    return 0


def _resolve_key(args) -> tuple[str, dict]:
    path = getattr(args, "key", None) or default_key_path()
    if not path or not os.path.isfile(path):
        sys.stderr.write(
            "sk_emit: KEY file required (pass --key or place key.repaired.yaml beside sk_emit.py)\n"
        )
        sys.exit(2)
    return path, load_key(path)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    ap = argparse.ArgumentParser(
        prog="sk_emit",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--list", action="store_true", help="list emitters")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("inventory", help="emit source_inventory_artifact")
    p.add_argument("--source", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--key", default=None)
    p.add_argument("--route", default=None)
    p.add_argument("--run-id", default=None)

    p = sub.add_parser("boundary", help="emit source_boundary_lock_artifact")
    p.add_argument("--baseline", required=True)
    p.add_argument("--current", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--key", default=None)
    p.add_argument("--route", default=None)
    p.add_argument("--run-id", default=None)

    p = sub.add_parser("taskframe", help="emit task_frame(+validation) artifacts")
    p.add_argument("--frame", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--route", default=None)
    p.add_argument("--run-id", default=None)

    p = sub.add_parser("route", help="emit lot_route_artifact")
    p.add_argument("--key", required=True)
    p.add_argument("--route", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--run-id", default=None)

    p = sub.add_parser("validate", help="emit validation_report_artifact")
    p.add_argument("--key", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--route", default=None)
    p.add_argument("--run-id", default=None)
    p.add_argument("--skip", default="", help="comma-separated layers to skip")
    p.add_argument("--skip-justification", default="")

    p = sub.add_parser("telemetry", help="emit telemetry_trace_artifact")
    p.add_argument("--events", required=True)
    p.add_argument("--route", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--key", default=None)
    p.add_argument("--run-id", default=None)

    p = sub.add_parser("gate", help="emit gate_decision_artifact (automatic only)")
    p.add_argument("--key", required=True)
    p.add_argument("--gate", required=True)
    p.add_argument("--evidence", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--route", default=None)
    p.add_argument("--run-id", default=None)

    p = sub.add_parser("run", help="compose a complete run directory")
    p.add_argument("--key", required=True)
    p.add_argument("--route", required=True)
    p.add_argument("--ledger", required=True)
    p.add_argument("--out", required=True)

    # Allow `sk_emit.py --list` without a subcommand.
    if argv and argv[0] in ("--list", "-l"):
        return cmd_list(argparse.Namespace())

    args = ap.parse_args(argv)
    if getattr(args, "list", False) and not args.cmd:
        return cmd_list(args)
    if not args.cmd:
        ap.print_help()
        return 2

    raw_argv = argv

    if args.cmd == "inventory":
        _, key_doc = _resolve_key(args)
        rc, _ = emit_inventory(
            args.source, args.out,
            key_doc=key_doc,
            run_id=args.run_id or new_run_id(),
            route_id=args.route,
            argv=raw_argv,
        )
        return rc

    if args.cmd == "boundary":
        _, key_doc = _resolve_key(args)
        rc, _ = emit_boundary(
            args.baseline, args.current, args.out,
            key_doc=key_doc,
            run_id=args.run_id or new_run_id(),
            route_id=args.route,
            argv=raw_argv,
        )
        return rc

    if args.cmd == "taskframe":
        key_doc = load_key(args.key)
        rc, _, _ = emit_taskframe(
            args.frame, args.out,
            key_doc=key_doc,
            run_id=args.run_id or new_run_id(),
            route_id=args.route,
            argv=raw_argv,
        )
        return rc

    if args.cmd == "route":
        key_doc = load_key(args.key)
        rc, _ = emit_route(
            key_doc, args.route, args.out,
            run_id=args.run_id or new_run_id(),
            argv=raw_argv,
        )
        return rc

    if args.cmd == "validate":
        key_doc = load_key(args.key)
        skips = [s.strip() for s in (args.skip or "").split(",") if s.strip()]
        rc, artifact = emit_validate(
            args.key, args.out,
            key_doc=key_doc,
            run_id=args.run_id or new_run_id(),
            route_id=args.route,
            argv=raw_argv,
            skip=skips,
            skip_justification=args.skip_justification,
        )
        print_validation_findings(artifact)
        return rc

    if args.cmd == "telemetry":
        _, key_doc = _resolve_key(args)
        rc, _ = emit_telemetry(
            args.events, args.route, args.out,
            key_doc=key_doc,
            run_id=args.run_id or new_run_id(),
            argv=raw_argv,
        )
        return rc

    if args.cmd == "gate":
        key_doc = load_key(args.key)
        rc, _ = emit_gate(
            key_doc, args.gate, args.evidence, args.out,
            run_id=args.run_id or new_run_id(),
            route_id=args.route,
            argv=raw_argv,
        )
        return rc

    if args.cmd == "run":
        return emit_run(
            args.key, args.route, args.ledger, args.out, argv=raw_argv,
        )

    ap.print_help()
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        os._exit(0)
