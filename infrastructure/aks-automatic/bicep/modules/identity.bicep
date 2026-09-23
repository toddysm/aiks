param name string
param location string
param tags object

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: name
  location: location
  tags: tags
}

output identityInfo object = {
  id: identity.id
  name: identity.name
  clientId: identity.properties.clientId
  principalId: identity.properties.principalId
}
