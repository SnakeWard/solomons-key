"""Unsigned DSSE envelope helpers for the in-toto interchange.

Structural only. No keys, no signature verification, no Rekor/Sigstore.
An envelope with keyid "unsigned" records that fact; it is not a signature.
"""

from __future__ import annotations

import base64
import json
from typing import Any

PAYLOAD_TYPE = "application/vnd.in-toto+json"
UNSIGNED_KEYID = "unsigned"

_ENVELOPE_KEYS = ("payload", "payloadType", "signatures")


def looks_like_dsse(document: Any) -> bool:
    if not isinstance(document, dict):
        return False
    return any(key in document for key in _ENVELOPE_KEYS)


def looks_like_statement(document: Any) -> bool:
    if not isinstance(document, dict):
        return False
    return "_type" in document or "predicateType" in document


def _canonical_bytes(statement: dict[str, Any]) -> bytes:
    return json.dumps(statement, separators=(",", ":"), sort_keys=True).encode("utf-8")


def build_envelope(statement: dict[str, Any]) -> dict[str, Any]:
    """Wrap a Statement dict in an unsigned DSSE envelope."""
    if not isinstance(statement, dict):
        raise TypeError("statement must be a JSON object")
    payload = base64.b64encode(_canonical_bytes(statement)).decode("ascii")
    return {
        "payload": payload,
        "payloadType": PAYLOAD_TYPE,
        "signatures": [{"keyid": UNSIGNED_KEYID, "sig": ""}],
    }


def validate_envelope(envelope: Any) -> list[str]:
    """Return structural errors. Empty list means the envelope shape is usable."""
    if not isinstance(envelope, dict):
        return ["dsse: envelope must be a JSON object"]

    errors: list[str] = []
    if "payload" not in envelope:
        errors.append("dsse: missing payload")
    elif not isinstance(envelope["payload"], str) or not envelope["payload"]:
        errors.append("dsse: payload must be a non-empty string")

    payload_type = envelope.get("payloadType")
    if payload_type is None:
        errors.append("dsse: missing payloadType")
    elif payload_type != PAYLOAD_TYPE:
        errors.append(
            f"dsse: wrong payloadType: expected {PAYLOAD_TYPE!r}, got {payload_type!r}"
        )

    signatures = envelope.get("signatures")
    if signatures is None:
        errors.append("dsse: missing signatures")
    elif not isinstance(signatures, list):
        errors.append("dsse: signatures must be an array")
    elif len(signatures) == 0:
        errors.append("dsse: empty signatures array")
    else:
        for index, item in enumerate(signatures):
            if not isinstance(item, dict):
                errors.append(f"dsse: signatures[{index}] must be an object")
                continue
            if "keyid" not in item or not isinstance(item["keyid"], str):
                errors.append(f"dsse: signatures[{index}] missing string keyid")
            if "sig" not in item or not isinstance(item["sig"], str):
                errors.append(f"dsse: signatures[{index}] missing string sig")
    return errors


def extract_payload(envelope: dict[str, Any]) -> dict[str, Any]:
    """Decode and parse the Statement from a structurally valid envelope."""
    errors = validate_envelope(envelope)
    if errors:
        raise ValueError("; ".join(errors))
    raw = envelope["payload"]
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"dsse: payload is not valid base64: {exc}") from exc
    try:
        document = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"dsse: payload is not JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("dsse: payload must decode to a JSON object")
    return document
