param name string
param location string
param tags object
param production bool
param nodeSubnetIds string[]
param allowedIpRanges string[]

resource vault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    enablePurgeProtection: production
    softDeleteRetentionInDays: 90
    publicNetworkAccess: production ? 'Disabled' : 'Enabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'None'
      ipRules: [for cidr in (production ? [] : allowedIpRanges): { value: cidr }]
      virtualNetworkRules: [
        for subnetId in (production ? [] : nodeSubnetIds): {
          id: subnetId
          ignoreMissingVnetServiceEndpoint: false
        }
      ]
    }
  }
}

@onlyIfNotExists()
resource marker 'Microsoft.KeyVault/vaults/keys@2024-11-01' = {
  parent: vault
  name: 'readiness-marker'
  properties: {
    kty: 'RSA'
    keySize: 2048
    keyOps: ['verify']
    attributes: {
      enabled: true
      exportable: false
    }
  }
}

output vaultInfo object = {
  name: vault.name
  id: vault.id
  uri: vault.properties.vaultUri
  markerKeyName: marker.name
}
