#!/usr/bin/env python3
"""
sk_verify.py — the judge.

sk-lint checks the KEY file. sk-ledger checks the witness. sk-artifacts checks
one piece of evidence. None of them answers the question that matters: *did
this run actually follow the contract?*

sk-verify reads a completed run directory and decides. It executes nothing.
That is the design: a judge does not need to have been present at the act, only
an evidentiary record and a rule it can apply. Any executor can produce a run
directory — Codex, a shell script, a person working by hand — and be judged the
same way. Enforcement without controlling execution.

THE BYPASS RULES
----------------
`gate_bypass_attempt` and `test_bypass_attempt` are classified critical in the
KEY file and no gate detects them, because a gate inside the system cannot
observe an actor routing around the system. They are detectable here and only
here: a run whose route requires a gate, and whose record contains no decision
for that gate, *is* the bypass. The absence is the evidence.

That is RUN06 and RUN12. They are the reason this file exists.

Usage:
    sk_verify.py <run-dir> [--key key.yaml] [--schemas DIR] [--ledger led.jsonl]
                 [--json] [--strict]

Exit codes: 0 run conforms · 1 violations · 3 unreadable
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
from dataclasses import dataclass, asdict

import yaml

import sk_artifacts as A
import sk_ledger as L

CRITICAL, ERROR, WARN, INFO = "CRITICAL", "ERROR", "WARN", "INFO"
SEV_ORDER = {CRITICAL: 0, ERROR: 1, WARN: 2, INFO: 3}

RUN_MANIFEST_REQUIRED = [
    "run_id", "key_file", "key_sha256", "task_frame_id",
    "selected_route_id", "actor", "result",
]


@dataclass
class Violation:
    rule: str
    severity: str
    failure_id: str | None
    message: str


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Run:
    def __init__(self, run_dir: str):
        self.dir = run_dir
        mpath = os.path.join(run_dir, "run.json")
        if not os.path.exists(mpath):
            raise FileNotFoundError(f"{run_dir} has no run.json — not a run directory")
        self.manifest = json.load(open(mpath, encoding="utf-8"))
        self.artifacts: list[dict] = []
        self.artifact_paths: dict[int, str] = {}
        for p in sorted(glob.glob(os.path.join(run_dir, "artifacts", "*.json"))):
            try:
                a = json.load(open(p, encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            self.artifact_paths[id(a)] = p
            self.artifacts.append(a)

    def by_id(self, aid: str) -> list[dict]:
        return [a for a in self.artifacts if a.get("artifact_id") == aid]


# ---------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------


def verify_run(
    run: Run,
    key_doc: dict,
    key_path: str,
    registry,
    by_artifact: dict,
    ledger_entries: list[dict] | None,
    schema_dir: str,
    trusted: dict[str, str] | None = None,
) -> list[Violation]:
    v: list[Violation] = []

    def add(rule, sev, msg, failure_id=None):
        v.append(Violation(rule, sev, failure_id, msg))

    m = run.manifest

    # --- RUN00: is this a real run or a shape fixture? ----------------
    # gen_runs.py produces runs with placeholder program hashes to exercise the
    # rules. They conform, which is the point, and that is exactly why they must
    # be impossible to cite as evidence that a build happened. A fixture that
    # can pass for a record is worse than a fixture that fails.
    if m.get("fixture") is True:
        add("RUN00", WARN,
            "this run is a generated shape fixture, not a record of a real build — "
            "it exercises the rules and must not be cited as evidence of a governed run",
            None)

    # --- RUN01: manifest completeness --------------------------------
    for f in RUN_MANIFEST_REQUIRED:
        if f not in m:
            add("RUN01", ERROR, f"run.json is missing required field '{f}'")
    if any(x.rule == "RUN01" for x in v):
        return v  # nothing further is meaningful

    # --- RUN02: the governing document is the one that was used ------
    actual_key = sha256_file(key_path)
    if m["key_sha256"] != actual_key:
        add("RUN02", CRITICAL,
            f"run was governed by a different KEY file: run records {m['key_sha256'][:12]}…, "
            f"{os.path.basename(key_path)} is {actual_key[:12]}… — the contract being judged "
            "is not the contract that was followed",
            "cross_section_consistency_violation")

    routes = {r["route_id"]: r for r in key_doc["lot"]["routes"]}
    rid = m["selected_route_id"]
    route = routes.get(rid)

    # --- RUN03: route is real, active, and eligible ------------------
    if route is None:
        add("RUN03", CRITICAL, f"run selected route '{rid}', which the KEY does not define",
            "lot_route_violation")
        return v
    if route.get("route_status") == "reserved":
        add("RUN03", ERROR, f"run executed reserved route '{rid}'", "lot_route_violation")
    eligible = key_doc.get("task_frame", {}).get("lot_route_eligibility") or []
    if eligible and rid not in eligible:
        add("RUN03", ERROR,
            f"route '{rid}' is not in task_frame.lot_route_eligibility", "lot_route_violation")

    # --- RUN04: every artifact in the run is valid evidence ----------
    for a in run.artifacts:
        issues = A.validate_instance(
            a, registry, by_artifact, key_doc, ledger_entries,
            os.path.join(run.dir, "artifacts"),
        )
        for i in issues:
            add("RUN04", ERROR,
                f"{a.get('artifact_id', '?')}: [{i.rule}] {i.path} — {i.message}",
                "artifact_invalid")

    # --- RUN05: every artifact the route requires is present ---------
    present = {a.get("artifact_id") for a in run.artifacts}
    for aid in route.get("required_artifacts") or []:
        if aid not in present:
            add("RUN05", ERROR, f"route '{rid}' requires artifact '{aid}', which the run does not contain",
                "artifact_missing")

    # --- RUN06: gate bypass. The absence IS the evidence. ------------
    decisions = {
        (a.get("body") or {}).get("gate_id"): a for a in run.by_id("gate_decision_artifact")
    }
    required_gates = route.get("required_gates") or []
    for gid in required_gates:
        if gid not in decisions:
            add("RUN06", CRITICAL,
                f"route '{rid}' requires gate '{gid}' and the run records no decision for it — "
                "the gate did not fire",
                "gate_bypass_attempt")

    # --- RUN07: no decisions for gates the route does not require ----
    # Severity note: this began as a WARN and was raised to ERROR when the
    # fixture made the shape obvious. The route defines the authorized gate
    # set; a decision outside it is an actor evaluating a gate it was never
    # granted, and the natural abuse is manufacturing an attested pass for a
    # gate — acceptance_lock_gate, say — that the route deliberately withheld.
    # Scope escalation, not extra diligence.
    for gid in decisions:
        if gid and gid not in required_gates:
            add("RUN07", ERROR,
                f"run records a decision for gate '{gid}', which route '{rid}' does not require — "
                "the actor evaluated a gate outside its authorized set",
                "unbounded_actor_authority")

    # --- RUN08: gate evidence resolves to artifacts in this run ------
    for gid, art in decisions.items():
        for ev in ((art.get("body") or {}).get("evidence_refs") or []):
            eid = ev.get("artifact_id")
            if eid not in present:
                add("RUN08", ERROR,
                    f"gate '{gid}' cites evidence '{eid}', which is not present in the run",
                    "artifact_missing")

    # --- RUN09: telemetry covers what the route requires -------------
    required_events = route.get("required_telemetry_events") or []
    traces = run.by_id("telemetry_trace_artifact")
    seen_events: set[str] = set()
    for t in traces:
        for e in ((t.get("body") or {}).get("events") or []):
            seen_events.add(e.get("event_type"))
    if required_events and not traces:
        add("RUN09", ERROR, f"run contains no telemetry trace for route '{rid}'",
            "telemetry_gap")
    for evt in required_events:
        if evt not in seen_events:
            add("RUN09", ERROR,
                f"route '{rid}' requires telemetry event '{evt}', which the trace does not contain",
                "telemetry_gap")

    # --- RUN10: ledger witnesses this run ----------------------------
    if ledger_entries is not None:
        breaks = L.verify(ledger_entries)
        if breaks:
            add("RUN10", CRITICAL,
                f"ledger chain is broken ({breaks[0].kind} at seq {breaks[0].seq}) — "
                "no artifact reference in this run can be trusted",
                "ledger_tamper_attempt")
        hashes = {e.get("entry_hash") for e in ledger_entries}
        types_present = {e.get("entry_type") for e in ledger_entries}
        for ref in m.get("ledger_entry_hashes") or []:
            if ref not in hashes:
                add("RUN10", ERROR, f"run cites ledger entry {ref[:12]}… which is not in the chain",
                    "ledger_entry_missing")
        for et in route.get("required_ledger_entries") or []:
            if et not in types_present:
                add("RUN10", ERROR,
                    f"route '{rid}' requires a '{et}' ledger entry; none is present",
                    "ledger_entry_missing")
        for a in run.artifacts:
            ref = a.get("ledger_ref")
            if ref and ref != "pending" and ref not in hashes:
                add("RUN10", ERROR,
                    f"{a.get('artifact_id')} cites ledger_ref {ref[:12]}… which is not in the chain",
                    "ledger_entry_missing")
    else:
        add("RUN10", WARN, "no ledger supplied; the run is unwitnessed and RUN10 was skipped")

    # --- RUN11: a failed gate must be answered -----------------------
    failed = {
        gid: (a.get("body") or {}).get("decision")
        for gid, a in decisions.items()
        if (a.get("body") or {}).get("decision") not in ("pass", None)
    }
    if failed and m.get("result") == "pass":
        add("RUN11", CRITICAL,
            f"run result is 'pass' but gate(s) {sorted(failed)} did not pass — "
            "a failing gate cannot produce a passing run",
            "gate_evaluation_violation")
    if failed and not (run.by_id("audit_report_artifact") or run.by_id("repair_report_artifact")):
        add("RUN11", ERROR,
            f"gate(s) {sorted(failed)} failed but the run records neither an audit nor a repair — "
            "the declared failure response was not carried out",
            "gate_evaluation_violation")

    # --- RUN12: test bypass. Same shape as RUN06. --------------------
    reports = run.by_id("validation_report_artifact")
    declared_layers = set(key_doc.get("validation", {}).get("validation_layers") or [])
    if declared_layers and not reports:
        add("RUN12", CRITICAL,
            "the run records no validation report — validation cannot be shown to have run",
            "test_bypass_attempt")
    for r in reports if declared_layers else []:
        b = r.get("body") or {}
        skipped = b.get("layers_skipped") or []
        if skipped and not (b.get("skip_justification") or "").strip():
            add("RUN12", CRITICAL,
                f"validation skipped {len(skipped)} layer(s) with no justification: {skipped}",
                "test_bypass_attempt")
        ran = set(b.get("layers_run") or [])
        if declared_layers and not ran:
            add("RUN12", CRITICAL, "validation report lists no layers run", "test_bypass_attempt")

    # --- RUN16: the chain of evidence under an automatic gate --------
    # A gate is only as automatic as the weakest thing it rests on. If an
    # automatic gate cites evidence that an actor wrote by hand, the gate has
    # been quietly downgraded and the record does not show it.
    gate_defs = {g["gate_id"]: g for g in key_doc.get("gates", {}).get("gate_entries", [])}
    by_aid_source = {a.get("artifact_id"): a.get("evidence_source") for a in run.artifacts}

    for gid, art in decisions.items():
        if gate_defs.get(gid, {}).get("enforcement_class") != "automatic":
            continue
        if art.get("evidence_source") != "program":
            add("RUN16", CRITICAL,
                f"gate '{gid}' is automatic but its decision was not program-produced "
                f"(evidence_source={art.get('evidence_source')!r}) — the gate was decided by assertion",
                "gate_bypass_attempt")
        for ev in ((art.get("body") or {}).get("evidence_refs") or []):
            eid = ev.get("artifact_id")
            src = by_aid_source.get(eid)
            if eid in present and src != "program":
                add("RUN16", ERROR,
                    f"automatic gate '{gid}' rests on '{eid}', which is {src!r} rather than "
                    "program-produced — the gate is only as automatic as its evidence",
                    "gate_evaluation_violation")

    # --- RUN17: the enforcement boundary, pinned --------------------
    # An artifact naming its producing program and hash is only meaningful
    # against an allowlist held OUTSIDE the run. Without one, "sk_emit.py at
    # hash X" is self-referential: a modified emitter records its own modified
    # hash and every check still passes. This is the true root of trust and it
    # must be stated, not implied.
    # A run that declares itself a fixture is claiming NOT to be evidence, and
    # RUN00 already prevents it being cited as such. Unrecognized program hashes
    # in a fixture are expected, so RUN17 reports at WARN there. This is not an
    # escape hatch: the price of the downgrade is the fixture flag itself, which
    # disqualifies the run from ever being offered as proof of a build.
    is_fixture = m.get("fixture") is True
    boundary_sev = WARN if is_fixture else CRITICAL

    if trusted is None:
        add("RUN17", WARN,
            "no trusted-programs allowlist supplied — produced_by_program hashes are "
            "self-reported and unchecked. Evidence is attributable, not yet trustworthy.",
            None)
    else:
        for a in run.artifacts:
            if a.get("evidence_source") != "program":
                continue
            prog = a.get("produced_by_program") or {}
            name = prog.get("name", "?")
            digest = prog.get("sha256", "")
            if not digest:
                add("RUN17", boundary_sev,
                    f"{a.get('artifact_id')} names '{name}' but records no executable hash — "
                    f"--program {name} did not resolve on PATH, so the producing binary is "
                    "unidentified. Do not add this artifact's input hash to the allowlist.",
                    "unbounded_actor_authority")
                continue
            base = name.split(".", 1)[0]
            expected = trusted.get(name) or trusted.get(base)
            if expected is None:
                add("RUN17", boundary_sev,
                    f"{a.get('artifact_id')} was produced by '{name}', which is not in the "
                    "trusted-programs allowlist — an unrecognized program produced evidence",
                    "unbounded_actor_authority")
            elif digest != expected:
                add("RUN17", boundary_sev,
                    f"{a.get('artifact_id')} names '{name}' at {digest[:12]}…, but the allowlist "
                    f"pins {expected[:12]}… — the producing program was modified",
                    "unbounded_actor_authority")

    # --- RUN18: fixture ledgers are not audit ledgers ----------------
    if ledger_entries:
        genesis = ledger_entries[0]
        cls = genesis.get("ledger_class")
        if cls is None:
            add("RUN18", WARN,
                "ledger genesis declares no ledger_class — a seeded demonstration chain and a "
                "real audit chain are indistinguishable to this tool",
                None)
        elif cls == "fixture":
            add("RUN18", WARN,
                "this run is witnessed by a FIXTURE ledger (seeded, retroactive) — it verifies, "
                "but it is not an audit record and must not be cited as one",
                None)

    # --- RUN13: producers are on the route ---------------------------
    selected = set(route.get("selected_roles") or [])
    for a in run.artifacts:
        producer = a.get("produced_by_role")
        if producer and selected and producer not in selected:
            add("RUN13", ERROR,
                f"{a.get('artifact_id')} was produced by '{producer}', "
                f"which route '{rid}' does not select",
                "role_sequence_violation")

    # --- RUN14: the run is one run -----------------------------------
    for a in run.artifacts:
        if a.get("run_id") and a["run_id"] != m["run_id"]:
            add("RUN14", ERROR,
                f"{a.get('artifact_id')} carries run_id '{a['run_id']}', "
                f"but the run is '{m['run_id']}' — evidence imported from another run",
                "cross_section_consistency_violation")

    # --- RUN15: no actor may claim final authority -------------------
    for a in run.artifacts:
        if a.get("claims_final_authority") is True:
            add("RUN15", CRITICAL,
                f"{a.get('artifact_id')} asserts final authority; only the user holds it",
                "unbounded_actor_authority")

    return v


# ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sk-verify", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--key", default=None, help="KEY file (default: from run.json key_file)")
    ap.add_argument("--schemas", default="schemas/artifacts")
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--trusted", default=None,
                    help="allowlist of program name -> sha256 (default: TRUSTED_PROGRAMS.sha256)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    args = ap.parse_args(argv)

    try:
        run = Run(args.run_dir)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"sk-verify: {exc}\n")
        return 3

    key_path = args.key or run.manifest.get("key_file")
    if not key_path or not os.path.exists(key_path):
        sys.stderr.write(f"sk-verify: KEY file not found: {key_path}\n")
        return 3
    key_doc = yaml.safe_load(open(key_path, encoding="utf-8"))

    registry, by_artifact = A.load_registry(args.schemas)

    ledger_path = args.ledger or run.manifest.get("ledger_file")
    ledger_entries = None
    if ledger_path and os.path.exists(ledger_path):
        ledger_entries = [json.loads(l) for l in open(ledger_path, encoding="utf-8") if l.strip()]

    trusted = None
    tp = args.trusted or run.manifest.get("trusted_programs_file")
    if not tp:
        beside_key = os.path.join(os.path.dirname(os.path.abspath(key_path)),
                                  "TRUSTED_PROGRAMS.sha256")
        if os.path.exists(beside_key):
            tp = beside_key
        elif os.path.exists("TRUSTED_PROGRAMS.sha256"):
            tp = "TRUSTED_PROGRAMS.sha256"
    if tp and os.path.exists(tp):
        trusted = {}
        for line in open(tp, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            digest, _, name = line.partition("  ")
            if name:
                trusted[name.strip()] = digest

    violations = verify_run(run, key_doc, key_path, registry, by_artifact,
                            ledger_entries, args.schemas, trusted)
    violations.sort(key=lambda x: (SEV_ORDER[x.severity], x.rule))

    counts = {s: sum(1 for x in violations if x.severity == s) for s in SEV_ORDER}

    if args.json:
        print(json.dumps({
            "run_dir": args.run_dir,
            "run_id": run.manifest.get("run_id"),
            "route": run.manifest.get("selected_route_id"),
            "artifacts": len(run.artifacts),
            "conforms": not any(x.severity in (CRITICAL, ERROR) for x in violations),
            "counts": counts,
            "violations": [asdict(x) for x in violations],
        }, indent=2))
    else:
        print(f"sk-verify {args.run_dir}")
        print(f"  run    {run.manifest.get('run_id')}")
        print(f"  route  {run.manifest.get('selected_route_id')}")
        print(f"  {len(run.artifacts)} artifacts, KEY {os.path.basename(key_path)}")
        print()
        if not violations:
            print("  run conforms to the KEY contract")
        for x in violations:
            fid = f"  [{x.failure_id}]" if x.failure_id else ""
            print(f"  {x.severity:<8} {x.rule}{fid}")
            print(f"           {x.message}")
        print(f"\n  {counts[CRITICAL]} critical, {counts[ERROR]} error, "
              f"{counts[WARN]} warn across 18 run rules")

    if any(x.severity in (CRITICAL, ERROR) for x in violations):
        return 1
    if args.strict and counts[WARN]:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        os._exit(0)
