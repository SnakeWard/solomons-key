# sk-lint findings — `solomons-key.logic-engine.v1.key.yaml`

Verifier run against the KEY file as it stands. 22 rules, 1,302 lines, 13 gates, 17 artifacts, 11 roles, 6 routes, 20 telemetry events, 10 failure classes.

**Result: 6 errors, 32 warnings.**

Everything below was produced by a program. None of it depends on a model's judgment being trusted.

---

## Errors — closed by `repair_pass.py`

### SK008 · Orphan gate

```
doctrine_consistency_gate is defined but never invoked. It cannot fire.
```

The gate appears exactly once in the file: at its own definition on line 656. No route requires it, no role requires it, the task frame does not list it, and `lot_gate_ref` does not point at it.

This matters because `doctrine_term_corruption` is classified **critical** with response `block`. The failure is defined, the detector is defined, and the wire between them was never run. A critical failure with no invocation point is worse than no gate at all, because it reads as coverage.

**Fix:** wired into `protocol_validation_route.required_gates`. Doctrine validation is already a declared validation layer, so this is where it belongs.

### SK011 · Role/route directional asymmetry (×5)

The routing graph declares selection in both directions — `route.selected_roles` and `role.selected_by_route_ids` — and the two disagree in five places:

| Direction | Route | Role |
|---|---|---|
| route → role only | `user_acceptance_escalation` | `acceptance_precheck_role` |
| route → role only | `user_acceptance_escalation` | `refusal_escalation_role` |
| role → route only | `final_assembly_route_reserved` | `acceptance_precheck_role` |
| role → route only | `protocol_validation_route` | `gate_evaluation_role` |
| role → route only | `protocol_repair_route` | `refusal_escalation_role` |

Read from the route, the graph is correct. Read from the role, it is different. A runtime that dispatches by reading routes and a validator that audits by reading roles would disagree about what happened, and both would believe themselves.

This defect class is effectively invisible to human review at 1,300 lines and trivial for a program. It is the strongest argument in the file for the verifier existing.

**Fix:** all five closed by adding the missing entry on whichever side omitted it.

---

## Warnings — partially closed, deliberately

### SK017 · No gate declared its enforcement strength (13)

Closed. Every gate now carries `enforcement_class`: 9 `automatic`, 3 `attested`, 1 `composite`. See README.

### SK021 · Critical failures with no declared detection point (4)

Two closed by adding `detects_failure_ids` to the gates that genuinely detect them:

- `doctrine_consistency_gate` → `doctrine_term_corruption`, `cross_section_consistency_violation`
- plus `source_boundary_gate`, `ledger_requirement_gate`, `actor_authority_gate`, `acceptance_lock_gate`, `artifact_requirement_gate`, `schema_validation_gate` mapped to their obvious failures.

**Two left open on purpose:** `gate_bypass_attempt` and `test_bypass_attempt`.

No gate in the file plausibly detects either. Both are behavioral — they describe an actor trying to route *around* the gate system, which by construction a gate inside that system cannot observe. Inventing a mapping would have produced a clean lint run and a false one.

This is a real design gap and it needs your decision, not mine. The honest options:

1. **Detect at the verifier, not at a gate.** A run record missing a required gate decision *is* the evidence of bypass. This is the right answer and it belongs in `sk-verify`, not in the KEY file.
2. **Reclassify.** If bypass is only ever detectable post-hoc, these are not gate-detected failures and the taxonomy should say so with a `detection_layer: verifier` field.
3. **Accept as undetectable** and mark them explicitly, so the gap is declared rather than implied.

Option 1 plus the `detection_layer` field from option 2 is what I would do.

### SK018 · Artifacts with no producing role (7)

`task_frame_artifact`, `source_inventory_artifact`, `source_boundary_lock_artifact`, `schema_artifact`, `audit_report_artifact`, `acceptance_lock_artifact`, `skeleton_example_artifact`.

Each is required by a gate or route as evidence, but no role in `runtime_protocol_roles` is declared to produce it. Under the current file these artifacts must exist and nothing is responsible for making them.

**Left open.** Fixing it properly means deciding producer roles, not adding a field — and it is entangled with the artifact-schema work below. Do them together.

### SK022 · Declared-required telemetry events nothing requires (7)

`task_frame_refused`, `lot_route_requested`, `lot_route_refused`, `gate_evaluation_started`, `role_invocation_requested`, `role_output_received`, `ledger_entry_required`.

Seven of twenty required events are required by no route and no role. Either the routes under-declare their telemetry (likely — most are refusal-path events on routes that only declare happy-path events), or the required list is aspirational.

**Left open.** This one is worth resolving before building any telemetry pipeline, because it determines whether a run record is checkable for completeness. My read: the refusal-path events belong on the routes that can refuse, and adding them is mechanical once you confirm the intent.

### SK009 · Orphan artifact (1)

`skeleton_example_artifact` — defined, never referenced. Probably a leftover from an earlier pass. Delete or wire.

---

## What this run does not establish

The verifier proves the KEY file's structure is sound. It does not prove:

- **That gate criteria are meaningful.** `artifact_requirement_gate` confirms artifacts "exist and are valid." *Valid* is undefined prose. A validator can currently prove the ceremony was performed, not that the judgment was right. Closing this requires JSON Schemas for all 17 artifacts — the largest remaining lift and the one that converts the most gates to genuinely automatic.
- **That the ledger is tamper-evident.** `ledger_tamper_attempt` is classified critical, but nothing detects rewriting. Hash-chain each entry with `prev_hash`; the verifier recomputes. Roughly 30 lines, same primitive as the SHA-256 verification already in the workflow.
- **That any of this was ever enforced during a real build.** No run records exist yet. That is `sk-verify`.

---

## Sequence from here

1. **Land this.** CI is written; the linter runs on every push. The orphan gate becomes reproducible by anyone rather than a claim.
2. **Hash-chain the ledger.** Smallest remaining piece, converts a classification into a detection.
3. **Write JSON Schemas for the 17 artifacts.** The real work. Do SK018 producer-role assignment in the same pass.
4. **Resolve SK021 and SK022 by decision**, not by patch.
5. **`sk-verify <run-dir>`.** Reads the KEY plus a run record, returns pass/fail. At that point Codex-produced runs become judgable and the framework has enforcement rather than a description of enforcement.
6. **Log every red case in `11_GPT_PROTOCOL_ITERATION_LOG`.** That file currently has a complete apparatus for recording what real builds taught the protocol and zero entries. The 22 fixtures are 22 entries. The orphan gate is the first one worth writing up.
