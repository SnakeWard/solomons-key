#!/usr/bin/env python3
"""
gen_artifact_examples.py — valid and invalid instances for every artifact.

Two corpora:

  examples/valid/    one well-formed instance per registered artifact (17)
  examples/invalid/  one instance per failure mode, each bound to the rule it
                     must trip — schema violations and semantic violations

The invalid corpus is the point. A schema that has only ever seen conforming
data proves nothing about what it rejects. Every semantic rule SEM01-SEM10 has
at least one instance constructed to trip it.

Usage:  gen_artifact_examples.py <key.yaml> <outdir>
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys

import yaml

RUN = "RUN_demo_0001"
TS = "2026-01-15T09:30:00Z"
H = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731

# The examples must reference a ledger entry that actually exists, or SEM07
# fires on the valid corpus. Read the live head; fall back to a synthetic hash
# only when no ledger is present.
def _live_ledger_head(path: str = "ledger/solomons-key-builder-ledger.jsonl") -> str:
    try:
        lines = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        return lines[-1]["entry_hash"]
    except Exception:
        return H("ledger-entry-demo")


LEDGER_REF = _live_ledger_head()


# Which artifacts are computed by a program vs. asserted by an actor.
# role_output_artifact and audit_report_artifact are inherently attestations:
# a model summarising its own output is a claim, and the schema now makes it
# say so rather than letting it pass as a measurement.
ATTESTED_ARTIFACTS = {
    "role_output_artifact",
    "audit_report_artifact",
    "acceptance_lock_artifact",
    "repair_report_artifact",
    "final_key_artifact_reserved",
    "final_report_artifact_reserved",
}

EMITTERS = {
    "lot_route_artifact": "sk_emit.route",
    "gate_decision_artifact": "sk_emit.gate",
    "ledger_entry_artifact": "sk_ledger.append",
    "schema_artifact": "sk_artifacts.validate",
    "skeleton_example_artifact": "sk_emit.skeleton",
    "validation_report_artifact": "sk_emit.validate",
    "source_inventory_artifact": "sk_emit.inventory",
    "source_boundary_lock_artifact": "sk_emit.boundary",
    "task_frame_artifact": "sk_emit.taskframe",
    "task_frame_validation_artifact": "sk_emit.taskframe",
    "telemetry_trace_artifact": "sk_emit.telemetry",
}


def env(aid: str, role: str, actor: str, gate: str, body: dict, route: str | None = None) -> dict:
    e = {
        "artifact_id": aid,
        "artifact_status": "validated",
        "schema_version": "1.0.0",
        "run_id": RUN,
        "pass_id": "PASS_18",
        "timestamp": TS,
        "produced_by_role": role,
        "produced_by_actor": actor,
        "ledger_ref": LEDGER_REF,
        "telemetry_refs": ["evt_0001"],
        "claims_final_authority": False,
        "body": body,
    }
    if aid in ATTESTED_ARTIFACTS:
        e["evidence_source"] = "attestation"
    else:
        e["evidence_source"] = "program"
        e["produced_by_program"] = {
            "name": EMITTERS.get(aid, "sk_emit"),
            "sha256": H(f"emitter:{EMITTERS.get(aid, 'sk_emit')}"),
        }
    if route:
        e["route_id"] = route
    if gate:
        e["required_gate"] = gate
    return e


BUILD = "protocol_build_route"
VALIDATE = "protocol_validation_route"

VALID: dict[str, dict] = {

    "lot_route_artifact": env(
        "lot_route_artifact", "route_resolution_role", "Codex", "lot_route_gate",
        {
            "selected_route_id": BUILD,
            "eligible_route_ids": [BUILD, VALIDATE, "protocol_repair_route", "protocol_audit_route"],
            "selection_basis": "Task frame declares a protocol build task; build route is eligible and its gates are satisfiable.",
            "fallback_route_id": "protocol_repair_route",
        },
        route=BUILD,
    ),

    "gate_decision_artifact": env(
        "gate_decision_artifact", "gate_evaluation_role", "Codex", "artifact_requirement_gate",
        {
            "gate_id": "schema_validation_gate",
            "decision": "pass",
            "enforcement_class": "automatic",
            "evidence_refs": [{"artifact_id": "schema_artifact", "sha256": H("schema-artifact")}],
            "failure_id": None,
        },
        route=VALIDATE,
    ),

    "ledger_entry_artifact": env(
        "ledger_entry_artifact", "ledger_request_role", "Codex", "ledger_requirement_gate",
        {
            "entry_hash": LEDGER_REF,
            "prev_hash": H("previous-entry"),
            "seq": 19,
            "entry_type": "validation_run",
            "chain_verified": True,
            "anchor_head": LEDGER_REF,
        },
        route=BUILD,
    ),

    "schema_artifact": env(
        "schema_artifact", "artifact_validation_role", "Codex", "schema_validation_gate",
        {
            "schema_id": "solomons-key.logic-engine.v1.schema.gates",
            "schema_sha256": H("gates-schema"),
            "instance_sha256": H("gates-instance"),
            "result": "pass",
            "errors": [],
        },
        route=VALIDATE,
    ),

    "skeleton_example_artifact": env(
        "skeleton_example_artifact", "artifact_production_role", "Codex", "schema_validation_gate",
        {
            "example_path": "examples/skeleton/gates.partial.json",
            "example_sha256": H("skeleton-gates"),
            "is_final": False,
            "covers_section": "gates",
        },
        route=BUILD,
    ),

    "validation_report_artifact": env(
        "validation_report_artifact", "artifact_validation_role", "Codex", "artifact_requirement_gate",
        {
            "layers_run": [
                "syntax_validation", "schema_validation", "doctrine_validation",
                "cross_section_consistency_validation",
            ],
            "layers_skipped": [],
            "result": "pass",
            "validator_sha256": H("sk_lint.py"),
            "findings": [],
        },
        route=VALIDATE,
    ),

    "repair_report_artifact": env(
        "repair_report_artifact", "repair_coordination_role", "Codex", "repair_authorization_gate",
        {
            "authorized_by": "user",
            "scope": "repair_generated_files_only",
            "files_changed": [
                {"path": "generated/gates.partial.json",
                 "sha256_before": H("before"), "sha256_after": H("after")}
            ],
            "protected_source_touched": False,
            "result": "pass",
            "rollback_notes": "Restore generated/gates.partial.json from sha256_before.",
        },
        route="protocol_repair_route",
    ),

    "audit_report_artifact": env(
        "audit_report_artifact", "refusal_escalation_role", "Grok", "actor_authority_gate",
        {
            "action": "refusal",
            "reason": "Task frame requested unrestricted shell execution, a forbidden task type.",
            "failure_id": "unbounded_actor_authority",
            "severity": "critical",
            "escalated_to": "user",
            "resolved_by_ai_actor": False,
        },
        route="protocol_audit_route",
    ),

    "source_inventory_artifact": env(
        "source_inventory_artifact", "task_intake_validator_role", "Codex", "source_boundary_gate",
        {
            "source_root": "D:\\Solomons_Forge_Source_Files_Unaltered",
            "files": [
                {"path": "00_Solomon_Forge_Core_Doctrine.txt", "sha256": H("doctrine"), "bytes": 22715},
                {"path": "09_Gpt_Solomons_Forge_Master_Loader.txt", "sha256": H("loader"), "bytes": 15545},
            ],
            "inventory_sha256": H("inventory"),
        },
        route=BUILD,
    ),

    "source_boundary_lock_artifact": env(
        "source_boundary_lock_artifact", "task_intake_validator_role", "Codex", "source_boundary_gate",
        {
            "baseline_inventory_sha256": H("inventory"),
            "current_inventory_sha256": H("inventory"),
            "unmodified": True,
            "modified_paths": [],
            "mutation_scope": "read_only",
        },
        route=BUILD,
    ),

    "task_frame_artifact": env(
        "task_frame_artifact", "task_intake_validator_role", "user", "task_frame_gate",
        {
            "task_frame_id": "task.solomons-key.v1.final",
            "task_type": "protocol_build",
            "task_scope": "full_governed_protocol_execution",
            "requested_by": "user",
            "declared_forbidden_types": [
                "protected_source_mutation", "unrestricted_shell_execution",
                "gate_bypass_request", "test_bypass_request", "ledger_bypass_request",
            ],
            "task_frame_sha256": H("task-frame"),
        },
        route=BUILD,
    ),

    "task_frame_validation_artifact": env(
        "task_frame_validation_artifact", "task_intake_validator_role", "Codex", "task_frame_gate",
        {
            "task_frame_id": "task.solomons-key.v1.final",
            "decision": "pass",
            "forbidden_type_matched": None,
            "refusal_condition": None,
        },
        route=BUILD,
    ),

    "acceptance_lock_artifact": env(
        "acceptance_lock_artifact", "acceptance_precheck_role", "user", "acceptance_lock_gate",
        {
            "user_approval_recorded": True,
            "authorization_record_ref": "docs/KEY_FINAL_ASSEMBLY_AUTHORIZATION_RECORD.md",
            "readiness_checks": {
                "all_tests_pass": True,
                "protected_source_unmodified": True,
                "ledger_append_only": True,
                "user_remains_final_authority": True,
            },
            "evidence_sufficiency_matrix": {
                "evidence_completeness": "certain",
                "telemetry_integrity": "probable",
            },
            "self_authorized": False,
        },
        route="user_acceptance_escalation",
    ),

    "role_output_artifact": env(
        "role_output_artifact", "artifact_production_role", "Codex", "artifact_requirement_gate",
        {
            "role_id": "artifact_production_role",
            "role_type": "artifact_production",
            "output_summary": "Produced gate_decision_artifact and schema_artifact for the build route.",
            "mutation_scope": "approved_generated_files_only",
            "handoff_to_role": "telemetry_recording_role",
            "claimed_final_authority": False,
        },
        route=BUILD,
    ),

    "telemetry_trace_artifact": env(
        "telemetry_trace_artifact", "telemetry_recording_role", "Codex", "telemetry_requirement_gate",
        {
            "route_id": BUILD,
            "events": [
                {"event_id": "evt_0000", "event_type": "task_frame_received", "event_sequence_index": 0},
                {"event_id": "evt_0001", "event_type": "task_frame_validated", "event_sequence_index": 1},
                {"event_id": "evt_0002", "event_type": "lot_route_selected", "event_sequence_index": 2},
                {"event_id": "evt_0003", "event_type": "gate_decision_recorded",
                 "event_sequence_index": 3, "ledger_ref": LEDGER_REF},
            ],
            "sequence_policy": "ordered_append_only",
        },
        route=BUILD,
    ),

    "final_key_artifact_reserved": {
        **env("final_key_artifact_reserved", "final_assembly_role_reserved", "user",
              "final_assembly_gate",
              {"reserved": True,
               "reason": "Runtime final assembly is not implemented. Requires user authorization.",
               "requires_user_authorization": True}),
        "artifact_status": "reserved",
    },

    "final_report_artifact_reserved": {
        **env("final_report_artifact_reserved", "final_assembly_role_reserved", "user",
              "final_assembly_gate",
              {"reserved": True,
               "reason": "Runtime final assembly report is not implemented.",
               "requires_user_authorization": True}),
        "artifact_status": "reserved",
    },
}


# name -> (base artifact_id, expected rule, why, mutator)
def _m(fn):
    return fn


INVALID: dict[str, tuple[str, str, str, object]] = {

    # ---- schema layer -----------------------------------------------
    "missing_ledger_ref": (
        "gate_decision_artifact", "json_schema",
        "Envelope drops ledger_ref — evidence with no witness",
        _m(lambda d: d.pop("ledger_ref")),
    ),
    "unbounded_actor": (
        "gate_decision_artifact", "json_schema",
        "produced_by_actor outside the declared bounded set",
        _m(lambda d: d.update(produced_by_actor="AnonymousShell")),
    ),
    "claims_final_authority": (
        "role_output_artifact", "json_schema",
        "Envelope asserts final authority; the field may only be false",
        _m(lambda d: d.update(claims_final_authority=True)),
    ),
    "gate_decision_no_evidence": (
        "gate_decision_artifact", "json_schema",
        "A gate decision with an empty evidence list is an assertion",
        _m(lambda d: d["body"].update(evidence_refs=[])),
    ),
    "skeleton_claims_final": (
        "skeleton_example_artifact", "json_schema",
        "A skeleton marked final — no fake completion",
        _m(lambda d: d["body"].update(is_final=True)),
    ),
    "unknown_body_field": (
        "schema_artifact", "json_schema",
        "Body carries an undeclared field; property sets are closed",
        _m(lambda d: d["body"].update(confidence="high")),
    ),
    "bad_status_value": (
        "schema_artifact", "json_schema",
        "artifact_status outside the registry's declared values",
        _m(lambda d: d.update(artifact_status="probably_fine")),
    ),
    "malformed_sha256": (
        "source_inventory_artifact", "json_schema",
        "A hash that is not 64 hex characters",
        _m(lambda d: d["body"]["files"][0].update(sha256="abc123")),
    ),

    # ---- semantic layer ---------------------------------------------
    "SEM01_telemetry_gap": (
        "telemetry_trace_artifact", "SEM01",
        "Sequence indices jump 0,1,3 — an ordered trace with a hole",
        _m(lambda d: d["body"]["events"].__setitem__(2, {**d["body"]["events"][2], "event_sequence_index": 3})),
    ),
    "SEM02_attested_without_attestation": (
        "gate_decision_artifact", "SEM02",
        "Gate declared attested but no attester recorded",
        _m(lambda d: (d["body"].update(gate_id="acceptance_lock_gate", enforcement_class="attested"),
                      d.update(evidence_source="attestation"), d.pop("produced_by_program", None))),
    ),
    "SEM11_automatic_gate_asserted": (
        "gate_decision_artifact", "SEM11",
        "An automatic gate decided by assertion instead of by a program",
        _m(lambda d: (d.update(evidence_source="attestation"),
                      d.pop("produced_by_program", None),
                      d["body"].update(attestation={"attested_by": "Claude",
                                                    "statement": "I checked it."}))),
    ),
    "SEM13_argv_records_output_path": (
        "schema_artifact", "SEM13",
        "Recorded argv includes --out, so identical measurements hash differently",
        _m(lambda d: d["produced_by_program"].update(
            argv=["validate", "--source", "src", "--out", "/tmp/run_0001"])),
    ),
    "SEM12_program_without_program": (
        "schema_artifact", "SEM12",
        "Claims to be program-produced but names no program",
        _m(lambda d: d.pop("produced_by_program", None)),
    ),
    "SEM02_automatic_with_attestation": (
        "gate_decision_artifact", "SEM02",
        "An automatic gate settled by an actor's say-so",
        _m(lambda d: d["body"].update(
            attestation={"attested_by": "Claude", "statement": "Looks fine to me."})),
    ),
    "SEM03_enforcement_class_mismatch": (
        "gate_decision_artifact", "SEM03",
        "Artifact downgrades an automatic gate to attested",
        _m(lambda d: (d["body"].update(
            enforcement_class="attested",
            attestation={"attested_by": "Codex", "statement": "Ran it locally."}),
            d.update(evidence_source="attestation"), d.pop("produced_by_program", None))),
    ),
    "SEM04_undefined_failure": (
        "audit_report_artifact", "SEM04",
        "Cites a failure_id not present in the taxonomy",
        _m(lambda d: d["body"].update(failure_id="vibes_violation")),
    ),
    "SEM05_role_not_on_route": (
        "role_output_artifact", "SEM05",
        "Role produced evidence on a route that does not select it",
        _m(lambda d: d.update(route_id="protocol_audit_route")),
    ),
    "SEM07_dangling_ledger_ref": (
        "schema_artifact", "SEM07",
        "ledger_ref resolves to no entry in the chain",
        _m(lambda d: d.update(ledger_ref=H("never-recorded"))),
    ),
    "SEM08_unjustified_skip": (
        "validation_report_artifact", "SEM08",
        "Validation layers skipped with no justification",
        _m(lambda d: d["body"].update(layers_skipped=["doctrine_validation", "ledger_validation"])),
    ),
    "SEM08_undeclared_layer": (
        "validation_report_artifact", "SEM08",
        "Runs a validation layer the KEY never declared",
        _m(lambda d: d["body"]["layers_run"].append("vibe_validation")),
    ),
    "SEM09_protected_source_touched": (
        "repair_report_artifact", "json_schema",
        "Repair reports touching protected source — terminal block",
        _m(lambda d: d["body"].update(protected_source_touched=True)),
    ),
    "SEM10_lock_contradicts_itself": (
        "source_boundary_lock_artifact", "SEM10",
        "Claims unmodified while the inventory hashes differ",
        _m(lambda d: d["body"].update(current_inventory_sha256=H("something-else"))),
    ),
    "SEM10_lock_lists_modified": (
        "source_boundary_lock_artifact", "SEM10",
        "Claims unmodified while listing modified paths",
        _m(lambda d: d["body"].update(modified_paths=["00_Solomon_Forge_Core_Doctrine.txt"])),
    ),
}


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write(__doc__)
        return 2
    key_path, outdir = sys.argv[1], sys.argv[2]
    doc = yaml.safe_load(open(key_path, encoding="utf-8"))
    registered = [e["artifact_id"] for e in doc["artifacts"]["artifact_entries"]]

    missing = [a for a in registered if a not in VALID]
    if missing:
        sys.stderr.write(
            "gen_artifact_examples: no valid example for registered artifact(s):\n"
            + "".join(f"  {a}\n" for a in missing)
        )
        return 1

    vdir = os.path.join(outdir, "valid")
    idir = os.path.join(outdir, "invalid")
    os.makedirs(vdir, exist_ok=True)
    os.makedirs(idir, exist_ok=True)

    for aid in registered:
        with open(os.path.join(vdir, f"{aid}.json"), "w", encoding="utf-8") as fh:
            json.dump(VALID[aid], fh, indent=2)
            fh.write("\n")

    manifest = []
    for name, (base, rule, why, mut) in INVALID.items():
        d = copy.deepcopy(VALID[base])
        mut(d)
        fname = f"{name}.json"
        with open(os.path.join(idir, fname), "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2)
            fh.write("\n")
        manifest.append({"fixture": fname, "base_artifact": base, "expected_rule": rule, "violation": why})

    with open(os.path.join(idir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"cases": manifest}, fh, indent=2)

    print(f"gen_artifact_examples: {len(registered)} valid, {len(manifest)} invalid -> {outdir}/")
    sem = sorted({c["expected_rule"] for c in manifest if c["expected_rule"].startswith("SEM")},
                 key=lambda r: int(r[3:]))
    print(f"  semantic rules covered: {', '.join(sem)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        os._exit(0)
