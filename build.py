#!/usr/bin/env python3
"""
build.py — cross-platform build runner.

The Makefile depends on `rm -rf`, `sha256sum`, and shell substitution, none of
which exist natively on Windows. Since the project is developed on Windows and
verified on Linux CI, a Makefile-only build means the author cannot run the
thing that judges the author's work. That is its own kind of gap.

This is the Makefile's logic in Python. Same targets, same order, no shell.

    python build.py            # everything
    python build.py test       # test suites only
    python build.py verify     # lint + artifacts + verify + ledger
    python build.py docs       # check documented counts against reality
    python build.py dist       # versioned tarball, recorded in RELEASES.md
    python build.py release-check   # re-derive recorded releases, report drift
    python build.py --list

Exit code is nonzero if any step fails.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

KEY_SRC = "key.yaml"
KEY = "key.repaired.yaml"
LEDGER = os.path.join("ledger", "solomons-key-builder-ledger.jsonl")
HEAD = os.path.join("ledger", "HEAD")

GENERATED_DIRS = ["redcorpus", "schemas", "examples", "runs", "__pycache__"]


class Step:
    def __init__(self, name: str, argv: list[str], allow_fail: bool = False):
        self.name, self.argv, self.allow_fail = name, argv, allow_fail


def run(argv: list[str], capture: bool = False) -> tuple[int, str]:
    p = subprocess.run([PY, *argv], cwd=HERE, capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    if not capture:
        sys.stdout.write(out)
    return p.returncode, out


def clean() -> int:
    for d in GENERATED_DIRS:
        shutil.rmtree(os.path.join(HERE, d), ignore_errors=True)
    print("clean: removed generated directories")
    return 0


def repair() -> int:
    return run(["repair_pass.py", KEY_SRC, KEY])[0]


def ledger() -> int:
    """Seed the demonstration ledger — refusing if that would destroy real history.

    This function used to open with an unconditional rmtree. `sk_ledger seed`
    already refuses to seed over a non-empty ledger; the build deleted the file
    first, which routed around that guard. Every full build silently destroyed
    any entry appended since the last one.

    That is the gate-bypass pattern this toolchain exists to detect, committed
    in the build script by the author, and it is why PASS_19 survived only in a
    worktree nobody rebuilt.
    """
    # Audit lineages live alongside the demo chain and are never regenerated.
    # Preserve them across the rmtree that reseeds the demonstration chain.
    ldir = os.path.join(HERE, "ledger")
    preserved = {}
    if os.path.isdir(ldir):
        for fn in os.listdir(ldir):
            if fn.startswith("audit") or fn == "AUDIT_HEAD":
                preserved[fn] = open(os.path.join(ldir, fn), "rb").read()

    path = os.path.join(HERE, LEDGER)
    if os.path.exists(path):
        keep = []
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            # An entry is seed-derived if it came from the KEY's pass history or
            # is the demo run this build creates. Anything else was appended by
            # someone recording a real event.
            pid = e.get("pass_id", "")
            if e.get("entry_type") == "genesis" or pid.startswith("PASS_") and pid <= "PASS_17":
                continue
            if pid == "RUN_build_0001":
                continue
            keep.append(pid or e.get("entry_type"))
        if keep:
            print("ledger: REFUSING to reseed — the existing ledger contains entries")
            print("        that reseeding would destroy:")
            for k in keep:
                print(f"          {k}")
            print()
            print("        Reseeding is only safe on a pure demonstration chain.")
            print("        To keep this history, move it aside first:")
            print(f"          mv {LEDGER} ledger/audit-<name>.jsonl")
            print("        Then reconcile the two lineages rather than discarding one.")
            return 1
    shutil.rmtree(ldir, ignore_errors=True)
    os.makedirs(ldir, exist_ok=True)
    for fn, blob in preserved.items():
        with open(os.path.join(ldir, fn), "wb") as fh:
            fh.write(blob)
    if preserved:
        print(f"ledger: preserved {len(preserved)} audit file(s) across reseed")
    rc, _ = run(["sk_ledger.py", "seed", LEDGER, "--from-key", KEY])
    if rc:
        return rc
    rc, _ = run([
        "sk_ledger.py", "append", LEDGER,
        "--pass", "RUN_build_0001", "--name", "Demo governed build run",
        "--actor", "Codex", "--actor-role", "builder",
        "--type", "validation_run", "--result", "pass",
        "--note", "Witnesses the run in runs/good.",
        # Fixed timestamp. A demonstration chain must be REPRODUCIBLE: two
        # clean builds should produce the same head. With a live clock the
        # fixture head moved every build, which cascaded into every example
        # artifact's ledger_ref — meaning a committed tree would show spurious
        # diffs on every build and train everyone to ignore them.
        # Real appends use the real clock; only the seeded demo is pinned.
        "--timestamp", "2026-01-01T00:00:01Z",
    ])
    if rc:
        return rc
    rc, out = run(["sk_ledger.py", "head", LEDGER], capture=True)
    if rc:
        sys.stdout.write(out)
        return rc
    with open(os.path.join(HERE, HEAD), "w", encoding="utf-8") as fh:
        fh.write(out.strip() + "\n")
    print(f"ledger: head written to {HEAD}")
    return 0


def ledger_verify() -> int:
    head = open(os.path.join(HERE, HEAD), encoding="utf-8").read().strip()
    return run(["sk_ledger.py", "verify", LEDGER, "--expect-head", head])[0]


def audit_verify() -> int:
    """Verify every audit lineage against its own anchor.

    The demonstration chain is regenerated each build and proves only that the
    mechanism works. An audit lineage is started once and never reseeded; it is
    the only chain whose entries witnessed anything.
    """
    ldir = os.path.join(HERE, "ledger")
    anchors = {"audit-solomons-key.jsonl": "AUDIT_HEAD"}
    rc_all = 0
    for name, anchor in anchors.items():
        p = os.path.join(ldir, name)
        a = os.path.join(ldir, anchor)
        if not os.path.exists(p):
            continue
        head = open(a, encoding="utf-8").read().strip() if os.path.exists(a) else None
        argv = ["sk_ledger.py", "verify", os.path.join("ledger", name)]
        if head:
            argv += ["--expect-head", head]
        rc, _ = run(argv)
        rc_all = rc_all or rc
    return rc_all


RELEASES = "RELEASES.md"

# Every tar member gets this mtime. Any constant works; what matters is that it
# does not come from the clock or from the filesystem, both of which differ
# between two packs of identical content.
SOURCE_EPOCH = 1_700_000_000


def _excluded(arcpath: str) -> bool:
    """Paths that must not ship, by archive-relative path (no leading name/).

    Two of these are exclusions the packer did not previously have, and both are
    load-bearing:

    RELEASES.md records the artifact hash of the tarball. Packing it would put
    the digest inside the file it describes — the fixed point again, and it also
    means two consecutive `dist` runs pack different trees, which fails the
    reproducibility check for a reason that has nothing to do with tar.

    runs/real/ is a real emitted run: fresh run_id, wall-clock timestamps, live
    ledger head. `.gitignore` already excludes it and says why — it SHOULD differ
    every time. Packing it made the tarball differ after every full build while
    two bare `dist` runs still agreed, so the acceptance test passed while the
    real build was irreproducible. Recipients emit their own with `sk_emit run`;
    runs/good and the RUN* corpus still ship.
    """
    parts = arcpath.split("/")
    base = parts[-1]
    if "__pycache__" in parts:
        return True
    # The old packer walked os.listdir and never shipped .git, so this is not a
    # new exclusion so much as one that was previously implicit. It has to be
    # explicit now: .git/index and .git/logs change on every git operation, so
    # packing them would make the tarball irreproducible by itself. .github is a
    # different path and still ships — CI config is part of what is released.
    if ".git" in parts:
        return True
    if base.endswith((".pyc", ".tar.gz")):
        return True
    if base == RELEASES:
        return True
    if parts[:2] == ["runs", "real"]:
        return True
    return False


def _normalize(ti: tarfile.TarInfo) -> tarfile.TarInfo:
    """Strip everything that varies between machines, users, and clocks."""
    ti.mtime = SOURCE_EPOCH
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    ti.mode = 0o755 if ti.isdir() else 0o644
    return ti


def _pack(dest: str, name: str) -> None:
    """Write a deterministic tarball of the tree to `dest`.

    tarfile.open(..., "w:gz") stamps the current time into the gzip header, so
    the same bytes gzip differently every second. GzipFile(mtime=0) zeroes it.
    Members are collected and sorted by archive path: os.walk order is not
    guaranteed and differs across filesystems.
    """
    members: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(HERE):
        rel_dir = os.path.relpath(dirpath, HERE).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = sorted(
            d for d in dirnames
            if not _excluded(f"{rel_dir}/{d}".lstrip("/"))
        )
        for fn in filenames:
            rel = f"{rel_dir}/{fn}".lstrip("/")
            if not _excluded(rel):
                members.append((rel, os.path.join(dirpath, fn)))
        if rel_dir:
            members.append((rel_dir, dirpath))

    with open(dest, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tf:
                for rel, full in sorted(set(members)):
                    ti = tf.gettarinfo(full, arcname=f"{name}/{rel}")
                    _normalize(ti)
                    if ti.isreg():
                        with open(full, "rb") as fh:
                            tf.addfile(ti, fh)
                    else:
                        tf.addfile(ti)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash() -> str:
    rc, out = run(["sk_handoff.py", "hash"], capture=True)
    return out.strip().splitlines()[-1] if rc == 0 and out.strip() else ""


def git_commit() -> str:
    """Current commit, or an em dash outside a repo. Never fails the build."""
    try:
        p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE,
                           capture_output=True, text=True)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    except OSError:
        pass
    return "—"


HEX64 = re.compile(r"\b([0-9a-f]{64})\b")
VER_RE = re.compile(r"v?(\d+\.\d+\.\d+)")


def read_releases() -> dict[str, dict[str, str]]:
    """Parse RELEASES.md the same way the acceptance gate does.

    A line is a release entry only if it carries a version AND two full 64-hex
    digests. Historical notes deliberately quote truncated hashes so that a
    superseded artifact or a skipped version is never read as a release.
    """
    path = os.path.join(HERE, RELEASES)
    entries: dict[str, dict[str, str]] = {}
    if not os.path.exists(path):
        return entries
    for line in open(path, encoding="utf-8").read().splitlines():
        m = VER_RE.search(line)
        hashes = HEX64.findall(line)
        if m and len(hashes) >= 2:
            entries[m.group(1)] = {"tree": hashes[0], "artifact": hashes[1]}
    return entries


def append_release(version: str, tree: str, artifact: str, commit: str) -> None:
    path = os.path.join(HERE, RELEASES)
    row = f"| v{version} | `{tree}` | `{artifact}` | `{commit}` | released |\n"
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    marker = "<!-- releases:end -->"
    if marker in text:
        i = text.index(marker)
        text = text[:i] + row + text[i:]
    else:
        text += row
    open(path, "w", encoding="utf-8").write(text)


def dist() -> int:
    version = open(os.path.join(HERE, "VERSION"), encoding="utf-8").read().strip()
    name = f"solomons-key-v{version}"
    tarball = os.path.join(HERE, f"{name}.tar.gz")

    # Pack to a temporary file and decide afterwards. The previous version
    # removed the existing tarball before packing, so any refusal below would
    # have destroyed the artifact it was refusing to overwrite.
    fd, staged = tempfile.mkstemp(prefix=f"{name}.", suffix=".tar.gz", dir=HERE)
    os.close(fd)
    try:
        _pack(staged, name)
        artifact = sha256_file(staged)
        tree = tree_hash()
        recorded = read_releases().get(version)

        if recorded:
            tree_moved = recorded["tree"] != tree
            art_moved = recorded["artifact"] != artifact
            if tree_moved or art_moved:
                print(f"dist: REFUSING to re-cut v{version} — it is already recorded")
                print(f"      in {RELEASES} with different content.")
                print()
                if tree_moved:
                    print(f"      recorded tree      {recorded['tree']}")
                    print(f"      current  tree      {tree}")
                if art_moved:
                    print(f"      recorded artifact  {recorded['artifact']}")
                    print(f"      current  artifact  {artifact}")
                print()
                print("      A version name resolves to exactly one artifact,")
                print("      permanently. The content changed, so the version must")
                print("      be bumped: edit VERSION, then run dist again.")
                print()
                print("      Do not delete the RELEASES.md entry to get past this.")
                print("      That is how v0.9.3 came to name two different artifacts.")
                return 1
            # Reproducible, so an unchanged rebuild agrees by construction.
            os.replace(staged, tarball)
            staged = ""
            print(f"dist: v{version} already recorded and unchanged — no-op")
            print(f"  sha256 {artifact}")
            return 0

        os.replace(staged, tarball)
        staged = ""
        append_release(version, tree, artifact, git_commit())
        print(f"dist: {name}.tar.gz")
        print(f"  tree     {tree}")
        print(f"  sha256   {artifact}")
        print(f"  recorded in {RELEASES}")
        print("\n  Recipient runs first:")
        print(f"    tar -xzf {name}.tar.gz && cd {name} && python build.py verify-drop")
        return 0
    finally:
        if staged and os.path.exists(staged):
            os.remove(staged)


def release_check() -> int:
    """Re-derive every recorded release that can be re-derived, and report drift.

    Exits nonzero only for a published file whose bytes changed. A tarball that
    is simply absent is informational — older artifacts legitimately live in
    ../archives/ rather than in the tree. A working tree that has moved past the
    last release is also normal, and reporting it as a failure would break every
    ordinary `build.py` run made between releases.
    """
    entries = read_releases()
    if not entries:
        print(f"release-check: no releases recorded in {RELEASES} yet")
        return 0

    version = open(os.path.join(HERE, "VERSION"), encoding="utf-8").read().strip()
    mismatches = 0
    print(f"release-check: {len(entries)} recorded release(s)")
    for v in sorted(entries):
        rec = entries[v]
        path = os.path.join(HERE, f"solomons-key-v{v}.tar.gz")
        if not os.path.exists(path):
            print(f"  v{v}  tarball not present — not checked")
            continue
        actual = sha256_file(path)
        if actual == rec["artifact"]:
            print(f"  v{v}  artifact matches  {actual[:12]}…")
        else:
            mismatches += 1
            print(f"  v{v}  ARTIFACT MISMATCH")
            print(f"        recorded {rec['artifact']}")
            print(f"        actual   {actual}")

    if version in entries:
        actual_tree = tree_hash()
        if actual_tree == entries[version]["tree"]:
            print(f"  v{version}  tree matches the working tree")
        else:
            print(f"  v{version}  tree has moved since the release — expected between")
            print(f"        releases; bump VERSION before cutting again")
            print(f"        recorded {entries[version]['tree']}")
            print(f"        actual   {actual_tree}")
    else:
        print(f"  v{version}  current version not yet released")

    if mismatches:
        print(f"\nrelease-check: {mismatches} published artifact(s) no longer match")
        print("  the hash recorded for them. A released file changed on disk.")
        return 1
    print("release-check: no drift")
    return 0


# --- documentation drift -------------------------------------------------

COUNTS_BEGIN = "<!-- counts:begin -->"
COUNTS_END = "<!-- counts:end -->"
COUNTED_DOCS = ["README_sk-lint.md", "REPORT.md"]


def actual_counts() -> dict[str, int]:
    rc, out = run(["sk_lint.py", "--rules"], capture=True)
    lint = len([l for l in out.splitlines() if l.strip().startswith("SK")])
    verify_src = open(os.path.join(HERE, "sk_verify.py"), encoding="utf-8").read()
    runr = len(set(re.findall(r'add\("(RUN\d+)"', verify_src)))
    art_src = open(os.path.join(HERE, "sk_artifacts.py"), encoding="utf-8").read()
    sem = len(set(re.findall(r'add\("(SEM\d+)"', art_src)))

    tests = 0
    for suite in ("test_sk_lint.py", "test_sk_ledger.py", "test_sk_artifacts.py",
                  "test_sk_verify.py", "test_sk_emit.py"):
        if os.path.exists(os.path.join(HERE, suite)):
            _, o = run([suite], capture=True)
            m = re.search(r"(\d+) passed, (\d+) failed", o)
            if m:
                tests += int(m.group(1))
    return {"lint": lint, "run": runr, "semantic": sem, "tests": tests}


def counts_block(c: dict[str, int]) -> str:
    return (
        f"{COUNTS_BEGIN}\n"
        f"**{c['lint']}** lint rules · **{c['run']}** run rules · "
        f"**{c['semantic']}** semantic rules · **{c['tests']}** tests passing\n"
        f"{COUNTS_END}"
    )


def docs(write: bool = False) -> int:
    """Regenerate the counts block in the docs and fail if it had drifted.

    An earlier version of this check scanned prose for phrases like "N lint
    rules". It passed on documents that never used that phrasing — a rule that
    could not fail, which is the exact defect this toolchain exists to catch,
    committed here. The fix is a delimited block that is regenerated and
    compared byte-for-byte, so there is no phrasing for it to miss.
    """
    c = actual_counts()
    block = counts_block(c)
    print(f"docs: actual — {c['lint']} lint, {c['run']} run, {c['semantic']} semantic, {c['tests']} tests")

    drifted, missing = [], []
    for doc in COUNTED_DOCS:
        path = os.path.join(HERE, doc)
        if not os.path.exists(path):
            continue
        text = open(path, encoding="utf-8").read()
        if COUNTS_BEGIN not in text or COUNTS_END not in text:
            missing.append(doc)
            continue
        i = text.index(COUNTS_BEGIN)
        j = text.index(COUNTS_END) + len(COUNTS_END)
        if text[i:j] != block:
            drifted.append((doc, text[i:j]))
            if write:
                open(path, "w", encoding="utf-8").write(text[:i] + block + text[j:])

    if missing:
        for d in missing:
            print(f"docs: {d} has no counts block — add {COUNTS_BEGIN} / {COUNTS_END}")
        return 1
    if drifted:
        for d, was in drifted:
            print(f"docs: DRIFT in {d}")
            print("      was: " + was.replace(COUNTS_BEGIN, "").replace(COUNTS_END, "").strip())
            print("      now: " + block.replace(COUNTS_BEGIN, "").replace(COUNTS_END, "").strip())
        if write:
            print("docs: rewritten")
            return 0
        print("\n  Run: python build.py docs-write")
        return 1
    print("docs: counts blocks match the code")
    return 0


def docs_write() -> int:
    return docs(write=True)


TARGETS: dict[str, list] = {
    "clean": [clean],
    "repair": [repair],
    "ledger": [ledger],
    "schemas": [Step("schemas", ["gen_artifact_schemas.py", KEY, "schemas/artifacts"])],
    "examples": [Step("examples", ["gen_artifact_examples.py", KEY, "examples"])],
    "corpus": [Step("corpus", ["gen_redcorpus.py", KEY, "redcorpus"])],
    "runs": [Step("runs", ["gen_runs.py", KEY, LEDGER, "runs"])],
    "emit": [Step("emit-real-run", ["sk_emit.py", "run", "--key", KEY,
                                    "--route", "protocol_build_route",
                                    "--ledger", LEDGER, "--out", "runs/real"])],
    "test": [
        Step("test_sk_lint", ["test_sk_lint.py"]),
        Step("test_sk_ledger", ["test_sk_ledger.py"]),
        Step("test_sk_artifacts", ["test_sk_artifacts.py"]),
        Step("test_sk_verify", ["test_sk_verify.py"]),
    ],
    "test-emit": [Step("test_sk_emit", ["test_sk_emit.py"])],
    "test-release": [Step("test_sk_release", ["test_sk_release.py"])],
    "lint": [Step("lint", ["sk_lint.py", KEY])],
    "artifacts": [Step("artifacts", ["sk_artifacts.py", "validate", "--dir", "examples/valid",
                                     "--key", KEY, "--ledger", LEDGER])],
    "verify": [
        Step("verify-fixture", ["sk_verify.py", "runs/good"]),
        # The real emitted run. Unlike runs/good this is not a shape fixture:
        # every automatic gate's evidence was computed by sk_emit.py and carries
        # that program's runtime hash. This is the record the toolchain exists
        # to produce, and verifying it is the only end-to-end proof.
        Step("verify-real", ["sk_verify.py", "runs/real"]),
    ],
    "ledger-verify": [ledger_verify, audit_verify],
    "docs": [docs],
    "docs-write": [docs_write],
    "pin": [Step("pin", ["sk_handoff.py", "pin"])],
    "verify-drop": [Step("verify-drop", ["sk_handoff.py", "verify-drop"])],
    "dist": [dist],
    "release-check": [release_check],
}

ALL = ["repair", "ledger", "schemas", "examples", "corpus", "runs", "emit",
       "test", "test-emit", "lint", "artifacts", "verify", "ledger-verify",
       "docs", "pin", "release-check"]


def execute(target: str) -> int:
    for item in TARGETS[target]:
        if callable(item):
            rc = item()
        else:
            print(f"--- {item.name}")
            rc, _ = run(item.argv)
        if rc and not (isinstance(item, Step) and item.allow_fail):
            print(f"\nbuild: FAILED at '{target}' (exit {rc})")
            return rc
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="build.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="*", default=[])
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        print("targets:")
        for t in TARGETS:
            print(f"  {t}")
        print(f"\ndefault (all): {' '.join(ALL)}")
        return 0

    targets = args.targets or ALL
    for t in targets:
        if t == "all":
            for a in ALL:
                if execute(a):
                    return 1
            continue
        if t not in TARGETS:
            sys.stderr.write(f"build: unknown target '{t}' (try --list)\n")
            return 2
        if execute(t):
            return 1
    print("\nbuild: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        os._exit(0)
