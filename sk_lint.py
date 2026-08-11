#!/usr/bin/env python3
"""
sk-lint — structural verifier for Solomon's Key KEY files.

Reads a KEY file and decides pass/fail by program, not by judgment.
No model is in the loop. Every rule is a predicate over the file's own
declared structure.

Exit codes:
    0  no findings at or above the fail threshold
    1  ERROR findings present
    2  --strict and WARN findings present
    3  file could not be read or parsed

Usage:
    sk_lint.py <key.yaml> [--json] [--strict] [--rules] [--only SK001,SK008]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict
from typing import Any, Callable, Iterable

try:
    import yaml
except ImportError:
    sys.stderr.write("sk-lint requires PyYAML (pip install pyyaml)\n")
    sys.exit(3)


class LineLoader(yaml.SafeLoader):
    """SafeLoader that records the source line of every mapping.

    Requested by external review: "it would be VERY useful if messages referred
    to a line number in the file. Just knowing an error exists is nice, knowing
    where is actually useful." Correct — a diagnostic without a position is half
    a tool.

    The key is `__line__`, stripped from every set-valued lookup so it never
    leaks into a rule's reasoning.
    """


def _construct_mapping_with_line(loader, node, deep=False):
    m = yaml.SafeLoader.construct_mapping(loader, node, deep=deep)
    m[LINE_KEY] = node.start_mark.line + 1
    return m


LINE_KEY = "__line__"
LineLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_with_line
)

ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"

KNOWN_ROOT_SECTIONS = [
    "key_protocol",
    "doctrine",
    "source_authority",
    "execution_identity",
    "task_frame",
    "lot",
    "roles",
    "gates",
    "artifacts",
    "validation",
    "failure_taxonomy",
    "telemetry",
    "ledger",
    "runtime_boundaries",
    "acceptance",
    "assembly_notes",
]

# The computed/asserted claim needs only a route, its gates, and the evidence
# registry. Project-specific doctrine, roles, telemetry, validation layers,
# ledgers, and release ceremony remain linted when declared, but are optional.
REQUIRED_ROOT_SECTIONS = ["lot", "gates", "artifacts"]

DEFAULT_FAILURE_SEVERITIES = {"low", "moderate", "high", "critical", "terminal"}
DEFAULT_FAILURE_RESPONSES = {"log", "repair", "refuse", "escalate", "block", "terminal_block"}

# Gates a program can decide with no model in the loop. Used by SK017 to
# report the promotion path from attested/unimplemented to automatic.
AUTOMATIC_CANDIDATES = {
    "source_boundary_gate",
    "doctrine_consistency_gate",
    "schema_validation_gate",
    "artifact_requirement_gate",
    "ledger_requirement_gate",
    "telemetry_requirement_gate",
    "lot_route_gate",
    "task_frame_gate",
    "role_handoff_gate",
}

VALID_ENFORCEMENT_CLASSES = {"automatic", "attested", "composite", "unimplemented"}

# Where artifact JSON Schemas live. SK023 is skipped if this is absent.
SCHEMA_DIR = os.environ.get("SK_SCHEMA_DIR", "schemas/artifacts")


@dataclass
class Finding:
    rule: str
    severity: str
    where: str
    ref: str
    message: str
    line: int | None = None


RULES: dict[str, str] = {}
_REGISTRY: list[tuple[str, Callable]] = []


def rule(rule_id: str, description: str):
    def deco(fn):
        RULES[rule_id] = description
        _REGISTRY.append((rule_id, fn))
        return fn

    return deco


class Index:
    """Resolved view of a KEY file. Everything the rules need, built once."""

    def __init__(self, doc: dict[str, Any]):
        self.doc = doc
        self.routes = self._list("lot", "routes")
        self.roles = self._list("roles", "runtime_protocol_roles")
        self.gates = self._list("gates", "gate_entries")
        self.artifacts = self._list("artifacts", "artifact_entries")
        self.failures = self._list("failure_taxonomy", "failure_entries")

        self.route_ids = {r.get("route_id") for r in self.routes} - {None}
        self.role_ids = {r.get("role_id") for r in self.roles} - {None}
        self.gate_ids = {g.get("gate_id") for g in self.gates} - {None}
        self.artifact_ids = {a.get("artifact_id") for a in self.artifacts} - {None}
        self.failure_ids = {f.get("failure_id") for f in self.failures} - {None}

        task_frame = doc.get("task_frame")
        self.task_frame = task_frame if isinstance(task_frame, dict) else {
            "lot_route_eligibility": sorted(self.route_ids),
        }

        tel = doc.get("telemetry")
        if isinstance(tel, dict):
            self.telemetry_events = set(tel.get("required_events") or [])
        else:
            # Without a separate telemetry registry, a route/role requirement
            # is its own declaration.
            self.telemetry_events = set().union(*(
                [_s(r.get("required_telemetry_events")) for r in self.routes + self.roles]
                + [_s(self.task_frame.get("required_telemetry_events"))]
            ))

        tax = doc.get("failure_taxonomy")
        if isinstance(tax, dict):
            self.severity_values = set(tax.get("severity_values") or [])
            self.response_values = set(tax.get("response_values") or [])
        else:
            self.severity_values = set(DEFAULT_FAILURE_SEVERITIES)
            self.response_values = set(DEFAULT_FAILURE_RESPONSES)

        # id -> source line, for diagnostics. Built once; rules stay unchanged.
        self.lines: dict[str, int] = {}
        for sec in KNOWN_ROOT_SECTIONS:
            body = doc.get(sec)
            if isinstance(body, dict) and LINE_KEY in body:
                self.lines[sec] = body[LINE_KEY]
        for coll, key in (
            (self.routes, "route_id"), (self.roles, "role_id"),
            (self.gates, "gate_id"), (self.artifacts, "artifact_id"),
            (self.failures, "failure_id"),
        ):
            for e in coll:
                if e.get(key) and LINE_KEY in e:
                    self.lines[e[key]] = e[LINE_KEY]
        self.schema_dir = SCHEMA_DIR

        # Reserved elements: anything whose status field says so.
        self.reserved_routes = {
            r["route_id"] for r in self.routes if r.get("route_status") == "reserved"
        }
        self.reserved_roles = {
            r["role_id"] for r in self.roles if r.get("status") == "reserved"
        }
        self.reserved_artifacts = {
            a["artifact_id"]
            for a in self.artifacts
            if a.get("artifact_status") == "reserved"
        }
        self.reserved_gates = {
            g["gate_id"] for g in self.gates if g.get("status") == "reserved"
        }

        self.active_routes = [
            r for r in self.routes if r.get("route_status") != "reserved"
        ]

    def _list(self, section: str, key: str) -> list[dict]:
        sec = self.doc.get(section) or {}
        val = sec.get(key)
        return val if isinstance(val, list) else []

    # ---- usage sets -------------------------------------------------

    def gates_used(self) -> set[str]:
        used: set[str] = set()
        for r in self.routes:
            used |= _s(r.get("required_gates"))
        for r in self.roles:
            used |= _s(r.get("required_gates"))
        used |= _s(self.task_frame.get("required_gates"))
        lot = self.doc.get("lot") or {}
        if lot.get("lot_gate_ref"):
            used.add(lot["lot_gate_ref"])
        for a in self.artifacts:
            if a.get("required_gate"):
                used.add(a["required_gate"])
        return used

    def artifacts_used(self) -> set[str]:
        used: set[str] = set()
        for r in self.routes:
            used |= _s(r.get("required_artifacts"))
        for r in self.roles:
            used |= _s(r.get("required_artifacts"))
        for g in self.gates:
            used |= _s(g.get("required_evidence"))
        used |= _s(self.task_frame.get("required_artifacts"))
        return used

    def roles_used(self) -> set[str]:
        used: set[str] = set()
        for r in self.routes:
            used |= _s(r.get("selected_roles"))
        return used

    def telemetry_used(self) -> set[str]:
        used: set[str] = set()
        for r in self.routes:
            used |= _s(r.get("required_telemetry_events"))
        for r in self.roles:
            used |= _s(r.get("required_telemetry_events"))
        used |= _s(self.task_frame.get("required_telemetry_events"))
        return used

    def failures_referenced(self) -> set[str]:
        used: set[str] = set()
        rb = self.doc.get("runtime_boundaries") or {}
        for v in rb.get("violation_failure_map") or []:
            if v.get("violation_failure_id"):
                used.add(v["violation_failure_id"])
        for g in self.gates:
            if g.get("detects_failure_id"):
                used.add(g["detects_failure_id"])
            used |= _s(g.get("detects_failure_ids"))
        return used


def _s(v: Any) -> set[str]:
    if not v:
        return set()
    if isinstance(v, str):
        return {v}
    return {x for x in v if isinstance(x, str)}


def _strip_lines(o):
    """Remove __line__ recursively. Used where raw structure is compared."""
    if isinstance(o, dict):
        return {k: _strip_lines(v) for k, v in o.items() if k != LINE_KEY}
    if isinstance(o, list):
        return [_strip_lines(x) for x in o]
    return o


# ---------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------


@rule("SK001", "All required root sections are present")
def sk001(ix: Index) -> Iterable[Finding]:
    for sec in REQUIRED_ROOT_SECTIONS:
        if sec not in ix.doc:
            yield Finding("SK001", ERROR, "root", sec, f"Required root section missing: {sec}")


@rule("SK002", "Every referenced gate_id is defined in gates.gate_entries")
def sk002(ix: Index) -> Iterable[Finding]:
    for where, refs in _gate_refs(ix):
        for r in refs:
            if r not in ix.gate_ids:
                yield Finding("SK002", ERROR, where, r, f"Undefined gate referenced: {r}")


@rule("SK003", "Every referenced artifact_id is defined in artifacts.artifact_entries")
def sk003(ix: Index) -> Iterable[Finding]:
    for where, refs in _artifact_refs(ix):
        for r in refs:
            if r not in ix.artifact_ids:
                yield Finding("SK003", ERROR, where, r, f"Undefined artifact referenced: {r}")


@rule("SK004", "Every referenced role_id is defined in roles.runtime_protocol_roles")
def sk004(ix: Index) -> Iterable[Finding]:
    for r in ix.routes:
        for ref in _s(r.get("selected_roles")):
            if ref not in ix.role_ids:
                yield Finding(
                    "SK004", ERROR, f"lot/{r.get('route_id')}", ref,
                    f"Undefined role referenced: {ref}",
                )


@rule("SK005", "Every referenced route_id (incl. fallback and escalation) is defined")
def sk005(ix: Index) -> Iterable[Finding]:
    for r in ix.routes:
        fb = r.get("fallback_route")
        if fb and fb not in ix.route_ids:
            yield Finding(
                "SK005", ERROR, f"lot/{r.get('route_id')}", fb,
                f"Undefined fallback route: {fb}",
            )
    for ref in _s(ix.task_frame.get("lot_route_eligibility")):
        if ref not in ix.route_ids:
            yield Finding("SK005", ERROR, "task_frame", ref, f"Undefined eligible route: {ref}")
    for role in ix.roles:
        for ref in _s(role.get("selected_by_route_ids")):
            if ref not in ix.route_ids:
                yield Finding(
                    "SK005", ERROR, f"role/{role.get('role_id')}", ref,
                    f"Undefined route in selected_by_route_ids: {ref}",
                )


@rule("SK006", "Every referenced telemetry event is declared in telemetry.required_events")
def sk006(ix: Index) -> Iterable[Finding]:
    for where, refs in _telemetry_refs(ix):
        for r in refs:
            if r not in ix.telemetry_events:
                yield Finding("SK006", ERROR, where, r, f"Undeclared telemetry event: {r}")


@rule("SK007", "Every referenced failure_id is defined in failure_taxonomy")
def sk007(ix: Index) -> Iterable[Finding]:
    rb = ix.doc.get("runtime_boundaries") or {}
    for v in rb.get("violation_failure_map") or []:
        fid = v.get("violation_failure_id")
        if fid and fid not in ix.failure_ids:
            yield Finding(
                "SK007", ERROR, "runtime_boundaries", fid,
                f"Undefined failure referenced: {fid}",
            )


@rule("SK008", "Every defined gate is required by at least one route, role, or task frame")
def sk008(ix: Index) -> Iterable[Finding]:
    for g in sorted(ix.gate_ids - ix.gates_used()):
        yield Finding(
            "SK008", ERROR, "gates", g,
            f"Orphan gate: {g} is defined but never invoked. It cannot fire.",
        )


@rule("SK009", "Every defined artifact is required by at least one route, role, or gate")
def sk009(ix: Index) -> Iterable[Finding]:
    for a in sorted(ix.artifact_ids - ix.artifacts_used()):
        sev = INFO if a in ix.reserved_artifacts else WARN
        yield Finding(
            "SK009", sev, "artifacts", a,
            f"Orphan artifact: {a} is defined but never required.",
        )


@rule("SK010", "Every defined role is selected by at least one route")
def sk010(ix: Index) -> Iterable[Finding]:
    for r in sorted(ix.role_ids - ix.roles_used()):
        sev = INFO if r in ix.reserved_roles else ERROR
        yield Finding(
            "SK010", sev, "roles", r,
            f"Orphan role: {r} is defined but never selected by a route.",
        )


@rule("SK011", "route.selected_roles and role.selected_by_route_ids agree in both directions")
def sk011(ix: Index) -> Iterable[Finding]:
    forward = {(r.get("route_id"), s) for r in ix.routes for s in _s(r.get("selected_roles"))}
    backward = {
        (rt, r.get("role_id")) for r in ix.roles for rt in _s(r.get("selected_by_route_ids"))
    }
    for route_id, role_id in sorted(forward - backward):
        yield Finding(
            "SK011", ERROR, f"lot/{route_id}", role_id,
            f"Route selects role {role_id}, but that role does not list {route_id} in selected_by_route_ids.",
        )
    for route_id, role_id in sorted(backward - forward):
        yield Finding(
            "SK011", ERROR, f"role/{role_id}", route_id,
            f"Role claims selection by {route_id}, but that route does not list it in selected_roles.",
        )


@rule("SK012", "Gate failure_response is a declared failure_taxonomy response value")
def sk012(ix: Index) -> Iterable[Finding]:
    alias = {"repair": "repair", "refuse": "refuse", "block": "block"}
    for g in ix.gates:
        fr = g.get("failure_response")
        if fr is None:
            yield Finding(
                "SK012", ERROR, "gates", g.get("gate_id", "?"),
                f"Gate {g.get('gate_id')} declares no failure_response.",
            )
            continue
        if fr not in ix.response_values and alias.get(fr) not in ix.response_values:
            yield Finding(
                "SK012", ERROR, "gates", g.get("gate_id", "?"),
                f"Gate {g.get('gate_id')} failure_response '{fr}' is not in failure_taxonomy.response_values.",
            )


@rule("SK013", "Gate severity is a declared failure_taxonomy severity value")
def sk013(ix: Index) -> Iterable[Finding]:
    for g in ix.gates:
        sev = g.get("severity")
        if sev and ix.severity_values and sev not in ix.severity_values:
            yield Finding(
                "SK013", ERROR, "gates", g.get("gate_id", "?"),
                f"Gate {g.get('gate_id')} severity '{sev}' is not in failure_taxonomy.severity_values.",
            )


@rule("SK014", "No active route requires a reserved gate, artifact, or role")
def sk014(ix: Index) -> Iterable[Finding]:
    for r in ix.active_routes:
        rid = r.get("route_id")
        for g in sorted(_s(r.get("required_gates")) & ix.reserved_gates):
            yield Finding("SK014", ERROR, f"lot/{rid}", g, f"Active route requires reserved gate {g}.")
        for a in sorted(_s(r.get("required_artifacts")) & ix.reserved_artifacts):
            yield Finding("SK014", ERROR, f"lot/{rid}", a, f"Active route requires reserved artifact {a}.")
        for s in sorted(_s(r.get("selected_roles")) & ix.reserved_roles):
            yield Finding("SK014", ERROR, f"lot/{rid}", s, f"Active route selects reserved role {s}.")


@rule("SK015", "Every active route is reachable from task_frame eligibility via fallback edges")
def sk015(ix: Index) -> Iterable[Finding]:
    seen: set[str] = set()
    stack = list(_s(ix.task_frame.get("lot_route_eligibility")))
    by_id = {r.get("route_id"): r for r in ix.routes}
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in by_id:
            continue
        seen.add(cur)
        fb = by_id[cur].get("fallback_route")
        if fb:
            stack.append(fb)
    for r in ix.active_routes:
        rid = r.get("route_id")
        if rid not in seen:
            yield Finding(
                "SK015", WARN, "lot", rid,
                f"Route {rid} is active but unreachable from task_frame.lot_route_eligibility.",
            )


@rule("SK016", "Fallback chains terminate; no cycles")
def sk016(ix: Index) -> Iterable[Finding]:
    by_id = {r.get("route_id"): r for r in ix.routes}
    for start in by_id:
        seen = []
        cur = start
        while cur:
            if cur in seen:
                yield Finding(
                    "SK016", ERROR, "lot", start,
                    f"Fallback cycle: {' -> '.join(seen + [cur])}",
                )
                break
            seen.append(cur)
            nxt = (by_id.get(cur) or {}).get("fallback_route")
            cur = nxt if nxt in by_id else None


@rule("SK017", "Every gate declares a valid enforcement_class")
def sk017(ix: Index) -> Iterable[Finding]:
    for g in ix.gates:
        gid = g.get("gate_id", "?")
        ec = g.get("enforcement_class")
        if ec is None:
            hint = " (mechanically decidable — candidate for 'automatic')" if gid in AUTOMATIC_CANDIDATES else ""
            yield Finding(
                "SK017", WARN, "gates", gid,
                f"Gate {gid} declares no enforcement_class; strength is unstated{hint}.",
            )
        elif ec not in VALID_ENFORCEMENT_CLASSES:
            yield Finding(
                "SK017", ERROR, "gates", gid,
                f"Gate {gid} enforcement_class '{ec}' is not one of {sorted(VALID_ENFORCEMENT_CLASSES)}.",
            )


@rule("SK018", "Every artifact declares a produced_by_role that exists and is selected by a route")
def sk018(ix: Index) -> Iterable[Finding]:
    used_roles = ix.roles_used()
    roles_declared = "roles" in ix.doc
    for a in ix.artifacts:
        aid = a.get("artifact_id", "?")
        producer = a.get("produced_by_role")
        if not producer:
            yield Finding(
                "SK018", ERROR, "artifacts", aid,
                f"Artifact {aid} declares no produced_by_role — nothing is responsible for making it.",
            )
            continue
        if roles_declared:
            if producer not in ix.role_ids:
                yield Finding(
                    "SK018", ERROR, "artifacts", aid,
                    f"Artifact {aid} names producer '{producer}', which is not a defined role.",
                )
            elif producer not in used_roles and producer not in ix.reserved_roles:
                yield Finding(
                    "SK018", WARN, "artifacts", aid,
                    f"Artifact {aid} is produced by '{producer}', which no route selects.",
                )


@rule("SK023", "Every artifact in the registry has a registered JSON Schema")
def sk023(ix: Index) -> Iterable[Finding]:
    if not os.path.isdir(ix.schema_dir):
        yield Finding(
            "SK023", INFO, "artifacts", ix.schema_dir,
            f"Schema directory '{ix.schema_dir}' not found; artifact schema check skipped. "
            "Without schemas, 'valid' in artifact_requirement_gate is undefined prose.",
        )
        return
    have = {
        os.path.basename(p)[: -len(".schema.json")]
        for p in os.listdir(ix.schema_dir)
        if p.endswith(".schema.json")
    }
    for aid in sorted(ix.artifact_ids - have):
        yield Finding(
            "SK023", ERROR, "artifacts", aid,
            f"Artifact {aid} has no schema at {ix.schema_dir}/{aid}.schema.json — "
            "the gate cannot decide whether an instance is valid.",
        )
@rule("SK019", "Every root section carries status and locked_by_pass metadata")
def sk019(ix: Index) -> Iterable[Finding]:
    metadata_convention_used = any(
        isinstance(ix.doc.get(sec), dict)
        and (
            "status" in ix.doc[sec]
            or "acceptance_status" in ix.doc[sec]
            or "locked_by_pass" in ix.doc[sec]
        )
        for sec in KNOWN_ROOT_SECTIONS
    )
    if not metadata_convention_used:
        return
    exempt = {"assembly_notes", "key_protocol"}
    for sec in KNOWN_ROOT_SECTIONS:
        if sec in exempt:
            continue
        body = ix.doc.get(sec)
        if not isinstance(body, dict):
            continue
        if "status" not in body and "acceptance_status" not in body:
            yield Finding("SK019", WARN, sec, "status", f"Section {sec} declares no status.")
        if "locked_by_pass" not in body:
            yield Finding("SK019", WARN, sec, "locked_by_pass", f"Section {sec} declares no locked_by_pass.")


@rule("SK020", "Doctrine scalar definitions appear verbatim among doctrine_claims")
def sk020(ix: Index) -> Iterable[Finding]:
    if "doctrine" not in ix.doc:
        return
    doc = ix.doc.get("doctrine") or {}
    claims = [c for c in (doc.get("doctrine_claims") or []) if isinstance(c, str)]
    if not claims:
        yield Finding("SK020", ERROR, "doctrine", "doctrine_claims", "No doctrine claims declared.")
        return
    joined = " ".join(claims)
    pairs = {
        "key_expansion": "KEY means {}.",
        "lot_expansion": "L.O.T. means {}.",
    }
    for field, tmpl in pairs.items():
        val = doc.get(field)
        if val and tmpl.format(val) not in claims:
            yield Finding(
                "SK020", ERROR, "doctrine", field,
                f"doctrine.{field} = '{val}' has no matching verbatim claim '{tmpl.format(val)}'.",
            )
    for field in ("logic_engine_definition", "forge_definition", "user_authority"):
        val = doc.get(field)
        if val and val not in joined:
            yield Finding(
                "SK020", ERROR, "doctrine", field,
                f"doctrine.{field} does not appear among doctrine_claims (term drift).",
            )


# Failures a gate inside the system cannot observe, because they describe an
# actor routing around the system. Detected by sk-verify against a run record:
# the absence of a required decision is the evidence. A failure may carry
# detection_layer: verifier to declare this rather than appear undetected.
VERIFIER_DETECTED = {"gate_bypass_attempt", "test_bypass_attempt"}


@rule("SK021", "Every critical or terminal failure has a declared detection point")
def sk021(ix: Index) -> Iterable[Finding]:
    referenced = ix.failures_referenced()
    for f in ix.failures:
        fid = f.get("failure_id")
        if f.get("severity") not in ("critical", "terminal") or fid in referenced:
            continue
        layer = f.get("detection_layer")
        if layer == "verifier":
            continue
        if layer and layer != "gate":
            yield Finding(
                "SK021", ERROR, "failure_taxonomy", fid,
                f"Failure '{fid}' declares detection_layer '{layer}', which is not 'gate' or 'verifier'.",
            )
            continue
        hint = (
            " No gate can observe this — it describes routing around the gate system. "
            "Declare detection_layer: verifier."
            if fid in VERIFIER_DETECTED else ""
        )
        yield Finding(
            "SK021", WARN, "failure_taxonomy", fid,
            f"Failure '{fid}' is {f.get('severity')} but no gate or boundary rule declares it as detected.{hint}",
        )


@rule("SK022", "Every declared required telemetry event is required by some route or role")
def sk022(ix: Index) -> Iterable[Finding]:
    for e in sorted(ix.telemetry_events - ix.telemetry_used()):
        yield Finding(
            "SK022", WARN, "telemetry", e,
            f"Telemetry event '{e}' is declared required but no route or role requires it.",
        )


# ---- reference collectors ------------------------------------------


def _gate_refs(ix: Index):
    for r in ix.routes:
        yield f"lot/{r.get('route_id')}", _s(r.get("required_gates"))
    for r in ix.roles:
        yield f"role/{r.get('role_id')}", _s(r.get("required_gates"))
    yield "task_frame", _s(ix.task_frame.get("required_gates"))
    lot = ix.doc.get("lot") or {}
    yield "lot", _s(lot.get("lot_gate_ref"))
    for a in ix.artifacts:
        yield f"artifact/{a.get('artifact_id')}", _s(a.get("required_gate"))


def _artifact_refs(ix: Index):
    for r in ix.routes:
        yield f"lot/{r.get('route_id')}", _s(r.get("required_artifacts"))
    for r in ix.roles:
        yield f"role/{r.get('role_id')}", _s(r.get("required_artifacts"))
    for g in ix.gates:
        yield f"gate/{g.get('gate_id')}", _s(g.get("required_evidence"))
    yield "task_frame", _s(ix.task_frame.get("required_artifacts"))


def _telemetry_refs(ix: Index):
    for r in ix.routes:
        yield f"lot/{r.get('route_id')}", _s(r.get("required_telemetry_events"))
    for r in ix.roles:
        yield f"role/{r.get('role_id')}", _s(r.get("required_telemetry_events"))
    yield "task_frame", _s(ix.task_frame.get("required_telemetry_events"))


# ---------------------------------------------------------------------


def lint(doc: dict[str, Any], only: set[str] | None = None) -> list[Finding]:
    ix = Index(doc)
    out: list[Finding] = []
    for rid, fn in _REGISTRY:
        if only and rid not in only:
            continue
        try:
            out.extend(fn(ix))
        except Exception as exc:  # a rule must never take the linter down
            out.append(Finding(rid, ERROR, "linter", rid, f"Rule {rid} raised {type(exc).__name__}: {exc}"))
    for f in out:
        if f.line is None:
            f.line = ix.lines.get(f.ref) or ix.lines.get(f.where.split("/")[-1]) \
                or ix.lines.get(f.where)
    order = {ERROR: 0, WARN: 1, INFO: 2}
    return sorted(out, key=lambda f: (order[f.severity], f.line or 10**9, f.rule))


def render(findings: list[Finding], path: str) -> str:
    lines = [f"sk-lint {path}", ""]
    if not findings:
        lines.append("  no findings")
    for f in findings:
        loc = f"{os.path.basename(path)}:{f.line}" if f.line else os.path.basename(path)
        lines.append(f"  {loc}")
        lines.append(f"    {f.severity:<5} {f.rule}  {f.message}")
    counts = {s: sum(1 for f in findings if f.severity == s) for s in (ERROR, WARN, INFO)}
    lines += [
        "",
        f"  {counts[ERROR]} error, {counts[WARN]} warn, {counts[INFO]} info "
        f"across {len(_REGISTRY)} rules",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sk-lint", description=__doc__)
    ap.add_argument("keyfile", nargs="?", help="path to a KEY yaml file")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--strict", action="store_true", help="exit 2 on warnings")
    ap.add_argument("--rules", action="store_true", help="list rules and exit")
    ap.add_argument("--only", help="comma-separated rule ids to run")
    args = ap.parse_args(argv)

    if args.rules:
        for rid in sorted(RULES):
            print(f"{rid}  {RULES[rid]}")
        return 0

    if not args.keyfile:
        ap.error("keyfile is required unless --rules is given")

    try:
        with open(args.keyfile, "r", encoding="utf-8") as fh:
            doc = yaml.load(fh, Loader=LineLoader)
    except FileNotFoundError:
        sys.stderr.write(f"sk-lint: no such file: {args.keyfile}\n")
        return 3
    except yaml.YAMLError as exc:
        sys.stderr.write(f"sk-lint: YAML parse error: {exc}\n")
        return 3

    if not isinstance(doc, dict):
        sys.stderr.write("sk-lint: KEY file must be a YAML mapping at the root\n")
        return 3

    only = set(args.only.split(",")) if args.only else None
    findings = lint(doc, only)

    if args.json:
        print(json.dumps(
            {
                "keyfile": args.keyfile,
                "rules_run": len(only) if only else len(_REGISTRY),
                "findings": [asdict(f) for f in findings],
                "counts": {
                    s: sum(1 for f in findings if f.severity == s)
                    for s in (ERROR, WARN, INFO)
                },
            },
            indent=2,
        ))
    else:
        print(render(findings, args.keyfile))

    if any(f.severity == ERROR for f in findings):
        return 1
    if args.strict and any(f.severity == WARN for f in findings):
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        os._exit(0)
