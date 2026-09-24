"""Readiness endpoints and bounded metrics without cloud requests."""

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from azure.core.exceptions import AzureError
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aiks.readiness import ReadinessSettings, app_factory, create_app, verify_identity


def settings(**overrides: object) -> ReadinessSettings:
    return ReadinessSettings.model_validate(
        {
            "environment": "dev",
            "target": "kind",
            "cluster": "local",
            "namespace": "aiks-readiness",
            "workload": "readiness",
            **overrides,
        }
    )


def test_local_routes_skip_cloud_and_bound_labels() -> None:
    def forbidden(config: ReadinessSettings) -> str:
        pytest.fail("local identity must never contact Azure")

    with TestClient(create_app(settings(), forbidden)) as client:
        assert client.get("/livez").json() == {"status": "alive"}
        assert client.get("/readyz").status_code == 200
        assert client.get("/identityz").json()["status"] == "skipped"
        assert client.get("/untrusted-path").status_code == 404
        metrics = client.get("/metrics").text
        assert "aiks_readiness_info{" in metrics
        assert 'status="ready"' in metrics and 'path="other"' in metrics
        assert "untrusted-path" not in metrics
        assert "aiks_readiness_duration_seconds_bucket" in metrics


def test_failed_readiness_metrics() -> None:
    with TestClient(create_app(settings(ready=False))) as client:
        assert client.get("/readyz").status_code == 503
        assert client.get("/livez").status_code == 200
        assert "aiks_readiness_failures_total" in client.get("/metrics").text


def test_identity_success_and_redacted_failure() -> None:
    config = settings(target="aks", vaultUri="https://vault.example.invalid")
    with TestClient(create_app(config, lambda config: "readiness-marker")) as client:
        assert client.get("/identityz").json() == {
            "status": "verified",
            "keyName": "readiness-marker",
        }

    def fail(config: ReadinessSettings) -> str:
        raise AzureError("Bearer never-emit-this")

    with TestClient(create_app(config, fail)) as client:
        response = client.get("/identityz")
        assert response.status_code == 503 and "never-emit-this" not in response.text


@pytest.mark.parametrize(
    "overrides", [{"target": "aks"}, {"vaultUri": "https://vault.example.invalid"}]
)
def test_identity_configuration_guard(overrides: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        settings(**overrides)


def test_app_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.json"
    path.write_text(settings().model_dump_json(by_alias=True))
    monkeypatch.setenv("AIKS_READINESS_CONFIG", str(path))
    with TestClient(app_factory()) as client:
        assert client.get("/readyz").status_code == 200


def test_invalid_startup_configuration_does_not_echo_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text('secret: "never-print-this"')
    monkeypatch.setenv("AIKS_READINESS_CONFIG", str(path))
    with pytest.raises(ValueError) as error:
        app_factory()
    assert "never-print-this" not in str(error.value)
    assert error.value.__suppress_context__


def test_sdk_path_uses_workload_identity_and_only_returns_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def credential(**options: object) -> object:
        assert options["exclude_environment_credential"] is True
        assert options["exclude_managed_identity_credential"] is True
        return nullcontext(object())

    class Client:
        name = "readiness-marker"

        def get_key(self, name: str) -> object:
            assert name == "readiness-marker"
            return SimpleNamespace(name=self.name)

    client = Client()
    monkeypatch.setattr("aiks.readiness.DefaultAzureCredential", credential)
    monkeypatch.setattr("aiks.readiness.KeyClient", lambda *args, **kwargs: nullcontext(client))
    config = settings(target="aks", vaultUri="https://vault.example.invalid")
    assert verify_identity(config) == "readiness-marker"
    client.name = "wrong"
    with pytest.raises(ValueError, match="unexpected marker"):
        verify_identity(config)
