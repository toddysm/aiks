"""Observed posture must fail closed on mismatches and unexpected privileges."""

from pathlib import Path

import pytest
from test_workload import foundation

from aiks.config import load_environment_config
from aiks.posture import assert_properties, verify_foundation, verify_roles

CONFIG = Path(__file__).resolve().parents[1] / "infrastructure/aks-automatic/config"


def live_fixture(environment="dev"):
    config = load_environment_config(CONFIG / f"{environment}.example.yaml")
    outputs = foundation(environment)
    name = f"rg-{config.spec.naming.prefix}-{environment}-abcdefgh"
    group = "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/" + name
    outputs.resource_group.name, outputs.resource_group.id = name, group
    for model, kind in (
        (outputs.cluster, "Microsoft.ContainerService/managedClusters"),
        (outputs.registry, "Microsoft.ContainerRegistry/registries"),
        (outputs.vault, "Microsoft.KeyVault/vaults"),
    ):
        model.id = group + "/providers/" + kind + "/" + model.name
    for label, identity in (
        ("cluster", outputs.identities.cluster),
        ("readiness", outputs.identities.readiness),
    ):
        identity.name = label
        identity.id = group + "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/" + label
    outputs.network.vnet_id = group + "/providers/Microsoft.Network/virtualNetworks/test"
    for field in ("api_server", "system_node", "user_node", "private_endpoint"):
        setattr(outputs.network.subnet_ids, field, outputs.network.vnet_id + "/subnets/" + field)
    outputs.monitoring.log_analytics_id = (
        group + "/providers/Microsoft.OperationalInsights/workspaces/logs"
    )
    if environment == "production":
        outputs.monitoring.azure_monitor_workspace_id = (
            group + "/providers/Microsoft.Monitor/accounts/metrics"
        )
        outputs.monitoring.grafana_id = group + "/providers/Microsoft.Dashboard/grafana/dashboard"
        outputs.monitoring.prometheus_query_endpoint = (
            "https://test.westus3.prometheus.monitor.azure.com"
        )
    network = config.spec.network
    production = environment == "production"
    subnets = outputs.network.subnet_ids
    node_ids = [] if production else [subnets.system_node, subnets.user_node]
    observed = {
        "cluster": {
            "id": outputs.cluster.id,
            "sku": {"name": "Automatic", "tier": "Standard"},
            "identity": {
                "type": "UserAssigned",
                "userAssignedIdentities": {outputs.identities.cluster.id: {}},
            },
            "properties": {
                "provisioningState": "Succeeded",
                "enableRBAC": True,
                "disableLocalAccounts": True,
                "aadProfile": {"managed": True, "enableAzureRBAC": True},
                "apiServerAccessProfile": {
                    "enablePrivateCluster": network.private_cluster,
                    "enableVnetIntegration": True,
                    "disableRunCommand": True,
                    "subnetId": subnets.api_server,
                    "authorizedIPRanges": []
                    if network.private_cluster
                    else network.authorized_ip_ranges,
                    "privateDNSZone": group
                    + "/providers/Microsoft.Network/privateDnsZones/private.westus3.azmk8s.io",
                },
                "hostedSystemProfile": {
                    "enabled": True,
                    "systemNodeSubnetID": subnets.system_node,
                    "nodeSubnetID": subnets.user_node,
                },
                "nodeProvisioningProfile": {"mode": "Auto"},
                "networkProfile": {
                    "networkPlugin": "azure",
                    "networkPluginMode": "overlay",
                    "networkDataplane": "cilium",
                    "networkPolicy": "cilium",
                    "podCidr": network.pod_cidr,
                    "serviceCidr": network.service_cidr,
                    "dnsServiceIP": network.dns_service_ip,
                },
                "oidcIssuerProfile": {
                    "enabled": True,
                    "issuerURL": "https://issuer.example.invalid/",
                },
                "securityProfile": {"workloadIdentity": {"enabled": True}},
                "autoUpgradeProfile": {
                    "upgradeChannel": "stable",
                    "nodeOSUpgradeChannel": "NodeImage",
                },
                "ingressProfile": {
                    "gatewayAPI": {"installation": "Standard"},
                    "webAppRouting": {
                        "gatewayAPIImplementations": {"appRoutingIstio": {"mode": "Enabled"}}
                    },
                },
                "addonProfiles": {
                    "azurepolicy": {"enabled": True},
                    "omsagent": {
                        "enabled": True,
                        "config": {
                            "logAnalyticsWorkspaceResourceID": outputs.monitoring.log_analytics_id,
                            "useAADAuth": "true",
                        },
                    },
                },
                "azureMonitorProfile": {
                    "metrics": {"enabled": config.spec.observability.managed_prometheus}
                },
                "identityProfile": {
                    "kubeletidentity": {"objectId": "22222222-2222-4222-8222-222222222222"}
                },
                "nodeResourceGroup": "MC_test",
                "fqdn": outputs.cluster.fqdn,
                "privateFQDN": outputs.cluster.fqdn,
            },
        },
        "registry": {
            "id": outputs.registry.id,
            "sku": {"name": "Premium"},
            "properties": {
                "adminUserEnabled": False,
                "anonymousPullEnabled": False,
                "roleAssignmentMode": "LegacyRegistryPermissions",
                "publicNetworkAccess": "Disabled" if production else "Enabled",
                "networkRuleBypassOptions": "None",
                "loginServer": outputs.registry.login_server,
                "networkRuleSet": {
                    "defaultAction": "Deny",
                    "ipRules": [
                        {"action": "Allow", "value": cidr}
                        for cidr in ([] if production else network.paas_allowed_ip_ranges)
                    ],
                    "virtualNetworkRules": [
                        {"action": "Allow", "virtualNetworkSubnetResourceId": identifier}
                        for identifier in node_ids
                    ],
                },
            },
        },
        "vault": {
            "id": outputs.vault.id,
            "properties": {
                "enableRbacAuthorization": True,
                "publicNetworkAccess": "Disabled" if production else "Enabled",
                "softDeleteRetentionInDays": 90,
                "enablePurgeProtection": production,
                "vaultUri": outputs.vault.uri,
                "networkAcls": {
                    "defaultAction": "Deny",
                    "bypass": "None",
                    "ipRules": [
                        {"value": cidr}
                        for cidr in ([] if production else network.paas_allowed_ip_ranges)
                    ],
                    "virtualNetworkRules": [{"id": identifier} for identifier in node_ids],
                },
            },
        },
        "vnet": {
            "id": outputs.network.vnet_id,
            "properties": {"addressSpace": {"addressPrefixes": [network.vnet_cidr]}},
        },
        "subnets": {
            label: {"properties": {"addressPrefix": cidr}}
            for label, cidr in (
                ("api", network.api_server_subnet_cidr),
                ("system", network.system_node_subnet_cidr),
                ("user", network.user_node_subnet_cidr),
                ("private", network.private_endpoint_subnet_cidr),
            )
        },
        "federation": {
            "properties": {
                "issuer": "https://issuer.example.invalid/",
                "subject": "system:serviceaccount:aiks-readiness:readiness",
                "audiences": ["api://AzureADTokenExchange"],
            }
        },
        "marker": {
            "properties": {
                "kty": "RSA",
                "keyOps": ["verify"],
                "attributes": {"enabled": True, "exportable": False},
            }
        },
        "clusterIdentity": {
            "properties": {
                "principalId": outputs.identities.cluster.principal_id,
                "clientId": outputs.identities.cluster.client_id,
            }
        },
        "readinessIdentity": {
            "properties": {
                "principalId": outputs.identities.readiness.principal_id,
                "clientId": outputs.identities.readiness.client_id,
            }
        },
    }
    observed["privateDnsLink"] = {
        "properties": {
            "virtualNetwork": {"id": outputs.network.vnet_id},
            "registrationEnabled": False,
        }
    }
    return config, outputs, observed


@pytest.mark.parametrize("environment", ["dev", "production"])
def test_complete_observed_foundation(environment):
    config, outputs, observed = live_fixture(environment)
    assert verify_foundation(config, outputs, observed)["identity"] == "verified"


@pytest.mark.parametrize("mutation", ["zone", "link"])
def test_private_api_dns_must_belong_to_foundation(mutation):
    config, outputs, observed = live_fixture("production")
    if mutation == "zone":
        observed["cluster"]["properties"]["apiServerAccessProfile"]["privateDNSZone"] = (
            "/foreign/zone"
        )
    else:
        observed["privateDnsLink"]["properties"]["virtualNetwork"]["id"] = "/foreign/network"
    with pytest.raises(ValueError, match="DNS"):
        verify_foundation(config, outputs, observed)


@pytest.mark.parametrize(
    "mutation", ["public", "allowlist", "identity", "vault", "subnet", "federation"]
)
def test_observed_drift_fails(mutation):
    config, outputs, observed = live_fixture()
    if mutation == "public":
        observed["cluster"]["properties"]["apiServerAccessProfile"][
            "enablePrivateClusterPublicFQDN"
        ] = True
    elif mutation == "allowlist":
        observed["registry"]["properties"]["networkRuleSet"]["ipRules"] = []
    elif mutation == "identity":
        observed["cluster"]["identity"]["userAssignedIdentities"] = {}
    elif mutation == "vault":
        observed["vault"]["properties"]["networkAcls"]["virtualNetworkRules"] = []
    elif mutation == "subnet":
        observed["subnets"]["api"]["properties"]["addressPrefix"] = "10.0.0.0/8"
    else:
        observed["federation"]["properties"]["subject"] = "other"
    with pytest.raises(ValueError):
        verify_foundation(config, outputs, observed)


@pytest.mark.parametrize("actual", [{}, {"profile": {"enabled": False}}, None])
def test_missing_or_wrong_property_refuses(actual):
    with pytest.raises(ValueError):
        assert_properties(actual, {"profile.enabled": True}, "cluster")


def test_role_contract_rejects_extra_privilege():
    expected = {("/resource", "principal", "reader")}
    role = {"scope": "/resource", "principalId": "principal", "roleDefinitionId": "/roles/reader"}
    verify_roles([role], expected)
    with pytest.raises(ValueError, match="exact"):
        verify_roles([role, {**role, "roleDefinitionId": "/roles/owner"}], expected)
