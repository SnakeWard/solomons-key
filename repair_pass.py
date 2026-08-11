#!/usr/bin/env python3
"""
repair_pass.py — minimal, auditable repair of the KEY file's ERROR findings.

Surgical string replacement only. Comments, key ordering, and formatting are
preserved because the file is never round-tripped through a YAML dumper.

Every edit is declared below with the finding it closes. An edit whose anchor
is not found exactly once is a hard failure — no silent partial application.

Usage:  repair_pass.py <in.yaml> <out.yaml>
"""

import os
import sys

# (finding, description, anchor, replacement)
EDITS = [
    (
        "SK008",
        "Wire doctrine_consistency_gate into protocol_validation_route so it can fire",
        '''    - route_id: "protocol_validation_route"
      route_name: "Protocol Validation Route"
      route_description: "Governed path for validating protocol artifacts, schemas, and examples."
      route_status: "active"
      required_gates:
        - "schema_validation_gate"''',
        '''    - route_id: "protocol_validation_route"
      route_name: "Protocol Validation Route"
      route_description: "Governed path for validating protocol artifacts, schemas, and examples."
      route_status: "active"
      required_gates:
        - "doctrine_consistency_gate"
        - "schema_validation_gate"''',
    ),
    (
        "SK011",
        "protocol_validation_route must list gate_evaluation_role (role already claims it)",
        '''      selected_roles:
        - "task_intake_validator_role"
        - "route_resolution_role"
        - "artifact_validation_role"
        - "telemetry_recording_role"
        - "ledger_request_role"
      fallback_route: "protocol_repair_route"
      refusal_conditions:
        - "schema validation gate fails"''',
        '''      selected_roles:
        - "task_intake_validator_role"
        - "route_resolution_role"
        - "gate_evaluation_role"
        - "artifact_validation_role"
        - "telemetry_recording_role"
        - "ledger_request_role"
      fallback_route: "protocol_repair_route"
      refusal_conditions:
        - "schema validation gate fails"''',
    ),
    (
        "RUN12",
        "protocol_build_route must select artifact_validation_role and require a validation "
        "report. Found by sk-verify: the build route required a 'validation_run' ledger entry "
        "but selected no role that validates and required no validation evidence. A build route "
        "that never validates is the test-bypass shape written into the contract.",
        '''      required_artifacts:
        - "task_frame_artifact"
        - "lot_route_artifact"
        - "gate_decision_artifact"
      required_telemetry_events:
        - "task_frame_validated"
        - "lot_route_selected"
        - "gate_decision_recorded"
      required_ledger_entries:
        - "validation_run"
      selected_roles:
        - "task_intake_validator_role"
        - "route_resolution_role"
        - "gate_evaluation_role"
        - "artifact_production_role"
        - "telemetry_recording_role"
        - "ledger_request_role"''',
        '''      required_artifacts:
        - "task_frame_artifact"
        - "lot_route_artifact"
        - "gate_decision_artifact"
        - "validation_report_artifact"
      required_telemetry_events:
        - "task_frame_validated"
        - "lot_route_selected"
        - "gate_decision_recorded"
      required_ledger_entries:
        - "validation_run"
      selected_roles:
        - "task_intake_validator_role"
        - "route_resolution_role"
        - "gate_evaluation_role"
        - "artifact_validation_role"
        - "artifact_production_role"
        - "telemetry_recording_role"
        - "ledger_request_role"''',
    ),
    (
        "SK011",
        "protocol_repair_route must list refusal_escalation_role (role already claims it)",
        '''      selected_roles:
        - "repair_coordination_role"
      fallback_route: "protocol_audit_route"''',
        '''      selected_roles:
        - "repair_coordination_role"
        - "refusal_escalation_role"
      fallback_route: "protocol_audit_route"''',
    ),
    (
        "SK011",
        "final_assembly_route_reserved must list acceptance_precheck_role (role already claims it)",
        '''      selected_roles:
        - "final_assembly_role_reserved"''',
        '''      selected_roles:
        - "final_assembly_role_reserved"
        - "acceptance_precheck_role"''',
    ),
    (
        "SK011",
        "refusal_escalation_role must acknowledge selection by user_acceptance_escalation",
        '''      role_id: "refusal_escalation_role"''',
        '''      role_id: "refusal_escalation_role"
      selected_by_route_ids_note: "user_acceptance_escalation added in repair pass"''',
    ),
]

# Roles whose selected_by_route_ids need an added entry: (role_id, route_id)
ROLE_ROUTE_ADDITIONS = [
    ("refusal_escalation_role", "user_acceptance_escalation"),
    ("acceptance_precheck_role", "user_acceptance_escalation"),
    ("artifact_validation_role", "protocol_build_route"),
]

# gate_id -> enforcement_class. Closes SK017.
ENFORCEMENT_CLASS = {
    "task_frame_gate": "automatic",
    "source_boundary_gate": "automatic",
    "artifact_requirement_gate": "automatic",
    "ledger_requirement_gate": "automatic",
    "schema_validation_gate": "automatic",
    "lot_route_gate": "automatic",
    "role_handoff_gate": "automatic",
    "telemetry_requirement_gate": "automatic",
    "doctrine_consistency_gate": "automatic",
    "actor_authority_gate": "attested",
    "repair_authorization_gate": "attested",
    "acceptance_lock_gate": "attested",
    "final_assembly_gate": "composite",
}

# artifact_id -> the role responsible for producing it. Closes SK018.
# Assignment follows the role that actually holds the evidence at that point in
# the route: the role that evaluates gates records gate decisions, the role that
# validates artifacts records schema and validation results, and so on.
PRODUCED_BY = {
    "lot_route_artifact": "route_resolution_role",
    "gate_decision_artifact": "gate_evaluation_role",
    "ledger_entry_artifact": "ledger_request_role",
    "schema_artifact": "artifact_validation_role",
    "skeleton_example_artifact": "artifact_production_role",
    "validation_report_artifact": "artifact_validation_role",
    "repair_report_artifact": "repair_coordination_role",
    "audit_report_artifact": "refusal_escalation_role",
    "source_inventory_artifact": "task_intake_validator_role",
    "source_boundary_lock_artifact": "task_intake_validator_role",
    "task_frame_artifact": "task_intake_validator_role",
    "task_frame_validation_artifact": "task_intake_validator_role",
    "acceptance_lock_artifact": "acceptance_precheck_role",
    "role_output_artifact": "artifact_production_role",
    "telemetry_trace_artifact": "telemetry_recording_role",
    "final_key_artifact_reserved": "final_assembly_role_reserved",
    "final_report_artifact_reserved": "final_assembly_role_reserved",
}

# gate_id -> failure_ids it is declared to detect. Partially closes SK021.
DETECTS = {
    "doctrine_consistency_gate": [
        "doctrine_term_corruption",
        "cross_section_consistency_violation",
    ],
    "source_boundary_gate": ["protected_source_mutation_attempt"],
    "ledger_requirement_gate": ["ledger_tamper_attempt"],
    "actor_authority_gate": ["unbounded_actor_authority"],
    "acceptance_lock_gate": ["final_assembly_before_acceptance"],
    "artifact_requirement_gate": ["artifact_missing"],
    "schema_validation_gate": ["validation_layer_skipped"],
}


def apply_anchor_edits(text: str) -> tuple[str, list[str]]:
    log = []
    for finding, desc, anchor, repl in EDITS:
        # The refusal_escalation_role note edit is superseded by the structured
        # role/route addition below; skip it.
        if "selected_by_route_ids_note" in repl:
            continue
        n = text.count(anchor)
        if n != 1:
            raise SystemExit(
                f"repair_pass: anchor for {finding} matched {n} times, expected 1\n"
                f"  {desc}"
            )
        text = text.replace(anchor, repl, 1)
        log.append(f"  {finding}  {desc}")
    return text, log


def add_role_route(text: str, role_id: str, route_id: str) -> tuple[str, str]:
    """Append route_id to the named role's selected_by_route_ids list."""
    marker = f'    - role_id: "{role_id}"'
    i = text.index(marker)
    key = "      selected_by_route_ids:\n"
    j = text.index(key, i)
    k = j + len(key)
    # advance past existing list items
    while text.startswith("        - ", k):
        k = text.index("\n", k) + 1
    if f'        - "{route_id}"\n' in text[j:k]:
        return text, f"  SK011  {role_id} already lists {route_id} (no change)"
    text = text[:k] + f'        - "{route_id}"\n' + text[k:]
    return text, f"  SK011  {role_id}.selected_by_route_ids += {route_id}"


def annotate_gates(text: str) -> tuple[str, list[str]]:
    log = []
    for gate_id, cls in ENFORCEMENT_CLASS.items():
        marker = f'    - gate_id: "{gate_id}"'
        if marker not in text:
            raise SystemExit(f"repair_pass: gate {gate_id} not found")
        i = text.index(marker)
        insert_at = text.index("\n", i) + 1
        block = f'      enforcement_class: "{cls}"\n'
        for fid in DETECTS.get(gate_id, []):
            if "detects_failure_ids:" not in block:
                block += "      detects_failure_ids:\n"
            block += f'        - "{fid}"\n'
        text = text[:insert_at] + block + text[insert_at:]
        detail = f" detects {len(DETECTS.get(gate_id, []))}" if gate_id in DETECTS else ""
        log.append(f"  SK017  {gate_id} -> enforcement_class={cls}{detail}")
    return text, log


def annotate_artifacts(text: str) -> tuple[str, list[str]]:
    log = []
    for aid, role in PRODUCED_BY.items():
        marker = f'    - artifact_id: "{aid}"'
        if marker not in text:
            raise SystemExit(f"repair_pass: artifact {aid} not found")
        i = text.index(marker)
        insert_at = text.index("\n", i) + 1
        text = text[:insert_at] + f'      produced_by_role: "{role}"\n' + text[insert_at:]
        log.append(f"  SK018  {aid} -> produced_by_role={role}")
    return text, log


# failure_id -> detection layer. Closes the remaining SK021 findings honestly:
# these two are not gate-detectable and now say so, rather than being wired to a
# gate that could not actually observe them.
DETECTION_LAYER = {
    "gate_bypass_attempt": "verifier",
    "test_bypass_attempt": "verifier",
}


def annotate_failures(text: str) -> tuple[str, list[str]]:
    log = []
    for fid, layer in DETECTION_LAYER.items():
        marker = f'    - failure_id: "{fid}"'
        if marker not in text:
            raise SystemExit(f"repair_pass: failure {fid} not found")
        i = text.index(marker)
        insert_at = text.index("\n", i) + 1
        text = text[:insert_at] + f'      detection_layer: "{layer}"\n' + text[insert_at:]
        log.append(f"  SK021  {fid} -> detection_layer={layer} (sk-verify RUN06/RUN12)")
    return text, log


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    text = open(src, encoding="utf-8").read()

    text, log1 = apply_anchor_edits(text)

    log2 = []
    for role_id, route_id in ROLE_ROUTE_ADDITIONS:
        text, entry = add_role_route(text, role_id, route_id)
        log2.append(entry)

    text, log3 = annotate_gates(text)
    text, log4 = annotate_artifacts(text)
    text, log5 = annotate_failures(text)

    header = (
        "# REPAIR PASS: applied by scripts/repair_pass.py. Closes sk-lint\n"
        "# findings SK008, SK011, SK017, SK018, RUN12 and partially SK021. Every\n"
        "# declared in repair_pass.py and reproducible from the source file.\n"
    )
    text = header + text

    open(dst, "w", encoding="utf-8").write(text)
    print(f"repair_pass: {src} -> {dst}")
    for line in log1 + log2 + log3 + log4 + log5:
        print(line)
    print(f"  {len(log1) + len(log2) + len(log3) + len(log4) + len(log5)} edits applied")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        os._exit(0)
