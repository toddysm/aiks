terraform {
  required_version = ">= 1.9.0, < 2.0.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 5.0.1, < 6.0.0"
    }
  }
}

provider "azurerm" {
  features {}
  resource_provider_registrations = "none"
  storage_use_azuread             = true
  use_cli                         = true
}

variable "config" {
  type = object({
    environment        = string
    location           = string
    owner              = string
    resource_group     = string
    storage_account    = string
    container          = string
    operator_object_id = string
    allowed_ip_ranges  = list(string)
    allowed_subnet_ids = list(string)
  })
  validation {
    condition = length(concat(var.config.allowed_ip_ranges, var.config.allowed_subnet_ids)) > 0 && alltrue([
      for cidr in var.config.allowed_ip_ranges : can(cidrhost(cidr, 0)) && !endswith(cidr, "/0")
    ])
    error_message = "State storage requires explicit operator IP/subnet allowlists; unrestricted access is prohibited."
  }
}

locals {
  tags = { aiks-managed = "true", aiks-purpose = "terraform-state", aiks-owner = var.config.owner, environment = var.config.environment }
}

resource "azurerm_resource_group" "backend" {
  name     = var.config.resource_group
  location = var.config.location
  tags     = local.tags
}

resource "azurerm_storage_account" "backend" {
  name                              = var.config.storage_account
  resource_group_name               = azurerm_resource_group.backend.name
  location                          = var.config.location
  account_tier                      = "Standard"
  account_replication_type          = "ZRS"
  min_tls_version                   = "TLS1_2"
  https_traffic_only_enabled        = true
  shared_access_key_enabled         = false
  default_to_oauth_authentication   = true
  allow_nested_items_to_be_public   = false
  infrastructure_encryption_enabled = true
  tags                              = local.tags
  network_rules {
    default_action             = "Deny"
    bypass                     = ["None"]
    ip_rules                   = [for cidr in var.config.allowed_ip_ranges : trimsuffix(cidr, "/32")]
    virtual_network_subnet_ids = var.config.allowed_subnet_ids
  }
  blob_properties {
    versioning_enabled = true
    delete_retention_policy { days = 7 }
    container_delete_retention_policy { days = 7 }
  }
}

resource "azurerm_storage_container" "backend" {
  name                  = var.config.container
  storage_account_id    = azurerm_storage_account.backend.id
  container_access_type = "private"
}

resource "azurerm_role_assignment" "operator" {
  scope                = azurerm_storage_container.backend.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.config.operator_object_id
}

output "backend" {
  value = {
    resource_group_name  = azurerm_resource_group.backend.name
    storage_account_name = azurerm_storage_account.backend.name
    container_name       = azurerm_storage_container.backend.name
    key                  = "bootstrap.tfstate"
    use_cli              = true
    use_azuread_auth     = true
  }
}