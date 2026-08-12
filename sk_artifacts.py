#!/usr/bin/env python3
"""
sk_artifacts.py — makes `artifact_requirement_gate` mean something.

Two layers:

  1. JSON Schema. Structure, types, required fields, closed property sets.
  2. Semantic rules. Things JSON Schema cannot express — contiguous telemetry
     sequences, attestation required when a gate is attested rather than
     automatic, evidence hashes matching files on disk, ledger references
     resolving to real chain entries.

Layer 2 is where most of the real enforcement lives. A schema can require an
`attestation` object to be well-formed; only a semantic rule can require that
it be *present precisely when the gate is attested and absent when the gate is
automatic*, which is the rule that stops an automatic gate from being quietly
downgraded to somebody's say-so.

Usage:
    sk_artifacts.py validate <artifact.json> [--schemas DIR] [--key key.yaml]
                             [--ledger led.jsonl] [--json]
    sk_artifacts.py validate-dir <DIR> [...]
    sk_artifacts.py list-schemas [--schemas DIR]

Exit codes: 0 valid · 1 invalid · 3 unreadable
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
from dataclasses import dataclass, asdict

from sk_resources import default_schema_dir

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
except ImportError:
    sys.stderr.write("sk-artifacts requires jsonschema>=4.18 (pip install jsonschema)\n")
    sys.exit(3)

DEFAULT_SCHEMA_DIR = default_schema_dir()


@dataclass
class Issue:
    layer: str      # "schema" | "semantic"
    rule: str
    path: str
    message: str


# ---------------------------------------------------------------------
# schema loading
# ---------------------------------------------------------------------


def load_registry(schema_dir: str) -> tuple[Registry, dict[str, dict]]:
    """Load all schemas so $ref to envelope.schema.json resolves locally."""
    resources = []
    by_artifact: dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(schema_dir, "*.schema.json"))):
        schema = json.load(open(path, encoding="utf-8"))
        uri = schema.get("$id") or os.path.basename(path)
        resources.append((uri, Resource.from_contents(schema)))
        # also register bare filename so relative $ref works
        resources.append((os.path.basename(path), Resource.from_contents(schema)))
        aid = (schema.get("properties", {}).get("artifact_id", {}) or {}).get("const")
        if aid:
            by_artifact[aid] = schema
    return Registry().with_resources(resources), by_artifact


# ---------------------------------------------------------------------
# semantic rules
# ---------------------------------------------------------------------


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def semantic_checks(
    inst: dict,
    key_doc: dict | None,
    ledger_entries: list[dict] | None,
    base_dir: str,
) -> list[Issue]:
    out: list[Issue] = []
    aid = inst.get("artifact_id")
    body = inst.get("body") or {}

    def add(rule: str, path: str, msg: str) -> None:
        out.append(Issue("semantic", rule, path, msg))

    # --- SEM01: telemetry sequence indices contiguous from zero -------
    if aid == "telemetry_trace_artifact":
        idx = [e.get("event_sequence_index") for e in body.get("events", [])]
        expected = list(range(len(idx)))
        if idx != expected:
            add("SEM01", "body.events",
                f"sequence indices {idx} are not contiguous from 0 — "
                "an ordered append-only trace cannot have gaps or repeats")

    # --- SEM02: attestation present iff the gate is attested ----------
    if aid == "gate_decision_artifact":
        ec = body.get("enforcement_class")
        has = "attestation" in body
        if ec == "attested" and not has:
            add("SEM02", "body.attestation",
                "gate is 'attested' but no attestation is recorded — "
                "an attested decision with no attester is unfalsifiable")
        if ec == "automatic" and has:
            add("SEM02", "body.attestation",
                "gate is 'automatic' but carries an attestation — "
                "an automatic gate must not be settled by an actor's say-so")

    # --- SEM03: gate_id and enforcement_class agree with the KEY ------
    if aid == "gate_decision_artifact" and key_doc:
        gates = {g["gate_id"]: g for g in key_doc.get("gates", {}).get("gate_entries", [])}
        gid = body.get("gate_id")
        if gid and gid not in gates:
            add("SEM03", "body.gate_id", f"gate '{gid}' is not defined in the KEY file")
        elif gid:
            declared = gates[gid].get("enforcement_class")
            claimed = body.get("enforcement_class")
            if declared and claimed and declared != claimed:
                add("SEM03", "body.enforcement_class",
                    f"artifact claims '{claimed}' but the KEY declares gate '{gid}' as '{declared}'")

    # --- SEM04: declared failure_id exists in the taxonomy ------------
    if key_doc and "failure_taxonomy" in key_doc:
        fids = {f["failure_id"] for f in key_doc.get("failure_taxonomy", {}).get("failure_entries", [])}
        for field in ("failure_id",):
            v = body.get(field)
            if v and v not in fids:
                add("SEM04", f"body.{field}", f"failure '{v}' is not defined in the failure taxonomy")

    # --- SEM05: produced_by_role exists and route selects it ----------
    if key_doc and "roles" in key_doc:
        roles = {r["role_id"]: r for r in key_doc.get("roles", {}).get("runtime_protocol_roles", [])}
        rid = inst.get("produced_by_role")
        if rid and rid not in roles:
            add("SEM05", "produced_by_role", f"role '{rid}' is not defined in the KEY file")
        route = inst.get("route_id")
        if rid and route and rid in roles:
            if route not in (roles[rid].get("selected_by_route_ids") or []):
                add("SEM05", "route_id",
                    f"role '{rid}' produced evidence on route '{route}' but is not selected by it")

    # --- SEM06: evidence hashes match files on disk -------------------
    input_path = inst.get("input_path")
    input_digest = inst.get("input_sha256")
    if input_path:
        full = input_path if os.path.isabs(input_path) else os.path.join(base_dir, input_path)
        if not os.path.exists(full):
            add("SEM06", "input_path", f"measured input file not found: {input_path}")
        else:
            actual = sha256_file(full)
        if os.path.exists(full) and actual != input_digest:
            add("SEM06", "input_sha256",
                f"input hash mismatch for {input_path}: file is {actual[:12]}…, "
                f"artifact claims {str(input_digest)[:12]}…")
    for i, ev in enumerate(body.get("evidence_refs", []) or []):
        p = ev.get("path")
        if not p:
            continue
        full = p if os.path.isabs(p) else os.path.join(base_dir, p)
        if not os.path.exists(full):
            add("SEM06", f"body.evidence_refs[{i}].path", f"evidence file not found: {p}")
        else:
            actual = sha256_file(full)
            if actual != ev.get("sha256"):
                add("SEM06", f"body.evidence_refs[{i}].sha256",
                    f"hash mismatch for {p}: file is {actual[:12]}…, artifact claims {str(ev.get('sha256'))[:12]}…")

    # --- SEM07: ledger_ref resolves to a real chain entry -------------
    if ledger_entries is not None:
        ref = inst.get("ledger_ref")
        if ref and ref != "pending":
            hashes = {e.get("entry_hash") for e in ledger_entries}
            if ref not in hashes:
                add("SEM07", "ledger_ref",
                    f"ledger_ref {ref[:12]}… does not resolve to any entry in the chain")

    # --- SEM08: validation layers skipped must be justified -----------
    if aid == "validation_report_artifact":
        skipped = body.get("layers_skipped") or []
        if skipped and not (body.get("skip_justification") or "").strip():
            add("SEM08", "body.layers_skipped",
                f"{len(skipped)} validation layer(s) skipped with no justification — "
                "validation bypass is rejected")
        if key_doc:
            declared = set(key_doc.get("validation", {}).get("validation_layers") or [])
            for lyr in (body.get("layers_run") or []):
                if declared and lyr not in declared:
                    add("SEM08", "body.layers_run", f"layer '{lyr}' is not a declared validation layer")

    # --- SEM09: repair must never report a protected-source touch -----
    if aid == "repair_report_artifact":
        if body.get("protected_source_touched") is True:
            add("SEM09", "body.protected_source_touched",
                "protected source was modified — terminal block, no repair path")

    # --- SEM11: an automatic gate decision must be program-produced ---
    # This is the rule that makes the automatic/attested split mean something.
    # Without it a model can hand-write a passing decision for a gate that was
    # supposed to be decided by a program, and nothing in the record shows the
    # difference. produced_by_program names the binary; the run verifier then
    # checks that binary is one it knows.
    if aid == "gate_decision_artifact":
        ec = body.get("enforcement_class")
        src = inst.get("evidence_source")
        if ec == "automatic" and src != "program":
            add("SEM11", "evidence_source",
                f"gate '{body.get('gate_id')}' is automatic but this decision declares "
                f"evidence_source '{src}' — an automatic gate decided by assertion is not automatic")
        if ec == "attested" and src != "attestation":
            add("SEM11", "evidence_source",
                f"gate '{body.get('gate_id')}' is attested but declares evidence_source '{src}' — "
                "an attested gate must not be dressed as a measurement")

    # --- SEM12: program-produced artifacts must name their program ----
    if inst.get("evidence_source") == "program":
        prog = inst.get("produced_by_program") or {}
        if not prog.get("name") or not prog.get("sha256"):
            add("SEM12", "produced_by_program",
                "artifact claims to be program-produced but does not name the program and its hash")

    # --- SEM13: output paths are not part of measurement identity -----
    # Found during the emitter pass. Two inventories of the same source, written
    # to different --out directories, are the same measurement. If raw argv is
    # recorded, they hash differently and determinism appears broken when it is
    # not. The recorded argv must describe what was measured, not where the
    # result was filed.
    prog = inst.get("produced_by_program") or {}
    argv = prog.get("argv") or []
    OUTPUT_FLAGS = {"--out", "-o", "--output", "--outdir", "--out-dir"}
    for i, tok in enumerate(argv):
        flag = tok.split("=", 1)[0]
        if flag in OUTPUT_FLAGS:
            add("SEM13", f"produced_by_program.argv[{i}]",
                f"recorded argv contains the output flag '{flag}' — output paths are not part of "
                "the measurement identity and make identical measurements hash differently")

    # --- SEM10: source boundary lock must agree with itself -----------
    if aid == "source_boundary_lock_artifact":
        unmodified = body.get("unmodified")
        same = body.get("baseline_inventory_sha256") == body.get("current_inventory_sha256")
        modified = body.get("modified_paths") or []
        if unmodified is True and not same:
            add("SEM10", "body.unmodified",
                "claims unmodified but baseline and current inventory hashes differ")
        if unmodified is True and modified:
            add("SEM10", "body.modified_paths",
                f"claims unmodified but lists {len(modified)} modified path(s)")
        if unmodified is False and same:
            add("SEM10", "body.unmodified",
                "claims modified but inventory hashes are identical")

    return out


# ---------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------


def validate_instance(
    inst: dict,
    registry: Registry,
    by_artifact: dict[str, dict],
    key_doc: dict | None,
    ledger_entries: list[dict] | None,
    base_dir: str,
) -> list[Issue]:
    aid = inst.get("artifact_id")
    if not aid:
        return [Issue("schema", "no_artifact_id", "$", "instance has no artifact_id")]
    schema = by_artifact.get(aid)
    if schema is None:
        return [Issue("schema", "unregistered_artifact", "artifact_id",
                      f"no registered schema for artifact_id '{aid}'")]

    validator = Draft202012Validator(schema, registry=registry)
    issues = [
        Issue("schema", "json_schema",
              "$" + "".join(f"[{p!r}]" if isinstance(p, int) else f".{p}" for p in e.absolute_path),
              e.message)
        for e in sorted(validator.iter_errors(inst), key=lambda e: list(e.absolute_path))
    ]
    issues += semantic_checks(inst, key_doc, ledger_entries, base_dir)
    return issues


def load_optional(path: str | None, loader) -> object | None:
    if not path:
        return None
    if not os.path.exists(path):
        sys.stderr.write(f"sk-artifacts: {path} not found; skipping the checks that need it\n")
        return None
    return loader(path)


def cmd_validate(args) -> int:
    registry, by_artifact = load_registry(args.schemas)

    key_doc = None
    if args.key:
        import yaml
        key_doc = load_optional(args.key, lambda p: yaml.safe_load(open(p, encoding="utf-8")))

    ledger = None
    if args.ledger:
        ledger = load_optional(
            args.ledger,
            lambda p: [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()],
        )

    paths = args.paths
    if args.dir:
        paths = sorted(glob.glob(os.path.join(args.dir, "*.json")))
        if not paths:
            sys.stderr.write(f"sk-artifacts: no .json files in {args.dir}\n")
            return 3

    results = []
    bad = 0
    for p in paths:
        try:
            inst = json.load(open(p, encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            results.append((p, [Issue("schema", "unreadable", "$", str(exc))]))
            bad += 1
            continue
        issues = validate_instance(
            inst, registry, by_artifact, key_doc, ledger, os.path.dirname(os.path.abspath(p))
        )
        results.append((p, issues))
        if issues:
            bad += 1

    if args.json:
        print(json.dumps({
            "checked": len(results),
            "invalid": bad,
            "results": [
                {"path": p, "valid": not i, "issues": [asdict(x) for x in i]}
                for p, i in results
            ],
        }, indent=2))
    else:
        for p, issues in results:
            name = os.path.basename(p)
            if not issues:
                print(f"  OK    {name}")
            else:
                print(f"  FAIL  {name}")
                for i in issues:
                    print(f"          [{i.layer}/{i.rule}] {i.path}")
                    print(f"          {i.message}")
        print(f"\n  {len(results) - bad} valid, {bad} invalid")
        if not args.key:
            print("  note: --key not given; cross-reference checks (SEM03-05, SEM08) were skipped")
        if not args.ledger:
            print("  note: --ledger not given; ledger_ref resolution (SEM07) was skipped")

    return 1 if bad else 0


def cmd_list(args) -> int:
    _, by_artifact = load_registry(args.schemas)
    for aid in sorted(by_artifact):
        gate = (by_artifact[aid].get("properties", {}).get("required_gate", {}) or {}).get("const", "-")
        print(f"  {aid:<34} gate={gate}")
    print(f"\n  {len(by_artifact)} registered artifact schemas")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sk-artifacts", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("validate")
    p.add_argument("paths", nargs="*")
    p.add_argument("--dir", help="validate every .json in this directory")
    p.add_argument("--schemas", default=DEFAULT_SCHEMA_DIR)
    p.add_argument("--key", help="KEY file, enables cross-reference checks")
    p.add_argument("--ledger", help="ledger JSONL, enables ledger_ref resolution")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("validate-dir")
    p.add_argument("dir")
    p.add_argument("--schemas", default=DEFAULT_SCHEMA_DIR)
    p.add_argument("--key")
    p.add_argument("--ledger")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_validate, paths=[])

    p = sub.add_parser("list-schemas")
    p.add_argument("--schemas", default=DEFAULT_SCHEMA_DIR)
    p.set_defaults(fn=cmd_list)

    args = ap.parse_args(argv)
    if not hasattr(args, "dir"):
        args.dir = None
    return args.fn(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        os._exit(0)
