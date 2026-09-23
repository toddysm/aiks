param names object
param location string
param tags object
param environmentName string
param adminGroupObjectId string
param network object
param observability object

var production = environmentName == 'production'
var effectiveNetwork = union(network, { privateCluster: production || network.privateCluster })
var effectiveObservability = union(observability, {
  containerInsights: production || observability.containerInsights
  managedPrometheus: production || observability.managedPrometheus
  managedGrafana: production || observability.managedGrafana
})

module clusterIdentity 'identity.bicep' = {
  name: 'cluster-identity'
  params: { name: 'id-aks-${names.base}', location: location, tags: tags }
}
module readinessIdentity 'identity.bicep' = {
  name: 'readiness-identity'
  params: { name: 'id-readiness-${names.base}', location: location, tags: tags }
}
module networking 'network.bicep' = {
  name: 'network'
  params: {
    name: 'vnet-${names.base}'
    location: location
    tags: tags
    production: production
    network: effectiveNetwork
    clusterPrincipalId: clusterIdentity.outputs.identityInfo.principalId
  }
}
module monitoring 'monitoring.bicep' = {
  name: 'monitoring'
  params: {
    name: names.base
    location: location
    tags: tags
    observability: effectiveObservability
    adminGroupObjectId: adminGroupObjectId
  }
}
module cluster 'aks.bicep' = {
  name: 'cluster'
  params: {
    name: 'aks-${names.base}'
    location: location
    tags: tags
    identityId: clusterIdentity.outputs.identityInfo.id
    adminGroupObjectId: adminGroupObjectId
    network: effectiveNetwork
    subnetIds: networking.outputs.subnetIds
    privateDnsZoneId: networking.outputs.privateDnsZoneId
    observability: effectiveObservability
    logAnalyticsId: monitoring.outputs.monitoringInfo.logAnalyticsId
  }
}
var nodeSubnetIds = [networking.outputs.subnetIds.systemNode, networking.outputs.subnetIds.userNode]
module registry 'registry.bicep' = {
  name: 'registry'
  params: {
    name: names.registry
    location: location
    tags: tags
    production: production
    nodeSubnetIds: nodeSubnetIds
    allowedIpRanges: network.paasAllowedIpRanges
  }
}
module vault 'vault.bicep' = {
  name: 'vault'
  params: {
    name: names.vault
    location: location
    tags: tags
    production: production
    nodeSubnetIds: nodeSubnetIds
    allowedIpRanges: network.paasAllowedIpRanges
  }
}
module registryEndpoint 'private-endpoint.bicep' = if (production) {
  name: 'registry-endpoint'
  params: {
    name: 'pe-acr-${names.base}'
    location: location
    tags: tags
    resourceId: registry.outputs.registryInfo.id
    groupId: 'registry'
    zoneName: 'privatelink.azurecr.io'
    vnetId: networking.outputs.vnetId
    subnetId: networking.outputs.subnetIds.privateEndpoint
  }
}
module vaultEndpoint 'private-endpoint.bicep' = if (production) {
  name: 'vault-endpoint'
  params: {
    name: 'pe-vault-${names.base}'
    location: location
    tags: tags
    resourceId: vault.outputs.vaultInfo.id
    groupId: 'vault'
    zoneName: 'privatelink.vaultcore.azure.net'
    vnetId: networking.outputs.vnetId
    subnetId: networking.outputs.subnetIds.privateEndpoint
  }
  dependsOn: [registryEndpoint]
}
module bindings 'bindings.bicep' = {
  name: 'bindings'
  params: {
    registryName: registry.outputs.registryInfo.name
    vaultName: vault.outputs.vaultInfo.name
    readinessIdentityName: readinessIdentity.outputs.identityInfo.name
    readinessPrincipalId: readinessIdentity.outputs.identityInfo.principalId
    kubeletObjectId: cluster.outputs.kubeletObjectId
    oidcIssuer: cluster.outputs.oidcIssuer
  }
}
module monitoringBindings 'monitoring-bindings.bicep' = {
  name: 'monitoring-bindings'
  params: {
    name: names.base
    location: location
    tags: tags
    environmentName: environmentName
    clusterName: cluster.outputs.clusterInfo.name
    observability: effectiveObservability
    monitoringInfo: monitoring.outputs.monitoringInfo
    containerRuleId: monitoring.outputs.containerRuleId
    prometheusRuleId: monitoring.outputs.prometheusRuleId
  }
}

output result object = {
  cluster: cluster.outputs.clusterInfo
  registry: registry.outputs.registryInfo
  vault: vault.outputs.vaultInfo
  network: { vnetId: networking.outputs.vnetId, subnetIds: networking.outputs.subnetIds }
  identities: {
    cluster: clusterIdentity.outputs.identityInfo
    readiness: readinessIdentity.outputs.identityInfo
  }
  monitoring: monitoring.outputs.monitoringInfo
  readiness: {
    namespace: 'aiks-readiness'
    serviceAccount: 'readiness'
    clientId: readinessIdentity.outputs.identityInfo.clientId
    tenantId: tenant().tenantId
    vaultUri: vault.outputs.vaultInfo.uri
    markerKeyName: 'readiness-marker'
    gatewayClassName: 'approuting-istio'
    internalGateway: production
    managedPrometheus: effectiveObservability.managedPrometheus
  }
}
