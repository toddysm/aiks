"""Prerequisite guards use fake metadata and network boundaries."""

import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from aiks.config import load_environment_config
from aiks.preflight import (
    allows_action,
    check_backend_permissions,
    check_tools,
    cloud_preflight,
    foundation_policy,
    platform_policy,
    probe_host,
    required_actions,
)
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
        ("resource-types", False, None, 0),
        ("resource-types", False, {}, 0),
        ("resource-types", False, [None], 0),
        ("resource-types", False, ["invalid"], 0),
        ("resource-types", False, [{"resourceType": None, "locations": []}], 0),
        ("resource-types", False, [{"resourceType": "managedClusters", "locations": None}], 0),
        (
            "resource-types",
            False,
            [{"resourceType": "managedClusters", "locations": "West US 3"}],
            0,
        ),
        ("resource-types", False, [{"resourceType": "managedClusters", "locations": [None]}], 0),
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
        ("permissions-shape", False, None, 0),
        ("permissions-shape", False, [], 0),
        ("permissions-shape", False, "invalid", 0),
        ("extension-shape", False, None, 0),
        ("extension-shape", False, {}, 0),
        ("extension-shape", False, "invalid", 0),
        ("extension-shape", False, [None], 0),
        ("extension-shape", False, [{}], 0),
        ("extension-shape", False, [{"name": None}], 0),
        ("extension-shape", False, [{"name": 1}], 0),
        ("extension-shape", False, [{"name": ""}], 0),
        ("quota-shape", False, None, 0),
        ("quota-shape", False, {}, 0),
        ("quota-shape", False, [None], 0),
        ("quota-shape", False, [{"name": None}], 0),
        ("quota-shape", False, [{"name": "cores"}], 0),
        ("quota-shape", False, [{"name": {}}], 0),
        ("quota-shape", False, [{"name": {"value": 1}}], 0),
        ("quota-shape", False, [{"name": {"value": ""}}], 0),
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
                        "resourceTypes": limit
                        if failure == "resource-types"
                        else [
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
                if failure == "permissions-shape":
                    return limit
                return {
                    "value": []
                    if failure == "permissions"
                    else [{"actions": ["*"], "notActions": []}]
                }
            if args[:2] == ("extension", "list"):
                if failure == "extension-shape":
                    return limit
                return [{"name": "aks-preview"}] if failure == "extension" else []
            if args[:2] == ("vm", "list-usage"):
                if failure == "quota-shape":
                    return limit
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


@pytest.mark.parametrize("environment", ["dev", "production"])
@pytest.mark.parametrize("engine", ["bicep", "terraform"])
def test_permission_policy_covers_all_deployed_resource_types(environment, engine):
    config = load_environment_config(CONFIG.with_name(f"{environment}.example.yaml"))
    actions = required_actions(config, engine)
    for kind, counts in foundation_policy("parity/contract.json")["inventory"].items():
        if counts[environment]:
            action = (
                "Microsoft.Resources/subscriptions/resourceGroups/write"
                if kind == "Microsoft.Resources/resourceGroups"
                else kind + "/write"
            )
            assert action in actions
            assert action.removesuffix("/write") + "/read" in actions
            if engine == "terraform" or kind == "Microsoft.Resources/resourceGroups":
                assert action.removesuffix("/write") + "/delete" in actions
    assert "Microsoft.ResourceGraph/resources/read" in actions
    assert "Microsoft.Authorization/roleAssignments/read" in actions
    assert "Microsoft.KeyVault/deletedVaults/read" in actions
    assert not any("purge" in action.lower() or "listkeys" in action.lower() for action in actions)
    assert ("Microsoft.Storage/storageAccounts/read" in actions) is (engine == "terraform")
    assert ("Microsoft.ContainerService/managedClusters/delete" in actions) is (
        engine == "terraform"
    )
    assert ("Microsoft.Resources/deployments/whatIf/action" in actions) is (engine == "bicep")


def test_permission_policy_only_requires_enabled_optional_features():
    config = load_environment_config(CONFIG)
    config.spec.observability.container_insights = False
    actions = required_actions(config)
    assert not any(
        action.startswith(
            (
                "Microsoft.OperationalInsights/",
                "Microsoft.Insights/",
                "Microsoft.Monitor/",
                "Microsoft.Dashboard/",
            )
        )
        for action in actions
    )
    config.spec.observability.managed_prometheus = True
    config.spec.observability.managed_grafana = True
    config.spec.observability.action_group_resource_ids = ["/existing/action-group"]
    config.spec.network.private_cluster = True
    actions = required_actions(config)
    assert "Microsoft.Monitor/accounts/write" in actions
    assert "Microsoft.Dashboard/grafana/write" in actions
    assert "Microsoft.AlertsManagement/prometheusRuleGroups/write" in actions
    assert "Microsoft.Insights/activityLogAlerts/write" in actions
    assert "Microsoft.Network/privateDnsZones/write" in actions
    assert "Microsoft.Network/privateEndpoints/write" not in actions
    assert "Microsoft.Insights/actionGroups/write" not in actions
    assert "Microsoft.Insights/scheduledQueryRules/write" not in actions


def test_permission_policy_skips_operator_push_for_existing_immutable_image():
    config = load_environment_config(CONFIG)
    assert "Microsoft.ContainerRegistry/registries/push/write" in required_actions(config)
    config.spec.workload.image = "test.azurecr.io/readiness@sha256:" + "a" * 64
    actions = required_actions(config)
    assert "Microsoft.ContainerRegistry/registries/push/write" not in actions
    assert "Microsoft.ContainerRegistry/registries/metadata/read" not in actions


@pytest.mark.parametrize("environment", ["dev", "production"])
@pytest.mark.parametrize("engine", ["bicep", "terraform"])
def test_preflight_reports_each_missing_required_action(environment, engine):
    config = load_environment_config(CONFIG.with_name(f"{environment}.example.yaml"))

    class Session:
        subscription = "11111111-1111-4111-8111-111111111111"

        def json(self, *args):
            if args[:2] == ("cloud", "show"):
                return {"name": "AzureCloud"}
            if args[:2] == ("provider", "list"):
                return [
                    {
                        "namespace": name,
                        "registrationState": "Registered",
                        "resourceTypes": [
                            {"resourceType": "managedClusters", "locations": ["West US 3"]}
                        ],
                    }
                    for name in platform_policy()["providers"]
                ]
            if args[0] == "rest":
                return {"value": [{"actions": ["*"], "notActions": [missing]}]}
            pytest.fail("permission denial must stop further preflight work")

    for missing in required_actions(config, engine):
        with pytest.raises(ValueError) as failure:
            cloud_preflight(config, Session(), engine=engine)
        assert missing in str(failure.value)


@pytest.mark.parametrize(
    "failure", [None, "read", "write", "delete", "control-only", "malformed", "paginated"]
)
def test_backend_permission_check_is_scoped_read_only_and_data_aware(failure):
    config = load_environment_config(CONFIG)

    class Session:
        subscription = "11111111-1111-4111-1111-111111111111"

        def json(self, *args):
            assert args[:3] == ("rest", "--method", "get")
            url = args[args.index("--url") + 1]
            assert (
                f"/subscriptions/{self.subscription}/resourceGroups/{config.spec.terraform.state_resource_group}/"
                in url
            )
            assert (
                f"/blobServices/default/containers/{config.spec.terraform.state_container}/providers/Microsoft.Authorization/permissions?"
                in url
            )
            if failure == "malformed":
                return None
            permissions = {
                "actions": ["*"],
                "dataActions": [] if failure == "control-only" else ["*"],
                "notDataActions": [f"*/{failure}"]
                if failure in {"read", "write", "delete"}
                else [],
            }
            return {"value": [permissions], "nextLink": "more" if failure == "paginated" else None}

    if failure:
        with pytest.raises(ValueError, match="permission"):
            check_backend_permissions(config, Session())
    else:
        check_backend_permissions(config, Session())


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("grant,excluded", [(False, False), (True, False), (True, True)])
def test_metric_data_permission_is_separate_and_conditional(enabled, grant, excluded):
    config = load_environment_config(CONFIG)
    config.spec.observability.managed_prometheus = enabled
    metric_read = "Microsoft.Monitor/accounts/data/metrics/read"

    class Session:
        subscription = "11111111-1111-4111-8111-111111111111"

        def json(self, *args):
            if args[:2] == ("cloud", "show"):
                return {"name": "AzureCloud"}
            if args[:2] == ("provider", "list"):
                return [
                    {
                        "namespace": name,
                        "registrationState": "Registered",
                        "resourceTypes": [
                            {"resourceType": "managedClusters", "locations": ["West US 3"]}
                        ],
                    }
                    for name in platform_policy()["providers"]
                ]
            if args[0] == "rest":
                return {
                    "value": [
                        {
                            "actions": ["*"],
                            "dataActions": [metric_read] if grant else [],
                            "notDataActions": [metric_read] if excluded else [],
                        }
                    ]
                }
            if args[:2] == ("extension", "list"):
                return []
            if args[:2] == ("vm", "list-usage"):
                return [{"name": {"value": "cores"}, "limit": 100, "currentValue": 0}]
            pytest.fail("unexpected request")

    if enabled and (not grant or excluded):
        with pytest.raises(ValueError, match="monitoring data permissions"):
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


@pytest.mark.parametrize("provider", [None, [], {}, {"namespace": None}, {"namespace": 1}])
def test_provider_metadata_requires_named_objects(provider):
    class Session:
        def json(self, *args):
            if args == ("cloud", "show"):
                return {"name": "AzureCloud"}
            assert args[:2] == ("provider", "list")
            return [provider]

    with pytest.raises(ValueError, match="resource provider"):
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
