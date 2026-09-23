"""Bicep input and infrastructure contract tests."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from aiks.config import load_environment_config
from aiks.engines.bicep import (
    deployment_command,
    destroy_command,
    parameters,
    template_path,
    write_parameters,
)
from aiks.outputs import FoundationOutputs

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "infrastructure/aks-automatic/config"


@pytest.mark.parametrize("environment", ["dev", "production"])
def test_parameters_match_environment_contract(environment: str) -> None:
    config = load_environment_config(CONFIG / f"{environment}.example.yaml")
    document = json.loads(json.dumps(parameters(config)))
    values = {name: entry["value"] for name, entry in document["parameters"].items()}
    assert set(values) == {
        "environment",
        "location",
        "prefix",
        "adminGroupObjectId",
        "network",
        "observability",
        "tags",
    }
    assert values["environment"] == environment
    assert values["adminGroupObjectId"] == str(config.spec.identity.admin_group_object_id)
    assert values["network"] == config.spec.network.model_dump(mode="json", by_alias=True)
    assert values["observability"] == config.spec.observability.model_dump(
        mode="json", by_alias=True
    )
    assert values["prefix"] == config.spec.naming.prefix
    assert values["location"] == config.spec.location
    assert values["tags"] == config.spec.tags


SUBSCRIPTION = "11111111-1111-4111-8111-111111111111"


def test_parameter_file_and_source_assets(tmp_path: Path) -> None:
    config = load_environment_config(CONFIG / "dev.example.yaml")
    destination = tmp_path / "parameters with spaces.json"
    write_parameters(config, destination)
    assert json.loads(destination.read_text()) == parameters(config)
    with template_path() as template:
        assert template.is_file()
        assert (template.parent / "modules/aks.bicep").is_file()


@pytest.mark.parametrize("operation", ["validate", "what-if", "create"])
def test_deployment_command(operation: str, tmp_path: Path) -> None:
    config = load_environment_config(CONFIG / "dev.example.yaml")
    template = tmp_path / "template with spaces.bicep"
    parameter_file = tmp_path / "parameters.json"
    command = deployment_command(
        operation,
        config=config,
        subscription_id=SUBSCRIPTION,
        template=template,
        parameter_file=parameter_file,
    )
    assert command[:4] == ("az", "deployment", "sub", operation)
    assert command[command.index("--subscription") + 1] == SUBSCRIPTION
    assert command[command.index("--location") + 1] == "westus3"
    assert command[command.index("--template-file") + 1] == str(template)
    assert command[command.index("--parameters") + 1] == f"@{parameter_file}"


@pytest.mark.parametrize("environment", ["dev", "production"])
def test_destroy_guards(environment: str) -> None:
    config = load_environment_config(CONFIG / f"{environment}.example.yaml")
    group = f"rg-{config.spec.naming.prefix}-{environment}-abcd1234"
    resource_id = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{group}"
    arguments = {
        "config": config,
        "subscription_id": SUBSCRIPTION,
        "resource_group_id": resource_id,
    }
    with pytest.raises(ValueError, match="confirmation"):
        destroy_command(**arguments, confirmed_environment="wrong")
    if environment == "production":
        with pytest.raises(ValueError, match="override"):
            destroy_command(**arguments, confirmed_environment=environment)
    command = destroy_command(
        **arguments,
        confirmed_environment=environment,
        allow_production=True,
    )
    assert command == (
        "az",
        "group",
        "delete",
        "--subscription",
        SUBSCRIPTION,
        "--name",
        group,
        "--yes",
        "--only-show-errors",
    )
    for invalid in (resource_id + "/child", resource_id.replace(group, "other-group")):
        with pytest.raises(ValueError, match="does not match"):
            destroy_command(
                config=config,
                subscription_id=SUBSCRIPTION,
                resource_group_id=invalid,
                confirmed_environment=environment,
                allow_production=True,
            )


@pytest.fixture(scope="module")
def compiled():
    compiler = shutil.which("bicep")
    if compiler is None:
        if os.environ.get("AIKS_REQUIRE_BICEP") == "1":
            pytest.fail("Bicep is required for the infrastructure policy gate")
        pytest.skip("Bicep compiler not installed; dedicated infrastructure CI requires it")
    with template_path() as template:
        result = subprocess.run(
            [compiler, "build", str(template), "--stdout"],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    assert result.returncode == 0, result.stderr
    assert not result.stderr.strip(), result.stderr
    return json.loads(result.stdout)


def resource_named(template, symbol):
    resources = template["resources"]
    if isinstance(resources, dict):
        return resources[symbol]
    deployed_name = {"networking": "network"}.get(symbol, symbol)
    for resource in resources:
        if resource["name"] == deployed_name:
            return resource
    resource_type = {
        "cluster": "Microsoft.ContainerService/managedClusters",
        "registry": "Microsoft.ContainerRegistry/registries",
        "vault": "Microsoft.KeyVault/vaults",
    }[symbol]
    return next(resource for resource in resources if resource["type"] == resource_type)


def module_template(template, symbol):
    return resource_named(template, symbol)["properties"]["template"]


def all_resources(template):
    resources = template.get("resources", {})
    for resource in resources.values() if isinstance(resources, dict) else resources:
        yield resource
        if resource["type"] == "Microsoft.Resources/deployments":
            yield from all_resources(resource["properties"]["template"])


def test_compiled_inventory_and_version_policy(compiled):
    resources = list(all_resources(compiled))
    types = {resource["type"].lower() for resource in resources}
    required = {
        "Microsoft.Resources/resourceGroups",
        "Microsoft.ContainerService/managedClusters",
        "Microsoft.Network/virtualNetworks/subnets",
        "Microsoft.Network/privateDnsZones/virtualNetworkLinks",
        "Microsoft.Network/privateEndpoints/privateDnsZoneGroups",
        "Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials",
        "Microsoft.ContainerRegistry/registries",
        "Microsoft.KeyVault/vaults/keys",
        "Microsoft.Monitor/accounts",
        "Microsoft.OperationalInsights/workspaces",
        "Microsoft.Dashboard/grafana",
        "Microsoft.Insights/dataCollectionRules",
        "Microsoft.Insights/dataCollectionRuleAssociations",
        "Microsoft.Insights/diagnosticSettings",
        "Microsoft.Insights/actionGroups",
        "Microsoft.Insights/activityLogAlerts",
        "Microsoft.Insights/scheduledQueryRules",
        "Microsoft.AlertsManagement/prometheusRuleGroups",
    }
    assert {resource_type.lower() for resource_type in required} <= types
    for resource in resources:
        if "preview" in resource["apiVersion"]:
            assert resource["type"] == "Microsoft.ContainerRegistry/registries"
            assert resource["apiVersion"] == "2026-03-01-preview"


def test_cluster_security_and_dependency_policy(compiled):
    foundation = module_template(compiled, "foundation")
    cluster_deployment = resource_named(foundation, "cluster")
    dependencies = json.dumps(cluster_deployment["dependsOn"])
    assert all(name in dependencies for name in ("network", "cluster-identity", "monitoring"))
    cluster = resource_named(module_template(foundation, "cluster"), "cluster")
    assert cluster["apiVersion"] == "2026-04-01"
    assert cluster["sku"] == {"name": "Automatic", "tier": "Standard"}
    properties = cluster["properties"]
    assert properties["enableRBAC"] and properties["disableLocalAccounts"]
    assert properties["aadProfile"] == {"managed": True, "enableAzureRBAC": True}
    assert properties["securityProfile"]["workloadIdentity"]["enabled"]
    assert properties["oidcIssuerProfile"]["enabled"]
    assert properties["nodeProvisioningProfile"] == {"mode": "Auto", "defaultNodePools": "Auto"}
    assert properties["networkProfile"]["networkDataplane"] == "cilium"
    assert properties["networkProfile"]["networkPluginMode"] == "overlay"
    assert "serviceMeshProfile" not in properties
    assert properties["ingressProfile"]["gatewayAPI"]["installation"] == "Standard"
    assert "network" in json.dumps(resource_named(foundation, "registry")["dependsOn"])


def test_network_and_paas_security_policy(compiled):
    foundation = module_template(compiled, "foundation")
    network = module_template(foundation, "networking")
    for symbol in ("vnet", "subnets"):
        assert "onlyIfNotExists" in network["resources"][symbol]["@options"]
    assert "subnets" not in network["resources"]["vnet"]["properties"]
    assert network["resources"]["subnets"]["copy"]["batchSize"] == 1
    registry = resource_named(module_template(foundation, "registry"), "registry")["properties"]
    assert registry["networkRuleSet"]["defaultAction"] == "Deny"
    assert not registry["adminUserEnabled"] and not registry["anonymousPullEnabled"]
    assert registry["roleAssignmentMode"] == "LegacyRegistryPermissions"
    assert "virtualNetworkSubnetResourceId" in json.dumps(registry)
    assert (
        registry["publicNetworkAccess"] == "[if(parameters('production'), 'Disabled', 'Enabled')]"
    )
    assert registry["networkRuleBypassOptions"] == "None"
    vault = module_template(foundation, "vault")
    properties = vault["resources"]["vault"]["properties"]
    assert properties["enableRbacAuthorization"] and properties["enableSoftDelete"]
    assert properties["enablePurgeProtection"] == "[parameters('production')]"
    assert properties["networkAcls"]["defaultAction"] == "Deny"
    assert properties["networkAcls"]["bypass"] == "None"
    assert (
        properties["publicNetworkAccess"] == "[if(parameters('production'), 'Disabled', 'Enabled')]"
    )
    assert not vault["resources"]["marker"]["properties"]["attributes"]["exportable"]
    assert "onlyIfNotExists" in vault["resources"]["marker"]["@options"]
    deployments = foundation["resources"]
    endpoints = [
        resource
        for resource in deployments
        if resource["name"] in {"registry-endpoint", "vault-endpoint"}
    ]
    assert len(endpoints) == 2
    for endpoint in endpoints:
        assert endpoint["condition"] == "[variables('production')]"
    assert "or(variables('production'), parameters('network').privateCluster)" in json.dumps(
        foundation["variables"]["effectiveNetwork"]
    )


def test_exact_roles_federation_and_alerts(compiled):
    text = json.dumps(compiled)
    for role in (
        "4abbcc35-e782-43d8-92c5-2d3f1bd2253f",
        "b1ff04bb-8a4e-4dc4-8eb5-8693973ce19b",
        "4d97b98b-1d4f-4787-a291-c67834d212e7",
        "b12aa53e-6015-4669-85d0-8515ebb3ae7f",
        "7f951dda-4ed3-4680-a7ca-43fe172d538d",
        "21090545-7ca7-4776-b22c-e363652d74d2",
        "43d0d8ad-25c7-4714-9337-8ba259a9fe05",
    ):
        assert role in text
    for resource in all_resources(compiled):
        if resource["type"] == "Microsoft.Authorization/roleAssignments":
            assert "guid(" in resource["name"]
            assert resource["properties"]["principalType"] in {"Group", "ServicePrincipal"}
    assert "system:serviceaccount:aiks-readiness:readiness" in text
    assert "api://AzureADTokenExchange" in text
    assert "ReadinessUnavailable" in text and "NodeNotReady" in text
    assert "CpuRequestsPressure" in text and "MemoryRequestsPressure" in text
    assert "KubePodInventory" in text


def test_legacy_diagnostics_metric_contract(compiled):
    diagnostics = next(
        resource
        for resource in all_resources(compiled)
        if resource["type"].lower() == "microsoft.insights/diagnosticsettings"
    )
    assert diagnostics["apiVersion"] == "2016-09-01"
    assert diagnostics["name"] == "service"
    assert diagnostics["properties"]["metrics"] == [
        {"timeGrain": "PT1M", "enabled": True, "retentionPolicy": {"enabled": False, "days": 0}}
    ]
    assert "Microsoft.ContainerService/managedClusters" in diagnostics["scope"]
    assert (
        diagnostics["properties"]["workspaceId"] == "[parameters('monitoringInfo').logAnalyticsId]"
    )


def test_grafana_reader_is_scoped_to_metrics_workspace(compiled):
    reader_template = next(
        resource["properties"]["template"]
        for resource in all_resources(compiled)
        if resource["type"] == "Microsoft.Resources/deployments"
        and resource["properties"]["template"].get("variables", {}).get("monitoringReaderRole")
        == "43d0d8ad-25c7-4714-9337-8ba259a9fe05"
    )
    assignments = [
        resource
        for resource in all_resources(reader_template)
        if resource["type"] == "Microsoft.Authorization/roleAssignments"
    ]
    assert len(assignments) == 1
    assignment = assignments[0]
    assert "Microsoft.Monitor/accounts" in assignment["scope"]
    assert "workspaceName" in assignment["scope"]
    assert assignment["properties"]["principalId"] == "[parameters('principalId')]"
    assert assignment["properties"]["principalType"] == "ServicePrincipal"
    assert "monitoringReaderRole" in assignment["properties"]["roleDefinitionId"]


def test_output_schema_matches_template(compiled):
    foundation = module_template(compiled, "foundation")
    fields = set(foundation["outputs"]["result"]["value"])
    fields |= {"environment", "location", "resourceGroup"}
    assert fields == set(FoundationOutputs.model_json_schema(by_alias=True)["properties"])
    for resource in all_resources(compiled):
        assert resource["type"] != "Microsoft.KeyVault/vaults/secrets"
    assert "listKeys(" not in json.dumps(compiled)
    with pytest.raises(ValidationError):
        FoundationOutputs.model_validate({"kubeconfig": "not-allowed"})


def test_dashboard_contract():
    with template_path() as template:
        dashboard = json.loads((template.parent / "dashboards/foundation.json").read_text())
    assert dashboard["uid"] == "aiks-foundation"
    assert len(dashboard["panels"]) == 7
    assert len({panel["id"] for panel in dashboard["panels"]}) == 7
    for panel in dashboard["panels"]:
        assert panel["datasource"]["uid"] == "${metrics}"
        assert panel["targets"]
        for target in panel["targets"]:
            assert 'cluster="$cluster"' in target["expr"]


def test_normalized_output_fixture_rejects_extra_credentials():
    resource = {"name": "example", "id": "/subscriptions/example/resourceGroups/example"}
    identity = {**resource, "clientId": "client", "principalId": "principal"}
    value = {
        "environment": "dev",
        "location": "westus3",
        "resourceGroup": resource,
        "cluster": {**resource, "fqdn": "example.invalid"},
        "registry": {**resource, "loginServer": "example.azurecr.io"},
        "vault": {**resource, "uri": "https://example.vault.azure.net", "markerKeyName": "marker"},
        "network": {
            "vnetId": resource["id"],
            "subnetIds": {
                "apiServer": "api",
                "systemNode": "system",
                "userNode": "user",
                "privateEndpoint": "pe",
            },
        },
        "identities": {"cluster": identity, "readiness": identity},
        "monitoring": {
            "logAnalyticsId": "logs",
            "azureMonitorWorkspaceId": "",
            "prometheusQueryEndpoint": "",
            "grafanaId": "",
            "grafanaEndpoint": "",
        },
        "readiness": {
            "namespace": "aiks-readiness",
            "serviceAccount": "readiness",
            "clientId": "client",
            "tenantId": "tenant",
            "vaultUri": "https://example.vault.azure.net",
            "markerKeyName": "marker",
            "gatewayClassName": "approuting-istio",
            "internalGateway": False,
            "managedPrometheus": False,
        },
    }
    result = FoundationOutputs.model_validate(value)
    assert result.model_dump(by_alias=True) == value
    for field in ("kubeconfig", "token", "privateKey", "storageKey"):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            FoundationOutputs.model_validate({**value, field: "not-allowed"})
