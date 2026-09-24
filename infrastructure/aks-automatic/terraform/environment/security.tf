resource "azurerm_key_vault" "readiness" {
  name                          = "kv-${substr(var.config.prefix, 0, 8)}-${local.suffix}"
  resource_group_name           = azurerm_resource_group.environment.name
  location                      = var.config.location
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  soft_delete_retention_days    = 90
  purge_protection_enabled      = var.config.environment == "production"
  public_network_access_enabled = var.config.environment != "production"
  tags                          = local.tags
  network_acls {
    bypass                     = "None"
    default_action             = "Deny"
    ip_rules                   = var.config.environment == "dev" ? var.config.network.paasAllowedIpRanges : []
    virtual_network_subnet_ids = var.config.environment == "dev" ? [azurerm_subnet.system.id, azurerm_subnet.user.id] : []
  }
}

resource "azapi_resource" "marker" {
  type      = "Microsoft.KeyVault/vaults/keys@2024-11-01"
  name      = "readiness-marker"
  parent_id = azurerm_key_vault.readiness.id
  body = {
    properties = {
      kty        = "RSA"
      keySize    = 2048
      keyOps     = ["verify"]
      attributes = { enabled = true, exportable = false }
    }
  }
  response_export_values = []
}

resource "azurerm_role_assignment" "cluster_admin" {
  for_each             = toset(["Azure Kubernetes Service Cluster User Role", "Azure Kubernetes Service RBAC Cluster Admin"])
  scope                = module.cluster.cluster.id
  role_definition_name = each.value
  principal_id         = var.config.adminGroupObjectId
  principal_type       = "Group"
}

resource "azurerm_role_assignment" "pull" {
  scope                = module.cluster.registry.id
  role_definition_name = "AcrPull"
  principal_id         = module.cluster.kubelet_object_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "vault_reader" {
  scope                = azurerm_key_vault.readiness.id
  role_definition_name = "Key Vault Reader"
  principal_id         = azurerm_user_assigned_identity.identity["readiness"].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_federated_identity_credential" "readiness" {
  name                      = "readiness"
  user_assigned_identity_id = azurerm_user_assigned_identity.identity["readiness"].id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = module.cluster.oidc_issuer
  subject                   = "system:serviceaccount:aiks-readiness:readiness"
}

locals {
  private_services = var.config.environment == "production" ? {
    registry = { zone = "privatelink.azurecr.io", group = "registry", id = module.cluster.registry.id }
    vault    = { zone = "privatelink.vaultcore.azure.net", group = "vault", id = azurerm_key_vault.readiness.id }
  } : {}
}

resource "azurerm_private_dns_zone" "service" {
  for_each            = local.private_services
  name                = each.value.zone
  resource_group_name = azurerm_resource_group.environment.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "service" {
  for_each             = local.private_services
  name                 = "pe-${each.key == "registry" ? "acr" : each.key}-${local.name}"
  private_dns_zone_id  = azurerm_private_dns_zone.service[each.key].id
  virtual_network_id   = azurerm_virtual_network.environment.id
  registration_enabled = false
  tags                 = local.tags
}

resource "azurerm_private_endpoint" "service" {
  for_each            = local.private_services
  name                = "pe-${each.key == "registry" ? "acr" : each.key}-${local.name}"
  resource_group_name = azurerm_resource_group.environment.name
  location            = var.config.location
  subnet_id           = azurerm_subnet.private_endpoint.id
  tags                = local.tags
  private_service_connection {
    name                           = "pe-${each.key == "registry" ? "acr" : each.key}-${local.name}"
    is_manual_connection           = false
    private_connection_resource_id = each.value.id
    subresource_names              = [each.value.group]
  }
  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [azurerm_private_dns_zone.service[each.key].id]
  }
}