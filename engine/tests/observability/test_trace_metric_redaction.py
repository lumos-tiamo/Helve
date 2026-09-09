"""The trace redactor must not eat the numbers the trace exists to carry.

Key-name redaction is a blunt instrument: ``_SENSITIVE_KEY`` matches the
substring "token", which is in ``token_estimate``, ``token_budget``,
``source_tokens``, ``required_tokens`` and ``rendered_tokens`` -- every
prompt-cost figure the assembler records.  All five were written as
"[REDACTED]" for the life of the feature, and nothing caught it because nothing
read the prompt manifest.

So the test below is deliberately *not* a list of the five keys that were broken
when it was written.  It asks the producers what they emit and checks that
whatever comes back survives.  A sixth metric added tomorrow is covered without
anyone remembering this file exists.
"""

from __future__ import annotations

import hashlib

import pytest

from engine.context.assembler import PromptManifest, PromptPlan
from engine.observability.trace_store import _bounded_trace_value

REDACTED = "[REDACTED]"


def _numeric_keys(payload: object, prefix: str = "") -> list[str]:
    """Every key in a nested structure whose value is a number."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                found.append(path)
            else:
                found.extend(_numeric_keys(value, path))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_numeric_keys(item, prefix))
    return found


def _sample_manifest() -> PromptManifest:
    return PromptManifest(
        rendered_prompt_hash=hashlib.sha256(b"prompt").hexdigest(),
        layers=(
            {
                "id": "durable_context",
                "source": "memory_durable",
                "source_ref": "memory:durable.md",
                "scope": "project",
                "authority": "reference",
                "trust": "untrusted_reference",
                "load_reason": "always",
                "content_hash": hashlib.sha256(b"memory").hexdigest(),
                "char_count": 512,
                "token_estimate": 137,
                "action": "loaded",
            },
        ),
    )


def _sample_plan() -> PromptPlan:
    return PromptPlan(
        token_budget=100_000,
        source_tokens=8_400,
        required_tokens=3_100,
        rendered_tokens=7_900,
        within_budget=True,
        trimmed_layers=(),
    )


@pytest.mark.parametrize(
    "producer, label",
    [(_sample_manifest, "PromptManifest"), (_sample_plan, "PromptPlan")],
)
def test_every_number_a_producer_emits_survives_redaction(producer, label):
    """Asked of the producer, not of a hardcoded list, so new metrics are covered."""
    payload = producer().to_trace_data()
    expected = _numeric_keys(payload)
    assert expected, f"{label}.to_trace_data() emitted no numbers to check"

    redacted = _bounded_trace_value(payload)
    survived = _numeric_keys(redacted)

    lost = sorted(set(expected) - set(survived))
    assert not lost, (
        f"{label} metrics were redacted and are unreadable in every trace: {lost}"
    )


def test_an_actual_credential_key_is_still_redacted():
    """The fix must not turn the allowlist into a hole."""
    payload = {
        "api_key": "sk-not-a-real-key",
        "authorization": "Bearer abcdef0123456789",
        "auth_token": "t0ken",
        "token_estimate": 42,
    }
    redacted = _bounded_trace_value(payload)

    assert redacted["api_key"] == REDACTED
    assert redacted["authorization"] == REDACTED
    assert redacted["auth_token"] == REDACTED
    # …while the metric beside them stays readable.
    assert redacted["token_estimate"] == 42


def test_a_credential_inside_a_value_is_still_caught():
    """Name-based allowlisting must not disable the value-based scan."""
    redacted = _bounded_trace_value(
        {"token_estimate": 10, "command": "curl -H 'Authorization: Bearer abc123def456ghi789'"}
    )
    assert redacted["token_estimate"] == 10
    assert REDACTED in redacted["command"]
