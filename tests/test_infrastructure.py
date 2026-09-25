"""Lifecycle tests isolate every external command from real cloud resources."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_posture import live_fixture

from aiks.config import load_environment_config
from aiks.infrastructure import InfrastructureRuntime

SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"
CONFIG = (
    Path(__file__).resolve().parents[1] / "infrastructure/aks-automatic/config/dev.example.yaml"
)


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    class Session:
        subscription = SUBSCRIPTION

        def __init__(self):
            self.environment = {}

        def json(self, *arguments):
            assert arguments == ("group", "list")
            return []

    monkeypatch.setattr("aiks.infrastructure.AzureSession", Session)
    monkeypatch.setattr("aiks.infrastructure.check_tools", lambda engine: {})
    return InfrastructureRuntime(
        load_environment_config(CONFIG), "bicep", directory=tmp_path / "infra"
    )


def test_bicep_preview_reports_noop_and_private_artifact(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "_preflight", lambda: {})
    calls = []

    def execute(operation):
        calls.append(operation)
        return {"changes": [{"changeType": "NoChange"}]}

    monkeypatch.setattr(runtime, "_bicep", execute)
    assert runtime.plan()["noOp"] is True
    assert calls == ["validate", "what-if"]
    assert (runtime.directory / "preview.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "response",
    [None, [], "invalid", {}, {"changes": [None]}, {"changes": [{"changeType": "Unknown"}]}],
)
def test_malformed_preview_is_a_structured_failure(runtime, monkeypatch, response):
    monkeypatch.setattr(runtime, "_preflight", lambda: {})
    monkeypatch.setattr(runtime, "_bicep", lambda *args: response)
    with pytest.raises(ValueError):
        runtime.plan()


@pytest.mark.parametrize(
    "delta",
    [
        None,
        [],
        [
            {
                "path": "properties.networkProfile",
                "propertyChangeType": "Modify",
                "children": [{"path": "podCidr", "propertyChangeType": "Delete"}],
            }
        ],
        [{"path": "properties.networkProfile.podCidr", "propertyChangeType": "Modify"}],
        [{"path": "properties", "propertyChangeType": "Array"}],
    ],
)
def test_bicep_nested_destructive_preview_is_refused(runtime, monkeypatch, delta):
    monkeypatch.setattr(runtime, "_preflight", lambda: {})
    monkeypatch.setattr(
        runtime, "_bicep", lambda *args: {"changes": [{"changeType": "Modify", "delta": delta}]}
    )
    with pytest.raises(ValueError):
        runtime.plan()


def test_bicep_recognized_metadata_delta_is_allowed(runtime):
    runtime._check_bicep_delta(
        [
            {
                "path": "tags",
                "propertyChangeType": "Modify",
                "children": [{"path": "tags.owner", "propertyChangeType": "Modify"}],
            }
        ]
    )


def test_partial_cleanup_requires_owner_tag(runtime, monkeypatch):
    _config, outputs, _observed = live_fixture()
    group = {
        "id": outputs.resource_group.id,
        "name": outputs.resource_group.name,
        "tags": {"aiks-instance": "instance"},
    }
    monkeypatch.setattr(
        runtime.azure,
        "json",
        lambda *args: [
            {"tags": {"aiks-instance": "instance", "aiks-engine": "bicep", "aiks-owner": "other"}}
        ],
    )
    with runtime.session():
        (runtime.directory / "intent.json").write_text(
            json.dumps({"engine": "bicep", "owner": runtime.owner, "instance": "instance"})
        )
        with pytest.raises(ValueError, match="ownership"):
            runtime._destroy_partial(group, "dev", False)


def test_cross_engine_group_refuses_before_plan(runtime, monkeypatch):
    group = {
        "name": "rg-aiks-dev-dev-abcdefgh",
        "id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-aiks-dev-dev-abcdefgh",
        "tags": {
            "aiks-managed": "true",
            "environment": "dev",
            "aiks-engine": "terraform",
            "aiks-owner": "aiks-dev-dev",
        },
    }
    monkeypatch.setattr(runtime.azure, "json", lambda *args: [group])
    monkeypatch.setattr(runtime, "_bicep", lambda *args: pytest.fail("must refuse before preview"))
    with pytest.raises(ValueError, match="another engine"):
        runtime.plan()


def test_environment_workspace_is_locked(runtime):
    with runtime.session(), pytest.raises(ValueError, match="another operation"), runtime.session():
        pass


def test_terraform_preview_uses_saved_plan(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "_preflight", lambda: {})
    runtime.engine = "terraform"
    calls = []
    monkeypatch.setattr(runtime, "_prepare_terraform", lambda: None)

    def execute(*arguments):
        calls.append(arguments)
        return json.dumps({"resource_changes": [{"change": {"actions": ["create"]}}]})

    monkeypatch.setattr(runtime, "_terraform", execute)
    assert runtime.plan()["changes"] == 1
    assert calls[0][0] == "plan" and "-out=environment.tfplan" in calls[0]
    assert calls[1] == ("show", "-json", "environment.tfplan")


@pytest.mark.parametrize(
    "change",
    [
        {"actions": ["delete", "create"]},
        {"actions": ["update"], "before": {"id": "/subscriptions/other/resourceGroups/foreign"}},
    ],
)
def test_plan_cannot_replace_or_touch_foreign_resources(runtime, change):
    with pytest.raises(ValueError):
        runtime._check_terraform_plan({"resource_changes": [{"change": change}]})


def test_graph_snapshot_refuses_truncation(runtime, monkeypatch):
    from test_workload import foundation

    monkeypatch.setattr(runtime.azure, "json", lambda *args: {"data": [], "$skipToken": "more"})
    with pytest.raises(ValueError, match="incomplete"):
        runtime._snapshot(foundation())


@pytest.mark.parametrize("engine", ["bicep", "terraform"])
def test_complete_mocked_lifecycle(runtime, monkeypatch, engine):
    _config, outputs, observed = live_fixture()
    runtime.engine = engine
    runtime.azure.tenant = SUBSCRIPTION
    events = []
    cloud = {"exists": False, "instance": None}

    def group():
        return {
            "name": outputs.resource_group.name,
            "id": outputs.resource_group.id,
            "tags": {
                "aiks-managed": "true",
                "environment": "dev",
                "aiks-engine": engine,
                "aiks-owner": runtime.owner,
                "aiks-instance": cloud["instance"],
            },
        }

    def azure(*arguments):
        events.append(arguments[:2])
        if arguments[:2] == ("group", "list"):
            return [group()] if cloud["exists"] else []
        if arguments[:2] == ("deployment", "sub"):
            return {
                "properties": {
                    "outputs": {"result": {"value": outputs.model_dump(mode="json", by_alias=True)}}
                }
            }
        if arguments[:2] == ("group", "exists"):
            return cloud["exists"]
        if arguments[:2] == ("keyvault", "list-deleted"):
            return [{"name": outputs.vault.name}]
        pytest.fail(f"unexpected Azure boundary: {arguments[:2]}")

    def execute(*arguments, **kwargs):
        events.append(arguments[:4])
        if arguments[0] == "bicep":
            Path(arguments[-1]).write_text("{}")
            return ""
        if arguments[:3] == ("az", "deployment", "sub"):
            if arguments[3] == "what-if":
                assert "--no-pretty-print" in arguments
                assert arguments[arguments.index("--result-format") + 1] == "FullResourcePayloads"
            if arguments[3] == "create":
                cloud.update(exists=True, instance=runtime.instance)
            return json.dumps(
                {"changes": [{"changeType": "NoChange" if cloud["exists"] else "Create"}]}
            )
        if arguments[:3] == ("az", "group", "delete"):
            cloud["exists"] = False
            return ""
        assert arguments[0] == "terraform"
        operation = arguments[2]
        if operation == "apply":
            cloud.update(exists=arguments[-1] != "destroy.tfplan", instance=runtime.instance)
        if operation == "output":
            return outputs.model_dump_json(by_alias=True)
        if operation == "show":
            return json.dumps(
                {
                    "resource_changes": [
                        {"change": {"actions": ["no-op" if cloud["exists"] else "create"]}}
                    ]
                }
            )
        return "{}"

    class Workload:
        def install(self):
            events.append(("workload", "install"))
            return {"gatewayAddress": "203.0.113.2"}

        def verify(self):
            events.append(("workload", "verify"))
            return {"gatewayAddress": "203.0.113.2"}

        def uninstall(self):
            events.append(("workload", "uninstall"))

    monkeypatch.setattr(runtime.azure, "json", azure)
    monkeypatch.setattr(runtime, "_run", execute)
    monkeypatch.setattr(runtime, "_preflight", lambda: {})
    monkeypatch.setattr(runtime, "_observed", lambda value: observed)
    monkeypatch.setattr(runtime, "_verify_roles", lambda *args: None)
    monkeypatch.setattr(runtime, "_credentials", lambda value: Workload())
    monkeypatch.setattr(runtime, "_snapshot", lambda value: {"cluster": 1})
    monkeypatch.setattr(
        runtime, "_remove_empty_environment_state", lambda: events.append(("state", "prune-empty"))
    )
    monkeypatch.setattr("aiks.infrastructure.probe_host", lambda *args, **kwargs: ["203.0.113.2"])
    monkeypatch.setattr(
        "aiks.infrastructure.verify_observability", lambda *args: {"logs": "ingesting"}
    )
    assert runtime.deploy(confirmed_environment="dev")["phase"] == "verified"
    assert cloud["exists"] and (runtime.directory / "owner.json").exists()
    assert runtime.verify()["readiness"] == "verified"
    original = cloud["instance"]
    cloud["instance"] = "replacement"
    with pytest.raises(ValueError, match="replaced"):
        runtime.destroy(confirmed_environment="dev")
    cloud["instance"] = original
    assert runtime.destroy(confirmed_environment="dev")["backendRetained"] is True
    assert not cloud["exists"]
    assert events.index(("workload", "uninstall")) < max(
        index for index, event in enumerate(events) if "delete" in event or "apply" in event
    )


@pytest.mark.parametrize("blocked", [False, True])
def test_only_empty_environment_state_is_removed(runtime, monkeypatch, blocked):
    runtime.engine = "terraform"
    deleted = []
    calls = []
    key = "aiks-dev-dev/environment.tfstate"

    class Backend:
        subscription = SUBSCRIPTION

        def inventory(self):
            return [] if deleted else [{"name": key, "properties": {"leaseStatus": "unlocked"}}]

        def _blob(self, *arguments):
            calls.append(arguments)
            if arguments[0] == "download":
                Path(arguments[arguments.index("--file") + 1]).write_text(
                    json.dumps(
                        {
                            "version": 4,
                            "lineage": "synthetic-lineage",
                            "serial": 1,
                            "outputs": {},
                            "resources": [{"mode": "managed"}] if blocked else [],
                        }
                    )
                )
            elif arguments[0] == "delete":
                deleted.append(key)

    monkeypatch.setattr("aiks.state.StateBackend", lambda config, **kwargs: Backend())
    with runtime.session():
        if blocked:
            with pytest.raises(ValueError, match="not empty"):
                runtime._remove_empty_environment_state()
        else:
            runtime._remove_empty_environment_state()
    assert bool(deleted) is not blocked
    assert any(call[:2] == ("lease", "release") for call in calls) is blocked


@pytest.mark.parametrize("environment", ["dev", "production"])
def test_resource_reads_and_private_credentials(runtime, monkeypatch, environment):
    runtime.config, outputs, _observed = live_fixture(environment)
    calls = []

    def azure(*arguments):
        calls.append(arguments)
        if arguments[0] == "rest":
            from urllib.parse import urlsplit

            return {"id": urlsplit(arguments[arguments.index("--url") + 1]).path}
        assert arguments[:2] == ("aks", "get-credentials")
        return None

    monkeypatch.setattr(runtime.azure, "json", azure)
    commands = []
    monkeypatch.setattr(
        runtime,
        "_run",
        lambda *args, **kwargs: commands.append(args) or "Merged context into private file",
    )
    observed = runtime._observed(outputs)
    assert observed["cluster"]["id"] == outputs.cluster.id
    if environment == "production":
        expected_zone = (
            outputs.resource_group.id
            + "/providers/Microsoft.Network/privateDnsZones/private.westus3.azmk8s.io"
        )
        assert observed["privateDnsZone"]["id"] == expected_zone
        assert observed["privateDnsLink"]["id"] == expected_zone + "/virtualNetworkLinks/test"
    with runtime.session():
        workload = runtime._credentials(outputs)
        assert workload.context == outputs.cluster.name
        assert workload.environment is runtime.azure.environment
        assert workload.kubeconfig.stat().st_mode & 0o777 == 0o600
        assert commands[0][:3] == ("az", "aks", "get-credentials")
        assert "--subscription" in commands[0]
    monkeypatch.setattr(runtime.azure, "json", lambda *args: {"id": "other"})
    with pytest.raises(ValueError, match="identity"):
        runtime._resource(outputs.cluster.id, "2026-04-01")


@pytest.mark.parametrize("environment", ["dev", "production"])
def test_live_role_inventory(runtime, monkeypatch, environment):
    config, outputs, observed = live_fixture(environment)
    runtime.config = config
    contract = json.loads((CONFIG.parent.parent / "parity/contract.json").read_text())
    roles = contract["roles"]
    admin = str(config.spec.identity.admin_group_object_id)
    expected = [
        (outputs.cluster.id, admin, "Azure Kubernetes Service Cluster User Role"),
        (outputs.cluster.id, admin, "Azure Kubernetes Service RBAC Cluster Admin"),
        (outputs.network.vnet_id, outputs.identities.cluster.principal_id, "Network Contributor"),
        (
            outputs.registry.id,
            observed["cluster"]["properties"]["identityProfile"]["kubeletidentity"]["objectId"],
            "AcrPull",
        ),
        (outputs.vault.id, outputs.identities.readiness.principal_id, "Key Vault Reader"),
    ]
    if environment == "production":
        expected.extend(
            [
                (
                    observed["cluster"]["properties"]["apiServerAccessProfile"]["privateDNSZone"],
                    outputs.identities.cluster.principal_id,
                    "Private DNS Zone Contributor",
                ),
                (outputs.monitoring.grafana_id, admin, "Grafana Admin"),
                (
                    outputs.monitoring.azure_monitor_workspace_id,
                    "grafana-principal",
                    "Monitoring Reader",
                ),
            ]
        )
    assignments = [
        {"scope": scope, "principalId": principal, "roleDefinitionId": "/roles/" + roles[role]}
        for scope, principal, role in expected
    ]
    monkeypatch.setattr(runtime.azure, "json", lambda *args: assignments)
    monkeypatch.setattr(
        runtime, "_resource", lambda *args: {"identity": {"principalId": "grafana-principal"}}
    )
    runtime._verify_roles(outputs, observed)
    assignments.append(
        {
            "scope": outputs.resource_group.id,
            "principalId": admin,
            "roleDefinitionId": "/roles/owner",
        }
    )
    with pytest.raises(ValueError, match="exact"):
        runtime._verify_roles(outputs, observed)


def test_private_endpoint_and_load_balancer_bindings(runtime, monkeypatch):
    from ipaddress import ip_network

    config, outputs, observed = live_fixture("production")
    runtime.config = config
    base = outputs.resource_group.name.removeprefix("rg-")
    address = str(ip_network(config.spec.network.private_endpoint_subnet_cidr)[4])
    api_address = str(ip_network(config.spec.network.api_server_subnet_cidr)[4])
    frontend = {"privateIPAddress": address}
    zone_drift = []

    def resource(identifier, version):
        if "/privateDnsZoneGroups/" in identifier:
            zone = (
                "privatelink.azurecr.io"
                if "/pe-acr-" in identifier
                else "privatelink.vaultcore.azure.net"
            )
            zone_id = (
                outputs.resource_group.id + "/providers/Microsoft.Network/privateDnsZones/" + zone
            )
            return {
                "properties": {
                    "privateDnsZoneConfigs": [
                        {"properties": {"privateDnsZoneId": "foreign" if zone_drift else zone_id}}
                    ]
                }
            }
        if "/virtualNetworkLinks/" in identifier:
            return {
                "properties": {
                    "virtualNetwork": {"id": outputs.network.vnet_id},
                    "registrationEnabled": False,
                }
            }
        if "networkInterfaces" in identifier:
            return {
                "properties": {"ipConfigurations": [{"properties": {"privateIPAddress": address}}]}
            }
        target = outputs.registry.id if identifier.endswith("pe-acr-" + base) else outputs.vault.id
        return {
            "properties": {
                "privateLinkServiceConnections": [
                    {
                        "properties": {
                            "privateLinkServiceId": target,
                            "privateLinkServiceConnectionState": {"status": "Approved"},
                        }
                    }
                ],
                "networkInterfaces": [
                    {
                        "id": outputs.resource_group.id
                        + "/providers/Microsoft.Network/networkInterfaces/test"
                    }
                ],
            }
        }

    monkeypatch.setattr(runtime, "_resource", resource)
    monkeypatch.setattr(
        "aiks.infrastructure.probe_host",
        lambda host, **kwargs: [api_address if host == outputs.cluster.fqdn else address],
    )
    properties = {
        "frontendIPConfigurations": [{"id": "private", "properties": frontend}],
        "loadBalancingRules": [{"properties": {"frontendIPConfiguration": {"id": "private"}}}],
    }
    monkeypatch.setattr(
        runtime.azure,
        "json",
        lambda *args: {"value": [{"properties": properties}]},
    )

    class Workload:
        def _get(self, *args):
            return {"status": {"addresses": [{"value": address}]}}

    runtime._private_frontends(outputs, observed, Workload(), {"gatewayAddress": address})
    zone_drift.append(True)
    with pytest.raises(ValueError, match="DNS-zone binding"):
        runtime._private_frontends(outputs, observed, Workload(), {"gatewayAddress": address})
    zone_drift.clear()
    properties["frontendIPConfigurations"].append(
        {"id": "egress", "properties": {"publicIPAddress": {"id": "public"}}}
    )
    properties["outboundRules"] = [{"properties": {"frontendIPConfigurations": [{"id": "egress"}]}}]
    runtime._private_frontends(outputs, observed, Workload(), {"gatewayAddress": address})
    properties["loadBalancingRules"].append(
        {"properties": {"frontendIPConfiguration": {"id": "egress"}}}
    )
    with pytest.raises(ValueError, match="public inbound"):
        runtime._private_frontends(outputs, observed, Workload(), {"gatewayAddress": address})
    properties["loadBalancingRules"].pop()
    del properties["outboundRules"]
    with pytest.raises(ValueError, match="public inbound"):
        runtime._private_frontends(outputs, observed, Workload(), {"gatewayAddress": address})
    properties["frontendIPConfigurations"].pop()
    frontend["publicIPAddress"] = {"id": "public"}
    with pytest.raises(ValueError, match="public"):
        runtime._private_frontends(outputs, observed, Workload(), {"gatewayAddress": address})
    monkeypatch.setattr(runtime.azure, "json", lambda *args: {"value": [], "nextLink": "more"})
    with pytest.raises(ValueError, match="incomplete"):
        runtime._private_frontends(outputs, observed, Workload(), {"gatewayAddress": address})


def test_resource_graph_sanitizes_inventory(runtime, monkeypatch):
    _config, outputs, _observed = live_fixture()
    resources = [
        {"id": model.id, "type": kind}
        for model, kind in (
            (outputs.cluster, "Microsoft.ContainerService/managedClusters"),
            (outputs.registry, "Microsoft.ContainerRegistry/registries"),
            (outputs.vault, "Microsoft.KeyVault/vaults"),
            (outputs.identities.cluster, "Microsoft.ManagedIdentity/userAssignedIdentities"),
            (outputs.identities.readiness, "Microsoft.ManagedIdentity/userAssignedIdentities"),
        )
    ]
    resources += [
        {"id": outputs.network.vnet_id, "type": "Microsoft.Network/virtualNetworks"},
        {
            "id": outputs.monitoring.log_analytics_id,
            "type": "Microsoft.OperationalInsights/workspaces",
        },
    ]
    monkeypatch.setattr(runtime.azure, "json", lambda *args: {"data": resources})
    monkeypatch.setattr(runtime, "_complete_inventory", lambda *args: None)
    with runtime.session():
        result = runtime._snapshot(outputs)
    assert result["microsoft.containerregistry/registries"] == 1
    assert SUBSCRIPTION not in (runtime.directory / "inventory.json").read_text()
    resources.append({"id": "unrelated", "type": "Microsoft.Compute/virtualMachines"})
    with pytest.raises(ValueError, match="unexpected resource type"), runtime.session():
        runtime._snapshot(outputs)


def test_telemetry_propagation_retries_only_pending(runtime, monkeypatch):
    from aiks.observability import TelemetryPendingError

    _config, outputs, _observed = live_fixture()
    calls = []
    delays = []

    def inspect(*args):
        calls.append(True)
        if len(calls) == 1:
            raise TelemetryPendingError("waiting")
        return {"logs": "ingesting"}

    monkeypatch.setattr("aiks.infrastructure.verify_observability", inspect)
    monkeypatch.setattr("aiks.infrastructure.sleep", delays.append)
    assert runtime._monitoring(outputs)["logs"] == "ingesting"
    assert len(delays) == 1

    def drift(*args):
        raise ValueError("security drift")

    monkeypatch.setattr("aiks.infrastructure.verify_observability", drift)
    with pytest.raises(ValueError, match="security drift"):
        runtime._monitoring(outputs)
    assert len(delays) == 1


@pytest.mark.parametrize("fail", [False, True])
def test_alert_drill_always_restores_replicas(runtime, monkeypatch, fail):
    _config, outputs, _observed = live_fixture()
    runtime.config.spec.observability.managed_prometheus = True
    commands = []

    class Workload:
        def verify(self):
            return {"ready": True}

        def _get(self, *args):
            return {"spec": {"replicas": 2}}

        def _kubectl(self, *args):
            commands.append(args)

    def wait(*args):
        if fail:
            raise ValueError("alert evidence timed out")

    monkeypatch.setattr(runtime, "_owned_group", lambda **kwargs: {})
    monkeypatch.setattr(runtime, "_outputs", lambda: outputs)
    monkeypatch.setattr(runtime, "_credentials", lambda value: Workload())
    monkeypatch.setattr(runtime, "_wait_alert", wait)
    if fail:
        with pytest.raises(ValueError, match="timed out"):
            runtime.exercise_alerts(confirmed_environment="dev")
    else:
        assert runtime.exercise_alerts(confirmed_environment="dev")["replicasRestored"]
    assert commands[0][-1] == "--replicas=0"
    assert commands[-1][-1] == "--replicas=2"


@pytest.mark.parametrize("failure", [None, "stale", "paginated"])
def test_alert_evidence_must_be_fresh_and_complete(runtime, monkeypatch, failure):
    _config, outputs, _observed = live_fixture("production")
    since = datetime(2026, 9, 24, tzinfo=UTC)
    response = {
        "value": [
            {
                "properties": {
                    "essentials": {
                        "alertRule": "ReadinessUnavailable",
                        "monitorCondition": "Fired",
                        "targetResource": outputs.monitoring.azure_monitor_workspace_id,
                        "lastModifiedDateTime": "2026-09-24T01:00:00Z"
                        if failure != "stale"
                        else "2026-09-23T00:00:00Z",
                    }
                }
            }
        ]
    }
    if failure == "paginated":
        response["nextLink"] = "more"
    monkeypatch.setattr(runtime.azure, "json", lambda *args: response)
    times = iter([0, 2000])
    monkeypatch.setattr("aiks.infrastructure.perf_counter", lambda: next(times))
    if failure:
        with pytest.raises(ValueError):
            runtime._wait_alert(outputs, "Fired", since)
    else:
        runtime._wait_alert(outputs, "Fired", since)


@pytest.mark.parametrize("engine", ["bicep", "terraform"])
def test_runtime_preflight_checks_backend_without_bootstrapping(runtime, monkeypatch, engine):
    runtime.engine = engine
    checked = []
    monkeypatch.setattr(
        "aiks.infrastructure.cloud_preflight", lambda *args: {"permissions": "verified"}
    )

    class Backend:
        subscription = SUBSCRIPTION

        def status(self):
            checked.append(True)

    monkeypatch.setattr("aiks.state.StateBackend", lambda config, **kwargs: Backend())
    assert runtime.preflight()["engine"] == engine
    assert bool(checked) is (engine == "terraform")


def test_partial_cleanup_refuses_unowned_resource(runtime, monkeypatch):
    group = {
        "id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-aiks-dev-dev-abcdefgh",
        "name": "rg-aiks-dev-dev-abcdefgh",
        "tags": {"aiks-instance": "instance"},
    }
    monkeypatch.setattr(runtime.azure, "json", lambda *args: [{"name": "unrelated", "tags": {}}])
    with runtime.session():
        (runtime.directory / "intent.json").write_text(
            json.dumps({"engine": "bicep", "owner": runtime.owner, "instance": "instance"})
        )
        with pytest.raises(ValueError, match="without verifiable ownership"):
            runtime._destroy_partial(group, "dev", False)


@pytest.mark.parametrize("engine", ["bicep", "terraform"])
def test_partial_cleanup_requires_verified_deletion(runtime, monkeypatch, engine):
    _config, outputs, _observed = live_fixture()
    runtime.engine = engine
    group = {
        "id": outputs.resource_group.id,
        "name": outputs.resource_group.name,
        "tags": {"aiks-instance": "instance"},
    }
    deleted = []

    def azure(*arguments):
        if arguments[:2] == ("resource", "list"):
            return [
                {
                    "tags": {
                        "aiks-instance": "instance",
                        "aiks-engine": engine,
                        "aiks-owner": runtime.owner,
                    }
                }
            ]
        if arguments[:2] == ("group", "exists"):
            return not deleted
        pytest.fail("unexpected call")

    def execute(*arguments):
        if arguments[0] == "show":
            return json.dumps({"resource_changes": []})
        if arguments[0] == "apply" or arguments[:3] == ("az", "group", "delete"):
            deleted.append(True)
        return ""

    monkeypatch.setattr(runtime.azure, "json", azure)
    monkeypatch.setattr(runtime, "_run", execute)
    monkeypatch.setattr(runtime, "_terraform", execute)
    monkeypatch.setattr(runtime, "_prepare_terraform", lambda: None)
    monkeypatch.setattr(runtime, "_check_terraform_plan", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "_remove_empty_environment_state", lambda: None)
    monkeypatch.setattr(runtime, "_owned_group", lambda **kwargs: group)
    with runtime.session():
        (runtime.directory / "intent.json").write_text(
            json.dumps({"engine": engine, "owner": runtime.owner, "instance": "instance"})
        )
        assert runtime._destroy_partial(group, "dev", False)["environmentDeleted"]
    group["tags"]["aiks-instance"] = "other"
    with pytest.raises(ValueError, match="matching deployment intent"):
        runtime.destroy(confirmed_environment="dev", allow_partial=True)


def test_runtime_confirmation_and_local_guard_failures(runtime, monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="confirmation"):
        runtime.deploy(confirmed_environment="wrong")
    runtime.config.spec.environment = "production"
    with pytest.raises(ValueError, match="production mutation"):
        runtime.destroy(confirmed_environment="production")
    monkeypatch.setattr("aiks.infrastructure.fcntl", None)
    with pytest.raises(ValueError, match="POSIX"), runtime.session():
        pass


def test_workspace_symlinks_are_rejected(runtime, tmp_path):
    runtime.directory.parent.mkdir(parents=True)
    runtime.directory.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"), runtime.session():
        pass


def test_lock_replacement_race_never_follows_symlink(runtime, monkeypatch, tmp_path):
    import os

    target = tmp_path / "unrelated.txt"
    target.write_text("unchanged")
    target.chmod(0o644)
    original = os.open

    def racing_open(path, flags, mode=0o777, **kwargs):
        path = Path(path)
        if path.name == ".operation.lock":
            lock_path = runtime.directory / path.name
            lock_path.unlink(missing_ok=True)
            lock_path.symlink_to(target)
        return original(path, flags, mode, **kwargs)

    monkeypatch.setattr("aiks.infrastructure.os.open", racing_open)
    with pytest.raises((OSError, ValueError)), runtime.session():
        pytest.fail("symlink race must refuse lock acquisition")
    assert target.read_text() == "unchanged"
    assert target.stat().st_mode & 0o777 == 0o644


def test_workspace_component_swap_does_not_redirect_lock(runtime, monkeypatch, tmp_path):
    import os

    external = tmp_path / "external"
    external.mkdir()
    workspace = runtime.directory
    moved = workspace.with_name(workspace.name + "-moved")
    previous = Path.cwd()
    original = os.open

    def racing_open(path, flags, mode=0o777, **kwargs):
        if str(path) == ".operation.lock":
            workspace.rename(moved)
            workspace.symlink_to(external, target_is_directory=True)
        return original(path, flags, mode, **kwargs)

    monkeypatch.setattr("aiks.infrastructure.os.open", racing_open)
    with pytest.raises(ValueError, match="directory changed"), runtime.session():
        pytest.fail("replaced directory must not receive artifacts")
    assert not list(external.iterdir())
    assert (moved / ".operation.lock").is_file()
    assert Path.cwd() == previous


def test_kubeconfig_replacement_does_not_write_through_symlink(runtime, monkeypatch, tmp_path):
    _config, outputs, _observed = live_fixture()
    external = tmp_path / "unrelated"
    external.write_text("unchanged")

    def execute(*arguments, **kwargs):
        if arguments[:3] == ("az", "aks", "get-credentials"):
            destination = runtime.directory / "kubeconfig"
            destination.symlink_to(external)
            temporary = Path(arguments[arguments.index("--file") + 1])
            assert temporary != destination
            temporary.write_text("synthetic kubeconfig")
        return "Merged context"

    monkeypatch.setattr(runtime, "_run", execute)
    with runtime.session():
        runtime._credentials(outputs)
    assert external.read_text() == "unchanged"
    assert not (runtime.directory / "kubeconfig").is_symlink()
    assert (runtime.directory / "kubeconfig").read_text() == "synthetic kubeconfig"


def test_credential_artifact_refuses_nonregular_file(runtime, tmp_path):
    import os

    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="regular"):
        runtime._regular_file(fifo)


def test_cloud_inventory_must_have_valid_shape(runtime, monkeypatch):
    monkeypatch.setattr(runtime.azure, "json", lambda *args: {"unexpected": []})
    with pytest.raises(ValueError, match="unverifiable"):
        runtime._groups()


def test_native_process_failure_does_not_echo_output(runtime, monkeypatch):
    from aiks.process import CommandResult

    monkeypatch.setattr(
        "aiks.infrastructure.run_command",
        lambda arguments, **kwargs: CommandResult(
            tuple(arguments), 1, "private-value", "private-value"
        ),
    )
    with pytest.raises(ValueError) as failure:
        runtime._run("terraform", "plan")
    assert "private-value" not in str(failure.value)


def test_verify_discovers_only_an_unambiguous_engine(runtime, monkeypatch, tmp_path):
    group = {"name": "rg-aiks-dev-dev-abcdefgh", "tags": {"aiks-engine": "bicep"}}
    monkeypatch.setattr(runtime.azure, "json", lambda *args: [group])
    monkeypatch.setattr("aiks.infrastructure.AzureSession", lambda: runtime.azure)
    discovered = InfrastructureRuntime(runtime.config, None, directory=tmp_path / "discovery")
    assert discovered.engine == "bicep"
    monkeypatch.setattr(runtime.azure, "json", lambda *args: [group, group])
    with pytest.raises(ValueError, match="discover"):
        InfrastructureRuntime(runtime.config, None, directory=tmp_path / "ambiguous")


def test_outputs_cannot_inject_group_name_with_valid_identifier(runtime, monkeypatch):
    _config, outputs, _observed = live_fixture()
    group = {"id": outputs.resource_group.id, "name": outputs.resource_group.name}
    outputs.resource_group.name = "other' | union Resources | where true or '"
    monkeypatch.setattr(runtime, "_owned_group", lambda **kwargs: group)
    monkeypatch.setattr(
        runtime.azure,
        "json",
        lambda *args: {
            "properties": {
                "outputs": {"result": {"value": outputs.model_dump(mode="json", by_alias=True)}}
            }
        },
    )
    with pytest.raises(ValueError, match="outputs do not match"):
        runtime._outputs()


@pytest.mark.parametrize("environment", ["dev", "production"])
def test_complete_inventory_counts_children_and_exact_service_interfaces(
    runtime, monkeypatch, environment
):
    config, outputs, _observed = live_fixture(environment)
    runtime.config = config
    contract = json.loads((CONFIG.parent.parent / "parity/contract.json").read_text())
    counts = {kind.lower(): modes[environment] for kind, modes in contract["inventory"].items()}
    resources = []
    interfaces = {}
    network_prefix = outputs.resource_group.id + "/providers/Microsoft.Network/"
    if environment == "production":
        for index in range(3):
            resources.append(
                {
                    "id": f"{network_prefix}privateDnsZones/zone{index}",
                    "type": "Microsoft.Network/privateDnsZones",
                }
            )
        for index in range(2):
            endpoint = f"{network_prefix}privateEndpoints/endpoint{index}"
            interface = f"{network_prefix}networkInterfaces/interface{index}"
            resources.extend(
                [
                    {"id": endpoint, "type": "Microsoft.Network/privateEndpoints"},
                    {"id": interface, "type": "Microsoft.Network/networkInterfaces"},
                ]
            )
            interfaces[endpoint] = interface
        counts["microsoft.network/networkinterfaces"] = 2

    def collection(identifier, version):
        if identifier == outputs.identities.cluster.id + "/federatedIdentityCredentials":
            return []
        count = (
            4
            if identifier.endswith("/subnets")
            else (2 if environment == "production" else 1)
            if identifier.endswith("/dataCollectionRuleAssociations")
            else 1
        )
        return [{} for _ in range(count)]

    monkeypatch.setattr(runtime, "_collection", collection)
    monkeypatch.setattr(
        runtime,
        "_resource",
        lambda identifier, version: {
            "properties": {"networkInterfaces": [{"id": interfaces[identifier]}]}
        },
    )
    roles = [
        {"scope": outputs.resource_group.id} for _ in range(8 if environment == "production" else 5)
    ]
    monkeypatch.setattr(runtime.azure, "json", lambda *args: roles)
    runtime._complete_inventory(outputs, counts, resources)
    roles.append({"scope": outputs.resource_group.id})
    with pytest.raises(ValueError, match="shared parity"):
        runtime._complete_inventory(outputs, counts, resources)


@pytest.mark.parametrize(
    "response,valid",
    [
        ({"value": [{"id": "resource"}]}, True),
        (None, False),
        ({"value": [], "nextLink": "more"}, False),
        ({"value": [None]}, False),
    ],
)
def test_child_collection_requires_complete_structured_response(
    runtime, monkeypatch, response, valid
):
    monkeypatch.setattr(runtime.azure, "json", lambda *args: response)
    if valid:
        assert runtime._collection("/resource/children", "2025-01-01") == response["value"]
    else:
        with pytest.raises(ValueError):
            runtime._collection("/resource/children", "2025-01-01")


@pytest.mark.parametrize(
    "change",
    [
        None,
        "invalid",
        {"change": None},
        {"change": {"actions": "delete"}},
        {"change": {"actions": [None]}},
        {"change": {"actions": ["update"], "before": []}},
        {"change": {"actions": ["update"], "before": {"id": 4}}},
    ],
)
def test_malformed_terraform_change_fails_cleanly(runtime, change):
    with pytest.raises(ValueError):
        runtime._check_terraform_plan({"resource_changes": [change]})


def test_terraform_missing_change_collection_is_not_a_noop(runtime):
    with pytest.raises(ValueError, match="collection"):
        runtime._check_terraform_plan({})


@pytest.mark.parametrize("action", ["create", "update"])
def test_destroy_plan_cannot_apply_noncleanup_actions(runtime, monkeypatch, action):
    monkeypatch.setattr(runtime, "_owned_group", lambda **kwargs: {})
    with pytest.raises(ValueError, match="non-cleanup"):
        runtime._check_terraform_plan(
            {"resource_changes": [{"change": {"actions": [action]}}]}, destroy=True
        )
