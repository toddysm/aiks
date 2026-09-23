from aiks.redaction import REDACTED, redact, redact_text


def test_recursive_redaction() -> None:
    value = {
        "clientSecret": "secret-value",
        "nested": [
            {
                "safe": "value",
                "token": "token-value",
                "apiKey": "api-value",
                "storageKey": "storage-value",
                "SharedAccessKey": "shared-value",
                "client-key-data": "kube-value",
                "clientCertificateData": "certificate-value",
            }
        ],
    }

    assert redact(value) == {
        "clientSecret": REDACTED,
        "nested": [
            {
                "safe": "value",
                "token": REDACTED,
                "apiKey": REDACTED,
                "storageKey": REDACTED,
                "SharedAccessKey": REDACTED,
                "client-key-data": REDACTED,
                "clientCertificateData": REDACTED,
            }
        ],
    }


def test_recursive_redaction_honors_terraform_sensitive_marker() -> None:
    value = {"output": {"sensitive": True, "value": "terraform-secret", "type": "string"}}

    assert redact(value) == {"output": {"sensitive": True, "value": REDACTED, "type": "string"}}


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
