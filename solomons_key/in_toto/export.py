"""Read an existing Key run directory and emit an in-toto statement.

Does not import or invoke sk_emit. The run must already exist.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from .dsse import build_envelope
from .schema import PREDICATE_TYPE, STATEMENT_TYPE


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _classify(sources: list[str]) -> tuple[str, str]:
    unique = {item for item in sources if item in ("program", "attestation")}
    if unique == {"program"}:
        return "program", "computed"
    if unique == {"attestation"}:
        return "attestation", "asserted"
    if unique == {"program", "attestation"}:
        return "mixed", "mixed"
    return "attestation", "asserted"


def _producer(block: Any) -> dict[str, str] | None:
    if not isinstance(block, dict):
        return None
    digest = block.get("sha256")
    if not isinstance(digest, str) or not digest:
        return None
    name = block.get("name")
    return {
        "name": name if isinstance(name, str) and name else "unknown",
        "sha256": digest,
    }


def _iter_artifacts(run_dir: str) -> list[tuple[str, str, dict[str, Any]]]:
    art_dir = os.path.join(run_dir, "artifacts")
    found: list[tuple[str, str, dict[str, Any]]] = []
    if not os.path.isdir(art_dir):
        return found
    for name in sorted(os.listdir(art_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(art_dir, name)
        try:
            data = _load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        found.append((name, path, data))
    return found


def emit_statement(run_dir: str) -> dict[str, Any]:
    """Build a Statement/v1 document from a Key run directory."""
    run_path = os.path.join(run_dir, "run.json")
    if not os.path.isfile(run_path):
        raise FileNotFoundError(f"{run_dir} has no run.json — not a run directory")
    manifest = _load_json(run_path)

    subjects: list[dict[str, Any]] = [
        {"name": "run.json", "digest": {"sha256": sha256_file(run_path)}},
    ]
    gates: list[dict[str, Any]] = []
    producers: list[dict[str, str]] = []
    sources: list[str] = []

    for name, path, data in _iter_artifacts(run_dir):
        subjects.append(
            {"name": f"artifacts/{name}", "digest": {"sha256": sha256_file(path)}}
        )
        source = data.get("evidence_source")
        if isinstance(source, str):
            sources.append(source)
        producer = _producer(data.get("produced_by_program"))
        if producer is not None:
            producers.append(producer)
        if data.get("artifact_id") == "gate_decision_artifact":
            body = data.get("body") if isinstance(data.get("body"), dict) else {}
            gate_id = body.get("gate_id") or data.get("required_gate") or "unknown"
            decision = body.get("decision") or "unknown"
            enforcement = body.get("enforcement_class") or "unknown"
            gate: dict[str, Any] = {
                "gate_id": gate_id if isinstance(gate_id, str) else "unknown",
                "decision": decision if isinstance(decision, str) else "unknown",
                "enforcement_class": (
                    enforcement if isinstance(enforcement, str) else "unknown"
                ),
                "evidence_source": (
                    source if source in ("program", "attestation") else "unknown"
                ),
                "produced_by_program": producer,
            }
            gates.append(gate)

    seen: set[tuple[str, str]] = set()
    unique_producers: list[dict[str, str]] = []
    for producer in producers:
        key = (producer["name"], producer["sha256"])
        if key in seen:
            continue
        seen.add(key)
        unique_producers.append(producer)
    unique_producers.sort(key=lambda item: (item["name"], item["sha256"]))
    gates.sort(key=lambda item: item["gate_id"])

    evidence_source, computed_or_asserted = _classify(sources)
    hashes = manifest.get("ledger_entry_hashes")
    if not isinstance(hashes, list):
        hashes = []
    entry_hashes = [item for item in hashes if isinstance(item, str)]

    run_body: dict[str, Any] = {
        "run_id": manifest.get("run_id") or "",
        "result": manifest.get("result") or "",
        "key_sha256": manifest.get("key_sha256") or "",
        "route_id": manifest.get("selected_route_id") or "",
    }
    if "fixture" in manifest:
        run_body["fixture"] = bool(manifest["fixture"])

    return {
        "_type": STATEMENT_TYPE,
        "subject": subjects,
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "evidence_source": evidence_source,
            "computed_or_asserted": computed_or_asserted,
            "gates": gates,
            "producers": unique_producers,
            "run": run_body,
            "ledger": {
                "file": manifest.get("ledger_file") or "",
                "entry_hashes": entry_hashes,
            },
        },
    }


def emit_envelope(run_dir: str) -> dict[str, Any]:
    """Build an unsigned DSSE envelope around emit_statement(run_dir)."""
    return build_envelope(emit_statement(run_dir))


def write_statement(statement: dict[str, Any], path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(statement, handle, indent=2, sort_keys=True)
        handle.write("\n")
