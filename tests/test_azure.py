"""Cloud boundary tests never authenticate or access Azure."""

import json
from pathlib import Path

import pytest

from aiks.azure import AzureSession, require_owned_group
from aiks.config import load_environment_config
from aiks.process import CommandResult

SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"
CONFIG = (
    Path(__file__).resolve().parents[1] / "infrastructure/aks-automatic/config/dev.example.yaml"
)


def test_context_pins_subscription_and_ignores_injected_credentials(monkeypatch):
    calls = []
    monkeypatch.setenv("ARM_CLIENT_SECRET", "not-a-real-secret")

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
    assert calls[-1][0][-2:] == ["--subscription", SUBSCRIPTION]


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
