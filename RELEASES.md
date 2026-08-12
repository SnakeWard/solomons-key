# Releases

One version name resolves to exactly one release set, permanently. `build.py dist` builds every
member before recording one row and refuses to re-cut any recorded version if even one digest
changes.

Current release rows carry four SHA-256 digests:

- **Tree hash** — `python sk_handoff.py hash`; the governed source state.
- **Drop tarball** — the complete `solomons-key-v*.tar.gz` verification drop.
- **Python sdist** — the `dist/solomons_key-*.tar.gz` source distribution.
- **Python wheel** — the `dist/solomons_key-*-py3-none-any.whl` wheel.

All three files are one identity. A version cannot record one file now and acquire the other two
later. `python build.py release-check` verifies every locally present member against the same row.

`RELEASES.md` is excluded from both the tree hash and the tarball. It records digests over
content, so it cannot be inside the content it describes — the same self-reference that keeps
the tree pin out of `TREE.sha256`'s own manifest.

> **Editing note.** A release-set row has exactly four full 64-hex digests. Historical rows have
> exactly two and remain valid but cannot be extended. Three-digest rows fail closed as partial
> identities. Notes below quote at most one full digest per line so they cannot become releases.

## Immutable release sets

| Version | Tree hash | Drop tarball | Python sdist | Python wheel | Commit | Status |
|---|---|---|---|---|---|---|
<!-- release-sets:end -->

## Legacy release identities

These pre-package-publication rows permanently retain their original tree and drop-tarball hashes.

| Version | Tree hash | Artifact hash | Commit | Status |
|---|---|---|---|---|
| v0.9.5 | `af67b105a781dbe25fceaab3e64650ef5846d8218aec97d2abd4e4426b97145e` | `e04cfeeeafa10477bf5d971d69a4480937d223e06a6877dc3ff469e7e1bc3b45` | `e50c6409e7e779ec49467f2ab4a47611abf0e077` | withdrawn — never distributed |
| v0.9.6 | `0debc0fe68cbed21f0e735592d0f9c1cd467948980915b95a41adc6a5b1ec401` | `7f2723d5ec640f5df4bf3adac7080e9e154c3f5fcaba89fb7f73dca569d97e71` | `e50c6409e7e779ec49467f2ab4a47611abf0e077` | released |
| v0.9.7 | `3dced80a1939966d1b6068cdddd8432c8258d4040c8a1683e8c98d46d215ed3e` | `636cd6204bd74e84d4788d9ba3046eb7a705839f1ea34895a433cbc6a95e04eb` | `e50c6409e7e779ec49467f2ab4a47611abf0e077` | released |
| v0.10.0 | `e0e5fac37d65c0be95d02d8c871ddff0c8f42b2c2b2045006f6f7d7d841b2cc2` | `8b6a86ff0f27201df52db0d839d40fba4ddc040190dbaaa8dab4090c0c5a0732` | `e50c6409e7e779ec49467f2ab4a47611abf0e077` | withdrawn — never distributed |
| v0.10.1 | `611ec5a466b564218d84af751c34aa46a7771ce4caf4fdd8224237f3cb80f4d8` | `cb58ee272fa37816c4191a75539a2dad1752996a7be24baec3d1bc4c8d3f3ede` | `e50c6409e7e779ec49467f2ab4a47611abf0e077` | released |
<!-- releases:end -->

---

## v0.10.0 — withdrawn, never distributed

The row remains as the permanent identity of the archive that was cut. A
post-cut escape-hatch probe found that `sk_init --force` correctly authorized
replacement of an existing trust root, then collided with the already-existing
`runs/first` demonstration directory. A distinct `--trusted-out` in the same
project hit the same collision. No trust root or prior run was overwritten, but
the two explicitly supported recovery paths could not complete.

The fix accumulates `runs/first-2`, `runs/first-3`, and so on. The acceptance
gate now executes both escape paths. The corrected tree was released as
v0.10.1; v0.10.0 was not distributed.

## v0.9.5 — withdrawn, never distributed

The row stands and is not rewritten. It records a tarball that really was built and really has
that hash; what it does not record is a release, because that file never left the machine that
built it.

It was cut against a working tree whose derived files were stale. `build.py verify-drop` passed
on the received drop — the tree matched its pin `17dd452c…` exactly — but the pin was not a
fixpoint of the tree's own build. Running `build.py` regenerated `key.repaired.yaml` from
`3edaf3aaa16c…` to `551ab00199…`, along with the ledger and the whole generated corpus, moving
the tree hash from `af67b105a781…` to `53f02dffb093…`. So the drop was internally consistent and
still not the state its own build produces, and `dist` recorded the transient state.

`dist` then refused to re-cut v0.9.5 against the settled tree, which is the guard behaving
correctly on its author. The refusal says not to delete the entry to get past it, so it was not
deleted: the version was bumped instead, which is what the rule requires of everyone else.

## v0.9.4 — skipped, not released

**There is no v0.9.4 release.** The number is a gap in the sequence.

Artifacts bearing the name exist, but they were built from a tree state that was never persisted
to the working tree: `VERSION` on disk went from `0.9.3` straight to `0.9.5`, and the bump to
`0.9.4` existed only in the environment where those tarballs were cut. The archived
`solomons-key-v0.9.4.tar.gz` does not contain `TASK_FRAME_release_identity.md`, which is part of
the tree that followed it, so it cannot be reproduced from anything in this repository.

Four different byte-states were recorded under the one name, none of them reproducible from a
tree that still exists:

- `0a4d67285abe…` — recorded in `ITERATION_LOG_001-015.md` ENTRY 012 as "cut clean"
- `f18adc2b7eff…` — measured while investigating the packer, per `test_sk_release.py`
- `99522ae9abc2…` — an immediate rebuild of the identical tree, same source
- `1ee8b3626290526e5e02c3f3d79be13161841dd90860eaa53f6a44520a2e85fb` — `archives/solomons-key-v0.9.4.tar.gz`, the only one that survives as a file

All four are explained by the non-reproducible packer this pass replaced: every invocation
minted new bytes, so an artifact hash was not an identity. No tree hash is recorded for any of
them, because the trees that produced them are gone and inventing one would be a fabrication.

## v0.9.3 — superseded, the name resolves to more than one artifact

ENTRY 012: `v0.9.3` was cut, the CI workflow was fixed, and the tag was moved rather than a new
version being cut — twice. The name was left pointing at more than one artifact.

What **is** recoverable, by hashing the file:

- `archives/solomons-key-v0.9.3.tar.gz` is `1223b31057c9ab1b3d0aa800726781a2603037954383e31caefeb2bb38bfe1ff`, which matches ENTRY 012's **CI-fix build — CI triggers on `main` and `master`**.
- `archives/solomons-forge-v0.9.3.tar.gz` is `0d0188ad01f703822020597f4329f314a159ec81ef1a55cc069a3ab0f6d95ec1` — a third file under the same version number, cut under the former product name.

What is **not** recoverable, and is not guessed at here:

- The other artifact, `855f24ba0935…` (the rebrand build, CI on `main` only), is not present in
  `archives/`. Only the twelve hex digits ENTRY 012 recorded are known.
- Which artifact the moved tag pointed at, and when. The tag history was overwritten, so the
  mapping from the name `v0.9.3` to a file at any given moment cannot be reconstructed.
- Tree hashes for any v0.9.3 artifact.

Both are superseded. The names cannot be repaired retroactively, only abandoned — which is why
the record stops at what can be checked.
