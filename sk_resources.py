#!/usr/bin/env python3
"""Locate immutable data shipped with Solomon's Key.

Source checkouts use the generated schemas beside this module. Installed
distributions use the same files through ``importlib.resources``. Callers may
still provide an explicit directory (or ``SK_SCHEMA_DIR``) when verifying a
different schema set.
"""

from __future__ import annotations

from importlib.resources import files
import os

SCHEMA_PACKAGE = "solomons_key._schemas"
SCHEMA_SENTINEL = "envelope.schema.json"


def _complete_schema_dir(path: str) -> bool:
    return os.path.isfile(os.path.join(path, SCHEMA_SENTINEL))


def default_schema_dir() -> str:
    """Return the checkout or installed directory containing artifact schemas."""
    override = os.environ.get("SK_SCHEMA_DIR")
    if override:
        return override

    checkout = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas", "artifacts")
    if _complete_schema_dir(checkout):
        return checkout

    try:
        resource_root = files(SCHEMA_PACKAGE)
        installed = os.fspath(resource_root)
    except (ModuleNotFoundError, TypeError):
        installed = ""
    if installed and _complete_schema_dir(installed):
        return installed

    raise FileNotFoundError(
        "Solomon's Key artifact schemas are unavailable: reinstall the distribution "
        "or set SK_SCHEMA_DIR to an explicit schema directory"
    )
