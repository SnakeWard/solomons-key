"""Structurally validate a Solomon's Key in-toto statement.

This path does not judge whether the attested run followed the KEY contract.
That remains sk_verify. Here: parse, check types, reject incomplete input.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from .dsse import (
    extract_payload,
    looks_like_dsse,
    looks_like_statement,
    validate_envelope,
)
from .schema import PREDICATE_TYPE, STATEMENT_TYPE, load_schema

_VALIDATOR = Draft202012Validator(load_schema())


@dataclass
class ConsumeResult:
    ok: bool
    errors: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


def consume_document(document: Any) -> ConsumeResult:
    """Accept a bare Statement or an unsigned DSSE envelope wrapping one."""
    if not isinstance(document, dict):
        return ConsumeResult(False, ["document must be a JSON object"])
    dsse = looks_like_dsse(document)
    statement = looks_like_statement(document)
    if dsse and statement:
        return ConsumeResult(
            False,
            ["ambiguous document: both Statement and DSSE envelope fields are present"],
        )
    if dsse:
        envelope_errors = validate_envelope(document)
        if envelope_errors:
            return ConsumeResult(False, envelope_errors)
        try:
            payload = extract_payload(document)
        except ValueError as exc:
            return ConsumeResult(False, [str(exc)])
        return consume_statement(payload)
    return consume_statement(document)


def consume_statement(document: Any) -> ConsumeResult:
    if not isinstance(document, dict):
        return ConsumeResult(False, ["statement must be a JSON object"])

    errors: list[str] = []
    statement_type = document.get("_type")
    if statement_type != STATEMENT_TYPE:
        errors.append(
            f"wrong _type: expected {STATEMENT_TYPE!r}, got {statement_type!r}"
        )
    predicate_type = document.get("predicateType")
    if predicate_type != PREDICATE_TYPE:
        errors.append(
            f"wrong predicateType: expected {PREDICATE_TYPE!r}, got {predicate_type!r}"
        )

    for err in sorted(_VALIDATOR.iter_errors(document), key=lambda item: list(item.path)):
        location = "/".join(str(part) for part in err.path) or "<root>"
        errors.append(f"{location}: {err.message}")

    # Deduplicate: the explicit type checks overlap with const constraints.
    unique: list[str] = []
    seen: set[str] = set()
    for item in errors:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return ConsumeResult(not unique, unique)


def consume_path(path: str) -> ConsumeResult:
    try:
        with open(path, encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        return ConsumeResult(False, [f"unreadable: {exc}"])
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        return ConsumeResult(False, [f"malformed JSON: {exc}"])
    return consume_document(document)
