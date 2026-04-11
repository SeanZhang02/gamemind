"""Unit tests for Amendment A10 secret redaction."""

from __future__ import annotations

from gamemind.events.scrub import scrub_secrets


# A fake 40+ char token that matches the shape. Not a real key.
FAKE_KEY = "sk-ant-" + "a" * 50
FAKE_KEY_2 = "sk-ant-" + "b" * 40 + "_-"
REDACTED = "sk-ant-REDACTED"


def test_scrub_string_with_key() -> None:
    result = scrub_secrets(f"my key is {FAKE_KEY} shhh")
    assert result == f"my key is {REDACTED} shhh"
    assert FAKE_KEY not in result


def test_scrub_string_without_key() -> None:
    assert scrub_secrets("nothing to see") == "nothing to see"


def test_scrub_dict() -> None:
    d = {"key": FAKE_KEY, "note": "plain"}
    result = scrub_secrets(d)
    assert result == {"key": REDACTED, "note": "plain"}
    # Original unchanged
    assert d["key"] == FAKE_KEY


def test_scrub_nested_dict() -> None:
    d = {"outer": {"inner": {"api_key": FAKE_KEY, "count": 3}}}
    result = scrub_secrets(d)
    assert result["outer"]["inner"]["api_key"] == REDACTED
    assert result["outer"]["inner"]["count"] == 3


def test_scrub_list() -> None:
    lst = [FAKE_KEY, "plain", FAKE_KEY_2]
    result = scrub_secrets(lst)
    assert result == [REDACTED, "plain", REDACTED]


def test_scrub_list_of_dicts() -> None:
    data = [
        {"token": FAKE_KEY},
        {"note": "nothing"},
    ]
    result = scrub_secrets(data)
    assert result[0]["token"] == REDACTED
    assert result[1]["note"] == "nothing"


def test_scrub_pass_through_primitives() -> None:
    assert scrub_secrets(42) == 42
    assert scrub_secrets(3.14) == 3.14
    assert scrub_secrets(True) is True
    assert scrub_secrets(None) is None


def test_scrub_traceback_style_multiline() -> None:
    tb = f"""Traceback (most recent call last):
  File "daemon.py", line 42
    call_api(api_key={FAKE_KEY!r})
ConnectionError: {FAKE_KEY}
"""
    result = scrub_secrets(tb)
    assert FAKE_KEY not in result
    assert REDACTED in result


def test_scrub_partial_match_not_replaced() -> None:
    # Too short to match the 40+ char regex
    short = "sk-ant-abc"
    assert scrub_secrets(short) == short


def test_scrub_tuple_preserves_type() -> None:
    t = (FAKE_KEY, "plain")
    result = scrub_secrets(t)
    assert isinstance(result, tuple)
    assert result == (REDACTED, "plain")
