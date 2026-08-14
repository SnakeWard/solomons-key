"""Optional in-toto interchange for an existing Key-governed run.

This package exports and structurally validates a custom in-toto predicate.
It does not emit Key evidence, does not implement RUN/SEM/SK rules, and
does not make Solomon's Key an SLSA builder. The trust root remains
TRUSTED_PROGRAMS.sha256 and the existing emitters. See TRUST_BOUNDARY.md.
"""

from __future__ import annotations

from .consume import ConsumeResult, consume_path, consume_statement
from .export import emit_statement, write_statement
from .schema import PREDICATE_TYPE, STATEMENT_TYPE

__all__ = [
    "ConsumeResult",
    "PREDICATE_TYPE",
    "STATEMENT_TYPE",
    "consume_path",
    "consume_statement",
    "emit_statement",
    "write_statement",
]
