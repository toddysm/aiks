terraform {
  required_version = ">= 1.9.0, < 2.0.0"
  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = ">= 2.0.0, < 3.0.0"
    }
  }
}

variable "config" {
  type = object({
    environment = string
    location    = string
    network = object({
      privateCluster      = bool
      authorizedIpRanges  = list(string)
      paasAllowedIpRanges = list(string)
      podCidr             = string
      serviceCidr         = string
      dnsServiceIp        = string
    })
    observability = object({
      containerInsights = bool
      managedPrometheus = bool
    })
  })
  validation {
    condition = contains(["dev", "production"], var.config.environment) && (
      var.config.environment != "production" || var.config.network.privateCluster
      ) && (
      var.config.network.privateCluster || length(var.config.network.authorizedIpRanges) > 0
      ) && (
      var.config.environment != "dev" || length(var.config.network.paasAllowedIpRanges) > 0
    ) && alltrue([for cidr in concat(var.config.network.authorizedIpRanges, var.config.network.paasAllowedIpRanges) : can(cidrhost(cidr, 0)) && !endswith(cidr, "/0")])
    error_message = "Require dev allowlists, private production, and no unrestricted source ranges."
  }
}

variable "name" { type = string }
variable "registry_name" { type = string }
variable "resource_group_id" { type = string }
variable "identity_id" { type = string }
variable "private_dns_zone_id" { type = string }
variable "log_analytics_id" { type = string }
variable "tags" { type = map(string) }
variable "subnet_ids" {
  type = object({ apiServer = string, systemNode = string, userNode = string })
}

resource "azapi_resource" "cluster" {
  type      = "Microsoft.ContainerService/managedClusters@2026-04-01"
  name      = "aks-${var.name}"
  parent_id = var.resource_group_id
  location  = var.config.location
  tags      = var.tags
  identity {
    type         = "UserAssigned"
    identity_ids = [var.identity_id]
  }
  body = {
    sku = { name = "Automatic", tier = "Standard" }
    properties = {
      dnsPrefix            = "aks-${var.name}"
      enableRBAC           = true
      disableLocalAccounts = true
      aadProfile           = { managed = true, enableAzureRBAC = true }
      apiServerAccessProfile = merge({
        enableVnetIntegration          = true
        subnetId                       = var.subnet_ids.apiServer
        enablePrivateCluster           = var.config.network.privateCluster
        enablePrivateClusterPublicFQDN = false
        disableRunCommand              = true
        }, var.config.network.privateCluster ? {
        privateDNSZone = var.private_dns_zone_id
        } : {}, var.config.network.privateCluster ? {} : {
        authorizedIPRanges = var.config.network.authorizedIpRanges
      })
      hostedSystemProfile = {
        enabled            = true
        nodeSubnetID       = var.subnet_ids.userNode
        systemNodeSubnetID = var.subnet_ids.systemNode
      }
      nodeProvisioningProfile = { mode = "Auto", defaultNodePools = "Auto" }
      networkProfile = {
        networkPlugin     = "azure"
        networkPluginMode = "overlay"
        networkDataplane  = "cilium"
        networkPolicy     = "cilium"
        podCidr           = var.config.network.podCidr
        serviceCidr       = var.config.network.serviceCidr
        dnsServiceIP      = var.config.network.dnsServiceIp
        loadBalancerSku   = "standard"
      }
      oidcIssuerProfile = { enabled = true }
      securityProfile   = { workloadIdentity = { enabled = true } }
      autoUpgradeProfile = {
        upgradeChannel       = "stable"
        nodeOSUpgradeChannel = "NodeImage"
      }
      ingressProfile = {
        gatewayAPI = { installation = "Standard" }
        webAppRouting = {
          enabled                   = true
          nginx                     = { defaultIngressControllerType = "None" }
          gatewayAPIImplementations = { appRoutingIstio = { mode = "Enabled" } }
        }
      }
      addonProfiles = {
        azurepolicy = { enabled = true }
        omsagent = {
          enabled = var.config.observability.containerInsights
          config = var.config.observability.containerInsights ? {
            logAnalyticsWorkspaceResourceID = var.log_analytics_id
            useAADAuth                      = "true"
          } : {}
        }
      }
      azureMonitorProfile = { metrics = { enabled = var.config.observability.managedPrometheus } }
    }
  }
  response_export_values = {
    fqdn            = "properties.fqdn"
    privateFqdn     = "properties.privateFQDN"
    oidcIssuer      = "properties.oidcIssuerProfile.issuerURL"
    kubeletObjectId = "properties.identityProfile.kubeletidentity.objectId"
  }
  replace_triggers_external_values = [var.config.network.podCidr, var.config.network.serviceCidr, var.config.network.dnsServiceIp, var.subnet_ids, var.private_dns_zone_id]
  lifecycle {
    precondition {
      condition     = !var.config.observability.containerInsights || var.log_analytics_id != ""
      error_message = "Container Insights requires an explicit Log Analytics destination."
    }
  }
}

resource "azapi_resource" "registry" {
  type      = "Microsoft.ContainerRegistry/registries@2026-03-01-preview"
  name      = var.registry_name
  parent_id = var.resource_group_id
  location  = var.config.location
  tags      = var.tags
  body = {
    sku = { name = "Premium" }
    properties = {
      adminUserEnabled         = false
      anonymousPullEnabled     = false
      roleAssignmentMode       = "LegacyRegistryPermissions"
      publicNetworkAccess      = var.config.environment == "production" ? "Disabled" : "Enabled"
      networkRuleBypassOptions = "None"
      dataEndpointEnabled      = true
      networkRuleSet = {
        defaultAction = "Deny"
        ipRules = var.config.environment == "production" ? [] : [for cidr in var.config.network.paasAllowedIpRanges : {
          action = "Allow", value = cidr
        }]
        virtualNetworkRules = var.config.environment == "production" ? [] : [for subnet in [var.subnet_ids.systemNode, var.subnet_ids.userNode] : {
          action = "Allow", virtualNetworkSubnetResourceId = subnet
        }]
      }
    }
  }
  response_export_values = { loginServer = "properties.loginServer" }
}

output "cluster" {
  value = {
    id   = azapi_resource.cluster.id
    name = azapi_resource.cluster.name
    fqdn = var.config.network.privateCluster ? azapi_resource.cluster.output.privateFqdn : azapi_resource.cluster.output.fqdn
  }
}
output "registry" {
  value = { id = azapi_resource.registry.id, name = azapi_resource.registry.name, loginServer = azapi_resource.registry.output.loginServer }
}
output "oidc_issuer" { value = azapi_resource.cluster.output.oidcIssuer }
output "kubelet_object_id" { value = azapi_resource.cluster.output.kubeletObjectId }