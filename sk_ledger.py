#!/usr/bin/env python3
"""
sk_ledger.py — the witness, made checkable.

The KEY file classifies `ledger_tamper_attempt` as critical with response
`block`, declares the ledger append-only, and states that prior entries must
not be rewritten. Nothing enforced any of it: a JSONL file on disk is exactly
as append-only as whoever holds the file handle.

This makes rewriting *evident*. Each entry carries the hash of the entry
before it. Changing, deleting, reordering, or inserting anything breaks the
chain at that point and every point after it.

WHAT THIS DOES AND DOES NOT PROVE
---------------------------------
A hash chain detects tampering by anyone who does not re-chain. It does NOT
detect an attacker who rewrites an entry and recomputes every subsequent hash
— that produces an internally valid chain.

Catching that requires an anchor held outside the file: the head hash,
published somewhere the attacker does not control. `verify --expect-head`
takes one. In practice the anchor is your git history — commit the ledger,
and the commit hash pins the head. State this limitation plainly rather than
implying the chain alone is sufficient.

Usage:
    sk_ledger.py init    <ledger.jsonl>
    sk_ledger.py append  <ledger.jsonl> --entry entry.json
    sk_ledger.py append  <ledger.jsonl> --pass PASS_18 --name "..." \
                         --actor Codex --type validation_run --result pass \
                         [--artifact path=/f.json] [--gate gid=pass] [--note "..."]
    sk_ledger.py verify  <ledger.jsonl> [--expect-head HASH] [--json]
    sk_ledger.py head    <ledger.jsonl>
    sk_ledger.py seed    <ledger.jsonl> --from-key key.yaml

Exit codes:
    0  chain intact (and head matched, if --expect-head given)
    1  chain broken or head mismatch
    3  file unreadable
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

GENESIS_PREV = "0" * 64
HASH_FIELD = "entry_hash"

# Field order is fixed so entries are readable and diffs are stable. The hash
# is computed over sorted keys regardless, so order here is cosmetic only.
FIELD_ORDER = [
    "seq",
    "ledger_class",
    "timestamp",
    "pass_id",
    "pass_name",
    "actor",
    "actor_role",
    "entry_type",
    "route_id",
    "result",
    "gate_decisions",
    "artifacts",
    "notes",
    "prev_hash",
    "entry_hash",
]

# From the KEY file's declared ledger entry types, plus lifecycle entries.
ENTRY_TYPES = {
    "genesis",
    "validation_run",
    "repair_run",
    "audit_run",
    "escalation_recorded",
    "pass_complete",
}

BOUNDED_ACTORS = {"Codex", "Grok", "Claude", "user"}


def canonical(entry: dict[str, Any]) -> bytes:
    """Deterministic serialization for hashing. entry_hash is excluded."""
    body = {k: v for k, v in entry.items() if k != HASH_FIELD}
    return json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def compute_hash(entry: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(entry)).hexdigest()


def order(entry: dict[str, Any]) -> dict[str, Any]:
    out = {k: entry[k] for k in FIELD_ORDER if k in entry}
    for k, v in entry.items():
        if k not in out:
            out[k] = v
    return out


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------
# read / write
# ---------------------------------------------------------------------


def read_chain(path: str) -> list[dict[str, Any]]:
    entries = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"sk-ledger: line {lineno} is not valid JSON: {exc}")
    return entries


def append_line(path: str, entry: dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(order(entry), ensure_ascii=False) + "\n")


def head_hash(path: str) -> str:
    entries = read_chain(path)
    return entries[-1][HASH_FIELD] if entries else GENESIS_PREV


# ---------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------


@dataclass
class Break:
    seq: Any
    line: int
    kind: str
    detail: str


def verify(entries: list[dict[str, Any]], expect_head: str | None = None) -> list[Break]:
    breaks: list[Break] = []
    prev = GENESIS_PREV

    for i, e in enumerate(entries):
        line = i + 1
        seq = e.get("seq", "?")

        for field in ("seq", "prev_hash", HASH_FIELD, "entry_type"):
            if field not in e:
                breaks.append(Break(seq, line, "missing_field", f"entry lacks '{field}'"))

        if HASH_FIELD not in e or "prev_hash" not in e:
            prev = e.get(HASH_FIELD, prev)
            continue

        if e.get("seq") != i:
            breaks.append(
                Break(seq, line, "sequence_break",
                      f"expected seq {i}, found {e.get('seq')} — entry deleted, inserted, or reordered")
            )

        if e["prev_hash"] != prev:
            breaks.append(
                Break(seq, line, "chain_break",
                      f"prev_hash {e['prev_hash'][:12]}… does not match preceding entry_hash {prev[:12]}…")
            )

        recomputed = compute_hash(e)
        if recomputed != e[HASH_FIELD]:
            breaks.append(
                Break(seq, line, "content_tamper",
                      f"entry content does not match its hash (recomputed {recomputed[:12]}…, stored {e[HASH_FIELD][:12]}…)")
            )

        et = e.get("entry_type")
        if et and et not in ENTRY_TYPES:
            breaks.append(Break(seq, line, "unknown_entry_type", f"entry_type '{et}' is not declared"))

        actor = e.get("actor")
        if actor and actor not in BOUNDED_ACTORS:
            breaks.append(Break(seq, line, "unknown_actor", f"actor '{actor}' is not a declared bounded actor"))

        prev = e[HASH_FIELD]

    if expect_head is not None:
        actual = entries[-1][HASH_FIELD] if entries else GENESIS_PREV
        if actual != expect_head:
            breaks.append(
                Break("head", len(entries), "head_mismatch",
                      f"head is {actual[:12]}…, anchor expects {expect_head[:12]}… — "
                      "the chain may have been rewritten and re-chained")
            )

    return breaks


# ---------------------------------------------------------------------
# build
# ---------------------------------------------------------------------


def make_entry(
    seq: int,
    prev: str,
    *,
    pass_id: str,
    pass_name: str = "",
    actor: str = "user",
    actor_role: str = "",
    entry_type: str = "pass_complete",
    result: str = "pass",
    route_id: str | None = None,
    gate_decisions: list[dict] | None = None,
    artifacts: list[dict] | None = None,
    notes: str = "",
    timestamp: str | None = None,
) -> dict[str, Any]:
    e: dict[str, Any] = {
        "seq": seq,
        "timestamp": timestamp or now(),
        "pass_id": pass_id,
        "pass_name": pass_name,
        "actor": actor,
        "actor_role": actor_role,
        "entry_type": entry_type,
        "route_id": route_id,
        "result": result,
        "gate_decisions": gate_decisions or [],
        "artifacts": artifacts or [],
        "notes": notes,
        "prev_hash": prev,
    }
    e[HASH_FIELD] = compute_hash(e)
    return order(e)


# ---------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------


LEDGER_CLASSES = ("audit", "fixture")


def cmd_init(args) -> int:
    if os.path.exists(args.ledger) and os.path.getsize(args.ledger) > 0:
        sys.stderr.write(f"sk-ledger: {args.ledger} already exists and is non-empty; refusing to reinitialize\n")
        return 1
    os.makedirs(os.path.dirname(os.path.abspath(args.ledger)), exist_ok=True)
    e = make_entry(
        0, GENESIS_PREV,
        pass_id="GENESIS",
        pass_name="Ledger genesis",
        actor="user",
        actor_role="final_authority",
        entry_type="genesis",
        result="pass",
        notes="Append-only witness ledger initialized. Prior entries must not be rewritten.",
    )
    e["ledger_class"] = args.ledger_class
    e[HASH_FIELD] = compute_hash(e)
    e = order(e)
    append_line(args.ledger, e)
    print(f"sk-ledger: initialized {args.ledger}")
    print(f"  head {e[HASH_FIELD]}")
    return 0


def cmd_append(args) -> int:
    entries = read_chain(args.ledger) if os.path.exists(args.ledger) else []
    breaks = verify(entries)
    if breaks:
        sys.stderr.write("sk-ledger: refusing to append to a broken chain. Run verify.\n")
        return 1

    seq = len(entries)
    prev = entries[-1][HASH_FIELD] if entries else GENESIS_PREV

    if args.entry:
        payload = json.load(open(args.entry, encoding="utf-8"))
        payload.pop("seq", None)
        payload.pop("prev_hash", None)
        payload.pop(HASH_FIELD, None)
        e = make_entry(seq, prev, **payload)
    else:
        artifacts = []
        for spec in args.artifact or []:
            aid, _, path = spec.partition("=")
            if not path:
                sys.stderr.write(f"sk-ledger: --artifact expects id=path, got '{spec}'\n")
                return 1
            if not os.path.exists(path):
                sys.stderr.write(f"sk-ledger: artifact file not found: {path}\n")
                return 1
            artifacts.append({"artifact_id": aid, "path": path, "sha256": sha256_file(path)})

        gates = []
        for spec in args.gate or []:
            gid, _, decision = spec.partition("=")
            gates.append({"gate_id": gid, "decision": decision or "pass"})

        e = make_entry(
            seq, prev,
            pass_id=args.pass_id,
            pass_name=args.name or "",
            actor=args.actor,
            actor_role=args.actor_role or "",
            entry_type=args.type,
            result=args.result,
            route_id=args.route,
            gate_decisions=gates,
            artifacts=artifacts,
            notes=args.note or "",
            timestamp=args.timestamp,
        )

    append_line(args.ledger, e)
    print(f"sk-ledger: appended seq {e['seq']} ({e['pass_id']})")
    print(f"  head {e[HASH_FIELD]}")
    return 0


def cmd_verify(args) -> int:
    try:
        entries = read_chain(args.ledger)
    except FileNotFoundError:
        sys.stderr.write(f"sk-ledger: no such file: {args.ledger}\n")
        return 3

    breaks = verify(entries, args.expect_head)

    if args.json:
        print(json.dumps({
            "ledger": args.ledger,
            "entries": len(entries),
            "head": entries[-1][HASH_FIELD] if entries else GENESIS_PREV,
            "intact": not breaks,
            "breaks": [b.__dict__ for b in breaks],
        }, indent=2))
    else:
        print(f"sk-ledger verify {args.ledger}")
        print(f"  {len(entries)} entries")
        cls = (entries[0].get("ledger_class") if entries else None)
        if cls:
            print(f"  class {cls}" + ("  (seeded demonstration — not an audit record)"
                                       if cls == "fixture" else ""))
        if not breaks:
            print(f"  head {entries[-1][HASH_FIELD] if entries else GENESIS_PREV}")
            print("  chain intact")
            if args.expect_head:
                print("  head matches anchor")
        else:
            for b in breaks:
                print(f"  BREAK  seq={b.seq} line={b.line}  {b.kind}")
                print(f"         {b.detail}")
            print(f"\n  {len(breaks)} break(s) — the ledger has been rewritten")
        if not args.expect_head and not breaks:
            print("\n  note: no --expect-head anchor given. A chain that was rewritten")
            print("        and fully re-chained would still verify. Anchor the head.")

    return 1 if breaks else 0


def common_prefix(a: list[dict], b: list[dict]) -> int:
    """Length of the shared history between two ledger lineages."""
    n = 0
    for x, y in zip(a, b):
        if x.get(HASH_FIELD) != y.get(HASH_FIELD):
            break
        n += 1
    return n


def cmd_reconcile(args) -> int:
    """Detect and describe a fork between two ledger lineages.

    A hash chain proves nothing was rewritten WITHIN a lineage. It says nothing
    about WHICH lineage is the record. Two actors appending to copies of the
    same ledger produce two chains that each verify perfectly and disagree about
    history. Internal consistency is not authority — the same lesson as the
    re-chaining blind spot, arriving from a different direction.
    """
    a = read_chain(args.canonical)
    b = read_chain(args.other)

    for name, chain in ((args.canonical, a), (args.other, b)):
        breaks = verify(chain)
        if breaks:
            print(f"sk-ledger: {name} is itself broken ({breaks[0].kind} at seq {breaks[0].seq})")
            print("  reconcile requires two internally valid chains. Fix this first.")
            return 1

    n = common_prefix(a, b)
    a_tail, b_tail = a[n:], b[n:]

    # Distinguish a fork from a schema migration. If the entries carry the same
    # content but different hashes, nobody forked — an envelope field changed
    # and re-chained the lineage. Reporting that as "no common ancestor" is
    # technically true and diagnostically useless: it sends the operator to
    # graft, which would duplicate every entry.
    def content(e: dict) -> str:
        skip = {"prev_hash", HASH_FIELD, "seq"}
        return json.dumps({k: v for k, v in e.items() if k not in skip},
                          sort_keys=True, separators=(",", ":"))

    if n == 0 and a and b:
        # Same events in the same order, different hashes, from entry zero:
        # nobody forked. An envelope field changed and re-chained the lineage.
        # Comparing the event sequence is the right signal — a match count with
        # an arbitrary threshold was the first attempt here and it missed this
        # exact case, because two unrelated tail entries dragged it under.
        def events(chain):
            return [e.get("pass_id") or e.get("entry_type") for e in chain]

        ea, eb = events(a), events(b)
        shared_events = 0
        for x, y in zip(ea, eb):
            if x != y:
                break
            shared_events += 1

        if shared_events >= 2:
            differing = sorted({
                k for x, y in list(zip(a, b))[:shared_events]
                for k in set(x) | set(y)
                if k not in ("prev_hash", HASH_FIELD, "seq", "timestamp", "notes")
                and x.get(k) != y.get(k)
            })
            print("  SCHEMA MIGRATION, not a fork.")
            print(f"  The first {shared_events} events are identical in name and order,")
            print("  but every hash differs from entry zero.")
            if differing:
                print(f"  Envelope fields that changed: {', '.join(differing)}")
            print()
            print("  A field was added or changed, which re-chained the whole lineage.")
            print("  Do NOT graft — there is no common prefix, so grafting would replay")
            print("  every entry and duplicate the history.")

            only_b = [e for e in b if (e.get("pass_id") or e.get("entry_type")) not in set(ea)]
            only_a = [e for e in a if (e.get("pass_id") or e.get("entry_type")) not in set(eb)]
            if only_b:
                print()
                print(f"  Real history present ONLY in {os.path.basename(args.other)} — "
                      "this is what a reseed would destroy:")
                for e in only_b:
                    print(f"    seq {e['seq']:>3}  {e.get('pass_id','?'):<20} "
                          f"{e.get('entry_type','?'):<18} {e[HASH_FIELD][:12]}…")
            if only_a:
                print()
                print(f"  Present only in {os.path.basename(args.canonical)}:")
                for e in only_a:
                    print(f"    seq {e['seq']:>3}  {e.get('pass_id','?'):<20} "
                          f"{e.get('entry_type','?'):<18} {e[HASH_FIELD][:12]}…")
            print()
            print("  Migrate forward by appending the unique entries to the canonical")
            print("  chain — do not discard either lineage until that is done.")
            return 1

    print(f"sk-ledger reconcile")
    print(f"  canonical  {args.canonical}  ({len(a)} entries, head {(a[-1][HASH_FIELD] if a else GENESIS_PREV)[:12]}…)")
    print(f"  other      {args.other}  ({len(b)} entries, head {(b[-1][HASH_FIELD] if b else GENESIS_PREV)[:12]}…)")
    print()

    if not a_tail and not b_tail:
        print(f"  identical — {n} shared entries, no fork")
        return 0

    print(f"  shared history: {n} entries", end="")
    if n:
        print(f", through seq {a[n-1]['seq']} ({a[n-1].get('pass_id')}) {a[n-1][HASH_FIELD][:12]}…")
    else:
        print(" — no common ancestor at all")
    print(f"  FORK at seq {n}")
    print()

    if a_tail:
        print(f"  only in canonical ({len(a_tail)}):")
        for e in a_tail:
            print(f"    seq {e['seq']:>3}  {e.get('pass_id','?'):<20} {e.get('entry_type','?'):<18} {e[HASH_FIELD][:12]}…")
    if b_tail:
        print(f"  only in other ({len(b_tail)}):")
        for e in b_tail:
            print(f"    seq {e['seq']:>3}  {e.get('pass_id','?'):<20} {e.get('entry_type','?'):<18} {e[HASH_FIELD][:12]}…")

    print()
    print("  Both lineages verify. Neither is wrong about its own contents; they")
    print("  disagree about what happened. Resolve by grafting the divergent tail")
    print("  onto the canonical head:")
    print(f"    sk_ledger.py graft {args.canonical} --from {args.other} --out merged.jsonl")
    return 1


def cmd_graft(args) -> int:
    """Replay another lineage's divergent tail onto the canonical head.

    Grafting re-chains the moved entries, so their hashes change. Each grafted
    entry records `grafted_from` — its hash in the original lineage — so the
    original position stays auditable. Content is preserved; only position
    changes, and the change is on the record.
    """
    canonical = read_chain(args.canonical)
    other = read_chain(args.source)

    for name, chain in ((args.canonical, canonical), (args.source, other)):
        if verify(chain):
            sys.stderr.write(f"sk-ledger: {name} is broken; refusing to graft\n")
            return 1

    n = common_prefix(canonical, other)
    tail = other[n:]
    if not tail:
        print("sk-ledger: nothing to graft — other lineage has no divergent entries")
        return 0

    merged = list(canonical)
    prev = merged[-1][HASH_FIELD] if merged else GENESIS_PREV
    seq = len(merged)
    grafted = []
    for e in tail:
        body = {k: v for k, v in e.items() if k not in ("seq", "prev_hash", HASH_FIELD)}
        body["grafted_from"] = e[HASH_FIELD]
        body["notes"] = (body.get("notes") or "") + \
            f" [grafted from {os.path.basename(args.source)} seq {e['seq']}]"
        new = dict(body)
        new["seq"] = seq
        new["prev_hash"] = prev
        new[HASH_FIELD] = compute_hash(new)
        merged.append(order(new))
        grafted.append((e[HASH_FIELD], new[HASH_FIELD]))
        prev, seq = new[HASH_FIELD], seq + 1

    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        for e in merged:
            fh.write(json.dumps(order(e), ensure_ascii=False) + "\n")

    print(f"sk-ledger: grafted {len(grafted)} entries -> {args.out}")
    for old, new in grafted:
        print(f"  {old[:12]}… -> {new[:12]}…")
    print(f"  head {prev}")
    print("\n  Re-anchor before this means anything:")
    print(f"    cp {args.out} <canonical path> && sk_ledger.py head <canonical path> > ledger/HEAD")
    return 0


def cmd_head(args) -> int:
    print(head_hash(args.ledger))
    return 0


def cmd_seed(args) -> int:
    """Build a real chained ledger from the KEY file's own declared pass history."""
    try:
        import yaml
    except ImportError:
        sys.stderr.write("sk-ledger: seed requires PyYAML\n")
        return 3
    doc = yaml.safe_load(open(args.from_key, encoding="utf-8"))
    summary = (doc.get("ledger") or {}).get("ledger_summary") or []
    if not summary:
        sys.stderr.write("sk-ledger: KEY file declares no ledger_summary to seed from\n")
        return 1
    if os.path.exists(args.ledger) and os.path.getsize(args.ledger) > 0:
        sys.stderr.write(f"sk-ledger: {args.ledger} is non-empty; refusing to seed over it\n")
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(args.ledger)) or ".", exist_ok=True)
    prev = GENESIS_PREV
    seq = 0
    stamp = "2026-01-01T00:00:00Z"  # deterministic; these are historical passes

    e = make_entry(
        seq, prev,
        pass_id="GENESIS", pass_name="Ledger genesis", actor="user",
        actor_role="final_authority", entry_type="genesis", result="pass",
        notes=f"Seeded from {os.path.basename(args.from_key)} ledger_summary. "
              "Historical passes recorded retroactively; timestamps are nominal.",
        timestamp=stamp,
    )
    # A seeded chain is a demonstration, not a witness. Entries were written
    # after the fact by a program reading a summary, so nothing here observed
    # anything happen. Recording that structurally — not in prose — is what
    # stops a fixture chain being cited as an audit record.
    e["ledger_class"] = "fixture"
    e[HASH_FIELD] = compute_hash(e)
    e = order(e)
    append_line(args.ledger, e)
    prev, seq = e[HASH_FIELD], seq + 1

    for p in summary:
        e = make_entry(
            seq, prev,
            pass_id=p.get("pass_id", "?"),
            pass_name=p.get("pass_name", ""),
            actor="Codex",
            actor_role="builder",
            entry_type="pass_complete",
            result="pass" if p.get("status") == "complete" else str(p.get("status")),
            notes="Retroactive record. Not independently witnessed at the time.",
            timestamp=stamp,
        )
        append_line(args.ledger, e)
        prev, seq = e[HASH_FIELD], seq + 1

    print(f"sk-ledger: seeded {args.ledger} with {seq} entries")
    print(f"  head {prev}")
    print("  anchor this head before it means anything:")
    print(f"    git add {args.ledger} && git commit -m 'ledger: seed, head {prev[:12]}'")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sk-ledger", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("ledger")
    p.add_argument("--class", dest="ledger_class", default="audit", choices=LEDGER_CLASSES,
                   help="audit = witnesses real events; fixture = seeded demonstration")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("append")
    p.add_argument("ledger")
    p.add_argument("--entry", help="JSON file containing the entry body")
    p.add_argument("--pass", dest="pass_id", default="")
    p.add_argument("--name", default="")
    p.add_argument("--actor", default="user", choices=sorted(BOUNDED_ACTORS))
    p.add_argument("--actor-role", default="")
    p.add_argument("--type", default="pass_complete", choices=sorted(ENTRY_TYPES))
    p.add_argument("--result", default="pass")
    p.add_argument("--route", default=None)
    p.add_argument("--artifact", action="append", metavar="ID=PATH")
    p.add_argument("--gate", action="append", metavar="GATE_ID=DECISION")
    p.add_argument("--note", default="")
    p.add_argument("--timestamp", default=None,
                   help="pin the timestamp (demonstration chains only; real appends use the clock)")
    p.set_defaults(fn=cmd_append)

    p = sub.add_parser("verify")
    p.add_argument("ledger")
    p.add_argument("--expect-head", default=None, help="externally held anchor hash")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("head"); p.add_argument("ledger"); p.set_defaults(fn=cmd_head)

    p = sub.add_parser("reconcile", help="detect a fork between two lineages")
    p.add_argument("canonical")
    p.add_argument("other")
    p.set_defaults(fn=cmd_reconcile)

    p = sub.add_parser("graft", help="replay a divergent tail onto the canonical head")
    p.add_argument("canonical")
    p.add_argument("--source", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_graft)

    p = sub.add_parser("seed")
    p.add_argument("ledger")
    p.add_argument("--from-key", required=True)
    p.set_defaults(fn=cmd_seed)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # downstream closed the pipe (e.g. `| head`); not an error
        os._exit(0)
