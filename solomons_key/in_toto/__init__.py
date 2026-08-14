"""Optional in-toto interchange for an existing Key-governed run.

This package exports and structurally validates a custom in-toto predicate.
It does not emit Key evidence, does not implement RUN/SEM/SK rules, and
does not make Solomon's Key an SLSA builder. The trust root remains
TRUSTED_PROGRAMS.sha256 and the existing emitters. See TRUST_BOUNDARY.md.
"""

from __future__ import annotations

from .consume import ConsumeResult, consume_document, consume_path, consume_statement
from .dsse import PAYLOAD_TYPE, build_envelope, extract_payload
from .export import emit_envelope, emit_statement, write_statement
from .schema import PREDICATE_TYPE, STATEMENT_TYPE

__all__ = [
    "ConsumeResult",
    "PAYLOAD_TYPE",
    "PREDICATE_TYPE",
    "STATEMENT_TYPE",
    "build_envelope",
    "consume_document",
    "consume_path",
    "consume_statement",
    "emit_envelope",
    "emit_statement",
    "extract_payload",
    "write_statement",
]
