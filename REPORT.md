# Computed or Asserted

<!-- counts:begin -->
**23** lint rules · **19** run rules · **13** semantic rules · **138** tests passing
<!-- counts:end -->

### A verification toolchain for AI-assisted software builds

---

## The one-sentence version

> Existing attestation proves **who** performed a build step. When the "who" is a language model, that is no longer the useful question. This toolchain proves whether a step was **computed or asserted**, and rejects a build where something claiming to be computed wasn't.

Everything below is an expansion of that sentence.

---

## 1. The gap

Software supply-chain security has a mature answer to provenance. in-toto defines *layouts* — the expected steps, and which functionaries are authorized to perform each one. Functionaries sign *links* attesting to what they did. SLSA wraps this into build provenance. GitHub Actions generates it natively. The tooling is good and widely deployed.

It rests on an assumption that used to be safe: **a signature tells you something useful about how the conclusion was reached.**

When the functionary was a build server, that held. A build server running `gcc` has no other mode of operation. "The build server signed this" and "the compiler actually ran" were the same claim.

When the functionary is a coding agent, it stops holding. "Claude signed this step" is compatible with two very different histories:

- The agent ran `sha256sum` over the source tree and recorded the output.
- The agent wrote a plausible-looking hash comparison because that is what the step called for.

Both produce a signed link. Both pass in-toto verification. The signature is valid in both cases, because the signature was never the thing in question.

This gap is documented elsewhere. Research on nonrepudiable experimental results puts it precisely: in-toto and Sigstore bind a released artifact to a specified build process, but neither binds a *result* to the *computation that produced it*. Provenance vendors say the same from the other direction — software provenance tracks the build and delivery chain but does not solve source-level attribution for AI-written code.

The gap is not that AI agents are dishonest. It is that **fluency and correctness produce identical-looking records**, and existing attestation has no field that distinguishes them.

---

## 2. What this is

A verifier. Not a runtime, not an agent framework, not a wrapper that controls execution.

It reads a record of work that already happened and decides, by program, whether that work followed a declared contract. Nothing is executed. That is the design, and it is what lets it work across vendors nobody controls: Codex, Grok, Claude, a shell script, or a person working by hand can all produce a run record, and all are judged identically.

Four tools, four questions:

| Tool | Question | Rules |
|---|---|---|
| `sk-lint` | Is the contract itself coherent? | 23 |
| `sk-ledger` | Has the record been rewritten? | 7 tamper shapes |
| `sk-artifacts` | Is this piece of evidence valid? | JSON Schema + 13 semantic |
| `sk-verify` | Did this run follow the contract? | 17 |
| `sk-handoff` | Was this work done against the tree it was specified against? | tree pin |

116 tests. 23 lint fixtures, 24 invalid-artifact fixtures, 20 run fixtures — every rule bound to a constructed input that must trip it.

---

## 3. The core mechanism

### Gates declare their own strength

Every gate carries an `enforcement_class`:

- **`automatic`** — a program decides. No model, no attestation. Nine of thirteen.
- **`attested`** — an actor asserts, and signs. Genuinely weaker. Forensic, not preventive. Three.
- **`composite`** — passes only if its constituents passed. One.

Marking the attested three honestly is the difference between a governance manifest and a compliance-theater document. It also gives a roadmap: every gate promoted from `attested` to `automatic` is a real, pointable increase in strength.

### Every artifact declares how it was arrived at

```json
{
  "evidence_source": "program",
  "produced_by_program": {
    "name": "sk_emit.inventory",
    "sha256": "<hash of the emitter, computed at runtime>",
    "argv": ["inventory", "--source", "src/"]
  }
}
```

or

```json
{
  "evidence_source": "attestation"
}
```

An artifact without provenance is a file, not evidence. An artifact that does not say **how** it was arrived at is a claim wearing the costume of a measurement.

### The rules that make the declaration binding

**SEM11** — a gate declared `automatic` whose decision carries `evidence_source: attestation` is rejected. An automatic gate decided by assertion is not automatic.

**SEM02** — an `automatic` gate carrying an `attestation` block is rejected. Schema can require an attestation to be well-formed; only a semantic rule can require it be *absent* when a program was supposed to decide. Without this, any automatic gate can be quietly downgraded to somebody's say-so and the artifact still validates.

**RUN16** — and this is the one that matters most — the check is **transitive**:

```
ERROR RUN16 [gate_evaluation_violation]
      automatic gate 'source_boundary_gate' rests on
      'source_boundary_lock_artifact', which is 'attestation'
      rather than program-produced —
      the gate is only as automatic as its evidence
```

Downgrading a gate is obvious. Downgrading one artifact three levels beneath it is not, and that is where a real system erodes.

---

## 4. Detection by absence

`gate_bypass_attempt` and `test_bypass_attempt` are classified **critical** in the contract, and no gate detects them. That is not an oversight. **A gate inside the system cannot observe an actor routing around the system.**

They are detectable at the verifier and only there. A run whose route requires a gate, and whose record contains no decision for that gate, *is* the bypass. The absence is the evidence.

```
$ sk_verify.py runs/RUN06_gate_bypassed

CRITICAL RUN06  [gate_bypass_attempt]
         route 'protocol_build_route' requires gate 'source_boundary_gate'
         and the run records no decision for it — the gate did not fire
```

The failure taxonomy now carries `detection_layer: verifier` on both, and the linter accepts that declaration instead of demanding a gate that could never work.

The test suite asserts these fire at CRITICAL **specifically**, and carry the correct `failure_id`. A bypass rule that degrades to a warning is not a bypass rule.

---

## 5. The witness

The contract classified `ledger_tamper_attempt` as critical, declared the ledger append-only, and stated prior entries must not be rewritten. Nothing enforced any of it. A JSONL file on disk is exactly as append-only as whoever holds the file handle.

Each entry now carries the hash of the entry before it.

```
$ sk_ledger.py verify tampered.jsonl

BREAK  seq=15 line=16  content_tamper
       entry content does not match its hash
       (recomputed 2d58ddeec3b8…, stored 5f632689cef3…)

1 break(s) — the ledger has been rewritten
```

### The blind spot is a test, not a footnote

A hash chain catches anyone who does not re-chain. It does nothing against an attacker who edits an entry and recomputes every hash after it — that produces an internally valid chain.

Two tests assert this **negative** result:

- `truncation_invisible_to_chain_alone`
- `rechained_history_passes_without_anchor`

If either ever starts passing, the suite fails and forces the documentation to be corrected. Both attacks are caught only with `--expect-head`, an anchor held outside the file — in practice, git. CI verifies the chain against the committed anchor *and* checks that the ledger in this commit is a byte-exact prefix of the ledger in the previous one.

A tamper-evidence claim that has never been shown its own blind spot is the same unearned confidence this toolchain exists to remove.

---

## 6. Rules about rules

Two meta-constraints keep this from decaying into a checklist.

**Every rule must have a red fixture.** `test_sk_lint.py` asserts that every rule in the registry has at least one committed input constructed to trip it. Without that assertion, rules accumulate that have never been shown a violation — the exact failure this tool exists to prevent, reproduced one level up.

**Every fixture must fail for its own reason.** The suite asserts rejection *by the bound rule specifically*. A fixture that fails for an unrelated reason gives a green test and proves nothing, which is how a suite quietly stops testing what it claims to test.

**Coverage gaps are declared, not silent.** Two semantic rules and one run rule have no fixture, each with a stated reason in source. The test fails if an *undeclared* gap appears.

---

## 7. What it found

The toolchain was built against a real 1,302-line governance manifest. Every defect below was found by a program, on a document its author had reviewed repeatedly.

### First run, six errors

```
ERROR SK008  Orphan gate: doctrine_consistency_gate is defined
             but never invoked. It cannot fire.
```

`doctrine_term_corruption` was classified **critical**. A gate existed to catch it. No route, role, or task frame ever required that gate. The failure was defined, the detector was defined, and the wire between them was never run.

Five more were directional asymmetries. The routing graph declares selection in both directions — `route.selected_roles` and `role.selected_by_route_ids` — and they disagreed in five places. Read from the route, correct. Read from the role, different. A dispatcher reading routes and an auditor reading roles would give different accounts of the same run, and both would be internally consistent.

This defect class is effectively invisible to human review at 1,300 lines and free for a program.

### The one the structural linter could not see

The first run of `sk-verify` against a *conforming* fixture failed with three errors — contract defects, not verifier bugs. The substantive one:

> `protocol_build_route` required a `validation_run` ledger entry, but selected no role that validates and required no validation evidence.

**A build route that never validates is the test-bypass shape written directly into the contract.** Every reference resolved. Every section was internally consistent. The structural linter had nothing to complain about. It took judging an actual run to expose it.

That is the argument for having both layers.

### Two bugs the corpus caught in seconds

The artifact envelope closed `additionalProperties` without declaring `required_gate`, so all 17 valid examples failed on first validation. And the examples referenced a ledger hash that had never been appended, so the ledger-resolution rule fired across the board. Neither would have been visible by reading.

### A severity call, corrected by a fixture

RUN07 — a decision recorded for a gate the route does not require — started as a warning. The fixture made the shape obvious: the natural abuse is manufacturing an attested pass for a gate the route deliberately withheld. That is scope escalation, not extra diligence. Raised to error, with the reasoning left in source.

---

## 8. The failure the framework had no gate for

The most instructive finding came from handing a build task to a second AI actor.

The report came back with six findings. Two named defects in the linter: a rule ignoring `produced_by_role`, another ignoring `detection_layer`. Both were **false** — running those rules against the current tree returned zero findings.

A seventh observation explained it. Three files the task specification depended on were absent from the tree the builder received. The builder had a stale, partial snapshot and reported accurately about it.

**Nothing in the handoff pinned the tree.** The specification named files, described rules, and defined an acceptance test — and said nothing about *which version* of any of it. Both parties acted correctly and reached incompatible conclusions.

This is the framework's own failure mode, one level up. Every artifact in the system carries a SHA-256. The handoff that transmitted the task carried none.

`sk_handoff.py` closes it — a sorted manifest of every governed file plus a tree hash, verified before work starts and recorded in the report. Verified by reconstructing the exact drift:

```
MISSING  gen_artifact_examples.py
MISSING  key.yaml
MISSING  test_sk_artifacts.py
CHANGED  sk_lint.py
         pinned 1b346413b975…, actual ff065b0ea1a6…

3 missing, 1 changed, 0 added

Findings derived against this tree are about a different codebase.
Re-derive them; do not argue about them.
```

### And a finding worth more than the tool

The same builder reported that recording raw `argv` in provenance breaks determinism: two inventories of the same source, written to different output directories, are the same measurement but hash differently.

Handled correctly by judgment. Now handled by rule — SEM13 rejects an artifact whose recorded arguments include an output flag. **Output paths are not part of measurement identity.**

Correctness resting on an implementer noticing is precisely what this exists to eliminate.

---

## 9. What it does not do

Stated plainly, because a verification tool that oversells itself is self-refuting.

**It does not judge whether a judgment was correct.** Only whether the structure carrying it is sound and the required steps demonstrably happened. An `attested` gate is still somebody's word — recorded and locatable, not verified.

**It does not prevent anything.** It detects, after the fact. Prevention requires controlling execution, which is the thing it deliberately gives up in exchange for working across vendors.

**Its primitives are not novel.** Hash chains, JSON Schema, CI enforcement, append-only logs with external anchors — all off-the-shelf. The contribution is the composition plus one enforced distinction.

**It does not detect AI-generated code.** That is a separate research problem. This assumes you already know an agent was involved and asks a different question.

**Eight warnings remain open in the reference contract**, by decision rather than by patch — seven declared telemetry events no route requires, and one orphan artifact. Those need intent decisions, and guessing at intent is how the orphan gate got there.

---

## 10. Who this is for

**Regulated builds adopting AI coding.** Medical devices (IEC 62304), avionics (DO-178C), automotive (ISO 26262), anything under FDA or FAA review. These already require evidence that specific verification steps occurred. They are now receiving AI-generated code with no machine-checkable way to distinguish *the test suite ran* from *the assistant said the test suite ran*. The `automatic`/`attested` split maps directly onto what an auditor asks. **Strongest fit by a wide margin.**

**Platform teams deploying coding agents at scale.** Not because agents lie, but because at volume `merged` and `verified` drift apart and nobody notices which one they have.

**Supply-chain security tooling.** `evidence_source` is a natural in-toto predicate type. This is an integration, not a competitor.

**Not for solo developers on ordinary work.** The overhead only pays where the record must survive a question asked later. Governing routine work is cost without return.

---

## 11. Try to break it in sixty seconds

```bash
make all                              # 116 tests, from scratch
sk_verify.py runs/RUN06_gate_bypassed # gate bypass, CRITICAL
sk_verify.py runs/RUN16_*             # evidence downgrade, transitive
sk_lint.py key.yaml                   # 6 errors in the original contract
sk_ledger.py verify tampered.jsonl    # chain break at the tampered entry
```

Then delete a gate decision from `runs/good/artifacts/` and run `sk_verify.py runs/good`. It will name the gate that did not fire.

That reproduction — a program producing a failure on an input constructed to make it fail — is the entire claim. Everything else in this document is commentary on it.

---

## Appendix: the standard

> **A gate is real if and only if you have seen it fail on an input constructed to make it fail, and the failure was produced by a program rather than by a model's judgment.**

Every rule in this toolchain has a committed fixture satisfying that standard. The test suite fails if one does not.
