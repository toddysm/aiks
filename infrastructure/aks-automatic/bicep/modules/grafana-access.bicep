param workspaceName string
param principalId string

resource workspace 'Microsoft.Monitor/accounts@2023-04-03' existing = {
  name: workspaceName
}

var monitoringReaderRole = '43d0d8ad-25c7-4714-9337-8ba259a9fe05'
resource reader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(workspace.id, principalId, monitoringReaderRole)
  scope: workspace
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringReaderRole)
  }
}
