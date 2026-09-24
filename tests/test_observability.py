"""Monitoring validation has no live service dependencies."""

from pathlib import Path

import pytest
from test_posture import live_fixture
from test_workload import foundation

from aiks.config import load_environment_config
from aiks.observability import verify_observability

CONFIG = (
    Path(__file__).resolve().parents[1] / "infrastructure/aks-automatic/config/dev.example.yaml"
)


def test_disabled_monitoring_does_not_query_azure():
    config = load_environment_config(CONFIG)
    config.spec.observability.container_insights = False

    def forbidden(*args, **kwargs):
        pytest.fail("disabled monitoring must not contact Azure")

    assert verify_observability(config, foundation(), None, forbidden)["logs"] == "disabled"


def test_monitoring_refuses_insecure_workspace():
    with pytest.raises(ValueError, match="missing"):
        verify_observability(load_environment_config(CONFIG), foundation(), None, lambda *args: {})


@pytest.mark.parametrize("environment", ["dev", "production"])
@pytest.mark.parametrize("failure", [None, "ingestion", "destination"])
def test_monitoring_configuration_and_samples(environment, failure):
    config, outputs, _observed = live_fixture(environment)
    group = outputs.resource_group.id
    base = outputs.resource_group.name.removeprefix("rg-")
    logs_rule = group + f"/providers/Microsoft.Insights/dataCollectionRules/ci-{base}"
    prom_rule = group + f"/providers/Microsoft.Insights/dataCollectionRules/prom-{base}"
    source = {
        outputs.monitoring.log_analytics_id: {
            "properties": {
                "features": {"disableLocalAuth": True},
                "retentionInDays": config.spec.observability.log_retention_days,
                "customerId": "11111111-1111-4111-8111-111111111111",
            }
        },
        logs_rule: {
            "properties": {
                "destinations": {
                    "logAnalytics": [
                        {
                            "workspaceResourceId": "wrong"
                            if failure == "destination"
                            else outputs.monitoring.log_analytics_id
                        }
                    ]
                },
                "dataFlows": [{"streams": ["Microsoft-ContainerInsights-Group-Default"]}],
                "dataSources": {
                    "extensions": [
                        {
                            "extensionSettings": {
                                "dataCollectionSettings": {"enableContainerLogV2": True}
                            }
                        }
                    ]
                },
            }
        },
        outputs.cluster.id + "/providers/Microsoft.Insights/dataCollectionRuleAssociations/"
        "ContainerInsightsExtension": {"properties": {"dataCollectionRuleId": logs_rule}},
        outputs.cluster.id + "/providers/Microsoft.Insights/diagnosticSettings/service": {
            "properties": {
                "workspaceId": outputs.monitoring.log_analytics_id,
                "logs": [
                    {"category": category, "enabled": True}
                    for category in (
                        ["kube-apiserver", "kube-controller-manager", "guard"]
                        + (
                            [
                                "kube-audit",
                                "kube-audit-admin",
                                "kube-scheduler",
                                "cluster-autoscaler",
                                "cloud-controller-manager",
                            ]
                            if environment == "production"
                            else []
                        )
                    )
                ],
            }
        },
    }
    if environment == "production":
        source.update(
            {
                outputs.monitoring.azure_monitor_workspace_id: {
                    "properties": {
                        "defaultIngestionSettings": {"dataCollectionEndpointResourceId": "endpoint"}
                    }
                },
                prom_rule: {
                    "properties": {
                        "destinations": {
                            "monitoringAccounts": [
                                {"accountResourceId": outputs.monitoring.azure_monitor_workspace_id}
                            ]
                        },
                        "dataFlows": [{"streams": ["Microsoft-PrometheusMetrics"]}],
                        "dataCollectionEndpointId": "endpoint",
                    }
                },
                outputs.cluster.id + "/providers/Microsoft.Insights/dataCollectionRuleAssociations/"
                f"prometheus-{base}": {"properties": {"dataCollectionRuleId": prom_rule}},
                outputs.monitoring.grafana_id: {
                    "identity": {"type": "SystemAssigned"},
                    "properties": {
                        "apiKey": "Disabled",
                        "grafanaMajorVersion": "12",
                        "grafanaIntegrations": {
                            "azureMonitorWorkspaceIntegrations": [
                                {
                                    "azureMonitorWorkspaceResourceId": (
                                        outputs.monitoring.azure_monitor_workspace_id
                                    )
                                }
                            ]
                        },
                    },
                },
                group + f"/providers/Microsoft.Insights/activityLogAlerts/health-{base}": {
                    "properties": {
                        "enabled": True,
                        "scopes": [outputs.cluster.id],
                        "condition": {"allOf": [{"equals": "ResourceHealth"}]},
                    }
                },
                group
                + f"/providers/Microsoft.AlertsManagement/prometheusRuleGroups/signals-{base}": {
                    "properties": {
                        "rules": [
                            {
                                "alert": name,
                                "enabled": True,
                                "actions": [{"actionGroupId": "configured"}],
                            }
                            for name in (
                                "ReadinessUnavailable",
                                "FailedPods",
                                "NodeNotReady",
                                "CpuRequestsPressure",
                                "MemoryRequestsPressure",
                            )
                        ]
                    }
                },
            }
        )

    class Azure:
        def json(self, *arguments):
            if arguments[0] == "monitor":
                return [] if failure == "ingestion" else [{"ready": True}]
            assert arguments[0] == "rest"
            return {"status": "success", "data": {"result": [{"value": [1, "1"]}]}}

    if failure:
        with pytest.raises(ValueError):
            verify_observability(
                config, outputs, Azure(), lambda identifier, version: source[identifier]
            )
    else:
        assert (
            verify_observability(
                config, outputs, Azure(), lambda identifier, version: source[identifier]
            )["logs"]
            == "ingesting"
        )
