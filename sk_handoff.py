#!/usr/bin/env python3
"""
sk_handoff.py — pin the tree a task frame was written against.

WHY THIS EXISTS
---------------
The emitter pass came back with two findings that named bugs in sk_lint.py:
SK018 ignoring produced_by_role, and SK021 ignoring detection_layer. Both were
false. The current sk_lint.py honors both fields, and running it produces zero
findings for either rule.

The builder was working against an older copy. Three files the task frame
depended on were also absent from the tree it received. Nothing in the handoff
pinned the tree state, so a report written in good faith described a codebase
that no longer existed, and four hours of work were judged against the wrong
contract.

No gate caught it, because there was no gate. This is the gate.

A pin is a sorted manifest of every governed file with its SHA-256, plus a
single tree hash over that manifest. A task frame carries the tree hash it was
written against. The builder verifies before starting. The report carries the
tree hash the work was done against. If the two differ, every finding in the
report is about a different codebase and must be re-derived, not argued about.

Usage:
    sk_handoff.py pin   [--root DIR] [--out TREE.sha256]
    sk_handoff.py check [--root DIR] [--pin TREE.sha256] [--json]
    sk_handoff.py hash  [--root DIR]          # tree hash only, for scripting

Exit codes: 0 tree matches the pin · 1 tree diverges · 3 unreadable
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

# Files under governance. Generated corpora are excluded deliberately: they are
# reproducible from their generators, and pinning them would make every
# regeneration look like tampering.
GOVERNED_SUFFIXES = (".py", ".yaml", ".yml", ".md", ".json", ".jsonl", ".toml", ".txt")
GOVERNED_NAMES = ("Makefile", "VERSION")

EXCLUDE_DIRS = {
    "__pycache__", ".git", ".github",
    "redcorpus", "examples", "runs", "schemas",  # generated
}
# Records ABOUT the tree, which cannot be inside the hash they carry. TREE.sha256
# holds the manifest and its own tree hash; RELEASES.md holds the tree hash of
# each release. Either one, if governed, would have to contain a digest computed
# over a file containing that digest — a fixed point SHA-256 does not offer.
# This is the same self-reference that keeps a tree pin out of the task frame it
# pins, and that made known_programs circular in ENTRY 009.
EXCLUDE_NAMES = {"TREE.sha256", "RELEASES.md"}


def governed_files(root: str) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for fn in sorted(filenames):
            if fn in EXCLUDE_NAMES:
                continue
            if not (fn.endswith(GOVERNED_SUFFIXES) or fn in GOVERNED_NAMES):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            out.append(rel)
    return sorted(out)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(root: str) -> tuple[dict[str, str], str]:
    manifest = {rel: sha256_file(os.path.join(root, rel)) for rel in governed_files(root)}
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return manifest, hashlib.sha256(canonical).hexdigest()


def read_pin(path: str) -> tuple[dict[str, str], str]:
    manifest: dict[str, str] = {}
    tree_hash = ""
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            if line.startswith("# tree_sha256:"):
                tree_hash = line.split(":", 1)[1].strip()
            continue
        digest, _, rel = line.partition("  ")
        if rel:
            manifest[rel] = digest
    return manifest, tree_hash


def cmd_pin(args) -> int:
    manifest, tree_hash = build_manifest(args.root)
    lines = [
        "# Solomon's Key tree pin.",
        "# Every governed file with its SHA-256, plus a tree hash over the sorted manifest.",
        "# Generated corpora (schemas/ examples/ runs/ redcorpus/) are excluded: they are",
        "# reproducible from their generators, so pinning them would make regeneration",
        "# indistinguishable from tampering.",
        f"# files: {len(manifest)}",
        f"# tree_sha256: {tree_hash}",
        "",
    ]
    lines += [f"{h}  {rel}" for rel, h in sorted(manifest.items())]
    text = "\n".join(lines) + "\n"
    if args.out == "-":
        sys.stdout.write(text)
    else:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"sk-handoff: pinned {len(manifest)} files -> {args.out}")
    print(f"  tree_sha256 {tree_hash}")
    return 0


def cmd_check(args) -> int:
    if not os.path.exists(args.pin):
        sys.stderr.write(f"sk-handoff: no pin at {args.pin}\n")
        return 3
    pinned, pinned_hash = read_pin(args.pin)
    actual, actual_hash = build_manifest(args.root)

    missing = sorted(set(pinned) - set(actual))
    added = sorted(set(actual) - set(pinned))
    changed = sorted(f for f in set(pinned) & set(actual) if pinned[f] != actual[f])
    matches = actual_hash == pinned_hash

    if args.json:
        print(json.dumps({
            "pin": args.pin,
            "pinned_tree_sha256": pinned_hash,
            "actual_tree_sha256": actual_hash,
            "matches": matches,
            "missing": missing,
            "changed": changed,
            "added": added,
        }, indent=2))
    else:
        print(f"sk-handoff check {args.root}")
        print(f"  pinned  {pinned_hash}")
        print(f"  actual  {actual_hash}")
        print()
        if matches:
            print(f"  tree matches the pin ({len(actual)} files)")
        else:
            for f in missing:
                print(f"  MISSING  {f}")
                print("           the task frame depends on this file and the tree does not have it")
            for f in changed:
                print(f"  CHANGED  {f}")
                print(f"           pinned {pinned[f][:12]}…, actual {actual[f][:12]}…")
            for f in added:
                print(f"  ADDED    {f}")
            print()
            print(f"  {len(missing)} missing, {len(changed)} changed, {len(added)} added")
            print("\n  Findings derived against this tree are about a different codebase.")
            print("  Re-derive them; do not argue about them.")
    return 0 if matches else 1


# Files a drop must contain to be workable. A partial drop is the documented
# cause of two rounds of false findings: a builder reports accurately about a
# tree that is missing the thing the task depends on. Checking this at RECEIPT
# costs one command; discovering it after the work costs the whole pass.
# Files that are not code and cannot be derived from invocation: the contract,
# the witness, the license, the trust root. Everything else is DERIVED below.
EXPLICIT_REQUIRED = {
    "contract": ["key.yaml", "key.repaired.yaml"],
    "witness": [
        "ledger/solomons-key-builder-ledger.jsonl", "ledger/HEAD",
        "ledger/audit-solomons-key.jsonl", "ledger/AUDIT_HEAD",
    ],
    "trust root": ["TRUSTED_PROGRAMS.sha256", "TRUST_BOUNDARY.md"],
    "build": ["build.py", "Makefile", "TREE.sha256", "VERSION"],
    "packaging": ["pyproject.toml", "requirements.txt", "LICENSE", ".gitignore"],
}


def derive_required(root: str) -> dict[str, list[str]]:
    """Compute the required-file set from what the build actually invokes.

    A hand-maintained list is a second copy of the truth, and it drifts. It did:
    the v0.6.0 release shipped `test_sk_emit.py` and not `sk_emit.py`, so the
    completeness gate passed on a tree that could not possibly pass its own
    acceptance test. The list did not pin the thing the next pass needed,
    because nothing connected the list to the build.

    So: read build.py for every script its targets invoke, read every test suite
    for the modules it imports and the scripts it shells out to, and require
    exactly that. Add a tool to the build and it becomes required automatically.
    """
    required: dict[str, list[str]] = {k: list(v) for k, v in EXPLICIT_REQUIRED.items()}

    build_path = os.path.join(root, "build.py")
    invoked: set[str] = set()
    if os.path.exists(build_path):
        src = open(build_path, encoding="utf-8").read()
        # Step(...) argv lists and direct references to <name>.py
        invoked |= set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*\.py)"', src))
        invoked.discard("build.py")

    tests: set[str] = set()
    tools: set[str] = set()
    for name in sorted(invoked):
        (tests if name.startswith("test_") else tools).add(name)

    # Whatever the suites import or invoke is required too — this is what
    # catches sk_emit.py: test_sk_emit.py names it, so it cannot be omitted.
    for name in sorted(tests):
        path = os.path.join(root, name)
        if not os.path.exists(path):
            continue
        src = open(path, encoding="utf-8").read()
        for mod in re.findall(r"^\s*import\s+(sk_[a-z_]+)", src, re.M):
            tools.add(f"{mod}.py")
        for ref in re.findall(r'"([A-Za-z_][A-Za-z0-9_]*\.py)"', src):
            tools.add(ref)
        for ref in re.findall(r'\b([A-Za-z_][A-Za-z0-9_]*\.py)\b', src):
            if ref.startswith(("sk_", "gen_", "repair_")):
                tools.add(ref)
    tools -= tests

    required["verifier and emitters"] = sorted(t for t in tools if t.startswith("sk_"))
    required["generators"] = sorted(t for t in tools if not t.startswith("sk_"))
    required["test suites"] = sorted(tests)
    return {k: v for k, v in required.items() if v}


def cmd_verify_drop(args) -> int:
    """Run this FIRST on any received drop, before starting work."""
    required = derive_required(args.root)
    missing: list[tuple[str, str]] = []
    present = 0
    for group, files in required.items():
        for rel in files:
            if os.path.exists(os.path.join(args.root, rel)):
                present += 1
            else:
                missing.append((group, rel))

    print(f"sk-handoff verify-drop {args.root}")
    print(f"  {present} of {present + len(missing)} required files present")
    print()

    if missing:
        current = None
        for group, rel in missing:
            if group != current:
                print(f"  MISSING — {group}")
                current = group
            print(f"    {rel}")
        print()
        print("  This drop is incomplete. Findings derived from it will be about")
        print("  a codebase that does not exist. Request a complete drop before")
        print("  starting work; do not work around the gaps.")
        return 1

    pin = os.path.join(args.root, "TREE.sha256")
    pinned, pinned_hash = read_pin(pin)
    _, actual_hash = build_manifest(args.root)
    if actual_hash != pinned_hash:
        print(f"  pinned  {pinned_hash}")
        print(f"  actual  {actual_hash}")
        print()
        print("  Drop is complete but does not match its own pin. Run 'check' for detail.")
        return 1

    print(f"  tree_sha256 {actual_hash}")
    print("  drop is complete and matches its pin — safe to start work")
    print()
    print("  Record this tree hash in your report. If it differs from the one in")
    print("  the task frame, every finding must be re-derived.")
    return 0


def cmd_hash(args) -> int:
    _, tree_hash = build_manifest(args.root)
    print(tree_hash)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sk-handoff", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pin")
    p.add_argument("--root", default=".")
    p.add_argument("--out", default="TREE.sha256")
    p.set_defaults(fn=cmd_pin)

    p = sub.add_parser("check")
    p.add_argument("--root", default=".")
    p.add_argument("--pin", default="TREE.sha256")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("verify-drop", help="run first on a received drop")
    p.add_argument("--root", default=".")
    p.set_defaults(fn=cmd_verify_drop)

    p = sub.add_parser("hash")
    p.add_argument("--root", default=".")
    p.set_defaults(fn=cmd_hash)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        os._exit(0)
