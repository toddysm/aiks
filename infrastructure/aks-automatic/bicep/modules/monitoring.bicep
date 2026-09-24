param name string
param location string
param tags object
param observability object
param adminGroupObjectId string

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = if (observability.containerInsights) {
  name: 'log-${name}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: observability.logRetentionDays
    features: { disableLocalAuth: true }
  }
}

resource metrics 'Microsoft.Monitor/accounts@2023-04-03' = if (observability.managedPrometheus) {
  name: 'amw-${name}'
  location: location
  tags: tags
  properties: {}
}

resource grafana 'Microsoft.Dashboard/grafana@2024-10-01' = if (observability.managedGrafana) {
  name: 'grafana-${uniqueString(resourceGroup().id, name)}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  sku: { name: 'Standard' }
  properties: {
    grafanaMajorVersion: '12'
    apiKey: 'Disabled'
    deterministicOutboundIP: 'Enabled'
    zoneRedundancy: 'Enabled'
    grafanaIntegrations: {
      azureMonitorWorkspaceIntegrations: observability.managedPrometheus
        ? [
            { azureMonitorWorkspaceResourceId: metrics!.id }
          ]
        : []
    }
  }
}

resource grafanaAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (observability.managedGrafana) {
  name: guid(grafana!.id, adminGroupObjectId, '22926164-76b3-42b3-bc55-97df8dab3e41')
  scope: grafana
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '22926164-76b3-42b3-bc55-97df8dab3e41'
    )
    principalId: adminGroupObjectId
    principalType: 'Group'
  }
}

module grafanaReader 'grafana-access.bicep' = if (observability.managedGrafana && observability.managedPrometheus) {
  name: 'grafana-access-${name}'
  params: {
    workspaceName: metrics!.name
    principalId: grafana!.identity.principalId
  }
}

resource containerRule 'Microsoft.Insights/dataCollectionRules@2023-03-11' = if (observability.containerInsights) {
  name: 'ci-${name}'
  location: location
  tags: tags
  kind: 'Linux'
  properties: {
    dataSources: {
      extensions: [
        {
          name: 'ContainerInsightsExtension'
          streams: ['Microsoft-ContainerInsights-Group-Default']
          extensionName: 'ContainerInsights'
          extensionSettings: {
            dataCollectionSettings: {
              interval: '1m'
              namespaceFilteringMode: 'Off'
              enableContainerLogV2: true
            }
          }
        }
      ]
    }
    destinations: {
      logAnalytics: [{ name: 'logs', workspaceResourceId: logs!.id }]
    }
    dataFlows: [
      { streams: ['Microsoft-ContainerInsights-Group-Default'], destinations: ['logs'] }
    ]
  }
}

resource prometheusRule 'Microsoft.Insights/dataCollectionRules@2023-03-11' = if (observability.managedPrometheus) {
  name: 'prom-${name}'
  location: location
  tags: tags
  kind: 'Linux'
  properties: {
    dataCollectionEndpointId: metrics!.properties.defaultIngestionSettings.dataCollectionEndpointResourceId
    dataSources: {
      prometheusForwarder: [
        { name: 'prometheus', streams: ['Microsoft-PrometheusMetrics'], labelIncludeFilter: {} }
      ]
    }
    destinations: {
      monitoringAccounts: [{ name: 'metrics', accountResourceId: metrics!.id }]
    }
    dataFlows: [
      { streams: ['Microsoft-PrometheusMetrics'], destinations: ['metrics'] }
    ]
  }
}

output monitoringInfo object = {
  logAnalyticsId: observability.containerInsights ? logs!.id : ''
  azureMonitorWorkspaceId: observability.managedPrometheus ? metrics!.id : ''
  prometheusQueryEndpoint: observability.managedPrometheus ? metrics!.properties.metrics.prometheusQueryEndpoint : ''
  grafanaId: observability.managedGrafana ? grafana!.id : ''
  grafanaEndpoint: observability.managedGrafana ? grafana!.properties.endpoint : ''
}
output containerRuleId string = observability.containerInsights ? containerRule!.id : ''
output prometheusRuleId string = observability.managedPrometheus ? prometheusRule!.id : ''
output metricsIngestionRuleId string = observability.managedPrometheus
  ? metrics!.properties.defaultIngestionSettings.dataCollectionRuleResourceId
  : ''
