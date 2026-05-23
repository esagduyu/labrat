"""PII redaction for SQL literals (M28).

Replaces common PII patterns (email, phone, SSN) in SQL strings with
labelled placeholders. Applied before writing to the history log.
"""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"""(['"])([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\1""")
_PHONE_RE = re.compile(r"""(['"])(\+?[\d\-\.\(\) ]{7,20})\1""")
_SSN_RE = re.compile(r"""(['"])(\d{3}-\d{2}-\d{4})\1""")


def redact_pii(sql: str) -> str:
    """Replace PII literals in a SQL string with redaction placeholders."""
    sql = _SSN_RE.sub(r"\1[REDACTED_SSN]\1", sql)
    sql = _EMAIL_RE.sub(r"\1[REDACTED_EMAIL]\1", sql)
    sql = _PHONE_RE.sub(r"\1[REDACTED_PHONE]\1", sql)
    return sql
