#!/usr/bin/env python3
"""
gen_runs.py — build the run corpus.

One conforming run, and one violating run per RUN rule. The violating runs are
the point: a verifier that has only ever seen a good run proves nothing about
what it rejects.

The two that matter most are RUN06 and RUN12 — gate bypass and test bypass.
Both are constructed by *removing* evidence, because that is what a bypass
looks like from the outside. Nothing is added to the record; something that
should be there is missing, and the missing thing is the proof.

Usage:  gen_runs.py <key.yaml> <ledger.jsonl> <outdir>
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys

import yaml

ROUTE = "protocol_build_route"
RUN_ID = "RUN_build_0001"
TS = "2026-02-03T14:20:00Z"

H = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_path(path: str) -> str:
    """Serialize paths canonically so fixtures are identical on every host."""
    return path.replace("\\", "/")


ATTESTED_ARTIFACTS = {"role_output_artifact", "audit_report_artifact",
                      "acceptance_lock_artifact", "repair_report_artifact"}

EMITTERS = {
    "lot_route_artifact": "sk_emit.route",
    "gate_decision_artifact": "sk_emit.gate",
    "ledger_entry_artifact": "sk_ledger.append",
    "validation_report_artifact": "sk_emit.validate",
    "source_inventory_artifact": "sk_emit.inventory",
    "source_boundary_lock_artifact": "sk_emit.boundary",
    "task_frame_artifact": "sk_emit.taskframe",
    "task_frame_validation_artifact": "sk_emit.taskframe",
    "telemetry_trace_artifact": "sk_emit.telemetry",
}


def env(aid, role, actor, gate, body, ledger_ref, route=ROUTE, run_id=RUN_ID):
    e = {
        "artifact_id": aid,
        "artifact_status": "validated",
        "schema_version": "1.0.0",
        "run_id": run_id,
        "pass_id": "PASS_18",
        "timestamp": TS,
        "produced_by_role": role,
        "produced_by_actor": actor,
        "route_id": route,
        "required_gate": gate,
        "ledger_ref": ledger_ref,
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
    return e


def build_good(key_doc: dict, key_path: str, ledger_path: str, ledger_ref: str) -> tuple[dict, dict]:
    route = next(r for r in key_doc["lot"]["routes"] if r["route_id"] == ROUTE)
    gates = route["required_gates"]

    arts: dict[str, dict] = {}

    arts["task_frame"] = env(
        "task_frame_artifact", "task_intake_validator_role", "user", "task_frame_gate",
        {
            "task_frame_id": "task.solomons-key.v1.build",
            "task_type": "protocol_build",
            "task_scope": "full_governed_protocol_execution",
            "requested_by": "user",
            "declared_forbidden_types": key_doc["task_frame"].get("forbidden_task_types", []),
            "task_frame_sha256": H("task-frame-build"),
        },
        ledger_ref,
    )

    arts["task_frame_validation"] = env(
        "task_frame_validation_artifact", "task_intake_validator_role", "Codex", "task_frame_gate",
        {
            "task_frame_id": "task.solomons-key.v1.build",
            "decision": "pass",
            "forbidden_type_matched": None,
            "refusal_condition": None,
        },
        ledger_ref,
    )

    arts["source_inventory"] = env(
        "source_inventory_artifact", "task_intake_validator_role", "Codex", "source_boundary_gate",
        {
            "source_root": "D:\\Solomons_Forge_Source_Files_Unaltered",
            "files": [{"path": "00_Solomon_Forge_Core_Doctrine.txt",
                       "sha256": H("doctrine"), "bytes": 22715}],
            "inventory_sha256": H("inventory"),
        },
        ledger_ref,
    )

    arts["source_lock"] = env(
        "source_boundary_lock_artifact", "task_intake_validator_role", "Codex", "source_boundary_gate",
        {
            "baseline_inventory_sha256": H("inventory"),
            "current_inventory_sha256": H("inventory"),
            "unmodified": True,
            "modified_paths": [],
            "mutation_scope": "read_only",
        },
        ledger_ref,
    )

    arts["lot_route"] = env(
        "lot_route_artifact", "route_resolution_role", "Codex", "lot_route_gate",
        {
            "selected_route_id": ROUTE,
            "eligible_route_ids": key_doc["task_frame"]["lot_route_eligibility"],
            "selection_basis": "Task frame declares protocol_build; the build route is eligible.",
            "fallback_route_id": route.get("fallback_route"),
        },
        ledger_ref,
    )

    # One gate decision per gate the route requires. Remove any of these and
    # RUN06 fires — that is the bypass rule.
    gate_evidence = {
        "task_frame_gate": ("task_frame_validation_artifact", "task_frame_validation"),
        "source_boundary_gate": ("source_boundary_lock_artifact", "source_lock"),
        "artifact_requirement_gate": ("lot_route_artifact", "lot_route"),
        "ledger_requirement_gate": ("ledger_entry_artifact", "ledger_entry"),
    }

    arts["ledger_entry"] = env(
        "ledger_entry_artifact", "ledger_request_role", "Codex", "ledger_requirement_gate",
        {
            "entry_hash": ledger_ref,
            "prev_hash": H("previous"),
            "seq": 19,
            "entry_type": "validation_run",
            "chain_verified": True,
            "anchor_head": ledger_ref,
        },
        ledger_ref,
    )

    for gid in gates:
        ev_artifact, _ = gate_evidence[gid]
        arts[f"gate_{gid}"] = env(
            "gate_decision_artifact", "gate_evaluation_role", "Codex",
            "artifact_requirement_gate",
            {
                "gate_id": gid,
                "decision": "pass",
                "enforcement_class": "automatic",
                "evidence_refs": [{"artifact_id": ev_artifact, "sha256": H(ev_artifact)}],
                "failure_id": None,
            },
            ledger_ref,
        )

    arts["telemetry"] = env(
        "telemetry_trace_artifact", "telemetry_recording_role", "Codex",
        "telemetry_requirement_gate",
        {
            "route_id": ROUTE,
            "events": [
                {"event_id": "evt_0000", "event_type": "task_frame_received", "event_sequence_index": 0},
                {"event_id": "evt_0001", "event_type": "task_frame_validated", "event_sequence_index": 1},
                {"event_id": "evt_0002", "event_type": "lot_route_selected", "event_sequence_index": 2},
                {"event_id": "evt_0003", "event_type": "gate_decision_recorded",
                 "event_sequence_index": 3, "ledger_ref": ledger_ref},
                {"event_id": "evt_0004", "event_type": "artifact_generated", "event_sequence_index": 4},
                {"event_id": "evt_0005", "event_type": "ledger_entry_recorded", "event_sequence_index": 5},
            ],
            "sequence_policy": "ordered_append_only",
        },
        ledger_ref,
    )

    arts["validation_report"] = env(
        "validation_report_artifact", "artifact_validation_role", "Codex",
        "artifact_requirement_gate",
        {
            "layers_run": [
                "syntax_validation", "schema_validation", "doctrine_validation",
                "task_frame_validation", "lot_routing_validation", "gate_validation",
                "artifact_validation", "telemetry_validation", "ledger_validation",
                "cross_section_consistency_validation",
            ],
            "layers_skipped": [],
            "result": "pass",
            "validator_sha256": H("sk_lint"),
            "findings": [],
        },
        ledger_ref,
    )

    arts["role_output"] = env(
        "role_output_artifact", "artifact_production_role", "Codex", "artifact_requirement_gate",
        {
            "role_id": "artifact_production_role",
            "role_type": "artifact_production",
            "output_summary": "Produced route, gate decision, telemetry and validation evidence.",
            "mutation_scope": "approved_generated_files_only",
            "handoff_to_role": "telemetry_recording_role",
            "claimed_final_authority": False,
        },
        ledger_ref,
    )

    manifest = {
        "run_id": RUN_ID,
        "fixture": True,
        "_fixture_reason": (
            "Generated by gen_runs.py with placeholder program hashes. Exercises the "
            "verifier rules. Not a record of a real build — use sk_emit run output for that."
        ),
        "key_file": manifest_path(key_path),
        "key_sha256": sha256_file(key_path),
        "task_frame_id": "task.solomons-key.v1.build",
        "task_type": "protocol_build",
        "selected_route_id": ROUTE,
        "actor": "Codex",
        "started_at": TS,
        "completed_at": "2026-02-03T14:24:00Z",
        "ledger_file": manifest_path(ledger_path),
        "ledger_entry_hashes": [ledger_ref],
        "result": "pass",
    }
    return manifest, arts


# name -> (rule, why, mutator(manifest, arts))
BAD: dict[str, tuple[str, str, object]] = {

    "RUN02_wrong_key": (
        "RUN02", "Run was governed by a different KEY file than the one being judged",
        lambda m, a: m.update(key_sha256=H("some-other-key")),
    ),
    "RUN03_undefined_route": (
        "RUN03", "Run executed a route the KEY does not define",
        lambda m, a: m.update(selected_route_id="freestyle_route"),
    ),
    "RUN04_invalid_artifact": (
        "RUN04", "An artifact in the run does not validate against its schema",
        lambda m, a: a["lot_route"]["body"].update(eligible_route_ids=[]),
    ),
    "RUN05_missing_required_artifact": (
        "RUN05", "The route requires task_frame_artifact and the run omits it",
        lambda m, a: a.pop("task_frame"),
    ),
    "RUN06_gate_bypassed": (
        "RUN06", "GATE BYPASS: route requires source_boundary_gate; no decision recorded",
        lambda m, a: a.pop("gate_source_boundary_gate"),
    ),
    "RUN06_all_gates_bypassed": (
        "RUN06", "GATE BYPASS: every gate decision removed; the run claims pass anyway",
        lambda m, a: [a.pop(k) for k in list(a) if k.startswith("gate_")],
    ),
    "RUN07_unrequired_gate": (
        "RUN07", "Run records a decision for a gate the route does not require",
        lambda m, a: a.__setitem__(
            "gate_extra",
            {**copy.deepcopy(a["gate_task_frame_gate"]),
             "body": {**a["gate_task_frame_gate"]["body"], "gate_id": "acceptance_lock_gate",
                      "enforcement_class": "attested",
                      "attestation": {"attested_by": "Codex", "statement": "self-approved"}}},
        ),
    ),
    "RUN08_evidence_not_present": (
        "RUN08", "A gate cites evidence that is not in the run",
        lambda m, a: a["gate_task_frame_gate"]["body"]["evidence_refs"].__setitem__(
            0, {"artifact_id": "acceptance_lock_artifact", "sha256": H("nope")}),
    ),
    "RUN09_telemetry_missing_event": (
        "RUN09", "Required telemetry event lot_route_selected is absent from the trace",
        lambda m, a: a["telemetry"]["body"].__setitem__(
            "events", [e for e in a["telemetry"]["body"]["events"]
                       if e["event_type"] != "lot_route_selected"]),
    ),
    "RUN09_no_telemetry": (
        "RUN09", "Run contains no telemetry trace at all",
        lambda m, a: a.pop("telemetry"),
    ),
    "RUN10_dangling_ledger_ref": (
        "RUN10", "Run cites a ledger entry that is not in the chain",
        lambda m, a: m.update(ledger_entry_hashes=[H("never-appended")]),
    ),
    "RUN11_failing_gate_passing_run": (
        "RUN11", "A gate failed and the run still reports pass",
        lambda m, a: a["gate_task_frame_gate"]["body"].update(
            decision="fail", failure_id="task_frame_violation"),
    ),
    "RUN12_no_validation_report": (
        "RUN12", "TEST BYPASS: the run records no validation report",
        lambda m, a: a.pop("validation_report"),
    ),
    "RUN12_unjustified_skip": (
        "RUN12", "TEST BYPASS: validation layers skipped with no justification",
        lambda m, a: a["validation_report"]["body"].update(
            layers_skipped=["gate_validation", "ledger_validation"]),
    ),
    "RUN13_producer_not_on_route": (
        "RUN13", "Evidence produced by a role the route does not select",
        lambda m, a: a["role_output"].update(produced_by_role="refusal_escalation_role"),
    ),
    "RUN14_foreign_evidence": (
        "RUN14", "Evidence imported from a different run",
        lambda m, a: a["lot_route"].update(run_id="RUN_someone_elses_0009"),
    ),
    "RUN16_automatic_gate_asserted": (
        "RUN16", "An automatic gate decision was asserted rather than computed",
        lambda m, a: (a["gate_source_boundary_gate"].update(evidence_source="attestation"),
                      a["gate_source_boundary_gate"].pop("produced_by_program", None)),
    ),
    "RUN16_evidence_hand_written": (
        "RUN16", "An automatic gate rests on evidence an actor wrote by hand",
        lambda m, a: (a["source_lock"].update(evidence_source="attestation"),
                      a["source_lock"].pop("produced_by_program", None)),
    ),
    "RUN15_claims_final_authority": (
        "RUN15", "An AI-produced artifact asserts final authority",
        lambda m, a: a["role_output"].update(claims_final_authority=True),
    ),
}


def write_run(outdir: str, name: str, manifest: dict, arts: dict, note: str | None = None) -> None:
    d = os.path.join(outdir, name)
    if os.path.exists(d):
        shutil.rmtree(d)
    os.makedirs(os.path.join(d, "artifacts"))
    if note:
        manifest = {**manifest, "_fixture_note": note}
    with open(os.path.join(d, "run.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    for key, art in arts.items():
        with open(os.path.join(d, "artifacts", f"{key}.json"), "w", encoding="utf-8") as fh:
            json.dump(art, fh, indent=2)
            fh.write("\n")


def main() -> int:
    if len(sys.argv) != 4:
        sys.stderr.write(__doc__)
        return 2
    key_path, ledger_path, outdir = sys.argv[1], sys.argv[2], sys.argv[3]
    key_doc = yaml.safe_load(open(key_path, encoding="utf-8"))

    entries = [json.loads(l) for l in open(ledger_path, encoding="utf-8") if l.strip()]
    ledger_ref = entries[-1]["entry_hash"]

    manifest, arts = build_good(key_doc, key_path, ledger_path, ledger_ref)
    os.makedirs(outdir, exist_ok=True)
    write_run(outdir, "good", manifest, arts)

    cases = []
    for name, (rule, why, mut) in BAD.items():
        m2, a2 = copy.deepcopy(manifest), copy.deepcopy(arts)
        mut(m2, a2)
        write_run(outdir, name, m2, a2, note=why)
        cases.append({"run": name, "expected_rule": rule, "violation": why})

    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"good": "good", "cases": cases}, fh, indent=2)

    print(f"gen_runs: 1 conforming run + {len(cases)} violating runs -> {outdir}/")
    rules = sorted({c["expected_rule"] for c in cases})
    print(f"  rules covered: {', '.join(rules)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        os._exit(0)
