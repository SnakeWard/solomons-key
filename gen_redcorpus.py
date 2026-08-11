#!/usr/bin/env python3
"""
gen_redcorpus.py — build the red corpus.

For each sk-lint rule, produce a KEY file mutated so that exactly that rule
fires. A gate you have never watched fail is not a gate. This is how the
rules earn the claim that they discriminate.

Fixtures are written to redcorpus/ alongside manifest.json, which binds each
fixture to the rule it must trip. test_sk_lint.py enforces the binding.

Usage:  gen_redcorpus.py <green.yaml> <outdir>
"""

import copy
import json
import os
import sys

import yaml


def routes(d):
    return d["lot"]["routes"]


def route(d, rid):
    return next(r for r in routes(d) if r["route_id"] == rid)


def role(d, rid):
    return next(r for r in d["roles"]["runtime_protocol_roles"] if r["role_id"] == rid)


def gate(d, gid):
    return next(g for g in d["gates"]["gate_entries"] if g["gate_id"] == gid)


# Each mutator takes a deep copy of the green doc and breaks exactly one thing.
MUTATIONS = {}


def mutation(rule_id: str, name: str, why: str):
    def deco(fn):
        MUTATIONS[rule_id] = (name, why, fn)
        return fn

    return deco


@mutation("SK001", "drop_lot_section", "A required root section is deleted")
def m001(d):
    del d["lot"]


@mutation("SK002", "undefined_gate_ref", "A route requires a gate that was never defined")
def m002(d):
    route(d, "protocol_build_route")["required_gates"].append("phantom_gate")


@mutation("SK003", "undefined_artifact_ref", "A gate demands evidence that was never defined")
def m003(d):
    gate(d, "schema_validation_gate")["required_evidence"].append("phantom_artifact")


@mutation("SK004", "undefined_role_ref", "A route selects a role that was never defined")
def m004(d):
    route(d, "protocol_build_route")["selected_roles"].append("phantom_role")


@mutation("SK005", "dangling_fallback", "A route falls back to a route that does not exist")
def m005(d):
    route(d, "protocol_build_route")["fallback_route"] = "phantom_route"


@mutation("SK006", "undeclared_telemetry", "A route emits an event not in required_events")
def m006(d):
    route(d, "protocol_build_route")["required_telemetry_events"].append("phantom_event")


@mutation("SK007", "undefined_failure_ref", "A boundary maps a violation to an undefined failure")
def m007(d):
    d["runtime_boundaries"]["violation_failure_map"][0]["violation_failure_id"] = "phantom_failure"


@mutation("SK008", "orphan_gate", "A gate is defined but no route invokes it — the original defect")
def m008(d):
    for r in routes(d):
        r["required_gates"] = [g for g in r.get("required_gates") or [] if g != "doctrine_consistency_gate"]


@mutation("SK009", "orphan_artifact", "An artifact is defined but never required")
def m009(d):
    d["artifacts"]["artifact_entries"].append(
        {
            "artifact_id": "unused_artifact",
            "artifact_name": "Unused",
            "artifact_purpose": "never referenced",
            "artifact_status": "accepted",
            "produced_by_role": "artifact_production_role",
        }
    )


@mutation("SK010", "orphan_role", "A role is defined but no route selects it")
def m010(d):
    d["roles"]["runtime_protocol_roles"].append(
        {
            "role_id": "unused_role",
            "role_name": "Unused",
            "role_type": "unused",
            "role_purpose": "never selected",
            "status": "active",
            "locked_by_pass": "PASS_13",
        }
    )


@mutation("SK011", "role_route_asymmetry", "A route selects a role that does not acknowledge it")
def m011(d):
    role(d, "gate_evaluation_role")["selected_by_route_ids"].remove("protocol_validation_route")


@mutation("SK012", "invalid_failure_response", "A gate declares a response outside the taxonomy")
def m012(d):
    gate(d, "schema_validation_gate")["failure_response"] = "shrug"


@mutation("SK013", "invalid_severity", "A gate declares a severity outside the taxonomy")
def m013(d):
    gate(d, "schema_validation_gate")["severity"] = "spicy"


@mutation("SK014", "active_route_needs_reserved", "An active route requires a reserved artifact")
def m014(d):
    route(d, "protocol_build_route")["required_artifacts"].append("final_key_artifact_reserved")


@mutation("SK015", "unreachable_route", "An active route cannot be reached from task frame eligibility")
def m015(d):
    d["task_frame"]["lot_route_eligibility"] = ["protocol_build_route"]
    route(d, "protocol_build_route")["fallback_route"] = None


@mutation("SK016", "fallback_cycle", "Two routes fall back to each other forever")
def m016(d):
    route(d, "protocol_repair_route")["fallback_route"] = "protocol_build_route"
    route(d, "protocol_build_route")["fallback_route"] = "protocol_repair_route"


@mutation("SK017", "bogus_enforcement_class", "A gate claims an enforcement class that does not exist")
def m017(d):
    gate(d, "schema_validation_gate")["enforcement_class"] = "vibes"


@mutation("SK018", "artifact_without_producer", "An artifact declares no producing role")
def m018(d):
    d["artifacts"]["artifact_entries"][0].pop("produced_by_role", None)


@mutation("SK023", "artifact_without_schema", "An artifact is registered with no JSON Schema")
def m023(d):
    d["artifacts"]["artifact_entries"].append(
        {
            "artifact_id": "unschemad_artifact",
            "artifact_name": "Unschemad",
            "artifact_purpose": "registered but no schema exists",
            "artifact_status": "accepted",
            "required_gate": "artifact_requirement_gate",
            "produced_by_role": "artifact_production_role",
        }
    )
    d["lot"]["routes"][0]["required_artifacts"].append("unschemad_artifact")


@mutation("SK021", "bogus_detection_layer", "A failure claims a detection layer that does not exist")
def m021b(d):
    f = next(x for x in d["failure_taxonomy"]["failure_entries"]
             if x["failure_id"] == "gate_bypass_attempt")
    f["detection_layer"] = "hope"


@mutation("SK019", "missing_lock_metadata", "A section drops its status and locked_by_pass")
def m019(d):
    d["telemetry"].pop("status", None)
    d["telemetry"].pop("locked_by_pass", None)


@mutation("SK020", "doctrine_term_drift", "A doctrine scalar no longer matches its claim")
def m020(d):
    d["doctrine"]["key_expansion"] = "Kernel Extensible YAML"


@mutation("SK021", "undetected_critical_failure", "A critical failure has no declared detection point")
def m021(d):
    gate(d, "doctrine_consistency_gate").pop("detects_failure_ids", None)


@mutation("SK022", "orphan_telemetry_event", "A required event is never required by a route or role")
def m022(d):
    d["telemetry"]["required_events"].append("phantom_declared_event")


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write(__doc__)
        return 2
    green_path, outdir = sys.argv[1], sys.argv[2]
    green = yaml.safe_load(open(green_path, encoding="utf-8"))
    os.makedirs(outdir, exist_ok=True)

    manifest = []
    for rule_id in sorted(MUTATIONS):
        name, why, fn = MUTATIONS[rule_id]
        d = copy.deepcopy(green)
        fn(d)
        fname = f"red_{rule_id}_{name}.yaml"
        header = (
            f"# RED CORPUS FIXTURE — deliberately invalid.\n"
            f"# Must trip: {rule_id}\n"
            f"# Violation: {why}\n"
            f"# Generated by gen_redcorpus.py from {os.path.basename(green_path)}.\n"
        )
        with open(os.path.join(outdir, fname), "w", encoding="utf-8") as fh:
            fh.write(header)
            yaml.safe_dump(d, fh, sort_keys=False, allow_unicode=True, width=100)
        manifest.append({"rule": rule_id, "fixture": fname, "violation": why})

    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"green": os.path.basename(green_path), "cases": manifest}, fh, indent=2)

    print(f"gen_redcorpus: wrote {len(manifest)} fixtures to {outdir}/")
    for c in manifest:
        print(f"  {c['rule']}  {c['fixture']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        os._exit(0)
