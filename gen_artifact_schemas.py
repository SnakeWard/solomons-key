#!/usr/bin/env python3
"""
gen_artifact_schemas.py — turn "valid" from prose into a predicate.

The KEY file requires artifacts as gate evidence and says gates confirm they
"exist and are valid." *Valid* was undefined, so `artifact_requirement_gate`
could only ever prove a file was named. This generates a JSON Schema for every
artifact in the registry, after which "valid" means "validates against its
registered schema" and the gate becomes genuinely automatic.

The artifact list is read from the KEY file, not duplicated here. Bodies are
declared below. If the KEY registers an artifact with no body definition — or
a body is defined for an artifact the KEY does not register — generation
fails. The two cannot drift apart silently.

Usage:  gen_artifact_schemas.py <key.yaml> <outdir>
"""

from __future__ import annotations

import json
import os
import sys

import yaml

SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"
BASE_URI = "https://solomons-key.dev/schemas/artifacts/v1"

BOUNDED_ACTORS = ["Codex", "Grok", "Claude", "user"]
DECISIONS = ["pass", "fail", "refuse", "refused", "repair_required", "blocked", "escalated"]
RESULTS = ["pass", "fail", "repair_required", "refused", "escalated", "blocked", "not_checked"]

SHA256 = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
ID = {"type": "string", "minLength": 1, "pattern": "^[a-z0-9][a-z0-9_.-]*$"}
NONEMPTY = {"type": "string", "minLength": 1}


def envelope_schema(status_values: list[str]) -> dict:
    """Provenance every artifact carries. Bodies extend this."""
    return {
        "$schema": SCHEMA_DRAFT,
        "$id": f"{BASE_URI}/envelope.schema.json",
        "title": "Solomon's Key Artifact Envelope",
        "description": (
            "Provenance common to every artifact. An artifact without provenance "
            "is not evidence — it is a file. Every field here exists so a claim can "
            "be traced to a run, a pass, a bounded actor, and a ledger entry."
        ),
        "type": "object",
        "allOf": [
            {
                "$comment": "evidence_source: program requires produced_by_program.",
                "if": {"properties": {"evidence_source": {"const": "program"}},
                       "required": ["evidence_source"]},
                "then": {"required": ["produced_by_program"]},
            },
            {
                "$comment": "An attested artifact must not claim a producing program.",
                "if": {"properties": {"evidence_source": {"const": "attestation"}},
                       "required": ["evidence_source"]},
                "then": {"not": {"required": ["produced_by_program"]}},
            },
            {
                "$comment": "A file-backed input path requires its measured digest.",
                "if": {"required": ["input_path"]},
                "then": {"required": ["input_sha256"]},
            },
        ],
        "required": [
            "artifact_id",
            "artifact_status",
            "schema_version",
            "run_id",
            "pass_id",
            "timestamp",
            "produced_by_role",
            "produced_by_actor",
            "ledger_ref",
            "evidence_source",
            "body",
        ],
        "additionalProperties": False,
        "properties": {
            "artifact_id": ID,
            "artifact_status": {"enum": status_values},
            "schema_version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+$"},
            "run_id": NONEMPTY,
            "pass_id": {"type": "string", "pattern": "^(PASS_[0-9]{2}|GENESIS|RUN_[A-Za-z0-9_-]+)$"},
            "timestamp": {"type": "string", "format": "date-time"},
            "produced_by_role": ID,
            "produced_by_actor": {
                "enum": BOUNDED_ACTORS,
                "description": "No actor outside the bounded set may produce evidence.",
            },
            "route_id": {"anyOf": [ID, {"type": "null"}]},
            "required_gate": {
                "description": "The gate this artifact serves as evidence for. Pinned per artifact schema.",
                "anyOf": [ID, {"type": "null"}],
            },
            "ledger_ref": {
                "description": "entry_hash of the ledger entry witnessing this artifact.",
                "anyOf": [SHA256, {"const": "pending"}],
            },
            "telemetry_refs": {"type": "array", "items": NONEMPTY},
            "input_path": {
                **NONEMPTY,
                "description": "Path of the file measured to produce this artifact.",
            },
            "input_sha256": {
                **SHA256,
                "description": "SHA-256 of the measured input, never of the producing executable.",
            },
            "claims_final_authority": {
                "const": False,
                "description": "No AI actor may claim final authority. This may only ever be false.",
            },
            "evidence_source": {
                "enum": ["program", "attestation"],
                "description": (
                    "How this artifact's content was arrived at. 'program' means a named "
                    "deterministic program computed it and produced_by_program is required. "
                    "'attestation' means an actor asserted it. The distinction is the whole "
                    "difference between a measurement and a claim, and it must be declared."
                ),
            },
            "produced_by_program": {
                "description": (
                    "The program that computed this artifact. Required when evidence_source "
                    "is 'program'. Recording which binary produced the evidence is what stops "
                    "a model from hand-writing an automatic gate's result."
                ),
                "type": "object",
                "required": ["name", "sha256"],
                "additionalProperties": False,
                "properties": {
                    "name": NONEMPTY,
                    "sha256": SHA256,
                    "argv": {"type": "array", "items": {"type": "string"}},
                },
            },
            "body": {"type": "object"},
        },
    }


# artifact_id -> (title, required body fields, body properties)
BODIES: dict[str, tuple[str, list[str], dict]] = {

    "lot_route_artifact": (
        "Proof of selected L.O.T. route",
        ["selected_route_id", "eligible_route_ids", "selection_basis"],
        {
            "selected_route_id": ID,
            "eligible_route_ids": {"type": "array", "items": ID, "minItems": 1},
            "selection_basis": NONEMPTY,
            "fallback_route_id": {"anyOf": [ID, {"type": "null"}]},
        },
    ),

    "gate_decision_artifact": (
        "Proof of gate decision for each evaluated gate",
        ["gate_id", "decision", "enforcement_class", "evidence_refs"],
        {
            "gate_id": ID,
            "decision": {"enum": DECISIONS},
            "enforcement_class": {"enum": ["automatic", "attested", "composite", "unimplemented"]},
            "evidence_refs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["artifact_id", "sha256"],
                    "additionalProperties": False,
                    "properties": {"artifact_id": ID, "path": NONEMPTY, "sha256": SHA256},
                },
                "minItems": 1,
                "description": "A gate decision with no evidence is an assertion, not a decision.",
            },
            "failure_id": {"anyOf": [ID, {"type": "null"}]},
            "attestation": {
                "description": "Required when enforcement_class is 'attested'.",
                "type": "object",
                "required": ["attested_by", "statement"],
                "additionalProperties": False,
                "properties": {
                    "attested_by": {"enum": BOUNDED_ACTORS},
                    "statement": NONEMPTY,
                    "model_version": {"type": "string"},
                },
            },
        },
    ),

    "ledger_entry_artifact": (
        "Proof of append-only ledger witness entry",
        ["entry_hash", "prev_hash", "seq", "entry_type"],
        {
            "entry_hash": SHA256,
            "prev_hash": {"anyOf": [SHA256, {"const": "0" * 64}]},
            "seq": {"type": "integer", "minimum": 0},
            "entry_type": {
                "enum": ["genesis", "validation_run", "repair_run", "audit_run",
                         "escalation_recorded", "pass_complete"]
            },
            "chain_verified": {"type": "boolean"},
            "anchor_head": {"anyOf": [SHA256, {"type": "null"}]},
        },
    ),

    "schema_artifact": (
        "Proof of schema validation against a governed partial schema",
        ["schema_id", "schema_sha256", "instance_sha256", "result"],
        {
            "schema_id": NONEMPTY,
            "schema_sha256": SHA256,
            "instance_sha256": SHA256,
            "result": {"enum": ["pass", "fail"]},
            "errors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["path", "message"],
                    "additionalProperties": False,
                    "properties": {"path": {"type": "string"}, "message": NONEMPTY},
                },
            },
        },
    ),

    "skeleton_example_artifact": (
        "Proof of skeleton example existence and non-final status",
        ["example_path", "example_sha256", "is_final"],
        {
            "example_path": NONEMPTY,
            "example_sha256": SHA256,
            "is_final": {
                "const": False,
                "description": "A skeleton is by definition not final. No fake completion.",
            },
            "covers_section": {"type": "string"},
        },
    ),

    "validation_report_artifact": (
        "Proof of validation script pass for a governed pass",
        ["layers_run", "layers_skipped", "result", "validator_sha256"],
        {
            "layers_run": {"type": "array", "items": NONEMPTY, "minItems": 1},
            "layers_skipped": {
                "type": "array",
                "items": NONEMPTY,
                "description": "Validation bypass is rejected. A non-empty list must be justified.",
            },
            "skip_justification": {"type": "string"},
            "result": {"enum": RESULTS},
            "validator_sha256": SHA256,
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["rule", "severity", "message"],
                    "additionalProperties": False,
                    "properties": {
                        "rule": NONEMPTY,
                        "severity": {"enum": ["low", "moderate", "high", "critical", "terminal"]},
                        "message": NONEMPTY,
                    },
                },
            },
        },
    ),

    "repair_report_artifact": (
        "Proof of authorized repair action",
        ["authorized_by", "scope", "files_changed", "result"],
        {
            "authorized_by": {"enum": BOUNDED_ACTORS},
            "scope": {
                "enum": ["generated_workspace_only", "approved_generated_files_only",
                         "repair_generated_files_only"],
                "description": "Repair may never touch the protected source zone.",
            },
            "files_changed": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["path", "sha256_before", "sha256_after"],
                    "additionalProperties": False,
                    "properties": {
                        "path": NONEMPTY,
                        "sha256_before": {"anyOf": [SHA256, {"const": "absent"}]},
                        "sha256_after": SHA256,
                    },
                },
            },
            "protected_source_touched": {
                "const": False,
                "description": "Terminal block if ever true. May only be false.",
            },
            "result": {"enum": RESULTS},
            "rollback_notes": {"type": "string"},
        },
    ),

    "audit_report_artifact": (
        "Proof of audit, refusal, or escalation action",
        ["action", "reason", "failure_id"],
        {
            "action": {"enum": ["audit", "refusal", "escalation"]},
            "reason": NONEMPTY,
            "failure_id": ID,
            "severity": {"enum": ["low", "moderate", "high", "critical", "terminal"]},
            "escalated_to": {"enum": ["user", "none"]},
            "resolved_by_ai_actor": {
                "const": False,
                "description": "No AI actor may resolve an escalation without user direction.",
            },
        },
    ),

    "source_inventory_artifact": (
        "Proof of protected source file inventory and read-only status",
        ["source_root", "files", "inventory_sha256"],
        {
            "source_root": NONEMPTY,
            "files": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["path", "sha256", "bytes"],
                    "additionalProperties": False,
                    "properties": {"path": NONEMPTY, "sha256": SHA256,
                                   "bytes": {"type": "integer", "minimum": 0}},
                },
            },
            "inventory_sha256": SHA256,
        },
    ),

    "source_boundary_lock_artifact": (
        "Proof of source boundary enforcement",
        ["baseline_inventory_sha256", "current_inventory_sha256", "unmodified"],
        {
            "baseline_inventory_sha256": SHA256,
            "current_inventory_sha256": SHA256,
            "unmodified": {"type": "boolean"},
            "modified_paths": {"type": "array", "items": NONEMPTY},
            "mutation_scope": {"enum": ["none", "read_only"]},
        },
    ),

    "task_frame_artifact": (
        "Proof of bounded Task Frame receipt",
        ["task_frame_id", "task_type", "task_scope", "requested_by"],
        {
            "task_frame_id": ID,
            "task_type": NONEMPTY,
            "task_scope": NONEMPTY,
            "requested_by": {"enum": BOUNDED_ACTORS},
            "declared_forbidden_types": {"type": "array", "items": NONEMPTY},
            "task_frame_sha256": SHA256,
        },
    ),

    "task_frame_validation_artifact": (
        "Proof of Task Frame gate pass",
        ["task_frame_id", "decision", "forbidden_type_matched"],
        {
            "task_frame_id": ID,
            "decision": {"enum": DECISIONS},
            "forbidden_type_matched": {"anyOf": [NONEMPTY, {"type": "null"}]},
            "refusal_condition": {"anyOf": [NONEMPTY, {"type": "null"}]},
        },
    ),

    "acceptance_lock_artifact": (
        "Proof of acceptance lock gate evaluation",
        ["user_approval_recorded", "authorization_record_ref", "readiness_checks"],
        {
            "user_approval_recorded": {"type": "boolean"},
            "authorization_record_ref": NONEMPTY,
            "readiness_checks": {
                "type": "object",
                "minProperties": 1,
                "additionalProperties": {"type": "boolean"},
            },
            "evidence_sufficiency_matrix": {
                "type": "object",
                "additionalProperties": {"enum": ["certain", "probable", "unknown", "violated"]},
            },
            "self_authorized": {
                "const": False,
                "description": "Final assembly before user acceptance is a terminal block.",
            },
        },
    ),

    "role_output_artifact": (
        "Proof of bounded role output",
        ["role_id", "role_type", "output_summary", "mutation_scope"],
        {
            "role_id": ID,
            "role_type": NONEMPTY,
            "output_summary": NONEMPTY,
            "mutation_scope": {
                "enum": ["none", "read_only", "generated_workspace_only",
                         "approved_generated_files_only", "repair_generated_files_only",
                         "final_artifact_only_after_acceptance", "forbidden"],
            },
            "handoff_to_role": {"anyOf": [ID, {"type": "null"}]},
            "claimed_final_authority": {"const": False},
        },
    ),

    "telemetry_trace_artifact": (
        "Proof of ordered telemetry trace for a governed route",
        ["route_id", "events"],
        {
            "route_id": ID,
            "events": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["event_id", "event_type", "event_sequence_index"],
                    "additionalProperties": False,
                    "properties": {
                        "event_id": NONEMPTY,
                        "event_type": NONEMPTY,
                        "event_sequence_index": {"type": "integer", "minimum": 0},
                        "event_purpose": {"type": "string"},
                        "ledger_ref": {"anyOf": [SHA256, {"type": "null"}]},
                    },
                },
                "description": "Sequence indices must be contiguous from 0. Checked by sk_artifacts, not by JSON Schema.",
            },
            "sequence_policy": {"const": "ordered_append_only"},
        },
    ),

    "final_key_artifact_reserved": (
        "Reserved artifact reference for runtime final KEY assembly",
        ["reserved", "reason"],
        {
            "reserved": {"const": True},
            "reason": NONEMPTY,
            "requires_user_authorization": {"const": True},
        },
    ),

    "final_report_artifact_reserved": (
        "Reserved artifact reference for runtime final assembly report",
        ["reserved", "reason"],
        {
            "reserved": {"const": True},
            "reason": NONEMPTY,
            "requires_user_authorization": {"const": True},
        },
    ),
}


def artifact_schema(entry: dict, status_values: list[str]) -> dict:
    aid = entry["artifact_id"]
    title, required, props = BODIES[aid]
    schema = {
        "$schema": SCHEMA_DRAFT,
        "$id": f"{BASE_URI}/{aid}.schema.json",
        "title": entry.get("artifact_name", aid),
        "description": entry.get("artifact_purpose", title),
        "allOf": [{"$ref": "envelope.schema.json"}],
        "type": "object",
        "properties": {
            "artifact_id": {"const": aid},
            "required_gate": {"const": entry.get("required_gate")},
            "body": {
                "type": "object",
                "title": title,
                "required": required,
                "additionalProperties": False,
                "properties": props,
            },
        },
        "required": ["artifact_id"],
    }
    if entry.get("artifact_status") == "reserved":
        schema["properties"]["artifact_status"] = {"const": "reserved"}
    return schema


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write(__doc__)
        return 2
    key_path, outdir = sys.argv[1], sys.argv[2]
    doc = yaml.safe_load(open(key_path, encoding="utf-8"))
    reg = doc["artifacts"]
    entries = reg["artifact_entries"]
    status_values = reg["artifact_status_values"]

    registered = {e["artifact_id"] for e in entries}
    defined = set(BODIES)

    missing = sorted(registered - defined)
    extra = sorted(defined - registered)
    if missing:
        sys.stderr.write(
            "gen_artifact_schemas: the KEY registers artifacts with no body definition:\n"
            + "".join(f"  {a}\n" for a in missing)
            + "Define them in BODIES. Refusing to emit a partial schema set.\n"
        )
        return 1
    if extra:
        sys.stderr.write(
            "gen_artifact_schemas: bodies defined for artifacts the KEY does not register:\n"
            + "".join(f"  {a}\n" for a in extra)
            + "Remove them or register them. Schemas must not outrun the registry.\n"
        )
        return 1

    os.makedirs(outdir, exist_ok=True)

    env = envelope_schema(status_values)
    with open(os.path.join(outdir, "envelope.schema.json"), "w", encoding="utf-8") as fh:
        json.dump(env, fh, indent=2)
        fh.write("\n")

    for e in entries:
        s = artifact_schema(e, status_values)
        path = os.path.join(outdir, f"{e['artifact_id']}.schema.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=2)
            fh.write("\n")

    print(f"gen_artifact_schemas: wrote envelope + {len(entries)} schemas to {outdir}/")
    for e in entries:
        gate = e.get("required_gate", "-")
        print(f"  {e['artifact_id']:<34} gate={gate}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        os._exit(0)
