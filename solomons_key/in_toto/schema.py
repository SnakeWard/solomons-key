"""Stable type URIs and the JSON Schema for the v1 statement."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://solomons-key.dev/attestation/v1"

SCHEMA_PATH = Path(__file__).with_name("statement.schema.json")


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
