# Repository setup

## Before the first commit

```bash
python build.py            # everything green
python build.py verify-drop
git init && git add -A && git commit -m "Solomon's Key v0.9.3"
```

Commit the whole tree as one initial commit. Reconstructing eleven passes of
history from stage folders would be fabricated history — the stages are already
the archive, and the iteration log is already the narrative.

## What is committed and why

**Generated corpora are committed on purpose.** `schemas/`, `examples/`,
`redcorpus/`, `runs/good/`, and the fixture `ledger/` are all reproducible from
their generators, and CI regenerates them and diffs against what is in the repo.
That diff is a real check: it proves the committed corpora are what the
generators actually produce, not hand-edited files that drifted.

This only works because the fixture chain is reproducible. It was not — the demo
ledger append used a live clock, so its head moved every build and cascaded into
every example's `ledger_ref`. A committed tree would have shown spurious diffs
constantly, which trains everyone to ignore diffs, which is how a real one gets
through. The demo timestamp is now pinned; real appends still use the clock.

**`runs/real/` is ignored.** A real emitted run carries a fresh `run_id`,
wall-clock timestamps, and the live ledger head. It *should* differ every time —
that is what makes it a record rather than a fixture.

## The ledger is the reason this repo matters

`TRUST_BOUNDARY.md` names git as the external anchor. That is not filing; it is
the security mechanism.

A hash chain alone cannot detect an attacker who rewrites an entry and recomputes
every hash after it — the result is internally consistent and verifies. Only an
anchor held outside the file catches that, and the anchor is the commit history.

The CI workflow already enforces it: every push checks the chain against the
committed `HEAD`, and checks that the ledger in this commit is a **byte-exact
prefix** of the ledger in the previous commit. Rewriting history fails the build.

That check has never run, because there has been no git history for it to read.

## Audit lineage: one location, one writer

`ledger/audit-solomons-key.jsonl` is `class: audit` — started once, never
reseeded, and the only chain in the project whose entries witnessed anything.

**Do not copy it into stage folders or release archives.** Two copies plus one
append is a real two-writer fork, which is exactly the failure ENTRY 011
untangled. Snapshots should carry the *head hash* (`ledger/AUDIT_HEAD`), not the
chain. A head is an anchor and does independent work; a copy is a second writer
waiting to happen.

## Suggested first tags

```bash
git tag -a v0.9.2 -m "Audit lineage, trust boundary, derived completeness gate"
git push --tags
```

Release tarballs go on the Releases page, not in the tree — `.gitignore`
excludes `*.tar.gz`.

## Private or public

Private is a reasonable staging choice, with one caveat worth naming: the stated
strategy is that public verifiable artifacts substitute for credentials. A
private repo produces none of that, and the existing public repo currently shows
v0.5.0 — a linter with eleven tests. Anyone who looks today sees a fraction of
the work and none of the iteration log.

A reasonable sequence: private until Floy's review lands and any findings are
addressed, then make it public in one move. The gap between what is public and
what exists is the cost of waiting, and it is currently large.
