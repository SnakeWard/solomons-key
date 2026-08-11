#!/usr/bin/env python3
"""
test_sk_release.py — acceptance gate for the release identity pass.

WRITTEN BEFORE THE WORK. Expected to FAIL until `build.py` and `RELEASES.md`
satisfy every claim below.

WHY THIS EXISTS
---------------
ENTRY 012: two different artifacts shipped as `v0.9.3`, differing in whether CI
ran at all. A tag was moved instead of a version being cut.

Investigating the fix exposed a deeper problem. `build.py dist` is **not
reproducible** — the same tree, packed twice, produces different bytes, because
tar records mtimes and gzip records its own timestamp. Verified directly: the
v0.9.4 tarball hashed `f18adc2b7eff…` and an immediate rebuild of the identical
tree hashed `99522ae9abc2…`.

That matters more than the retag. If a release cannot be reproduced, then
"version X has hash H" pins one particular file rather than one particular
*state*, and nobody can independently rebuild and confirm. Recording hashes on
top of a non-reproducible packer would produce a ledger of arbitrary numbers.

So the order is: make `dist` deterministic first, then record.

WHAT IS UNDER TEST
------------------
  A. `dist` is reproducible — same tree, same bytes, every time.
  B. `RELEASES.md` records version, tree hash, artifact hash, and commit,
     in a machine-parseable form.
  C. `dist` REFUSES to re-cut a version already recorded with different
     content, rather than silently overwriting it.
  D. `dist` recording an unchanged rebuild of the same version is allowed —
     reproducibility means the hash agrees, so this is a no-op, not a conflict.
  E. `release-check` re-derives every recorded entry it can and reports drift.
  F. Release identity is content identity: the recorded tree hash matches
     `sk_handoff hash` for that tree.

Run:  python3 test_sk_release.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
RELEASES = os.path.join(HERE, "RELEASES.md")

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))


def run(argv: list[str], cwd: str | None = None) -> tuple[int, str]:
    p = subprocess.run([PY, *argv], cwd=cwd or HERE, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def sha256(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def version() -> str:
    return open(os.path.join(HERE, "VERSION"), encoding="utf-8").read().strip()


def tarball(v: str) -> str:
    return os.path.join(HERE, f"solomons-key-v{v}.tar.gz")


def main() -> int:
    v = version()

    # --- A. reproducibility ------------------------------------------
    rc, out = run(["build.py", "dist"])
    ok_first = rc == 0 and os.path.exists(tarball(v))
    check("dist_runs", ok_first, out.strip()[-160:])
    if not ok_first:
        report()
        return 1

    first = sha256(tarball(v))
    shutil.copy(tarball(v), os.path.join(tempfile.gettempdir(), "rel_a.tar.gz"))
    import time
    time.sleep(2)  # defeat same-second coincidence
    run(["build.py", "dist"])
    second = sha256(tarball(v))

    check(
        "dist_is_reproducible",
        first == second,
        f"same tree packed twice gave {first[:12]}… then {second[:12]}… — "
        "tar mtimes and the gzip timestamp must be pinned",
    )

    # --- B. RELEASES.md is present and parseable ---------------------
    have_releases = os.path.exists(RELEASES)
    check("releases_file_exists", have_releases, f"missing {RELEASES}")
    entries: dict[str, dict[str, str]] = {}
    if have_releases:
        text = open(RELEASES, encoding="utf-8").read()
        # Any line carrying a version and two 64-hex digests, in any table or
        # list format. The parser is deliberately permissive about layout and
        # strict about content.
        for line in text.splitlines():
            m = re.search(r"v?(\d+\.\d+\.\d+)", line)
            hashes = re.findall(r"\b([0-9a-f]{64})\b", line)
            if m and len(hashes) >= 2:
                entries[m.group(1)] = {"tree": hashes[0], "artifact": hashes[1],
                                       "line": line.strip()}
        check(
            "releases_entries_parse",
            bool(entries),
            "no line contained a version plus two 64-hex digests "
            "(expected tree hash and artifact hash)",
        )
        check(
            "current_version_is_recorded",
            v in entries,
            f"v{v} has no entry in RELEASES.md",
        )
        if v in entries:
            check(
                "recorded_artifact_hash_matches",
                entries[v]["artifact"] == second,
                f"RELEASES.md records {entries[v]['artifact'][:12]}…, "
                f"actual artifact is {second[:12]}…",
            )
            rc, tree_out = run(["sk_handoff.py", "hash"])
            actual_tree = tree_out.strip().splitlines()[-1] if tree_out.strip() else ""
            check(
                "recorded_tree_hash_matches",
                entries[v]["tree"] == actual_tree,
                f"RELEASES.md records tree {entries[v]['tree'][:12]}…, "
                f"sk_handoff hash gives {actual_tree[:12]}…",
            )

    # --- D. an unchanged rebuild is a no-op, not a conflict ----------
    rc, out = run(["build.py", "dist"])
    check(
        "unchanged_rebuild_is_allowed",
        rc == 0,
        f"re-cutting an identical version failed (exit {rc}): {out.strip()[-160:]}",
    )

    # --- C. a changed rebuild of a recorded version must REFUSE ------
    with tempfile.TemporaryDirectory() as td:
        work = os.path.join(td, "work")
        shutil.copytree(HERE, work, ignore=shutil.ignore_patterns("__pycache__", "*.tar.gz"))
        # Change what ships without changing the version.
        with open(os.path.join(work, "README.md"), "a", encoding="utf-8") as fh:
            fh.write("\n<!-- content changed, version not bumped -->\n")
        rc, out = run(["build.py", "dist"], cwd=work)
        check(
            "changed_content_same_version_refused",
            rc != 0,
            "dist re-cut a recorded version with different content and exited 0 — "
            "this is exactly how v0.9.3 came to name two artifacts",
        )
        check(
            "refusal_explains_itself",
            any(w in out.lower() for w in ("refus", "already", "bump", "recorded")),
            f"refusal gave no actionable message: {out.strip()[-160:]}",
        )

    # --- E. release-check re-derives what it can ---------------------
    rc, out = run(["build.py", "release-check"])
    check(
        "release_check_target_exists",
        "unknown target" not in out.lower(),
        "build.py has no 'release-check' target",
    )
    check("release_check_passes_on_clean_tree", rc == 0, out.strip()[-160:])

    report()
    return 1 if any(s == FAIL for s, _, _ in results) else 0


def report() -> None:
    width = max((len(n) for _, n, _ in results), default=10)
    for status, name, detail in results:
        line = f"  {status}  {name.ljust(width)}"
        if detail and status == FAIL:
            line += f"   {detail}"
        print(line)
    failed = sum(1 for s, _, _ in results if s == FAIL)
    print(f"\n  {len(results) - failed} passed, {failed} failed")
    if failed:
        print("\n  This is the acceptance gate for the release identity pass.")
        print("  It is expected to fail until build.py and RELEASES.md are built to spec.")


if __name__ == "__main__":
    sys.exit(main())
