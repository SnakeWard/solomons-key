#!/usr/bin/env python3
"""
test_sk_ledger.py — can the witness be caught lying?

Six tamper shapes are constructed against a good ledger. Five must be caught.
One must NOT be caught by the chain alone, and is asserted to be caught only
when an external anchor is supplied.

That last case is the point of the file. A tamper-evidence claim that has
never been shown its own blind spot is the same category of unearned
confidence this whole toolchain exists to remove.

Run:  python3 test_sk_ledger.py
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import sys
import tempfile

import sk_ledger as L

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))


def build_good(n: int = 5) -> list[dict]:
    entries = []
    prev = L.GENESIS_PREV
    e = L.make_entry(
        0, prev, pass_id="GENESIS", pass_name="Ledger genesis", actor="user",
        actor_role="final_authority", entry_type="genesis", result="pass",
        timestamp="2026-01-01T00:00:00Z",
    )
    entries.append(e)
    prev = e[L.HASH_FIELD]
    for i in range(1, n):
        e = L.make_entry(
            i, prev, pass_id=f"PASS_{i:02d}", pass_name=f"Pass {i}",
            actor="Codex", actor_role="builder", entry_type="validation_run",
            result="pass", timestamp="2026-01-01T00:00:00Z",
            gate_decisions=[{"gate_id": "schema_validation_gate", "decision": "pass"}],
        )
        entries.append(e)
        prev = e[L.HASH_FIELD]
    return entries


def rechain(entries: list[dict]) -> list[dict]:
    """Recompute seq, prev_hash and entry_hash across the whole chain.

    This is the attacker who does the work properly.
    """
    out = []
    prev = L.GENESIS_PREV
    for i, e in enumerate(entries):
        e = dict(e)
        e["seq"] = i
        e["prev_hash"] = prev
        e.pop(L.HASH_FIELD, None)
        e[L.HASH_FIELD] = L.compute_hash(e)
        out.append(L.order(e))
        prev = e[L.HASH_FIELD]
    return out


# --- tamper shapes ---------------------------------------------------


def t_content(entries):
    """Flip a recorded result from fail to pass without touching hashes."""
    e = copy.deepcopy(entries)
    e[3]["result"] = "pass_but_actually_edited"
    return e


def t_delete(entries):
    """Remove an inconvenient entry."""
    e = copy.deepcopy(entries)
    del e[2]
    return e


def t_insert(entries):
    """Splice in an entry that was never appended."""
    e = copy.deepcopy(entries)
    forged = copy.deepcopy(e[2])
    forged["pass_id"] = "PASS_FORGED"
    e.insert(2, forged)
    return e


def t_reorder(entries):
    """Swap two entries to change the apparent order of events."""
    e = copy.deepcopy(entries)
    e[2], e[3] = e[3], e[2]
    return e


def t_truncate(entries):
    """Drop the tail — hide everything after a point."""
    return copy.deepcopy(entries[:3])


def t_forged_actor(entries):
    """Record an actor outside the declared bounded set."""
    e = copy.deepcopy(entries)
    e[2]["actor"] = "AnonymousShell"
    e[2][L.HASH_FIELD] = L.compute_hash(e[2])
    return e


def t_rechained(entries):
    """The real attack: edit, then recompute the entire chain.

    Internally consistent. Undetectable without an external anchor.
    """
    e = copy.deepcopy(entries)
    e[3]["result"] = "pass"
    e[3]["notes"] = "history rewritten"
    return rechain(e)


CAUGHT_BY_CHAIN = [
    ("content_tamper", t_content, "content_tamper"),
    ("entry_deleted", t_delete, "chain_break"),
    ("entry_inserted", t_insert, "chain_break"),
    ("entries_reordered", t_reorder, "chain_break"),
    ("unbounded_actor", t_forged_actor, "unknown_actor"),
]


def main() -> int:
    good = build_good()

    # --- baseline ----------------------------------------------------
    check("good_chain_verifies", not L.verify(good))
    check(
        "good_chain_matches_its_own_head",
        not L.verify(good, expect_head=good[-1][L.HASH_FIELD]),
    )

    # --- tampering the chain catches ---------------------------------
    for name, fn, expected_kind in CAUGHT_BY_CHAIN:
        breaks = L.verify(fn(good))
        kinds = {b.kind for b in breaks}
        check(
            f"detects_{name}",
            expected_kind in kinds,
            "" if expected_kind in kinds else f"expected {expected_kind}, got {sorted(kinds) or 'nothing'}",
        )

    # --- truncation: chain alone cannot see it, anchor can ------------
    trunc = t_truncate(good)
    check(
        "truncation_invisible_to_chain_alone",
        not L.verify(trunc),
        "chain unexpectedly reported a break; the honest limitation may have changed",
    )
    breaks = L.verify(trunc, expect_head=good[-1][L.HASH_FIELD])
    check(
        "detects_truncation_with_anchor",
        any(b.kind == "head_mismatch" for b in breaks),
    )

    # --- re-chaining: the blind spot ---------------------------------
    rc = t_rechained(good)
    check(
        "rechained_history_passes_without_anchor",
        not L.verify(rc),
        "if this fails, the documented limitation is wrong and README must be corrected",
    )
    breaks = L.verify(rc, expect_head=good[-1][L.HASH_FIELD])
    check(
        "detects_rechained_history_with_anchor",
        any(b.kind == "head_mismatch" for b in breaks),
    )

    # --- append refuses to extend a broken chain ---------------------
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "led.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for e in t_content(good):
                fh.write(json.dumps(e) + "\n")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc_append = L.main(["append", path, "--pass", "PASS_99", "--actor", "Codex"])
        check("append_refuses_broken_chain", rc_append == 1, f"exit {rc_append}")

    # --- round trip through disk -------------------------------------
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "led.jsonl")
        with contextlib.redirect_stdout(io.StringIO()):
            L.main(["init", path])
            L.main(["append", path, "--pass", "PASS_01", "--actor", "Codex",
                    "--type", "validation_run", "--gate", "schema_validation_gate=pass"])
            L.main(["append", path, "--pass", "PASS_02", "--actor", "Grok",
                    "--type", "audit_run"])
        entries = L.read_chain(path)
        check("disk_roundtrip_three_entries", len(entries) == 3, f"got {len(entries)}")
        check("disk_roundtrip_verifies", not L.verify(entries))
        check("head_command_matches_last_hash", L.head_hash(path) == entries[-1][L.HASH_FIELD])

    # --- report -------------------------------------------------------
    width = max(len(n) for _, n, _ in results)
    for status, name, detail in results:
        line = f"  {status}  {name.ljust(width)}"
        if detail and status == FAIL:
            line += f"   {detail}"
        print(line)
    failed = sum(1 for s, _, _ in results if s == FAIL)
    print(f"\n  {len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
