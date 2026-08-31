"""Field-name and nested-value redaction in `log_event`'s ``kv`` payload.

Complements `test_no_secrets.py` (static repo scan) and `test_migrations.py`
(the Alembic reconfiguration regression) with focused, isolated coverage of
`_RedactFilter`/`_scrub_kv_value` itself: a credential-named field must be
redacted even when its value doesn't *look* like a credential, and the same
must hold recursively for a nested dict/list - exactly the shape of leak
reproduced against this repo (`api_key="plain text"`, `nested={"token": "..."}`).
"""

from __future__ import annotations

import io
import logging

from app.core.logging import _KeyValueFormatter, _RedactFilter, log_event

CANARY_TOP = "SENSITIVE_KEY_CANARY"
CANARY_NESTED = "SENSITIVE_NESTED_CANARY"
CANARY_LIST = "SENSITIVE_LIST_CANARY"


def _capture(**kv: object) -> str:
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(_KeyValueFormatter(fmt="%(message)s"))
    handler.addFilter(_RedactFilter())
    logger = logging.getLogger("test.logging_redaction.isolated")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    log_event(logger, "test.event", **kv)
    handler.flush()
    return buffer.getvalue()


def test_a_credential_named_field_is_redacted_even_with_a_plain_value():
    """`redact()`'s value-shape regexes would never flag a plain string like
    this on their own - the field *name* is what must trigger redaction."""
    output = _capture(api_key=CANARY_TOP)
    assert CANARY_TOP not in output
    assert "***redacted***" in output


def test_a_nested_credential_named_field_is_redacted():
    output = _capture(nested={"token": CANARY_NESTED})
    assert CANARY_NESTED not in output
    assert "***redacted***" in output


def test_the_exact_reproduced_leak_is_fully_redacted():
    """`log_event(..., api_key=..., nested={"token": ...})` - the precise
    shape reported against this repo."""
    output = _capture(api_key=CANARY_TOP, nested={"token": CANARY_NESTED})
    assert CANARY_TOP not in output
    assert CANARY_NESTED not in output


def test_a_list_of_nested_dicts_is_also_scrubbed():
    output = _capture(results=[{"job_id": 1, "token": CANARY_LIST}])
    assert CANARY_LIST not in output
    assert "job_id" in output


def test_safe_scalar_fields_are_never_redacted():
    """Redaction must be targeted - it must not swallow ordinary, safe ids
    and counters just because they sit next to a credential field."""
    output = _capture(job_id=42, count=3, category="authentication", api_key=CANARY_TOP)
    assert "job_id=42" in output
    assert "count=3" in output
    assert "category=authentication" in output
    assert CANARY_TOP not in output


def test_a_credential_named_container_is_redacted_wholesale_before_recursing():
    """Regression: `authorization={"bearer": "..."}` used to leak, because the
    old code recursed into the dict *before* ever checking whether the outer
    field name (`authorization`) was itself credential-named - only the inner
    key `bearer` (not credential-named on its own) got checked."""
    output = _capture(authorization={"bearer": CANARY_NESTED})
    assert CANARY_NESTED not in output
    assert "***redacted***" in output


def test_tokens_used_counter_survives_untouched():
    """Regression: matching the bare substring `token` inside a field name
    used to redact `tokens_used`, a safe usage counter, even though the field
    is not itself named `token`."""
    output = _capture(tokens_used=12, task_id=7)
    assert "tokens_used=12" in output
    assert "task_id=7" in output


def test_request_id_field_is_never_redacted():
    """`request_id` must survive - it is not a credential-named field, only
    superficially similar to one."""
    output = _capture(request_id="req_abc123")
    assert "req_abc123" in output


def test_non_string_nested_dict_keys_do_not_crash_the_filter():
    """A nested mapping with non-string keys must not raise inside the
    redaction filter - it should simply never be treated as credential-named."""
    output = _capture(mapping={1: "safe-value", None: "also-safe"})
    assert "safe-value" in output
    assert "also-safe" in output
