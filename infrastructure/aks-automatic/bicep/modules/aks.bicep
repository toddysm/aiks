param name string
param location string
param tags object
param identityId string
param adminGroupObjectId string
param network object
param subnetIds object
param privateDnsZoneId string
param observability object
param logAnalyticsId string

resource cluster 'Microsoft.ContainerService/managedClusters@2026-04-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Automatic'
    tier: 'Standard'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    dnsPrefix: name
    enableRBAC: true
    disableLocalAccounts: true
    aadProfile: {
      managed: true
      enableAzureRBAC: true
    }
    apiServerAccessProfile: union(
      {
        enableVnetIntegration: true
        subnetId: subnetIds.apiServer
        enablePrivateCluster: network.privateCluster
        enablePrivateClusterPublicFQDN: false
        disableRunCommand: true
      },
      network.privateCluster
        ? {
            privateDNSZone: privateDnsZoneId
          }
        : {
            authorizedIPRanges: network.authorizedIpRanges
          }
    )
    hostedSystemProfile: {
      enabled: true
      nodeSubnetID: subnetIds.userNode
      systemNodeSubnetID: subnetIds.systemNode
    }
    nodeProvisioningProfile: {
      mode: 'Auto'
      defaultNodePools: 'Auto'
    }
    networkProfile: {
      networkPlugin: 'azure'
      networkPluginMode: 'overlay'
      networkDataplane: 'cilium'
      networkPolicy: 'cilium'
      podCidr: network.podCidr
      serviceCidr: network.serviceCidr
      dnsServiceIP: network.dnsServiceIp
      loadBalancerSku: 'standard'
    }
    oidcIssuerProfile: {
      enabled: true
    }
    securityProfile: {
      workloadIdentity: {
        enabled: true
      }
    }
    autoUpgradeProfile: {
      upgradeChannel: 'stable'
      nodeOSUpgradeChannel: 'NodeImage'
    }
    ingressProfile: {
      gatewayAPI: {
        installation: 'Standard'
      }
      webAppRouting: {
        enabled: true
        nginx: {
          defaultIngressControllerType: 'None'
        }
        gatewayAPIImplementations: {
          appRoutingIstio: {
            mode: 'Enabled'
          }
        }
      }
    }
    addonProfiles: {
      azurepolicy: {
        enabled: true
      }
      omsagent: {
        enabled: observability.containerInsights
        config: observability.containerInsights
          ? {
              logAnalyticsWorkspaceResourceID: logAnalyticsId
              useAADAuth: 'true'
            }
          : {}
      }
    }
    azureMonitorProfile: {
      metrics: {
        enabled: observability.managedPrometheus
      }
    }
  }
}

var adminRoles = [
  '4abbcc35-e782-43d8-92c5-2d3f1bd2253f'
  'b1ff04bb-8a4e-4dc4-8eb5-8693973ce19b'
]

resource platformAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for role in adminRoles: {
    name: guid(cluster.id, adminGroupObjectId, role)
    scope: cluster
    properties: {
      principalId: adminGroupObjectId
      principalType: 'Group'
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
    }
  }
]

output clusterInfo object = {
  name: cluster.name
  id: cluster.id
  fqdn: network.privateCluster ? cluster.properties.privateFQDN : cluster.properties.fqdn
}
output oidcIssuer string = cluster.properties.oidcIssuerProfile.issuerURL
output kubeletObjectId string = cluster.properties.identityProfile.kubeletidentity.objectId
