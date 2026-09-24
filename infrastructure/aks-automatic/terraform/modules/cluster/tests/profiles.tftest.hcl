mock_provider "azapi" {}

variables {
  name                = "aiks-dev-12345678"
  registry_name       = "aiksdev12345678"
  resource_group_id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test"
  identity_id         = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cluster"
  private_dns_zone_id = ""
  log_analytics_id    = ""
  tags                = { environment = "dev" }
  subnet_ids = {
    apiServer  = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Network/virtualNetworks/test/subnets/api"
    systemNode = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Network/virtualNetworks/test/subnets/system"
    userNode   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Network/virtualNetworks/test/subnets/user"
  }
  config = {
    environment = "dev"
    location    = "westus3"
    network = {
      privateCluster      = false
      authorizedIpRanges  = ["203.0.113.10/32"]
      paasAllowedIpRanges = ["203.0.113.10/32"]
      podCidr             = "10.240.0.0/16"
      serviceCidr         = "10.2.0.0/16"
      dnsServiceIp        = "10.2.0.10"
    }
    observability = { containerInsights = false, managedPrometheus = false }
  }
}

run "custom_dev_network" {
  command = plan
  assert {
    condition     = azapi_resource.cluster.body.properties.networkProfile.serviceCidr == "10.2.0.0/16" && azapi_resource.cluster.body.properties.networkProfile.dnsServiceIP == "10.2.0.10" && azapi_resource.cluster.body.properties.networkProfile.podCidr == "10.240.0.0/16"
    error_message = "Custom creation-time networks must not be replaced with service defaults."
  }
  assert {
    condition     = length(azapi_resource.registry.body.properties.networkRuleSet.virtualNetworkRules) == 2 && azapi_resource.registry.body.properties.networkRuleSet.defaultAction == "Deny"
    error_message = "Development registry must restrict both node subnets."
  }
  assert {
    condition     = azapi_resource.cluster.body.properties.disableLocalAccounts && azapi_resource.cluster.body.properties.apiServerAccessProfile.disableRunCommand && azapi_resource.cluster.body.sku.name == "Automatic"
    error_message = "Cluster security defaults and Automatic SKU are mandatory."
  }
  assert {
    condition     = !contains(keys(azapi_resource.cluster.response_export_values), "kube_config") && length(azapi_resource.cluster.response_export_values) == 4
    error_message = "Only nonsecret cluster fields may be exported."
  }
}

run "private_production" {
  command = plan
  variables {
    private_dns_zone_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Network/privateDnsZones/private.westus3.azmk8s.io"
    config = {
      environment = "production"
      location    = "westus3"
      network = {
        privateCluster = true, authorizedIpRanges = [], paasAllowedIpRanges = []
        podCidr        = "10.240.0.0/16", serviceCidr = "10.2.0.0/16", dnsServiceIp = "10.2.0.10"
      }
      observability = { containerInsights = false, managedPrometheus = false }
    }
  }
  assert {
    condition     = azapi_resource.registry.body.properties.publicNetworkAccess == "Disabled" && azapi_resource.cluster.body.properties.apiServerAccessProfile.enablePrivateCluster && !azapi_resource.cluster.body.properties.apiServerAccessProfile.enablePrivateClusterPublicFQDN
    error_message = "Production endpoints must be private."
  }
}