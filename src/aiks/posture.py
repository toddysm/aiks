"""Pure assertions for observed Azure resources; failures contain no private values."""

from __future__ import annotations

from typing import Any

from aiks.config import EnvironmentConfig
from aiks.outputs import FoundationOutputs
from aiks.parity import value_at


def assert_properties(actual: Any, expected: dict[str, Any], category: str) -> None:
    for path, value in expected.items():
        try:
            observed = value_at(actual, path)
        except (KeyError, TypeError, IndexError) as error:
            raise ValueError(f"{category}: required field {path} is missing") from error
        if observed != value:
            raise ValueError(f"{category}: {path} posture drift")


def verify_foundation(
    config: EnvironmentConfig, outputs: FoundationOutputs, observed: dict[str, Any]
) -> dict[str, Any]:
    spec = config.spec
    production = spec.environment == "production"
    cluster = observed["cluster"]
    assert_properties(
        cluster,
        {
            "sku.name": "Automatic",
            "sku.tier": "Standard",
            "properties.provisioningState": "Succeeded",
            "properties.disableLocalAccounts": True,
            "properties.enableRBAC": True,
            "properties.aadProfile.managed": True,
            "properties.aadProfile.enableAzureRBAC": True,
            "properties.apiServerAccessProfile.enablePrivateCluster": spec.network.private_cluster,
            "properties.apiServerAccessProfile.disableRunCommand": True,
            "properties.apiServerAccessProfile.enableVnetIntegration": True,
            "properties.apiServerAccessProfile.subnetId": outputs.network.subnet_ids.api_server,
            "properties.hostedSystemProfile.enabled": True,
            "properties.hostedSystemProfile.systemNodeSubnetID": (
                outputs.network.subnet_ids.system_node
            ),
            "properties.hostedSystemProfile.nodeSubnetID": outputs.network.subnet_ids.user_node,
            "properties.nodeProvisioningProfile.mode": "Auto",
            "properties.networkProfile.networkPlugin": "azure",
            "properties.networkProfile.networkPluginMode": "overlay",
            "properties.networkProfile.networkDataplane": "cilium",
            "properties.networkProfile.networkPolicy": "cilium",
            "properties.networkProfile.podCidr": spec.network.pod_cidr,
            "properties.networkProfile.serviceCidr": spec.network.service_cidr,
            "properties.networkProfile.dnsServiceIP": spec.network.dns_service_ip,
            "properties.oidcIssuerProfile.enabled": True,
            "properties.securityProfile.workloadIdentity.enabled": True,
            "properties.autoUpgradeProfile.upgradeChannel": "stable",
            "properties.autoUpgradeProfile.nodeOSUpgradeChannel": "NodeImage",
            "properties.ingressProfile.gatewayAPI.installation": "Standard",
            (
                "properties.ingressProfile.webAppRouting."
                "gatewayAPIImplementations.appRoutingIstio.mode"
            ): "Enabled",
            "properties.addonProfiles.azurepolicy.enabled": True,
            "properties.addonProfiles.omsagent.enabled": spec.observability.container_insights,
            "properties.azureMonitorProfile.metrics.enabled": spec.observability.managed_prometheus,
            "identity.type": "UserAssigned",
        },
        "cluster",
    )
    if set(cluster["identity"].get("userAssignedIdentities", {})) != {
        outputs.identities.cluster.id
    }:
        raise ValueError("cluster identity assignment drift")
    for name, identity in (
        ("clusterIdentity", outputs.identities.cluster),
        ("readinessIdentity", outputs.identities.readiness),
    ):
        assert_properties(
            observed[name],
            {
                "properties.principalId": identity.principal_id,
                "properties.clientId": identity.client_id,
            },
            name,
        )
    endpoint = "privateFQDN" if spec.network.private_cluster else "fqdn"
    assert_properties(cluster, {f"properties.{endpoint}": outputs.cluster.fqdn}, "cluster endpoint")
    api = cluster["properties"]["apiServerAccessProfile"]
    if spec.network.private_cluster:
        expected_zone = (
            outputs.resource_group.id
            + f"/providers/Microsoft.Network/privateDnsZones/private.{spec.location}.azmk8s.io"
        )
        assert_properties(
            cluster,
            {"properties.apiServerAccessProfile.privateDNSZone": expected_zone},
            "private API DNS",
        )
        assert_properties(
            observed["privateDnsLink"],
            {
                "properties.virtualNetwork.id": outputs.network.vnet_id,
                "properties.registrationEnabled": False,
            },
            "private API DNS link",
        )
    if api.get("enablePrivateClusterPublicFQDN", False) is not False:
        raise ValueError("public private-cluster name is enabled")
    if set(api.get("authorizedIPRanges", [])) != set(
        [] if spec.network.private_cluster else spec.network.authorized_ip_ranges
    ):
        raise ValueError("API allowlist drift")
    if spec.observability.container_insights:
        assert_properties(
            cluster,
            {
                "properties.addonProfiles.omsagent.config.logAnalyticsWorkspaceResourceID": (
                    outputs.monitoring.log_analytics_id
                ),
                "properties.addonProfiles.omsagent.config.useAADAuth": "true",
            },
            "monitoring addon",
        )
    registry = observed["registry"]
    assert_properties(
        registry, {"properties.loginServer": outputs.registry.login_server}, "registry endpoint"
    )
    assert_properties(
        registry,
        {
            "sku.name": "Premium",
            "properties.adminUserEnabled": False,
            "properties.anonymousPullEnabled": False,
            "properties.roleAssignmentMode": "LegacyRegistryPermissions",
            "properties.publicNetworkAccess": "Disabled" if production else "Enabled",
            "properties.networkRuleBypassOptions": "None",
            "properties.networkRuleSet.defaultAction": "Deny",
        },
        "registry",
    )
    rules = registry["properties"]["networkRuleSet"]
    if {rule["value"] for rule in rules.get("ipRules", []) if rule.get("action") == "Allow"} != set(
        [] if production else spec.network.paas_allowed_ip_ranges
    ):
        raise ValueError("registry IP allowlist drift")
    expected_subnets = (
        set()
        if production
        else {outputs.network.subnet_ids.system_node, outputs.network.subnet_ids.user_node}
    )
    if {
        rule["virtualNetworkSubnetResourceId"]
        for rule in rules.get("virtualNetworkRules", [])
        if rule.get("action") == "Allow"
    } != expected_subnets:
        raise ValueError("registry subnet allowlist drift")
    vault = observed["vault"]
    assert_properties(vault, {"properties.vaultUri": outputs.vault.uri}, "vault endpoint")
    assert_properties(
        vault,
        {
            "properties.enableRbacAuthorization": True,
            "properties.publicNetworkAccess": "Disabled" if production else "Enabled",
            "properties.networkAcls.defaultAction": "Deny",
            "properties.networkAcls.bypass": "None",
            "properties.softDeleteRetentionInDays": 90,
        },
        "vault",
    )
    if production and vault["properties"].get("enablePurgeProtection") is not True:
        raise ValueError("production vault purge protection drift")
    acl = vault["properties"]["networkAcls"]
    if {rule["value"] for rule in acl.get("ipRules", [])} != set(
        [] if production else spec.network.paas_allowed_ip_ranges
    ) or {rule["id"] for rule in acl.get("virtualNetworkRules", [])} != expected_subnets:
        raise ValueError("vault network allowlist drift")
    assert_properties(
        observed["vnet"],
        {"properties.addressSpace.addressPrefixes": [spec.network.vnet_cidr]},
        "network",
    )
    for label, cidr in (
        ("api", spec.network.api_server_subnet_cidr),
        ("system", spec.network.system_node_subnet_cidr),
        ("user", spec.network.user_node_subnet_cidr),
        ("private", spec.network.private_endpoint_subnet_cidr),
    ):
        properties = observed["subnets"][label]["properties"]
        prefixes = properties.get("addressPrefixes") or [properties.get("addressPrefix")]
        if prefixes != [cidr]:
            raise ValueError(f"{label} subnet address-space drift")
    assert_properties(
        observed["federation"],
        {
            "properties.issuer": cluster["properties"]["oidcIssuerProfile"]["issuerURL"],
            "properties.subject": "system:serviceaccount:aiks-readiness:readiness",
            "properties.audiences": ["api://AzureADTokenExchange"],
        },
        "federation",
    )
    assert_properties(
        observed["marker"],
        {
            "properties.kty": "RSA",
            "properties.keyOps": ["verify"],
            "properties.attributes.enabled": True,
            "properties.attributes.exportable": False,
        },
        "marker",
    )
    return {
        "network": "verified",
        "identity": "verified",
        "clusterProfile": "verified",
        "restrictedEndpoints": "verified",
    }


def verify_roles(assignments: Any, expected: set[tuple[str, str, str]]) -> None:
    if not isinstance(assignments, list):
        raise ValueError("role assignment inventory is unverifiable")
    actual = set()
    for assignment in assignments:
        properties = assignment.get("properties", assignment)
        try:
            actual.add(
                (
                    properties["scope"].lower(),
                    properties["principalId"].lower(),
                    properties["roleDefinitionId"].rsplit("/", 1)[-1].lower(),
                )
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("role assignment metadata is malformed") from error
    if actual != {
        (scope.lower(), principal.lower(), role.lower()) for scope, principal, role in expected
    }:
        raise ValueError("role assignments differ from the exact foundation access contract")
