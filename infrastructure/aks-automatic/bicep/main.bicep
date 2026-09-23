targetScope = 'subscription'

import { resourceNames } from './modules/naming.bicep'

@description('Validated environment name from the shared configuration contract.')
@allowed(['dev', 'production'])
param environment string

@description('Azure region for the environment.')
param location string

@description('Lowercase naming prefix from the validated configuration.')
@minLength(3)
@maxLength(24)
param prefix string

@description('Object ID of the Entra platform-administrator group.')
@minLength(36)
@maxLength(36)
param adminGroupObjectId string

@description('Validated network configuration; existing address spaces and subnets are immutable.')
param network object

@description('Validated monitoring configuration, including production notification receivers.')
param observability object

@description('Nonsecret resource tags. Ownership tags cannot be overridden.')
param tags object = {}

var names = resourceNames(prefix, environment, subscription().subscriptionId)
var resourceTags = union(tags, { environment: environment, 'aiks-managed': 'true' })

resource environmentGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: names.group
  location: location
  tags: resourceTags
}

module foundation './modules/foundation.bicep' = {
  name: 'aiks-${names.base}'
  scope: environmentGroup
  params: {
    names: names
    location: location
    tags: resourceTags
    environmentName: environment
    adminGroupObjectId: adminGroupObjectId
    network: network
    observability: observability
  }
}

output result object = union(foundation.outputs.result, {
  environment: environment
  location: location
  resourceGroup: { name: environmentGroup.name, id: environmentGroup.id }
})
