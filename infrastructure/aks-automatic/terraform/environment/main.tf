provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy = false
    }
  }
  resource_provider_registrations = "none"
  storage_use_azuread             = true
  use_cli                         = true
}

provider "azapi" {
  use_cli = true
}

variable "config" {
  type = object({
    environment        = string
    location           = string
    prefix             = string
    adminGroupObjectId = string
    tags               = map(string)
    network = object({
      vnetCidr                  = string
      apiServerSubnetCidr       = string
      systemNodeSubnetCidr      = string
      userNodeSubnetCidr        = string
      privateEndpointSubnetCidr = string
      podCidr                   = string
      serviceCidr               = string
      dnsServiceIp              = string
      privateCluster            = bool
      authorizedIpRanges        = list(string)
      paasAllowedIpRanges       = list(string)
    })
    observability = object({
      containerInsights      = bool
      managedPrometheus      = bool
      managedGrafana         = bool
      logRetentionDays       = number
      actionGroupResourceIds = list(string)
      actionGroupReceivers   = list(object({ name = string, emailAddress = string }))
    })
  })
  validation {
    condition = contains(["dev", "production"], var.config.environment) && (
      var.config.environment != "production" || (
        var.config.network.privateCluster && var.config.observability.containerInsights &&
        var.config.observability.managedPrometheus && var.config.observability.managedGrafana &&
        length(concat(var.config.observability.actionGroupResourceIds, [for receiver in var.config.observability.actionGroupReceivers : receiver.name])) > 0
      )
    ) && can(regex("^[a-z][a-z0-9-]{1,22}[a-z0-9]$", var.config.prefix))
    error_message = "Require a valid prefix/environment and private, monitored production with alert receivers."
  }
}

data "azurerm_client_config" "current" {}

locals {
  suffix = substr(sha256("${data.azurerm_client_config.current.subscription_id}/${var.config.prefix}/${var.config.environment}"), 0, 8)
  name   = "${var.config.prefix}-${var.config.environment}-${local.suffix}"
  tags   = merge(var.config.tags, { environment = var.config.environment, aiks-managed = "true" })
}

resource "azurerm_resource_group" "environment" {
  name     = "rg-${local.name}"
  location = var.config.location
  tags     = local.tags
}

resource "azurerm_user_assigned_identity" "identity" {
  for_each            = toset(["cluster", "readiness"])
  name                = "id-${each.key}-${local.name}"
  resource_group_name = azurerm_resource_group.environment.name
  location            = var.config.location
  tags                = local.tags
}

module "cluster" {
  source              = "../modules/cluster"
  config              = var.config
  name                = local.name
  registry_name       = "acr${replace(var.config.prefix, "-", "")}${var.config.environment}${local.suffix}"
  resource_group_id   = azurerm_resource_group.environment.id
  identity_id         = azurerm_user_assigned_identity.identity["cluster"].id
  private_dns_zone_id = try(azurerm_private_dns_zone.api[0].id, "")
  log_analytics_id    = try(azurerm_log_analytics_workspace.logs[0].id, "")
  tags                = local.tags
  subnet_ids = {
    apiServer  = azurerm_subnet.api.id
    systemNode = azurerm_subnet.system.id
    userNode   = azurerm_subnet.user.id
  }
  depends_on = [azurerm_role_assignment.network, azurerm_role_assignment.dns, azurerm_private_dns_zone_virtual_network_link.api, azurerm_monitor_data_collection_rule.containers, azurerm_monitor_data_collection_rule.prometheus]
}