"""Verify managed monitoring destinations, ingestion and alert definitions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode, urlsplit

from aiks.azure import AzureSession
from aiks.config import EnvironmentConfig
from aiks.outputs import FoundationOutputs
from aiks.posture import assert_properties


class TelemetryPendingError(ValueError):
    """A configured pipeline has not yet produced a current sample."""


def verify_observability(
    config: EnvironmentConfig,
    outputs: FoundationOutputs,
    azure: AzureSession,
    resource: Callable[[str, str], dict[str, Any]],
) -> dict[str, str]:
    settings = config.spec.observability
    base = outputs.resource_group.name.removeprefix("rg-")
    group = outputs.resource_group.id
    report = {
        "logs": "disabled",
        "prometheus": "disabled",
        "grafana": "disabled",
        "alerts": "disabled",
    }
    if settings.container_insights:
        workspace = resource(outputs.monitoring.log_analytics_id, "2023-09-01")
        assert_properties(
            workspace,
            {
                "properties.features.disableLocalAuth": True,
                "properties.retentionInDays": settings.log_retention_days,
            },
            "logs",
        )
        rule_id = group + f"/providers/Microsoft.Insights/dataCollectionRules/ci-{base}"
        rule = resource(rule_id, "2023-03-11")
        assert_properties(
            rule,
            {
                "properties.destinations.logAnalytics.0.workspaceResourceId": (
                    outputs.monitoring.log_analytics_id
                ),
                "properties.dataFlows.0.streams": ["Microsoft-ContainerInsights-Group-Default"],
                (
                    "properties.dataSources.extensions.0.extensionSettings."
                    "dataCollectionSettings.enableContainerLogV2"
                ): True,
            },
            "container collection rule",
        )
        association = resource(
            outputs.cluster.id + "/providers/Microsoft.Insights/dataCollectionRuleAssociations/"
            "ContainerInsightsExtension",
            "2023-03-11",
        )
        assert_properties(
            association,
            {"properties.dataCollectionRuleId": rule_id},
            "container collection association",
        )
        diagnostics = resource(
            outputs.cluster.id + "/providers/Microsoft.Insights/diagnosticSettings/service",
            "2016-09-01",
        )
        assert_properties(
            diagnostics,
            {"properties.workspaceId": outputs.monitoring.log_analytics_id},
            "diagnostics",
        )
        required = {"kube-apiserver", "kube-controller-manager", "guard"}
        if config.spec.environment == "production":
            required |= {
                "kube-audit",
                "kube-audit-admin",
                "kube-scheduler",
                "cluster-autoscaler",
                "cloud-controller-manager",
            }
        if {
            entry["category"]
            for entry in diagnostics["properties"].get("logs", [])
            if entry.get("enabled")
        } != required:
            raise ValueError("control-plane diagnostic category drift")
        query = (
            "KubePodInventory | where TimeGenerated > ago(15m) "
            f'| where ClusterId =~ "{outputs.cluster.id}" | take 1'
        )
        records = azure.json(
            "monitor",
            "log-analytics",
            "query",
            "--workspace",
            workspace["properties"]["customerId"],
            "--analytics-query",
            query,
        )
        if not isinstance(records, list) or not records:
            raise TelemetryPendingError("fresh Container Insights ingestion was not verified")
        report["logs"] = "ingesting"
    if settings.managed_prometheus:
        workspace = resource(outputs.monitoring.azure_monitor_workspace_id, "2023-04-03")
        rule_id = group + f"/providers/Microsoft.Insights/dataCollectionRules/prom-{base}"
        rule = resource(rule_id, "2023-03-11")
        assert_properties(
            rule,
            {
                "properties.destinations.monitoringAccounts.0.accountResourceId": (
                    outputs.monitoring.azure_monitor_workspace_id
                ),
                "properties.dataFlows.0.streams": ["Microsoft-PrometheusMetrics"],
                "properties.dataCollectionEndpointId": workspace["properties"][
                    "defaultIngestionSettings"
                ]["dataCollectionEndpointResourceId"],
            },
            "Prometheus collection rule",
        )
        association = resource(
            outputs.cluster.id
            + f"/providers/Microsoft.Insights/dataCollectionRuleAssociations/prometheus-{base}",
            "2023-03-11",
        )
        assert_properties(
            association,
            {"properties.dataCollectionRuleId": rule_id},
            "Prometheus collection association",
        )
        endpoint = outputs.monitoring.prometheus_query_endpoint
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".prometheus.monitor.azure.com")
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("untrusted managed Prometheus query endpoint")
        expression = (
            f'aiks_readiness_info{{environment="{config.spec.environment}",'
            f'cluster="{outputs.cluster.name}",namespace="aiks-readiness",'
            'workload="readiness",target="aks",status="ready"}'
        )
        metrics = azure.json(
            "rest",
            "--method",
            "get",
            "--resource",
            "https://prometheus.monitor.azure.com",
            "--url",
            endpoint.rstrip("/") + "/api/v1/query?" + urlencode({"query": expression}),
        )
        results = metrics.get("data", {}).get("result", [])
        if (
            metrics.get("status") != "success"
            or not results
            or any(float(entry["value"][1]) != 1 for entry in results)
        ):
            raise TelemetryPendingError("managed readiness metric target/sample was not verified")
        report["prometheus"] = "ingesting-readiness"
    if settings.managed_grafana:
        grafana = resource(outputs.monitoring.grafana_id, "2024-10-01")
        assert_properties(
            grafana,
            {
                "identity.type": "SystemAssigned",
                "properties.apiKey": "Disabled",
                "properties.grafanaMajorVersion": "12",
            },
            "Grafana",
        )
        integrations = grafana["properties"]["grafanaIntegrations"].get(
            "azureMonitorWorkspaceIntegrations", []
        )
        if settings.managed_prometheus and {
            entry["azureMonitorWorkspaceResourceId"] for entry in integrations
        } != {outputs.monitoring.azure_monitor_workspace_id}:
            raise ValueError("Grafana monitoring workspace linkage drift")
        report["grafana"] = "linked"
    if (
        config.spec.environment == "production"
        or settings.action_group_resource_ids
        or settings.action_group_receivers
    ):
        health = resource(
            group + f"/providers/Microsoft.Insights/activityLogAlerts/health-{base}", "2020-10-01"
        )
        assert_properties(
            health,
            {
                "properties.enabled": True,
                "properties.scopes": [outputs.cluster.id],
                "properties.condition.allOf.0.equals": "ResourceHealth",
            },
            "resource-health alert",
        )
        if settings.managed_prometheus:
            rules = resource(
                group
                + f"/providers/Microsoft.AlertsManagement/prometheusRuleGroups/signals-{base}",
                "2023-03-01",
            )
            expected = {
                "ReadinessUnavailable",
                "FailedPods",
                "NodeNotReady",
                "CpuRequestsPressure",
                "MemoryRequestsPressure",
            }
            entries = rules["properties"].get("rules", [])
            if {entry.get("alert") for entry in entries} != expected or any(
                not entry.get("enabled") or not entry.get("actions") for entry in entries
            ):
                raise ValueError("operational alerts or notification actions are missing")
        report["alerts"] = "definitions-verified-delivery-not-exercised"
    return report
