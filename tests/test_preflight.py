"""Prerequisite guards use fake metadata and network boundaries."""

import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from aiks.config import load_environment_config
from aiks.preflight import allows_action, check_tools, cloud_preflight, platform_policy, probe_host
from aiks.process import CommandResult

CONFIG = (
    Path(__file__).resolve().parents[1] / "infrastructure/aks-automatic/config/dev.example.yaml"
)


@pytest.mark.parametrize(
    "permissions,expected",
    [
        ([{"actions": ["*"], "notActions": []}], True),
        ([{"actions": ["*"], "notActions": ["Microsoft.Authorization/*"]}], False),
        ([{"actions": ["Microsoft.Authorization/roleAssignments/*"]}], True),
        ([], False),
        (None, False),
        ([{"actions": "*"}], False),
    ],
)
def test_effective_permission_matching(permissions, expected):
    assert allows_action(permissions, "Microsoft.Authorization/roleAssignments/write") is expected


@pytest.mark.parametrize(
    "failure,lowercase,limit,current_value",
    [
        (None, False, 100, 0),
        (None, True, "100", "18"),
        (None, True, 100, "96"),
        (None, False, "100", 96),
        ("region", True, 100, 0),
        ("providers", True, 100, 0),
        ("permissions", False, 100, 0),
        ("extension", False, 100, 0),
        ("quota", False, 0, 0),
        ("quota", True, "100", "97"),
        ("quota", False, 100, 101),
        ("quota", False, None, 0),
        ("quota", False, 100, None),
        ("quota", False, True, 0),
        ("quota", False, 100, False),
        ("quota", False, 100.0, 0),
        ("quota", False, 100, -1),
        ("quota", False, -1, 0),
        ("quota", False, "unlimited", 0),
        ("quota", False, "100.0", 0),
        ("quota", False, "100", "-1"),
        ("quota", False, {}, 0),
    ],
)
def test_cloud_preflight_fails_before_mutation(failure, lowercase, limit, current_value):
    config = load_environment_config(CONFIG)
    policy = platform_policy()

    class Session:
        subscription = "11111111-1111-4111-8111-111111111111"

        def json(self, *args):
            if args[:2] == ("cloud", "show"):
                return {"name": "AzureCloud"}
            if args[:2] == ("provider", "list"):
                assert args[2:] == (
                    "--query",
                    "[].{namespace:namespace,registrationState:registrationState,"
                    "resourceTypes:resourceTypes[].{resourceType:resourceType,locations:locations}}",
                )
                return [
                    {
                        "namespace": provider.lower() if lowercase else provider,
                        "registrationState": "NotRegistered"
                        if failure == "providers"
                        else "Registered",
                        "resourceTypes": [
                            {
                                "resourceType": "managedclusters"
                                if lowercase
                                else "managedClusters",
                                "locations": [] if failure == "region" else ["West US 3"],
                            }
                        ],
                    }
                    for provider in policy["providers"]
                ]
            if args[0] == "rest":
                return {
                    "value": []
                    if failure == "permissions"
                    else [{"actions": ["*"], "notActions": []}]
                }
            if args[:2] == ("extension", "list"):
                return [{"name": "aks-preview"}] if failure == "extension" else []
            if args[:2] == ("vm", "list-usage"):
                return [
                    {
                        "name": {"value": "cores"},
                        "limit": limit,
                        "currentValue": current_value,
                    }
                ]
            pytest.fail("unexpected or mutating cloud call")

    if failure:
        with pytest.raises(ValueError):
            cloud_preflight(config, Session())
    else:
        assert cloud_preflight(config, Session())["permissions"] == "verified"


@pytest.mark.parametrize("response", [None, [], "invalid"])
def test_cloud_context_requires_an_object(response):
    class Session:
        def json(self, *args):
            assert args == ("cloud", "show")
            return response

    with pytest.raises(ValueError, match="cloud context"):
        cloud_preflight(load_environment_config(CONFIG), Session())


def test_public_resolution_is_rejected_for_private_probe(monkeypatch):
    monkeypatch.setattr(
        "aiks.preflight.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("203.0.113.1", 443))],
    )
    monkeypatch.setattr(
        "aiks.preflight.socket.create_connection",
        lambda *args, **kwargs: pytest.fail("must refuse before connecting"),
    )
    with pytest.raises(ValueError, match="outside"):
        probe_host("private.example.invalid", private=True, timeout=1)


@pytest.mark.parametrize("failure", [None, "missing", "old", "unknown"])
def test_tools_parse_native_versions(monkeypatch, failure):
    monkeypatch.setenv("ARM_CLIENT_SECRET", "not-a-real-secret")
    monkeypatch.setenv("TF_CLI_ARGS", "unexpected")
    policy = platform_policy()["tools"]

    def execute(arguments, **kwargs):
        assert "ARM_CLIENT_SECRET" not in kwargs["environment"]
        assert "TF_CLI_ARGS" not in kwargs["environment"]
        settings = policy[arguments[0]]
        value = ".".join(str(number) for number in settings["minimum"])
        if failure == "old":
            value = "0.0.1"
        elif failure == "unknown":
            value = "unknown"
        if "field" in settings:
            result = value
            for part in reversed(settings["field"].split(".")):
                result = {part: result}
            text = json.dumps(result)
        else:
            text = f"{arguments[0]} version v{value}"
        return CommandResult(tuple(arguments), 1 if failure == "missing" else 0, text, "")

    monkeypatch.setattr("aiks.preflight.run_command", execute)
    if failure:
        with pytest.raises(ValueError):
            check_tools("terraform")
    else:
        assert "terraform" in check_tools("terraform")
        assert "bicep" in check_tools("bicep")


def test_private_probe_connects_to_every_resolved_address(monkeypatch):
    connections = []
    monkeypatch.setattr(
        "aiks.preflight.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (None, None, None, None, (address, 443)) for address in ("10.1.2.3", "10.1.2.4")
        ],
    )
    monkeypatch.setattr(
        "aiks.preflight.socket.create_connection",
        lambda address, **kwargs: connections.append(address) or nullcontext(),
    )
    assert len(probe_host("private.invalid", private=True, timeout=1)) == 2
    assert len(connections) == 2
