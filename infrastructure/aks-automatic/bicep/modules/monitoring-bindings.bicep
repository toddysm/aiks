param name string
param location string
param tags object
param clusterName string
param environmentName string
param observability object
param monitoringInfo object
param containerRuleId string
param prometheusRuleId string

resource cluster 'Microsoft.ContainerService/managedClusters@2026-04-01' existing = {
  name: clusterName
}

resource containers 'Microsoft.Insights/dataCollectionRuleAssociations@2023-03-11' = if (observability.containerInsights) {
  name: 'ContainerInsightsExtension'
  scope: cluster
  properties: { dataCollectionRuleId: containerRuleId }
}

resource prometheus 'Microsoft.Insights/dataCollectionRuleAssociations@2023-03-11' = if (observability.managedPrometheus) {
  name: 'prometheus-${name}'
  scope: cluster
  properties: { dataCollectionRuleId: prometheusRuleId }
}

var diagnosticCategories = environmentName == 'production'
  ? [
      'kube-audit'
      'kube-audit-admin'
      'kube-apiserver'
      'kube-controller-manager'
      'kube-scheduler'
      'cluster-autoscaler'
      'cloud-controller-manager'
      'guard'
    ]
  : [
      'kube-apiserver'
      'kube-controller-manager'
      'guard'
    ]

resource diagnostics 'Microsoft.Insights/diagnosticSettings@2016-09-01' = if (observability.containerInsights) {
  name: 'service'
  location: location
  scope: cluster
  properties: {
    workspaceId: monitoringInfo.logAnalyticsId
    logs: [
      for category in diagnosticCategories: {
        category: category
        enabled: true
        retentionPolicy: { enabled: false, days: 0 }
      }
    ]
    metrics: [
      { timeGrain: 'PT1M', enabled: true, retentionPolicy: { enabled: false, days: 0 } }
    ]
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = if (!empty(observability.actionGroupReceivers)) {
  name: 'alerts-${name}'
  location: 'global'
  tags: tags
  properties: {
    enabled: true
    groupShortName: take(name, 12)
    emailReceivers: [
      for receiver in observability.actionGroupReceivers: {
        name: receiver.name
        emailAddress: receiver.emailAddress
        useCommonAlertSchema: true
      }
    ]
  }
}

var actionGroupIds = concat(
  observability.actionGroupResourceIds,
  empty(observability.actionGroupReceivers) ? [] : [actionGroup!.id]
)
var alertsEnabled = environmentName == 'production' || !empty(actionGroupIds)
var prometheusActions = [for actionGroupId in actionGroupIds: { actionGroupId: actionGroupId }]

resource health 'Microsoft.Insights/activityLogAlerts@2020-10-01' = if (alertsEnabled) {
  name: 'health-${name}'
  location: 'global'
  tags: tags
  properties: {
    enabled: true
    description: 'Severity 1: AKS Resource Health reports unavailable or degraded.'
    scopes: [cluster.id]
    condition: {
      allOf: [
        { field: 'category', equals: 'ResourceHealth' }
        { field: 'resourceId', equals: cluster.id }
        {
          anyOf: [
            { field: 'properties.currentHealthStatus', equals: 'Unavailable' }
            { field: 'properties.currentHealthStatus', equals: 'Degraded' }
          ]
        }
      ]
    }
    actions: {
      actionGroups: [
        for actionGroupId in actionGroupIds: {
          actionGroupId: actionGroupId
          webhookProperties: { severity: '1', environment: environmentName }
        }
      ]
    }
  }
}

var signals = [
  {
    alert: 'ReadinessUnavailable'
    expression: 'absent(aiks_readiness_info{status="ready"}) or min(aiks_readiness_info{status="ready"}) != 1'
    severity: 1
    window: 'PT5M'
  }
  { alert: 'FailedPods', expression: 'sum(kube_pod_status_phase{phase="Failed"}) > 0', severity: 2, window: 'PT5M' }
  {
    alert: 'NodeNotReady'
    expression: 'kube_node_status_condition{condition="Ready",status="true"} == 0'
    severity: 1
    window: 'PT5M'
  }
  {
    alert: 'CpuRequestsPressure'
    expression: 'sum by(node) (kube_pod_container_resource_requests{resource="cpu"}) / sum by(node) (kube_node_status_capacity{resource="cpu"}) > 0.85'
    severity: 2
    window: 'PT15M'
  }
  {
    alert: 'MemoryRequestsPressure'
    expression: 'sum by(node) (kube_pod_container_resource_requests{resource="memory"}) / sum by(node) (kube_node_status_capacity{resource="memory"}) > 0.85'
    severity: 2
    window: 'PT15M'
  }
]

resource rules 'Microsoft.AlertsManagement/prometheusRuleGroups@2023-03-01' = if (observability.managedPrometheus && alertsEnabled) {
  name: 'signals-${name}'
  location: location
  tags: tags
  properties: {
    enabled: true
    clusterName: clusterName
    scopes: [monitoringInfo.azureMonitorWorkspaceId]
    interval: 'PT1M'
    rules: [
      for signal in signals: {
        alert: signal.alert
        expression: signal.expression
        enabled: true
        severity: signal.severity
        for: signal.window
        labels: { environment: environmentName, cluster: clusterName }
        actions: prometheusActions
        resolveConfiguration: { autoResolved: true, timeToResolve: 'PT5M' }
      }
    ]
  }
}

resource pipeline 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = if (observability.containerInsights && alertsEnabled) {
  name: 'pipeline-${name}'
  location: location
  tags: tags
  kind: 'LogAlert'
  properties: {
    displayName: 'AKS monitoring pipeline heartbeat missing'
    enabled: true
    severity: 2
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    scopes: [monitoringInfo.logAnalyticsId]
    skipQueryValidation: true
    autoMitigate: true
    criteria: {
      allOf: [
        {
          query: 'KubePodInventory | where ClusterId =~ "${cluster.id}"'
          timeAggregation: 'Count'
          operator: 'LessThan'
          threshold: 1
          failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
        }
      ]
    }
    actions: { actionGroups: actionGroupIds }
  }
}
