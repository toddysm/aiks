param name string
param location string
param tags object
param network object
param clusterPrincipalId string
param production bool

@onlyIfNotExists()
resource vnet 'Microsoft.Network/virtualNetworks@2025-01-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [network.vnetCidr]
    }
  }
}

var subnetDefinitions = [
  { name: 'api-server', cidr: network.apiServerSubnetCidr, node: false }
  { name: 'system-node', cidr: network.systemNodeSubnetCidr, node: true }
  { name: 'user-node', cidr: network.userNodeSubnetCidr, node: true }
  { name: 'private-endpoint', cidr: network.privateEndpointSubnetCidr, node: false }
]

@onlyIfNotExists()
@batchSize(1)
resource subnets 'Microsoft.Network/virtualNetworks/subnets@2025-01-01' = [
  for subnet in subnetDefinitions: {
    parent: vnet
    name: subnet.name
    properties: {
      addressPrefix: subnet.cidr
      privateEndpointNetworkPolicies: subnet.name == 'private-endpoint' ? 'Disabled' : 'Enabled'
      delegations: subnet.name == 'api-server'
        ? [
            {
              name: 'aks-delegation'
              properties: {
                serviceName: 'Microsoft.ContainerService/managedClusters'
              }
            }
          ]
        : []
      serviceEndpoints: !production && subnet.node
        ? [
            { service: 'Microsoft.ContainerRegistry' }
            { service: 'Microsoft.KeyVault' }
          ]
        : []
    }
  }
]

var networkRole = '4d97b98b-1d4f-4787-a291-c67834d212e7'
resource networkAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vnet.id, clusterPrincipalId, networkRole)
  scope: vnet
  properties: {
    principalId: clusterPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', networkRole)
  }
}

resource aksZone 'Microsoft.Network/privateDnsZones@2024-06-01' = if (network.privateCluster) {
  name: 'private.${location}.azmk8s.io'
  location: 'global'
  tags: tags
}

resource aksLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (network.privateCluster) {
  parent: aksZone
  name: name
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnet.id }
  }
}

var dnsRole = 'b12aa53e-6015-4669-85d0-8515ebb3ae7f'
resource dnsAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (network.privateCluster) {
  name: guid(aksZone!.id, clusterPrincipalId, dnsRole)
  scope: aksZone
  properties: {
    principalId: clusterPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', dnsRole)
  }
}

output vnetId string = vnet.id
output subnetIds object = {
  apiServer: subnets[0].id
  systemNode: subnets[1].id
  userNode: subnets[2].id
  privateEndpoint: subnets[3].id
}
output privateDnsZoneId string = network.privateCluster ? aksZone!.id : ''
