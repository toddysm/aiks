resource "azurerm_virtual_network" "environment" {
  name                = "vnet-${local.name}"
  resource_group_name = azurerm_resource_group.environment.name
  location            = var.config.location
  address_space       = [var.config.network.vnetCidr]
  tags                = local.tags
}

resource "azurerm_subnet" "api" {
  name                 = "api-server"
  resource_group_name  = azurerm_resource_group.environment.name
  virtual_network_name = azurerm_virtual_network.environment.name
  address_prefixes     = [var.config.network.apiServerSubnetCidr]
  delegation {
    name = "aks-delegation"
    service_delegation {
      name    = "Microsoft.ContainerService/managedClusters"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "system" {
  name                 = "system-node"
  resource_group_name  = azurerm_resource_group.environment.name
  virtual_network_name = azurerm_virtual_network.environment.name
  address_prefixes     = [var.config.network.systemNodeSubnetCidr]
  dynamic "service_endpoint" {
    for_each = var.config.environment == "dev" ? toset(["Microsoft.ContainerRegistry", "Microsoft.KeyVault"]) : toset([])
    content { service = service_endpoint.value }
  }
  lifecycle { ignore_changes = [delegation] }
  depends_on = [azurerm_subnet.api]
}

resource "azurerm_subnet" "user" {
  name                 = "user-node"
  resource_group_name  = azurerm_resource_group.environment.name
  virtual_network_name = azurerm_virtual_network.environment.name
  address_prefixes     = [var.config.network.userNodeSubnetCidr]
  dynamic "service_endpoint" {
    for_each = var.config.environment == "dev" ? toset(["Microsoft.ContainerRegistry", "Microsoft.KeyVault"]) : toset([])
    content { service = service_endpoint.value }
  }
  depends_on = [azurerm_subnet.system]
}

resource "azurerm_subnet" "private_endpoint" {
  name                              = "private-endpoint"
  resource_group_name               = azurerm_resource_group.environment.name
  virtual_network_name              = azurerm_virtual_network.environment.name
  address_prefixes                  = [var.config.network.privateEndpointSubnetCidr]
  private_endpoint_network_policies = "Disabled"
  depends_on                        = [azurerm_subnet.user]
}

resource "azurerm_role_assignment" "network" {
  scope                = azurerm_virtual_network.environment.id
  role_definition_name = "Network Contributor"
  principal_id         = azurerm_user_assigned_identity.identity["cluster"].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_private_dns_zone" "api" {
  count               = var.config.network.privateCluster ? 1 : 0
  name                = "private.${var.config.location}.azmk8s.io"
  resource_group_name = azurerm_resource_group.environment.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "api" {
  count                = var.config.network.privateCluster ? 1 : 0
  name                 = "vnet-${local.name}"
  private_dns_zone_id  = azurerm_private_dns_zone.api[0].id
  virtual_network_id   = azurerm_virtual_network.environment.id
  registration_enabled = false
  tags                 = local.tags
}

resource "azurerm_role_assignment" "dns" {
  count                = var.config.network.privateCluster ? 1 : 0
  scope                = azurerm_private_dns_zone.api[0].id
  role_definition_name = "Private DNS Zone Contributor"
  principal_id         = azurerm_user_assigned_identity.identity["cluster"].principal_id
  principal_type       = "ServicePrincipal"
}