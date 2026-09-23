from aiks.redaction import REDACTED, redact, redact_text


def test_recursive_redaction() -> None:
    value = {
        "clientSecret": "secret-value",
        "nested": [{"safe": "value", "token": "token-value"}],
    }

    assert redact(value) == {
        "clientSecret": REDACTED,
        "nested": [{"safe": "value", "token": REDACTED}],
    }


def test_text_redaction() -> None:
    redacted = redact_text("Bearer abc.def AccountKey=key-value?sig=signature")

    assert "abc.def" not in redacted
    assert "key-value" not in redacted
    assert "signature" not in redacted
