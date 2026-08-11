# TASK FRAME — Release Identity Pass

**Tree pin:** see `TREE.sha256` (line `# tree_sha256:`), and compare against the hash quoted in the handoff message.

A tree hash cannot be embedded in this file: this file is part of the tree, so
writing the hash in changes the hash. That is not a nuisance, it is the same
self-reference that made `known_programs` circular in ENTRY 009 — an identity
recorded inside the thing it identifies is not an identity. The pin therefore
lives in `TREE.sha256` and, independently, in the handoff message. Two places,
neither of them inside the pinned content.

**Verify before starting:** `python build.py verify-drop`. If the reported tree
hash differs from the one in the handoff message, stop and request a matching
drop. Do not work around gaps.

**Actor:** Grok
**Actor role:** builder
**Route:** `protocol_build_route`
**Mutation scope:** `approved_generated_files_only` — modify `build.py`, create `RELEASES.md`. Nothing else.
**Acceptance gate:** `python test_sk_release.py` exits 0. That file already exists and currently fails 6 of 8. It was written before this spec.

---

## Why this pass exists

`v0.9.3` was cut, the CI workflow was fixed, and the tag was moved rather than a new version cut. Two distinct artifacts now carry that name, differing in whether CI runs at all:

| SHA-256 | Contents |
|---|---|
| `855f24ba0935…` | CI triggers on `main` only |
| `1223b31057c9…` | CI triggers on `main` and `master` |

The archived `solomons-key-v0.9.3.tar.gz` is one of them and the name cannot say which.

**Then investigating the fix found something worse.** `build.py dist` is not reproducible. The same tree, packed twice two seconds apart, produced `f18adc2b7eff…` and then `99522ae9abc2…`. `tar` records file mtimes and `gzip` records its own timestamp, so byte-identical content yields a different archive every time.

That reorders the work. **Recording hashes on a non-reproducible packer produces a ledger of arbitrary numbers** — nobody could rebuild and confirm, and a mismatch would carry no information. Determinism first, then the record.

This is also the third defect found *above* the layer the tools inspect: handoffs (ENTRY 001), packaging completeness (ENTRY 010), release identity (ENTRY 012). Each was a name or container assumed stable that wasn't.

---

## Deliverable 1 — make `dist` reproducible

`build.py dist` must produce byte-identical output from an identical tree, on any machine, at any time.

Requirements:

- **Pin every tar member's mtime** to a fixed value. Use the `VERSION` file's content as the source of a constant, or a hardcoded epoch — do not use the current time or any file's real mtime.
- **Pin the gzip timestamp.** `tarfile.open(..., "w:gz")` writes the current time into the gzip header. Use `gzip.GzipFile(..., mtime=0)` wrapped around a `tarfile.open(fileobj=..., mode="w")`, or an equivalent that zeroes it.
- **Sort members deterministically** by archive path. Directory iteration order is not guaranteed.
- **Normalize member metadata** — uid, gid, uname, gname to fixed values. A tarball built as `root` must match one built as anyone else.
- Keep the existing exclusions (`__pycache__`, `*.pyc`, `*.tar.gz`).

The acceptance test packs twice with a two-second gap and compares. Same-second coincidence will not save a partial implementation.

## Deliverable 2 — `RELEASES.md`

A human-readable, machine-parseable record. One line per release containing the version and **two** 64-hex digests, in this order:

1. **tree hash** — from `sk_handoff.py hash`. Content identity. Reproducible by anyone.
2. **artifact hash** — SHA-256 of the tarball. Byte identity of the shipped file.

Both matter and they answer different questions. The tree hash says *what state was released*; the artifact hash says *what file was published*. With a reproducible packer these are two views of one fact, and a divergence between them is a signal worth catching.

Also record the git commit where available (`git rev-parse HEAD`, degrading gracefully outside a repo).

Table or list format is your call — the parser accepts any line carrying a version and two digests. Write it for a human reading the file, not for the parser.

Seed it with `v0.9.4`. Do **not** invent entries for the two ambiguous `v0.9.3` artifacts; record them as superseded with a note that their identity is unrecoverable, or omit them. Fabricating a resolution for an ambiguous release is exactly the failure this pass exists to prevent.

## Deliverable 3 — `dist` refuses to re-cut a changed version

Before writing, `dist` computes the tree hash and consults `RELEASES.md`.

- **Version not recorded** → build, record, done.
- **Version recorded, tree hash matches** → allowed. Reproducibility means the artifact hash will agree; treat it as a no-op and say so. This must not fail — a clean rebuild is a legitimate operation.
- **Version recorded, tree hash differs** → **refuse, exit nonzero**, and say plainly what changed and that the version must be bumped.

The refusal message is part of the deliverable. The test checks it contains actionable language, because a bare nonzero exit teaches people to delete the file.

## Deliverable 4 — `release-check` target

`python build.py release-check` re-derives what it can and reports drift:

- For each recorded release whose tarball is present, recompute and compare the artifact hash.
- For the current version, recompute the tree hash and compare.
- Report missing tarballs as informational, not as failures — old artifacts legitimately live elsewhere.
- Exit 0 on a clean tree, nonzero on a real mismatch.

Wire it into the default `ALL` sequence after `pin`.

---

## Constraints

**Do not modify** any `sk_*.py`, any `test_*.py`, any generator, the schemas, the KEY files, or the ledgers. If an existing rule blocks a correct implementation, stop and report it — do not route around it. A rule that blocks correct behavior is a finding worth more than the workaround.

**Do not touch the ledgers.** `build.py ledger` already refuses to reseed a chain holding entries a reseed would destroy, and `ledger/audit-*.jsonl` must survive untouched. Nothing in this pass appends to any ledger.

**Do not fabricate history.** The two `v0.9.3` artifacts cannot be disambiguated. Say so in `RELEASES.md`; do not guess.

**Determinism must be real, not asserted.** If you cannot make some component reproducible, say which and why. A `dist` that claims reproducibility and isn't is worse than one that admits it, because the whole point of the hash record is that someone else can check it.

---

## Definition of done

```bash
python test_sk_release.py     # exit 0
python build.py               # everything still green, 135+ tests
python build.py verify-drop   # all required files present
```

Then confirm reproducibility by hand across a gap:

```bash
python build.py dist && sha256sum solomons-key-v0.9.4.tar.gz
sleep 5
python build.py dist && sha256sum solomons-key-v0.9.4.tar.gz
```

Both digests must be identical.

## Handoff back

Report, as artifacts rather than claims:

1. The tree hash you verified at the start (`python build.py verify-drop` output, verbatim).
2. `python test_sk_release.py` output, **verbatim** — not a summary table.
3. The two `sha256sum` lines from the reproducibility check above.
4. `RELEASES.md` contents.
5. Anything you could not make deterministic, and why.
6. Any existing rule that blocked correct behavior.

Items 2 and 3 are the evidence; a status table asserting they passed is a model's judgment about a program's output, not the program's output. Item 6 is worth the most — every rule in this repo was written by inference about what a governed build needs, and where the inference was wrong is what the iteration log is for.
