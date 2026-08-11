# TASK FRAME — Emitter Pass (`sk_emit.py`)

**Actor:** Grok
**Actor role:** builder
**Route:** `protocol_build_route`
**Mutation scope:** `approved_generated_files_only` — create `sk_emit.py` only. Do not modify any existing file.
**Acceptance gate:** `python3 test_sk_emit.py` exits 0. That test already exists and currently fails. It was written before this spec deliberately: a gate written after the thing it judges is shaped around what was built rather than what was required.

---

## Why this pass exists

Every tool in this repo verifies structure. None of them can yet tell the difference between an artifact a program computed and an artifact a model wrote to look like one.

That gap is now closed at the schema level: every artifact declares `evidence_source`, and `SEM11`/`RUN16` reject an automatic gate decided by assertion. But the rules can only check declarations. Until real emitters exist, `runs/good` demonstrates the *shape* of a governed build rather than being the record of one.

`sk_emit.py` is what makes the declarations true.

**The rule this pass encodes:** the tool that performs the check writes the artifact. A model never writes an artifact for an `automatic` gate.

---

## Deliverable

One file: `sk_emit.py`. Python 3.11, standard library plus `pyyaml` only. No new dependencies.

### CLI contract

```
sk_emit.py --list
sk_emit.py inventory  --source DIR --out DIR
sk_emit.py boundary   --baseline FILE --current FILE --out DIR
sk_emit.py taskframe  --frame FILE --key FILE --out DIR
sk_emit.py route      --key FILE --route ID --out DIR
sk_emit.py validate   --key FILE --out DIR
sk_emit.py telemetry  --events FILE --route ID --out DIR
sk_emit.py gate       --key FILE --gate ID --evidence DIR --out DIR
sk_emit.py run        --key FILE --route ID --ledger FILE --out DIR
```

Each subcommand writes `<out>/<artifact_id>.json`. `run` composes all of them into a complete run directory (`run.json` + `artifacts/`).

**Exit codes:** `0` the check passed · `1` the check FAILED (artifact still written, recording the failure) · `2` bad arguments · `3` unreadable input.

Exit 1 on a detected violation is not optional. An emitter that detects a boundary violation and exits 0 has reported a fact and enforced nothing.

### Envelope every emitted artifact must carry

```json
{
  "artifact_id": "...",
  "artifact_status": "validated",
  "schema_version": "1.0.0",
  "run_id": "...",
  "pass_id": "PASS_19",
  "timestamp": "<ISO 8601 UTC, seconds precision, Z suffix>",
  "produced_by_role": "<from the KEY's produced_by_role for this artifact>",
  "produced_by_actor": "Codex",
  "route_id": "...",
  "required_gate": "...",
  "ledger_ref": "pending",
  "evidence_source": "program",
  "produced_by_program": {
    "name": "sk_emit.<subcommand>",
    "sha256": "<SHA-256 of sk_emit.py itself, computed at runtime>",
    "argv": ["..."]
  },
  "claims_final_authority": false,
  "body": { }
}
```

`produced_by_program.sha256` must be **computed by reading `sk_emit.py` at runtime**. The acceptance test hashes the file independently and compares. A hardcoded constant fails.

Read `produced_by_role` from the KEY's artifact registry rather than hardcoding a table. The registry is authoritative; a second copy is a second thing that can drift.

---

## Per-emitter requirements

### `inventory` → `source_inventory_artifact`
Walk `--source` recursively. For each file record `path` (relative, forward slashes), `sha256`, `bytes`. **Sort by path** — unsorted directory iteration is the most common source of nondeterminism and the test checks for it. `inventory_sha256` is the SHA-256 of the canonical JSON of the sorted file list.

### `boundary` → `source_boundary_lock_artifact`
Compare two inventory artifacts. Set `unmodified` from the actual hash comparison, populate `modified_paths` with the real differences, and **exit 1 when `unmodified` is false**.

Note the schema already forbids the contradictions here: SEM10 rejects an artifact claiming unmodified while its hashes disagree. Don't work around it — if you find yourself needing to, the emitter is wrong.

### `taskframe` → `task_frame_artifact` + `task_frame_validation_artifact`
Read a task frame JSON. Check its `task_type` against `task_frame.forbidden_task_types` in the KEY. On a match: decision `refuse`, `forbidden_type_matched` set, exit 1.

### `route` → `lot_route_artifact`
Read `lot_route_eligibility` from the KEY. If `--route` is not eligible, exit 1. `eligible_route_ids` comes from the KEY, not from arguments.

### `validate` → `validation_report_artifact`
Run the existing test suites as subprocesses (`test_sk_lint.py`, `test_sk_ledger.py`, `test_sk_artifacts.py`, `test_sk_verify.py`) plus `sk_lint.py` on the KEY. Map results onto the KEY's declared `validation_layers`. `layers_skipped` must be empty unless `--skip` is passed with a justification — and SEM08 rejects a skip with no justification, so there is no path around this.

`validator_sha256` is the hash of the validator that ran.

### `telemetry` → `telemetry_trace_artifact`
Read an events JSON. Assign `event_sequence_index` contiguously from 0 in file order. SEM01 rejects gaps, so renumbering rather than passing indices through is the correct behavior.

### `gate` → `gate_decision_artifact`
For an `automatic` gate only. Read the gate's `enforcement_class` from the KEY; **refuse with exit 2 if it is not `automatic`** — attested gates require a human attestation and are out of scope for this pass. Populate `evidence_refs` by hashing the actual artifact files in `--evidence`. Decision follows from whether the evidence artifacts themselves passed.

### `run` → complete run directory
Compose the above for `--route`. Emit `run.json` with `key_sha256` computed from the KEY file, all four `gate_decision_artifact`s the build route requires, and one `role_output_artifact` with `evidence_source: "attestation"` (a role summarizing its own work is a claim, and the schema makes it say so).

---

## Constraints

**Do not modify** `sk_lint.py`, `sk_artifacts.py`, `sk_verify.py`, `sk_ledger.py`, `test_sk_emit.py`, the schemas, or `key.repaired.yaml`. If an existing rule blocks a correct emitter, stop and report it — do not route around it. A rule that blocks correct behavior is a finding worth more than the workaround.

**Do not write artifacts by hand** anywhere in this pass, including in tests or examples. Every artifact comes from code that computed it.

**Determinism.** Two runs on identical input must produce byte-identical output except `timestamp` and `run_id`. Sort every collection. Use `json.dumps(..., sort_keys=True, separators=(",", ":"))` for anything you hash.

**No fake completion.** If an emitter cannot be built correctly, leave it unimplemented and say so. `--list` should show it as unimplemented rather than emitting an artifact that asserts a check that did not happen. A missing emitter is a known gap; a lying emitter is a corrupted record.

---

## Definition of done

```bash
python3 test_sk_emit.py        # exit 0
make all                       # everything still green
python3 sk_verify.py runs/good # still conforms
```

Then, and only then:

```bash
sha256sum sk_emit.py
python3 sk_ledger.py append ledger/solomons-key-builder-ledger.jsonl \
    --pass PASS_19 --name "Emitter pass" --actor Grok --actor-role builder \
    --type validation_run --result pass \
    --artifact validation_report_artifact=<path to emitted validation report>
python3 sk_ledger.py head ledger/solomons-key-builder-ledger.jsonl > ledger/HEAD
```

## Handoff back

Report: the SHA-256 of `sk_emit.py`, the `test_sk_emit.py` output verbatim, the new ledger head, any emitter left unimplemented and why, and any existing rule that blocked correct behavior.

The last item is the one worth the most. Every rule in this repo was written by inference about what a governed build needs. This is the first pass where a real build tests that inference, and where the inference was wrong is exactly what `11_GPT_PROTOCOL_ITERATION_LOG` exists to record.
