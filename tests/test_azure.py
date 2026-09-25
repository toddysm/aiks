"""Cloud boundary tests never authenticate or access Azure."""

import json
from pathlib import Path

import pytest

from aiks.azure import AzureSession, cli_environment, require_owned_group
from aiks.config import load_environment_config
from aiks.process import CommandResult

SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"
CONFIG = (
    Path(__file__).resolve().parents[1] / "infrastructure/aks-automatic/config/dev.example.yaml"
)


def test_relative_tool_configuration_is_captured_before_workspace_change(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "tools:/usr/bin")
    monkeypatch.setenv("AZURE_CONFIG_DIR", "azure-config")
    environment = cli_environment()
    assert environment["PATH"].split(":")[0] == str(tmp_path / "tools")
    assert environment["AZURE_CONFIG_DIR"] == str(tmp_path / "azure-config")


def test_context_pins_subscription_and_ignores_injected_credentials(monkeypatch):
    calls = []
    monkeypatch.setenv("ARM_CLIENT_SECRET", "not-a-real-secret")
    monkeypatch.setenv("AZURE_USERNAME", "synthetic-user")
    monkeypatch.setenv("AZURE_PASSWORD", "synthetic-value")

    def execute(command, **kwargs):
        calls.append((command, kwargs))
        response = (
            {"state": "Enabled", "id": SUBSCRIPTION, "tenantId": SUBSCRIPTION}
            if command[1:3] == ["account", "show"]
            else []
        )
        return CommandResult(tuple(command), 0, json.dumps(response), "")

    monkeypatch.setattr("aiks.azure.run_command", execute)
    session = AzureSession()
    assert session.json("group", "list") == []
    assert "ARM_CLIENT_SECRET" not in session.environment
    assert "AZURE_USERNAME" not in session.environment
    assert "AZURE_PASSWORD" not in session.environment
    assert calls[-1][0][-2:] == ["--subscription", SUBSCRIPTION]
    for arguments in (("cloud", "show"), ("extension", "list")):
        assert session.json(*arguments) == []
        assert "--subscription" not in calls[-1][0]


@pytest.mark.parametrize(
    "url,resource_arguments,expected",
    [
        ("https://management.azure.com/subscriptions", (), "https://management.azure.com/"),
        ("https://MANAGEMENT.azure.com:443/subscriptions", (), "https://management.azure.com/"),
        ("http://management.azure.com/subscriptions", (), None),
        ("https://management.azure.com.example.invalid/subscriptions", (), None),
        ("https://user@management.azure.com/subscriptions", (), None),
        ("https://management.azure.com:8443/subscriptions", (), None),
        ("https://graph.microsoft.com/v1.0/me", (), None),
        (
            "https://metrics.example.invalid/api/v1/query",
            ("--resource", "https://prometheus.monitor.azure.com"),
            "https://prometheus.monitor.azure.com",
        ),
        (
            "https://management.azure.com/subscriptions",
            ("--resource", "https://management.core.windows.net/"),
            "https://management.core.windows.net/",
        ),
        (
            "https://management.azure.com/subscriptions",
            ("--resource=https://management.core.windows.net/",),
            None,
        ),
    ],
)
def test_management_rest_authentication_is_explicit_and_host_scoped(
    monkeypatch, url, resource_arguments, expected
):
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        response = (
            {"state": "Enabled", "id": SUBSCRIPTION, "tenantId": SUBSCRIPTION}
            if command[1:3] == ["account", "show"]
            else {}
        )
        return CommandResult(tuple(command), 0, json.dumps(response), "")

    monkeypatch.setattr("aiks.azure.run_command", execute)
    assert AzureSession().json("rest", "--method", "get", "--url", url, *resource_arguments) == {}
    command = calls[-1]
    assert command[-2:] == ["--subscription", SUBSCRIPTION]
    for argument in resource_arguments:
        assert argument in command
    if expected is None:
        assert "--resource" not in command
    else:
        assert command.count("--resource") == 1
        assert command[command.index("--resource") + 1] == expected


@pytest.mark.parametrize(
    "response,code",
    [
        ("not-json", 0),
        ('{"state":"Disabled"}', 0),
        ('{"state":"Enabled"}', 0),
        ("private-value", 1),
    ],
)
def test_invalid_cloud_context_fails_without_echo(monkeypatch, response, code):
    monkeypatch.setattr(
        "aiks.azure.run_command",
        lambda command, **kwargs: CommandResult(tuple(command), code, response, "private-value"),
    )
    with pytest.raises(ValueError) as failure:
        AzureSession()
    assert "private-value" not in str(failure.value)


@pytest.mark.parametrize(
    "mutation",
    [
        "valid",
        "foreign-subscription",
        "foreign-name",
        "missing-tags",
        "backend",
        "mismatched-id",
        "malformed",
    ],
)
def test_group_ownership_is_fail_closed(mutation):
    config = load_environment_config(CONFIG)
    name = "rg-aiks-dev-dev-abcdefgh"
    group = {
        "name": name,
        "id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{name}",
        "tags": {"aiks-managed": "true", "environment": "dev"},
    }
    if mutation == "foreign-subscription":
        group["id"] = group["id"].replace(SUBSCRIPTION, "22222222-2222-4222-8222-222222222222")
    elif mutation == "foreign-name":
        group["name"] = "unrelated"
    elif mutation == "missing-tags":
        group["tags"] = {}
    elif mutation == "backend":
        group["tags"]["aiks-purpose"] = "terraform-state"
    elif mutation == "mismatched-id":
        group["name"] = "rg-aiks-dev-dev-12345678"
    elif mutation == "malformed":
        group = None
    if mutation == "valid":
        assert require_owned_group(group, config, SUBSCRIPTION) == name
    else:
        with pytest.raises(ValueError):
            require_owned_group(group, config, SUBSCRIPTION)
