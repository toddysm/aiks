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


def test_text_redaction_covers_credential_and_sensitive_output_formats() -> None:
    value = """Password=password-value
ClientSecret=client-value
connectionString=connection-value
client-key-data: kube-value
?client_secret=query-value
{"sensitive": true, "value": "terraform-value"}
"""

    redacted = redact_text(value)

    for secret in (
        "password-value",
        "client-value",
        "connection-value",
        "kube-value",
        "query-value",
        "terraform-value",
    ):
        assert secret not in redacted
