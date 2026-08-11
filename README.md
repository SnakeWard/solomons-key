# sk-lint

A structural verifier for Solomon's Key KEY files.

**A gate is real if and only if you have seen it fail on an input constructed to make it fail, and the failure was produced by a program rather than by a model's judgment.**

`sk-lint` is the first thing in Solomon's Key that meets that bar. It reads a KEY file and decides pass/fail by program. No model is in the loop. Twenty-two rules, each with a committed red-corpus fixture proving it discriminates.

<!-- counts:begin -->
**23** lint rules · **19** run rules · **13** semantic rules · **136** tests passing
<!-- counts:end -->

## Why a verifier and not a runtime

The obvious plan is: build the Logic Engine, then the engine enforces the KEY file. That blocks all enforcement behind a runtime that does not exist yet.

A verifier inverts it. It does not execute anything — it reads a record and judges it. Once the verifier exists, *any* executor produces runs it can judge: Codex, a shell script, a person working by hand. You get real enforcement without controlling execution, which is also the only model that survives contact with multi-vendor AI tooling you do not own.

This is the KEY file's own doctrine — *tests serve as the judge*. A judge does not need to have been present at the act. It needs an evidentiary record and a rule it can apply.

## Install and run

```bash
pip install pyyaml

python3 sk_lint.py key.yaml            # human-readable
python3 sk_lint.py key.yaml --json     # machine-readable
python3 sk_lint.py key.yaml --strict   # exit 2 on warnings too
python3 sk_lint.py --rules             # list all rules
python3 sk_lint.py key.yaml --only SK008,SK011
```

Exit codes: `0` clean · `1` errors · `2` `--strict` with warnings · `3` unreadable file.

Or `make all` to run repair, corpus generation, tests, and lint in order.

## What it found

On the unmodified `solomons-key.logic-engine.v1.key.yaml`: **6 errors, 32 warnings**.

The load-bearing one:

```
ERROR SK008  gates  Orphan gate: doctrine_consistency_gate is defined
                    but never invoked. It cannot fire.
```

`doctrine_term_corruption` is classified **critical** in the failure taxonomy. A gate exists to catch it. No route, role, or task frame ever requires that gate. The enforcement point for a critical failure was wired to nothing.

Five more (SK011) are directional asymmetries: routes selecting roles that do not list those routes in `selected_by_route_ids`, and vice versa. The graph reads correctly in one direction and is broken in the other. This class of defect is effectively invisible to human review of a 1,300-line file and trivial for a program.

See `FINDINGS.md` for the full report and the open items the repair pass deliberately does not close.

## Files

| File | Role |
|---|---|
| `sk_lint.py` | The verifier. 22 rules, no dependencies beyond PyYAML. |
| `repair_pass.py` | Closes the 6 errors by surgical string edit. Never round-trips YAML, so comments and ordering survive. Every edit is declared in source and fails loudly if its anchor does not match exactly once. |
| `gen_redcorpus.py` | Generates one deliberately-invalid fixture per rule from the green baseline. |
| `test_sk_lint.py` | The judge. Green passes; every red fixture trips its bound rule; every rule has a fixture. |
| `key.yaml` | The KEY file as it stands today. Lints with 6 errors. |
| `key.repaired.yaml` | Output of the repair pass. Lints with 0 errors. |
| `redcorpus/` | 22 fixtures + `manifest.json` binding each to its rule. |
| `sk_ledger.py` | Hash-chained append-only witness ledger. `init`, `seed`, `append`, `verify`, `head`. |
| `test_sk_ledger.py` | Seven tamper shapes. Five must be caught; two must not be, without an anchor. |
| `ledger/` | Ledger seeded from the KEY file's own 18-pass history, plus the committed `HEAD` anchor. |
| `sk_verify.py` | The judge. 15 rules over a completed run directory. |
| `gen_runs.py` | One conforming run, 17 violating runs bound to the rules they must trip. |
| `test_sk_verify.py` | Good run conforms; every bad run convicted; bypass rules fire at CRITICAL. |
| `runs/` | Generated run corpus. |
| `.github/workflows/sk-lint.yml` | CI: two jobs. Structure (corpus reproducible, rules discriminate, KEY lints clean) and witness (tamper detection works, chain matches anchor, prior entries byte-identical to the previous commit). |

## The rule that matters most

`test_sk_lint.py` asserts that **every rule in `sk_lint.RULES` has at least one red fixture**. Without that assertion, rules accumulate that have never been shown a violation — which is the exact failure this tool exists to prevent, reproduced one level up. A rule with no red case is a gate that has never fired.

## `enforcement_class`

The repair pass adds one field to every gate, because thirteen gates that look identically strong are not:

- **`automatic`** (9) — a program decides. No model, no attestation. `source_boundary_gate`, `doctrine_consistency_gate`, `schema_validation_gate`, `artifact_requirement_gate`, `ledger_requirement_gate`, `telemetry_requirement_gate`, `lot_route_gate`, `task_frame_gate`, `role_handoff_gate`.
- **`attested`** (3) — requires a signed human or model declaration recorded in the ledger. Genuinely weaker than automatic. Forensic, not preventive. `actor_authority_gate`, `repair_authorization_gate`, `acceptance_lock_gate`.
- **`composite`** (1) — passes only if its constituents passed in this run. `final_assembly_gate`.

Marking the attested three honestly is the difference between a governance manifest and a compliance-theater document. It also gives you a roadmap: every gate promoted from `attested` to `automatic` is a real, pointable increase in strength.

## sk-ledger — the witness, made checkable

The KEY file classifies `ledger_tamper_attempt` as **critical** with response `block`, declares the ledger append-only, and states prior entries must not be rewritten. Nothing enforced any of it. A JSONL file on disk is exactly as append-only as whoever holds the file handle.

`sk_ledger.py` chains each entry to the one before it: `entry_hash = sha256(canonical_json(entry - entry_hash))`, and every entry carries the preceding `entry_hash` as `prev_hash`.

```bash
python3 sk_ledger.py seed ledger/solomons-key-builder-ledger.jsonl --from-key key.repaired.yaml
python3 sk_ledger.py append ledger/... --pass PASS_18 --actor Codex \
        --type validation_run --gate schema_validation_gate=pass \
        --artifact validation_report_artifact=reports/v18.json
python3 sk_ledger.py verify ledger/... --expect-head "$(cat ledger/HEAD)"
```

Artifacts referenced in an entry are hashed at append time, so the ledger witnesses file contents, not just filenames. `append` refuses to extend a chain that is already broken.

### What it detects, and what it cannot

`test_sk_ledger.py` runs seven tamper shapes. Five are caught by the chain alone:

| Attack | Detected as |
|---|---|
| Edit a recorded result in place | `content_tamper` |
| Delete an entry | `chain_break` + `sequence_break` |
| Splice in a forged entry | `chain_break` |
| Reorder entries | `chain_break` |
| Record an actor outside the bounded set | `unknown_actor` |

**Two are not**, and the test suite asserts that they are not:

- **Truncation.** Dropping the tail leaves a valid chain.
- **Full re-chaining.** Edit an entry, recompute every hash after it. The result is internally consistent and passes verification.

Both are caught only with `--expect-head`, an anchor held outside the file. In practice the anchor is git: `ledger/HEAD` is committed, and CI checks the chain against it plus verifies that the prior entries are a byte-exact prefix of what the previous commit contained.

`test_sk_ledger.py` deliberately asserts the blind spot rather than hiding it — `rechained_history_passes_without_anchor` fails loudly if that limitation ever changes, so the README cannot silently become wrong. A tamper-evidence claim that has never been shown its own blind spot is the same unearned confidence this toolchain exists to remove.

## sk-verify — the judge

`sk-lint` checks the KEY file. `sk-ledger` checks the witness. `sk-artifacts` checks one piece of evidence. None of them answers the question that matters: **did this run actually follow the contract?**

`sk_verify.py` reads a completed run directory and decides. It executes nothing. Any executor can produce a run directory — Codex, a shell script, a person working by hand — and be judged identically. Enforcement without controlling execution.

```bash
python3 sk_verify.py runs/good
python3 sk_verify.py runs/RUN06_gate_bypassed --json
```

A run directory is just `run.json` plus `artifacts/*.json`. Fifteen rules check it: the KEY hash the run was governed by, route eligibility, artifact validity, required-artifact presence, gate-evidence resolution, telemetry coverage, ledger witness, failure response, producer authorization, run-id coherence, and final-authority claims.

### RUN06 and RUN12 — the reason this file exists

`gate_bypass_attempt` and `test_bypass_attempt` are classified **critical** in the KEY and no gate detects them. That gap is not an oversight: a gate inside the system cannot observe an actor routing *around* the system.

They are detectable here and only here. A run whose route requires a gate, and whose record contains no decision for that gate, **is** the bypass. The absence is the evidence.

```
CRITICAL RUN06  [gate_bypass_attempt]
         route 'protocol_build_route' requires gate 'source_boundary_gate'
         and the run records no decision for it — the gate did not fire

CRITICAL RUN12  [test_bypass_attempt]
         validation skipped 2 layer(s) with no justification:
         ['gate_validation', 'ledger_validation']
```

The failure taxonomy now carries `detection_layer: verifier` on both, and SK021 accepts that declaration instead of demanding a gate that could never work. The open item from FINDINGS is closed — by naming the right layer, not by inventing a mapping.

`test_sk_verify.py` asserts these two fire at CRITICAL specifically and carry the right `failure_id`. A bypass rule that degrades to a warning is not a bypass rule.

### What the run corpus found

The first `sk-verify` run against my own *conforming* fixture failed with three errors, and they were contract defects rather than verifier bugs. The most substantive:

> `protocol_build_route` required a `validation_run` ledger entry, but selected no role that validates and required no validation evidence.

A build route that never validates is the test-bypass shape written directly into the contract. The structural linter could not see it — every reference resolved, every section was internally consistent. It took judging an actual run to expose it. That is the argument for having both layers, and the repair is recorded in `repair_pass.py` under finding RUN12.

One severity call was also wrong. RUN07 (a decision recorded for a gate the route does not require) started as a WARN. The fixture made the shape obvious: the natural abuse is manufacturing an attested pass for a gate the route deliberately withheld — `acceptance_lock_gate`, say. That is scope escalation, not extra diligence. Raised to ERROR, with the reasoning left in the source.

## Scope

`sk-lint` verifies the KEY file's internal structure. `sk-ledger` verifies the witness. Neither verifies a *run*. That is `sk-verify`, which needs JSON Schemas for all 17 artifacts first.

Neither can check whether a judgment was *correct* — only whether the structure carrying it is sound. Gate criteria are still prose (`artifact_requirement_gate` confirms artifacts "exist and are valid"; *valid* is undefined). Closing that is the artifact-schema work, and it is the next real lift.
