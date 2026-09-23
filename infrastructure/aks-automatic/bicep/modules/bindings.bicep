param registryName string
param vaultName string
param readinessIdentityName string
param readinessPrincipalId string
param kubeletObjectId string
param oidcIssuer string

resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' existing = {
  name: registryName
}
resource vault 'Microsoft.KeyVault/vaults@2024-11-01' existing = {
  name: vaultName
}
resource readiness 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  name: readinessIdentityName
}

var pullRole = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
resource pull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, kubeletObjectId, pullRole)
  scope: registry
  properties: {
    principalId: kubeletObjectId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', pullRole)
  }
}

var readerRole = '21090545-7ca7-4776-b22c-e363652d74d2'
resource metadataReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, readinessPrincipalId, readerRole)
  scope: vault
  properties: {
    principalId: readinessPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', readerRole)
  }
}

resource federation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2024-11-30' = {
  parent: readiness
  name: 'readiness'
  properties: {
    issuer: oidcIssuer
    subject: 'system:serviceaccount:aiks-readiness:readiness'
    audiences: ['api://AzureADTokenExchange']
  }
}
