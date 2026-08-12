# Solomon's Key

**A verifier for AI-assisted software builds.**

Part of the Solomon's Forge lineage. **Forge** is the normative layer — doctrine, specs, handoff contracts: how an AI-assisted build is *supposed* to be governed. **Key** is the evidentiary layer: whether a given build actually *was*. Forge states the contract; Key decides whether you kept it.

> Existing attestation proves *who* performed a build step. When the "who" is a language model, that is no longer the useful question. This proves whether a step was **computed or asserted**, and rejects a build where something claiming to be computed wasn't.

*Little Revelations Studio · Apache-2.0*

*Solomon's Forge (doctrine, JavaScript): https://github.com/SnakeWard/Solomons-Forge*

---

## Start here

**You want to use it on your project:** derive a contract from the CI you already
have. Classify each decision by answering the only governance question: did a
program decide it, or did a person?

```bash
python sk_init.py --repo . --out project.key.yaml
python sk_lint.py project.key.yaml
python sk_verify.py runs/first --key project.key.yaml
```

`sk_init.py` writes a small contract, a project-local
`TRUSTED_PROGRAMS.sha256`, and `runs/first`. Its final message gives the exact
file to remove to see RUN06 reject a bypass. It refuses to overwrite an existing
trust root; use `--trusted-out PATH` for a separate candidate, or the explicit
`--force` only after reviewing the entries it prints.

**You want to develop Solomon's Key itself:** take the newest tarball in
`archives/`, extract, and run:

```bash
python build.py verify-drop    # refuses to proceed on an incomplete tree
python build.py                # full build: generate, test, lint, verify
python sk_verify.py runs/good  # the reference governed run
```

A release tarball does not ship `runs/real`. A real emitted run carries a fresh run id,
wall-clock timestamps, and the live ledger head — it is the record of one execution, not part of
a release, and shipping it would make two builds of identical source differ. Produce your own:

```bash
python build.py emit           # writes runs/real
python sk_verify.py runs/real  # verify the run you just produced
```

**You want to see it catch something** — sixty seconds:

```bash
python sk_verify.py runs/RUN06_gate_bypassed   # gate bypass, CRITICAL
python sk_lint.py key.yaml                     # 6 errors in the original contract
```

Then delete any file from `runs/good/artifacts/` and re-run `sk_verify.py runs/good`. It will name the gate that did not fire.

**You want to understand the limits before the claims:** read `TRUST_BOUNDARY.md` first. It states what is proven, what is assumed, and where the assumptions stop.

The eight warnings from `python sk_lint.py key.repaired.yaml` are open by decision, not accidental breakage: seven unused telemetry declarations and one orphan artifact still need intent decisions. See `REPORT.md` under “What remains open.”

**You want the argument:** `REPORT.md`.

**You want to know whether this is honest:** `ITERATION_LOG_001-015.md`. Fifteen entries, most of them defects in the work of the people building it, including several of the author's own.

---

## Repository layout

```
archives/    immutable zip and tarball history of every drop
legacy/      the original flattened tree, preserved and superseded
stages/      numbered progression — the readable story of the stack
```

### Stages

| Stage | What it is |
|---|---|
| `01-sk-lint-foundation` | Structure verifier only — KEY lint, red corpus, repair |
| `02-sk-ledger-increment` | Hash-chained ledger and tamper tests |
| `03-artifact-run-verifier-increment` | Schemas, `sk_artifacts`, `sk_verify`, run corpus |
| `04-pass19-emitter-worktree` | Full stack plus `sk_emit.py` — the emitter pass |
| `05-v0.6.0-release-packaging` | Packaging layer: `sk_handoff`, iteration log, dist |

**Stages 01–03 are increments, not self-contained.** Do not try to run them standalone. The newest release tarball in `archives/` is the only complete, runnable tree.

---

## The four layers

| Tool | Question |
|---|---|
| `sk-init` | What decisions already exist in this project's CI, and who decides each one? |
| `sk-adapt` | Turn JUnit, SARIF, or an exit code into evidence and a gate decision. |
| `sk-lint` | Is the governance contract itself coherent? |
| `sk-ledger` | Has the record been rewritten, or forked? |
| `sk-artifacts` | Is this piece of evidence valid? |
| `sk-emit` | Produce evidence by running the checks that measure the build. |
| `sk-verify` | Did this run follow the contract? |
| `sk-handoff` | Was this work done against the tree it was specified against? |

`sk_emit.py` is the root of trust for the evidence it produces. See `TRUST_BOUNDARY.md`.

The verifier is implemented in Python, but the build being verified can use any language. Evidence formats such as JUnit and SARIF let the same contract model govern Python, JavaScript, Java, .NET, Go, Rust, and other toolchains.

---

## Operating model

The normal lifecycle is **derive → execute → adapt or emit → witness → verify**.

1. A user invokes `sk_init.py` once per project, and again when CI decision
   steps change. It reads CI configuration, asks whether each decision is
   automatic or attested, writes the contract and pins every executable it can
   resolve in `TRUSTED_PROGRAMS.sha256`. It never silently replaces an existing
   allowlist. It does not edit the source tree.
2. The existing CI system executes the build in any language. Solomon's Key
   does not control that executor.
3. For ordinary CI output, the CI job invokes `sk_adapt.py`: JUnit for tests,
   SARIF for scanners, or `exit-code` for a command. Each invocation emits the
   measured evidence plus its gate decision. For Solomon-native measurements,
   a user or build script invokes `sk_emit.py`; `sk_emit.py run` composes its
   lower-level emitters into a run directory.

For every program-produced artifact, `produced_by_program.sha256` means the
executable hash, without exception. Adapter inputs use `input_sha256`; file-backed
inputs also carry `input_path`, which SEM06 re-hashes during artifact validation.
4. If the contract declares a ledger, orchestration invokes `sk_ledger.py
   append` once a governed pass has a result, gate decisions, and artifact
   paths worth witnessing. `sk_emit.py` never appends silently: it reads the
   current ledger head and binds emitted evidence to it. Typical append events
   are `validation_run`, `repair_run`, `audit_run`, `escalation_recorded`, and
   `pass_complete`.
5. A user or CI invokes `sk_verify.py` only after the run directory is
   assembled. The verifier executes no build steps and mutates nothing; it
   compares the recorded run with the contract, schemas, optional ledger, and
   trusted-programs allowlist. Missing required gate decisions trigger RUN06;
   missing validation evidence triggers CRITICAL RUN12 only when the contract
   declares validation layers.

`build.py` is this repository's orchestration wrapper: it invokes the component
tools and their tests. Outside this repository there is no required orchestrator;
CI may call the same tools directly.

---

## What this is not

A reference implementation of a verification model, **not** a secure proof-of-computation system. It makes a false record structurally visible to anyone holding the trusted-programs allowlist and the ledger anchor. It does not make a false record infeasible to produce.

That distinction is stated at its true size on purpose. Claiming the larger thing when you have the smaller one is the exact failure this project exists to detect.

---

## Requirements

Python 3.11+, `PyYAML`, `jsonschema`. Nothing else, deliberately — a verifier with a large dependency surface is a verifier with a large attack surface.

Install the complete command suite from a checkout with:

```text
python -m pip install .
```

The wheel provides `sk-init`, `sk-adapt`, `sk-lint`, `sk-ledger`,
`sk-artifacts`, `sk-emit`, `sk-verify`, and `sk-handoff`. Its artifact schemas
ship as package resources, so validation works outside the source checkout;
`--schemas` and `SK_SCHEMA_DIR` remain explicit override paths.

Windows users: use `build.py`, not the Makefile. The Makefile needs `rm`, `sha256sum`, and shell substitution.

Release builds use a separate, exactly pinned tool environment so packaging
tools do not enlarge the verifier's runtime dependency surface:

```text
python -m venv .venv-release
.venv-release/Scripts/python -m pip install -r requirements-release.txt
$env:SK_RELEASE_PYTHON = ".venv-release/Scripts/python.exe"
python build.py acceptance
python build.py dist
```

On POSIX, use
`SK_RELEASE_PYTHON=.venv-release/bin/python python build.py acceptance` before
`dist`. The acceptance gate builds twice in independent temporary source
trees, runs `twine check --strict`, installs the wheel into a fresh environment
outside the checkout, and exercises the installed
`sk-init -> sk-lint -> sk-adapt -> sk-verify` path. It does not alter the live
release record or create live release artifacts. `VERSION` is the only version source;
`pyproject.toml` reads it dynamically. `dist` records the governed tree, drop
tarball, Python sdist, and wheel hashes together as one immutable release set.

Beta tags matching `vX.Y.ZaN`, `vX.Y.ZbN`, or `vX.Y.ZrcN` invoke the dedicated
TestPyPI workflow. Its build job proves the recorded release set before handing
only the sdist and wheel to an isolated `testpypi` environment. The publishing
job receives a short-lived OIDC identity and has no repository write permission
or stored API token. A manual dispatch accepts one existing prerelease tag only
as a recovery path for a pre-normalization release: it retrieves the original
release bytes from the corresponding `release-assets-vTAG` transport branch,
checks every byte against `RELEASES.md`, runs strict metadata and installed-wheel
acceptance, and only then hands the sdist and wheel to the same OIDC publisher.
It cannot rename the release, move the tag, or substitute a rebuilt artifact.
