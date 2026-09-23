param name string
param location string
param tags object
param production bool
param nodeSubnetIds string[]
param allowedIpRanges string[]

resource registry 'Microsoft.ContainerRegistry/registries@2026-03-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Premium'
  }
  properties: {
    adminUserEnabled: false
    anonymousPullEnabled: false
    roleAssignmentMode: 'LegacyRegistryPermissions'
    publicNetworkAccess: production ? 'Disabled' : 'Enabled'
    networkRuleBypassOptions: 'None'
    dataEndpointEnabled: true
    networkRuleSet: {
      defaultAction: 'Deny'
      ipRules: [
        for cidr in (production ? [] : allowedIpRanges): {
          action: 'Allow'
          value: cidr
        }
      ]
      virtualNetworkRules: [
        for subnetId in (production ? [] : nodeSubnetIds): {
          action: 'Allow'
          virtualNetworkSubnetResourceId: subnetId
        }
      ]
    }
  }
}

output registryInfo object = {
  name: registry.name
  id: registry.id
  loginServer: registry.properties.loginServer
}
