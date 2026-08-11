# PROTOCOL ITERATION LOG — Entries 001–013

Entries 001–004 opened this log. Source: emitter pass, PASS_19, actor Grok,
ledger head `bc04fb9f5326…`.

Every rule in the toolchain before that point was written by inference about
what a governed build would need. PASS_19 was the first pass where a real build
tested that inference. Two of those first four entries record inference that was
wrong.

---

## ENTRY 001 — Handoff without a tree pin produces findings about a codebase that does not exist

**Layer:** protocol / handoff
**Classification:** repeatable
**Severity:** high

### What happened

The emitter pass returned six findings. Two named defects in `sk_lint.py`:

> 3. SK018 still warns that several artifacts have "no producing role" even
>    though the KEY registry sets `produced_by_role`.
> 4. SK021 still warns on `gate_bypass_attempt` / `test_bypass_attempt` despite
>    `detection_layer: verifier` in the KEY.

Both are false against the current tree. `sk_lint.py --only SK018,SK021` returns
zero findings. SK018 was rewritten to read `produced_by_role` from the registry;
SK021 honors `detection_layer` and skips failures declaring `verifier`.

A seventh observation explains it: three files the task frame depended on
(`key.yaml`, `gen_artifact_examples.py`, `test_sk_artifacts.py`) were absent from
the tree the builder received. The builder had a partial, stale snapshot and
reported accurately about it.

### Why it was not caught

Nothing in the handoff pinned the tree. The task frame named files, described
rules, and specified an acceptance test — and said nothing about *which version*
of any of it. Both parties acted correctly and reached incompatible conclusions.

This is the framework's own failure mode, one level up. Every artifact in the
system carries a SHA-256. The handoff that transmits the task carried none.

### Rule derived

> A task frame is incomplete without the tree hash it was written against. The
> builder verifies before starting. The report carries the tree hash the work was
> done against. When the two differ, findings are re-derived, not argued.

### Enforcement

`sk_handoff.py`. `pin` writes a sorted manifest of every governed file with its
SHA-256 plus a tree hash; `check` reports missing, changed, and added files and
exits 1 on divergence. Generated corpora are excluded — they are reproducible
from their generators, and pinning them would make regeneration look like
tampering.

Verified by reconstructing the exact drift: removing the three files and
reverting the SK018 change produced, in one command, `3 missing, 1 changed`.

### Overcorrection guard

Do not pin generated output. Do not require a pin for exploratory work. The pin
is for handoffs that produce findings, because a finding is a claim about a
specific codebase.

---

## ENTRY 002 — Output paths are not part of measurement identity

**Layer:** artifact / provenance
**Classification:** repeatable
**Severity:** moderate

### What happened

Reported by the builder:

> Two inventories with different `--out` would diverge if raw `sys.argv` were
> recorded. The emitter drops `--out` from recorded argv so identical
> measurement inputs stay byte-identical.

The envelope invites `produced_by_program.argv` to record how a measurement was
produced. Recording it naively breaks the determinism check that the same
envelope exists to support: two inventories of the same source, filed to
different directories, are the same measurement and must hash identically.

### Why it was not caught

`produced_by_program` was designed to answer *which program produced this*.
Nobody asked what belongs in `argv` and what does not. The distinction between
the inputs to a measurement and the destination of its result did not exist in
the schema, so the correct behavior depended on the implementer noticing.

The builder noticed and handled it. That is the problem: correctness rested on
judgment where a rule was available.

### Rule derived

> Recorded provenance describes what was measured, not where the result was
> filed. Output paths are excluded from measurement identity.

### Enforcement

`SEM13` in `sk_artifacts.py`: an artifact whose recorded `argv` contains an
output flag (`--out`, `-o`, `--output`, `--outdir`, `--out-dir`, including
`=`-joined forms) is rejected. Red fixture
`SEM13_argv_records_output_path.json`.

### Overcorrection guard

Do not strip argv wholesale — the input arguments are the provenance and are
worth keeping. Only destination flags are excluded.

---

## ENTRY 003 — A fixture that can pass for a record is worse than a fixture that fails

**Layer:** verifier / evidence
**Classification:** repeatable
**Severity:** high

### What happened

Reported by the builder:

> `runs/good` remains a shape fixture (`gen_runs` with placeholder program
> hashes). It still conforms, but it is not a record of a real emit.

Correct, and it was already known — the README says so. But *knowing* it was
carried in prose while `sk_verify runs/good` printed `run conforms to the KEY
contract` with zero findings. The tool said the run was good. The prose said the
run was not real. Anyone reading the tool output would have been misled, and the
tool output is what gets pasted into reports.

### Why it was not caught

The verifier was designed to answer *did this run follow the contract*. A
fixture built to exercise the rules follows the contract by construction. There
was no way for the record to declare its own status, so a demonstration and a
record were indistinguishable at the point where it mattered.

### Rule derived

> A generated fixture must declare itself as one in its own record, and the
> verifier must surface that declaration. Conformance and authenticity are
> separate questions and must not share an answer.

### Enforcement

`gen_runs.py` writes `"fixture": true` into every generated `run.json`.
`RUN00` in `sk_verify.py` emits a WARN naming it as a shape fixture that must
not be cited as evidence of a governed run.

### Overcorrection guard

Fixtures still conform, and should. The warning does not make them fail — it
prevents them from being *quoted* as proof. Downgrading fixtures to failures
would destroy their value as rule exercises.

---

## ENTRY 004 — A handoff protocol that specifies deliverables but not evidence gets a summary

**Layer:** protocol / handoff
**Classification:** repeatable
**Severity:** moderate

### What happened

The task frame requested five things back: the SHA-256 of `sk_emit.py`, the
`test_sk_emit.py` output verbatim, the new ledger head, unimplemented emitters
with reasons, and any rule that blocked correct behavior.

Three arrived. The ledger head, the unimplemented list with sound reasoning
(attested gates refuse with exit 2 rather than fabricating a program decision),
and the findings. Two did not: no file hash, and a status table reading `ready`
across eight emitters in place of the test output.

The status table may be entirely accurate. It is not evidence. Under this
framework's own standard — *a gate is real if and only if you have seen it fail
on an input constructed to make it fail, and the failure was produced by a
program rather than by a model's judgment* — a summary asserting that a test
passed is a model's judgment about a program's output, not the program's output.

### Why it was not caught

The task frame listed what to report and did not say what form the report must
take, so the most natural form won: prose. This is the same shape as the twelve
protocol documents themselves. Asking for evidence in a sentence produces a
sentence about evidence.

### Rule derived

> A handoff requesting evidence must specify the artifact, not the fact. Request
> the test output stream and the file hash as attachments, not as claims in a
> summary. A report is accepted when its claims are re-derivable from what it
> encloses.

### Enforcement

Partial. `sk_handoff.py check` verifies the tree; nothing yet verifies that a
report encloses what it claims. The correct closure is a report schema — an
attested `role_output_artifact` with the test stream as a program-produced
`validation_report_artifact` attached — which makes SEM11 do the work: the
report is an attestation, the test result inside it is a measurement, and the
envelope forces them to be labeled differently.

Open. Next pass.

---

## Standing observations

**Not every finding is a defect.** Entry 001's findings 3 and 4 were correct
reports about a stale tree. The defect was in the handoff, not in either party's
work. A protocol that treats every disagreement as someone's error will find the
wrong bug.

**The builder refused correctly.** `gate` exits 2 on attested and composite
gates rather than emitting a program decision for something no program decided,
and `ledger_entry_artifact` is produced by reading the real chain rather than
asserting `chain_verified: true`. Both are the no-fake-completion rule applied
without being asked. Worth recording because it is the behavior the whole stack
is built to produce.

**Unverified as of this entry.** `sk_emit.py` has not been run in this
environment. `test_sk_emit.py` remains red here. Everything above about the
emitters' behavior is the builder's report, and entries 002 and 003 are
actionable regardless of whether that report holds. Entry 001 is confirmed
independently — the false findings were verified false against the current tree.

---

## ENTRY 005 — The same handoff defect recurred, which makes it a packaging problem

**Layer:** protocol / distribution
**Classification:** repeatable
**Severity:** high — supersedes ENTRY 001's severity assessment

### What happened

A second report came back listing five items. Two of them:

> Full Makefile "everything" still assumes a few pieces (e.g. examples
> generator, `test_sk_artifacts`) that weren't in the drop.
>
> SK018 vs registry `produced_by_role` — inference friction.

Both are false against the current tree, for the second consecutive pass.
`gen_artifact_examples.py`, `test_sk_artifacts.py`, and `key.yaml` are all
present. `sk_lint.py --only SK018` returns zero findings; SK018 was rewritten
to read `produced_by_role` from the registry.

ENTRY 001 diagnosed this as a missing tree pin and built `sk_handoff.py pin` /
`check`. That was correct and insufficient. A pin only helps if someone runs
it, and nothing in the delivery mechanism required it — because there was no
delivery mechanism. The tree was transmitted as an assemble-yourself set of
files, so completeness was a matter of care rather than a checkable property.

### Why the first fix did not hold

ENTRY 001 treated this as a *handoff protocol* defect. It is a *packaging*
defect that manifests at handoff. The distinction matters: a protocol
requirement can be forgotten, and this one was, immediately. A package that
refuses to be worked on when incomplete cannot be.

Packaging was on the task list as polish, described as "still a multi-drop
assemble-yourself tree." It is not polish. It is the direct cause of two rounds
of findings derived against a codebase that did not exist.

### Rule derived

> Completeness must be checkable at receipt, before work begins, by the
> recipient, in one command — and the check must fail closed. A drop that
> cannot be verified complete is not a drop.

### Enforcement

`sk_handoff.py verify-drop` declares the 21 files a workable tree requires,
grouped by role, and exits 1 when any is absent with the instruction not to
work around the gap. `make dist` produces a single versioned tarball with the
tree pin inside it.

Verified by reconstructing the exact drop that caused this entry: removing the
three files produces `18 of 21 required files present`, names all three by
group, and exits 1.

### Overcorrection guard

`verify-drop` checks presence and pin match, not content correctness — that is
what the test suites are for. Do not grow the required-files list to cover
generated output; generated corpora are reproducible and pinning them makes
regeneration look like tampering.

### Standing note

Two consecutive passes produced findings that were artifacts of stale trees
rather than defects in the work. In both cases the builder reported accurately
about what it had. The lesson is not about builder reliability; it is that a
distribution mechanism which permits partial delivery will produce partial
delivery, and the resulting reports will be internally consistent and wrong.

---

## ENTRY 006 — Two ledgers, both verifying, disagreeing about history

**Layer:** witness / distribution
**Classification:** repeatable
**Severity:** critical

### What happened

An independent inventory of five accumulated delivery drops found two ledgers:
a 21-entry chain in the assembled working tree with head `bc04fb9f5326…`, and
a 20-entry chain in the packaged v0.6.0 release with a different head. Both
were reported as verifying cleanly. Both did.

They share history through seq 18 (`PASS_17`, `be9edee6545b…`) and diverge at
seq 19. One lineage records the demo build run; the other records the emitter
pass. Neither is wrong about its own contents. They disagree about what
happened.

### Why it was not caught

The hash chain proves nothing was rewritten **within** a lineage. It says
nothing about **which lineage is the record.** Two actors appending to copies
of the same ledger produce two chains that each verify perfectly.

This is the re-chaining blind spot arriving from a different direction. That
one was documented and tested: an attacker who recomputes every hash produces
an internally consistent chain, and only an external anchor catches it. A fork
is the same failure without an attacker — internal consistency is not
authority. `ledger/HEAD` was treated as *the* anchor, but each copy carried its
own `HEAD`, so each fork anchored itself and agreed with itself.

An append-only log with multiple writers and no single anchor of record is not
append-only. It is two logs.

### Rule derived

> A lineage is authoritative only relative to an anchor held outside every copy
> of it. One anchor, one record. Divergence must be detectable by comparing two
> chains, and resolvable without discarding either one's contents.

### Enforcement

`sk_ledger.py reconcile <canonical> <other>` computes the common prefix, names
the fork point, and lists the divergent entries on each side. `sk_ledger.py
graft` replays the divergent tail onto the canonical head, re-chaining the
moved entries and recording `grafted_from` — each entry's hash in its original
lineage — so position changes are on the record and content is preserved.

Verified by reconstructing the fork: `FORK at seq 19`, one entry named on each
side, shared history through `be9edee6545b…`. Grafting produced a 21-entry
chain, head `2d440014156f…`, with the moved entry carrying both its new hash
and `grafted_from: b6d046ec866a…`.

### Overcorrection guard

Do not automate grafting into the build. Which lineage is canonical is a
judgment about what actually happened, and a tool that picks one silently has
made that judgment on the operator's behalf. `reconcile` reports and exits 1;
a human chooses; `graft` then executes the choice reproducibly.

### Standing note

The correct operating discipline is one writer per lineage. Where that is not
possible, `reconcile` must run before any append — and it should be the first
command run against any recovered or received ledger, not the last.

---

## ENTRY 007 — A documentation check that could not fail

**Layer:** build / self-verification
**Classification:** one-time (but the class is repeatable)
**Severity:** moderate

### What happened

The inventory noted the release README claimed 22 lint and 15 run rules while
the code had 23 and 17. Nobody misstated anything; the counts were transcribed
once and the rules kept growing.

The first fix scanned the documents for phrases like `"N lint rules"` and
compared. It reported "documented counts match the code" — on documents that
never used that phrasing. Deliberately corrupting a count did not make it fail.

**A rule that cannot fail, committed in the tool whose founding standard is
that every rule must be shown failing on a constructed input.** Written by the
same author, in the same session, immediately after articulating the standard.

### Rule derived

> A check over prose must compare a delimited, regenerated region — never a
> phrasing. If the check cannot be made to fail by corrupting the thing it
> checks, it is not a check.

### Enforcement

`build.py docs` regenerates a delimited `<!-- counts:begin -->` block from the
live rule registries and test output, and compares byte-for-byte.
`docs-write` rewrites it. Verified by corrupting a count: `docs: DRIFT in
README.md`, exit 1.

### Standing note

The lapse is worth more than the fix. Every rule in this toolchain was written
by someone who believed at the time that it worked. The red-corpus meta-rule
exists because that belief is unreliable — and it was unreliable here, in the
hands of the person who wrote the meta-rule, thirty minutes after writing it.

---

## ENTRY 008 — Closing ENTRY 004: the emitter verified, not reported

**Layer:** protocol / evidence
**Classification:** one-time closure
**Severity:** n/a — resolution entry

### What was open

ENTRY 004 recorded that a handoff requesting evidence had returned a summary.
The builder's report stated eight emitters `ready` and an acceptance test
passing; neither `sk_emit.py` nor the test output stream was enclosed. Under
this project's own standard, a summary asserting a test passed is a model's
judgment about a program's output, not the program's output. The claim was left
open, explicitly, rather than accepted.

### What closed it

`sk_emit.py` (1,376 lines, SHA-256 `f868a1a8d99f501c…`) was delivered and run
against the acceptance gate that was written *before* the spec:

```
19 passed, 0 failed
```

The builder's report was accurate. It was also, correctly, not sufficient —
and the distinction cost nothing to maintain, because the gate already existed.

### What the gate actually established

Every claim below was produced by running a program in the verifying
environment, not by reading the report.

- **Determinism holds across output directories.** Two inventories of identical
  source, written to different `--out` paths, are byte-identical modulo
  `timestamp` and `run_id`. Recorded argv is `['inventory', '--source',
  '/tmp/src']` — the output flag is excluded, exactly as ENTRY 002 required and
  SEM13 now enforces.
- **The program hash is computed, not transcribed.** All 12 program-produced
  artifacts in a real run carry `f868a1a8d99f501c…`, matching an independent
  hash of `sk_emit.py`. A hardcoded constant would have failed.
- **The emitters enforce, not merely report.** `boundary` on a tampered source
  returns `unmodified: False`, `modified_paths: ['f1.txt']`, and **exit 1**. A
  detector that exits 0 has reported a fact and enforced nothing.
- **The builder refused where refusal was correct.** `gate` exits 2 on attested
  and composite gates rather than fabricating a program decision, and
  `ledger_entry_artifact` is produced by reading the real chain rather than
  asserting `chain_verified: true`. No-fake-completion, applied unprompted.

### The first real record

`sk_emit run` produced `runs/real` — `RUN_emit_bb81a907e339`, 13 artifacts,
12 `program` and 1 `attestation` (the role output, correctly labeled a claim).
It verifies at **0 critical, 0 error, 0 warn**, and `fixture` is absent, so
RUN00 does not fire.

This is the first artifact in the project's history that is a record of a
governed build rather than a demonstration of the shape of one. ENTRY 003's
distinction between conformance and authenticity now has both sides populated:
`runs/good` conforms and announces itself a fixture; `runs/real` conforms and
does not.

### Adversarial confirmation

The real…3637 tokens truncated…f24ba0935…` | Rebrand build — CI triggers on `main` only |
| `1223b31057c9…` | CI-fix build — CI triggers on `main` and `master` |

`archives/solomons-key-v0.9.3.tar.gz` holds one of them, and **which one is not
determinable from the name.** The two differ in whether continuous integration
runs at all, which is not a cosmetic difference: one of them silently never
executes the ledger append-only check while appearing to have CI configured.

### Why this matters more here than elsewhere

Moving a tag is ordinary practice in many projects and mostly harmless. It is
not harmless in this one.

The entire claim of this toolchain is that records do not move — that a
`ledger_ref` resolves, that a `produced_by_program` hash pins a specific binary,
that an anchor makes rewriting detectable. A release name that resolves to two
different artifacts is the same defect the tools exist to catch, at the
distribution layer, where no tool is watching.

It also reproduces ENTRY 011 in miniature. There, the build silently replaced
the witness. Here, a retag silently replaced the artifact. Both are "the name
stayed the same, the thing behind it changed."

### Rule derived

> A version name resolves to exactly one artifact, permanently. Any change that
> alters what ships gets a new version, however small. Tags are never moved.

### Enforcement

None mechanical yet, and that is the honest state. `build.py dist` prints the
tarball's SHA-256 on every build, so the collision is *visible* to anyone who
records it — but nothing refuses to overwrite an existing version, and nothing
compares a rebuild against a previously published hash.

A `RELEASES.md` recording version → SHA-256 → commit, checked by `dist` before
writing, would close it. Left open deliberately rather than half-built.

### Resolution

`v0.9.4` cut clean: `0a4d67285abe…`. Both prior `v0.9.3` artifacts should be
treated as ambiguous and superseded rather than reconciled — the names cannot
be repaired retroactively, only abandoned.

### Standing note

This is the third time a defect has appeared at a layer above the one the tools
inspect: handoffs (ENTRY 001), packaging completeness (ENTRY 010), and now
release identity. The tools verify runs; nothing verifies the process that
distributes the tools. Each time, the failure has been the same shape — a name
or a container that was assumed stable and was not.

---

## ENTRY 013 — Six findings, three ways of catching them

**Layer:** protocol / verification semantics
**Classification:** repeatable
**Pass:** release identity (v0.9.6), builder: Claude Code

**Severity is per-finding.** A single header cannot carry a set running from
design traps resolved inside the pass to a pin that is blind to the file whose
modification defined ENTRY 012.

| Finding | Severity | Status |
|---|---|---|
| 1 — `RELEASES.md` has no fixed point | moderate | resolved in pass |
| 2 — second fixed point, gate trap | moderate | resolved in pass |
| 3 — "unrecoverable" asserted | high | resolved in pass |
| 4 — tree hash omits shipped files | high | mitigated, root cause open |
| 5 — a check that was true and meant nothing | high | open |
| 6 — CI workflow invisible to the pin | **critical** | mitigated only |

Finding 6 is classified critical on the ENTRY 011 precedent, which used that
severity for a structurally identical defect: a guard rendered decorative. The
pin cannot see the change that created the failure this pass was convened to
fix. The artifact-hash comparison is the only thing standing in its place.

> **Provenance of the measurements below.** They were produced once, in the
> builder's environment. A later review pass by the same agent re-derived a
> narrow subset — the absence of `.github` entries in `TREE.sha256`, the
> workflow file's presence in the shipped tarball, the 32-file pin count, and the
> ENTRY 006/009/011 headers. (`EXCLUDE_DIRS` itself was read during the build
> pass, not the review pass; the review re-derived the observable consequence by
> a different route.) **That is not independent verification** — the reviewer
> carries every assumption the build pass carried.
>
> The load-bearing measurements were **not** re-checked: `7f2723d5…`, the
> `3edaf3aa…` → `551ab001…` transition, `af67b105…` → `53f02dff…`, the `v0.9.3`
> and `v0.9.4` artifact hashes, and the test counts stand exactly where the
> build pass left them. The authoring environment is behind the product tree and
> verified none of it.
>
> **This entry reached its fourth draft.** Draft 1 claimed the protocol "caught
> all five before a line of code was written," which its own narrative disproved
> two paragraphs earlier; it merged two taxonomies and omitted Finding 6.
> Draft 2 fixed those and introduced a new overclaim — it described the reviewer
> as "a second reviewer" performing "independent" re-derivation across the whole
> set, when it was the same agent checking a fraction of it. **The paragraph
> whose only job is to bound what has been verified overclaimed verification**,
> which is Finding 5's own rule applied to Finding 5's own entry.
> Draft 3 corrected that provenance paragraph and reversed back an inverted
> ENTRY 011 precedent, which had cited as obedience the entry whose point is that
> the guard never fired.
> Draft 4 removed the separate-party framing where it survived in Finding 6 and
> the closing, corrected an unmeasured claim inherited from ENTRY 012, and
> replaced the flat severity header with the table above — then left two
> sentences behind that still depended on the flat ranking, and narrated itself
> as Draft 3, skipping the round that had made the most substantive correction.
>
> That last slip is the one worth keeping. A silently replaced draft was sitting
> inside the paragraph whose stated rationale is that drafts are not silently
> replaced.
>
> The drafts are recorded rather than silently replaced, because an entry that
> needed correcting three times for overclaiming is the kind of data this log
> exists to hold.

### What the pass was for

ENTRY 012 recorded a release name resolving to two artifacts. The fix was meant
to be bookkeeping: make `dist` deterministic, record releases, refuse to re-cut
a changed version.

Six defects surfaced. They were caught three different ways, and the difference
between those ways is the most useful thing here.

---

## Detection mode 1 — static review, before any code

Four findings were legible on the page and were found by verifying the task
frame against the tree before starting work.

### Finding 1 — `RELEASES.md` has no fixed point under its own governance

`sk_handoff.py` governs every `.md` file and excludes only `TREE.sha256` by
name. `RELEASES.md` must record the tree hash, so it sits inside the tree it
hashes. SHA-256 has no reachable fixed point; the acceptance test's
`recorded_tree_hash_matches` was unsatisfiable.

Not a wrong instruction — a **collision between the tooling's governance rule
and the frame's mutation scope**. The only working fix required editing
`sk_handoff.py`, which the frame forbade.

The frame's preamble argues that an identity recorded inside the thing it
identifies is not an identity. Its Deliverable 2, in a later section, specified
exactly that.

**Resolved:** `RELEASES.md` added to `EXCLUDE_NAMES` beside `TREE.sha256`, for
the identical reason. The builder reported the blocker and stopped rather than
routing around it, which is what the frame's own rule requires.

*(Draft 2 added "the first time that rule has been exercised in-band rather than
as a post-mortem." That is an assertion about the project's history, not a
measurement, and it was not checked against the log. Removed — it is the exact
category this entry warns about.)*

**Standing rule:** any new file recording a tree hash goes in `EXCLUDE_NAMES` by
default, not by discovery.

### Finding 2 — a second fixed point, and a trap in the acceptance gate

A `RELEASES.md` recording the artifact hash would be packed *into* the tarball it
describes. Worse: if `dist` writes `RELEASES.md` between the two packs the test
performs, the second pack sees a changed tree and the hashes differ, so
`dist_is_reproducible` fails regardless of how well tar metadata is pinned.

**A defect in the acceptance gate's design** — not in the frame, not in the
packer. Writing the gate before the spec is correct practice and did not make
the gate correct.

**Resolved:** `RELEASES.md` excluded from the tarball alongside `*.tar.gz`. A
record about a release does not ship inside it.

### Finding 3 — "unrecoverable" asserted where a hash would have settled it

The frame instructed that the prior `v0.9.3` artifacts be recorded as *identity
unrecoverable*. `archives/solomons-key-v0.9.3.tar.gz` hashes to
`1223b31057c9ab1b…` — an exact match for ENTRY 012's second row. Its identity is
recoverable in one command.

**A genuine instruction error.** Following it verbatim would have written a
fabricated resolution into the document whose purpose is to refuse fabricated
resolutions.

What *is* unrecoverable is narrower: the tag→artifact history, and the
whereabouts of `855f24ba0935…`, which is not in `archives/`. A third file,
`solomons-forge-v0.9.3.tar.gz` (`0d0188ad01f7…`), exists under the former
product name.

**Rule:** unrecoverable is a measurement, not an assumption. Hash the file before
declaring its identity lost.

### Finding 4 — the tree hash does not cover what ships

`GOVERNED_SUFFIXES` covers `.py .yaml .yml .md .json .jsonl` plus `Makefile`. It
omits `VERSION`, `LICENSE`, `pyproject.toml`, `requirements.txt`, `.gitignore`,
`ledger/HEAD`, and `ledger/AUDIT_HEAD` — all of which `dist` packs.

**A gap in the codebase**, not an instruction error. A guard keyed only on the
tree hash would let someone edit `pyproject.toml`, re-cut the same version, and
ship different bytes under a recorded name.

**Mitigated** by comparing the artifact hash as well as the tree hash. See
Finding 6 for why the underlying gap is more urgent than it first appeared.

---

## Detection mode 2 — a guard firing at runtime, on its author

### Finding 5 — a check that was true and meant nothing

**`verify-drop` reported "safe to start work" on a tree whose own build
immediately rewrote governed, pinned files.**

Running `build.py` regenerated `key.repaired.yaml` (`3edaf3aa…` → `551ab001…`),
the ledger, and the entire generated corpus, moving the tree from `af67b105…` to
`53f02dff…`.

The cause was in the authoring environment: `sk_handoff.py pin` was run directly
after edits, repeatedly, without an intervening full build. The pin therefore
recorded a tree that was not a fixpoint of its own build.

`verify-drop`'s factual statements were accurate — the required files were
present and their hashes matched. The false part was the literal string it
printed: **`safe to start work`**. That sentence claims stability, which the
check never examined.

Two different sets are also involved, and conflating them obscures which check
said what: the completeness check covers **34 required files**; the pin covers
**32 governed files**.

**This is a different failure from Findings 1–4.** Those are *a name does not pin
what you think*. This is **a check that is internally valid and semantically
empty** — the same confusion as ENTRY 006's forked ledgers, ENTRY 009's circular
trust root, and ENTRY 011's self-reseeding witness, arriving this time at the
verification layer, in the tool built to prevent it.

Four instances now of: *a record that agrees with itself is not thereby true.*

**How it was caught matters as much as what it was.** Static review did not find
it. It surfaced only after `build.py`, `RELEASES.md`, and the gate were written,
the gate had gone green, and `v0.9.5` had been cut — at which point `dist`
refused to re-cut it and the moved tree became visible.

**That is an argument for enforcement over review.** The only finding at the
verification layer, at the highest severity reached during the pass itself, was
invisible to careful reading and was produced by a guard firing during ordinary
work.

**Rule derived:**

> A tree pin is meaningful only if the tree is a fixpoint of its own build. A
> check whose plain-language reading exceeds what it verifies is worse than no
> check, because it is believed.

**Enforcement — revised design.** The first proposal was `verify-drop --strict`,
running `build.py` and re-pinning. That is wrong: the checker would mutate the
tree it is checking, and a recipient running it on a received drop would alter
that drop before starting work — an unacceptable property for a receipt-time
gate.

The correct shape puts the invariant in `pin`, on the authoring side, where
Finding 5 says the cause actually was: **`pin` refuses to write a pin for a tree
that is not a fixpoint of its own build**, verified against a temporary copy so
nothing in place is disturbed. A drop then cannot carry an unstable pin, and the
recipient's check needs no change.

Open. Belongs in its own pass.

### The guard was obeyed rather than weakened

`v0.9.5` was cut, and `dist` then refused to re-cut it, for the reason above.

The refusal message reads: *"Do not delete the RELEASES.md entry to get past
this."* Deleting it was precisely the available shortcut. It was not taken.
`v0.9.5` is recorded as **withdrawn — never distributed**, with the reason
written into `RELEASES.md`, and the version bumped to `v0.9.6`.

**On the log's evidence this is the first time a guard has fired unbidden on its
author and been obeyed.**

ENTRY 011 is the inverse case, and citing it as precedent would reverse it. Its
central point is that `sk_ledger seed` already refused to seed over a non-empty
ledger, and *the build deleted the file first, so the guard never fired* — for
nine entries' worth of history, every one of them written about record integrity
against a ledger the next build would destroy. The only firing in that entry is
the author deliberately testing a guard they had just written.

The real parallel is different and worth keeping: both are defects the author
committed in the build script of a toolchain built to detect that exact class.
ENTRY 011 is what the bypass looks like. The `v0.9.5` refusal is what it looks
like when the bypass is available and not taken.

---

## Detection mode 3 — review of the record itself

### Finding 6 — the CI workflow is invisible to the tree hash

`sk_handoff.py`'s `EXCLUDE_DIRS` drops `.github` wholesale. So
`.github/workflows/sk-lint.yml` is **not in the pin** — 32 pinned files, zero
`.github` entries — while `dist` **does** pack it, confirmed in the shipped
v0.9.6 tarball.

That is the CI workflow file. ENTRY 012's two `v0.9.3` artifacts differed **at
least** in whether it triggered CI on `main` or on `main` and `master`.

The stronger word — *only* — cannot be checked. `855f24ba0935…` is not in
`archives/`; hashing every file there produces no match, so there is no second
artifact to diff against `1223b31057c9…`. The claim rests on ENTRY 012's own
table, whose labels ("Rebrand build", "CI-fix build") describe intent rather than
a measured delta, and "rebrand" hints the difference may not have been confined
to one file. Finding 6's argument needs only that the CI file was **among** the
differences.

Inheriting an unmeasured claim from an earlier entry is precisely what Finding 3
is about, arriving here by a different door.

**The change ENTRY 012 identifies as creating the defect this entire pass exists
to fix is invisible to the tree hash.** A tree-hash-only guard would have
permitted ENTRY 012 to recur verbatim — which holds as long as the CI file was
among the differences, and that much is measured.

This was found neither by static review nor by a guard. It was found by **the
same agent re-reading its own first draft of this entry against the tree** — a
third detection mode, and the only one that caught it.

The honest version is the more useful one. This was not a fresh pair of eyes; it
was re-reading a record against the source of truth it describes, which is a
mode you can **schedule** rather than one that requires staffing.

Two consequences:

- The artifact-hash comparison in Finding 4's mitigation is **considerably
  better motivated** than first understood. It is not belt-and-braces; it is the
  only thing standing between the new guard and a verbatim repeat.
- The deferred `GOVERNED_SUFFIXES` / `EXCLUDE_DIRS` work is **more urgent than
  "needs its own pass" implied.** The pin currently omits the file whose
  modification defined the failure it exists to prevent.

---

## Instruction errors — a separate list

The first draft conflated "the findings" with "errors in the task frame." They
are different sets. Findings 2, 4, and 6 are not instruction errors at all;
Finding 1 is a scope collision; Finding 5 is a process error plus a tool gap.

The frame's actual instruction errors, four of them:

1. **Seed `RELEASES.md` with `v0.9.4`.** `VERSION` on disk was `0.9.3`; the
   `0.9.4` bump lived only in the authoring environment and was never persisted.
2. **Record `v0.9.3` as unrecoverable.** False, as Finding 3 shows.
3. **A mutation scope forbidding the only working fix.** Finding 1's blocker.
4. **"The `v0.9.4` tarballs were built from the current tree."** They were not —
   the archived one lacks `TASK_FRAME_release_identity.md`. `v0.9.4` names four
   distinct hashes. The reasoning was false and the conclusion (bump past it) was
   correct.

Reaching a right answer from a wrong premise is indistinguishable from judgment
until it isn't.

**Rule:** an instruction from the author is an assertion, not a measurement.
Every one of these was checkable in a single command, and none was checked before
being issued.

---

## Reproducibility: demonstrated, not asserted

The prediction going in was that `dist` twice would pass while the real build
stayed irreproducible, because `runs/real` carries a fresh `run_id` and
wall-clock timestamps on every emit. Confirmed, then closed by excluding
`runs/real/` from the tarball — already excluded from git for the same reason: a
real emitted run is the record of one execution, not part of a release.

Full build, twice, across a five-second gap:

```
7f2723d5ec640f5df4bf3adac7080e9e154c3f5fcaba89fb7f73dca569d97e71  solomons-key-v0.9.6.tar.gz
7f2723d5ec640f5df4bf3adac7080e9e154c3f5fcaba89fb7f73dca569d97e71  solomons-key-v0.9.6.tar.gz
```

Three consecutive full builds hold `key.repaired.yaml`, the ledger, the corpora,
and the tree hash constant.

## One exclusion the plan missed

`os.walk` would have swept `.git` into the tarball. The previous `os.listdir`
packer never reached it, so the exclusion was implicit and had to be made
explicit. `.git/index` changes on every git operation and would have defeated
reproducibility by itself. `.github` still ships — see Finding 6.

**Class worth remembering:** replacing a traversal changes what is in scope, and
an exclusion that was previously implicit becomes a silent inclusion.

---

## What this pass actually established

Not that careful review suffices. Review found four findings, all legible on the
page. It missed a `high` one at the verification layer, which a guard caught at
runtime during ordinary work. And it missed the `critical` one — the pin blind to
the file whose modification defined ENTRY 012 — which no guard caught either, and
which surfaced only when the record was re-read against the tree it describes.

The severity ordering runs the same direction as the detection modes. The most
serious defect was the one furthest from static review.

Three detection modes, three disjoint sets of findings, and the two most
important defects were invisible to the first. None of the three required a
second party — the third mode is the same agent checking its own output against
the source of truth, which is schedulable.

---

## ENTRY 014 — Universalization reached a repository it had not seen

### What happened

An external programmer tried the product cold and never reached the product's
central demonstration. The tree communicated a KEY-file linter whose only
visible contact with source code was an inventory hash. Nothing accepted a
repository and derived the computed-versus-asserted contract.

The pass was deliberately reordered to **diagnostics → pruning → initialization
→ adapters → operating model**. Pruning came before initialization because a
generator built against sixteen required root sections would have embedded
thirteen ceremonial stubs into every contract it produced.

Only `lot`, `gates`, and `artifacts` remain required. RUN09 is conditional on
route-declared telemetry. RUN12 remains CRITICAL when validation layers are
declared and their report is absent or skipped without justification; a
contract declaring no validation layers does not manufacture that requirement.
RUN17 was not relaxed.

`sk_init.py` derives a three-section contract and a first demonstrator run.
`sk_adapt.py` converts JUnit, SARIF, and exit codes into evidence and gate
decisions. The minimal contract is 21 non-comment lines and lints with zero
findings.

### Evidence

The acceptance gate passed end to end, then the same path was run against a
fresh clone of PyPA `sampleproject`: a derived contract linted with zero
findings and its run verified with zero critical or error findings.

### Rule derived

> Remove ceremony before generating contracts. Otherwise the generator turns
> yesterday's accidental shape into tomorrow's required interface.

---

## ENTRY 015 — The trust-boundary gate asserted the wrong identity

Two findings, both inside the code written to enforce provenance.

### Finding 1 — the acceptance gate contradicted the architecture

The prewritten universalization gate asserted that
`produced_by_program.sha256` equaled the hash of the JUnit results file. The
architecture and `TRUST_BOUNDARY.md` say that field identifies the producing
binary. The gate therefore enforced the opposite of the trust model and went
green.

The same gate's `adapter_run_verifies` check built its run with `sk_init` demo
artifacts, not `sk_adapt` output. Its name claimed an end-to-end path that it
never executed.

**Enforcement:** the gate now uses real resolvable program files, requires
`produced_by_program.sha256` to match the named allowlist entry, requires
`input_sha256` to match the JUnit/SARIF input, assembles automatic gate decisions
from `sk_adapt`, and invokes `sk_verify --trusted` on that run. It also changes
the measured input after emission and requires SEM06 to reject the stale hash.

### Finding 2 — a field's meaning depended on its author

Native `sk_emit` artifacts used `produced_by_program.sha256` for the executable.
Adapter artifacts used the same field for the measured input. RUN17 then
preferred a new `executable_sha256` field but fell back to `sha256`, so the
meaning changed according to which tool wrote the artifact. The trust boundary
contained the exact ambiguity the product exists to reject.

**Enforcement:** `produced_by_program.sha256` now means executable hash without
exception. `input_sha256` means the measured input; `input_path` lets SEM06
recompute file-backed measurements. RUN17 has no identity fallback. Missing
`sha256` produces a distinct fail-closed diagnostic that says the binary is
unidentified and explicitly warns not to copy the input hash into the
allowlist.

### The least-defended record received the strongest overwrite guard

The same review found that `sk_init` would overwrite an existing
`TRUSTED_PROGRAMS.sha256`. That file is the one record nothing else in the
system verifies. This is the same destructive-regeneration shape as ENTRY 011's
ledger deletion and the same identity-loss class as ENTRY 012's moved release
name.

`sk_init` now refuses if the target exists, prints every active trust entry that
would be lost, and requires either explicit `--force` or a distinct
`--trusted-out` path. Authorized re-initialization selects a fresh numbered
demonstration-run directory instead of colliding with or replacing `runs/first`.
The acceptance gate proves refusal, byte-for-byte preservation, both explicit
escape paths, and demonstration-run accumulation.

### Rule derived

> A field at a trust boundary has one meaning for every author, and an
> accumulating trust record is never a generator output. A green gate that
> asserts otherwise is evidence against the gate, not for the implementation.

### Resolution

The corrected universalization gate passes with adapter-produced evidence and
explicit trust. The full build, semantic corpus, verifier corpus, and release
checks pass without weakening RUN06, RUN12, RUN16, or RUN17.
