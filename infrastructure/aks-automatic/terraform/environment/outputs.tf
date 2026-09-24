output "result" {
  value = {
    environment   = var.config.environment
    location      = var.config.location
    resourceGroup = { name = azurerm_resource_group.environment.name, id = azurerm_resource_group.environment.id }
    cluster       = module.cluster.cluster
    registry      = module.cluster.registry
    vault = {
      name          = azurerm_key_vault.readiness.name
      id            = azurerm_key_vault.readiness.id
      uri           = azurerm_key_vault.readiness.vault_uri
      markerKeyName = azapi_resource.marker.name
    }
    network = {
      vnetId = azurerm_virtual_network.environment.id
      subnetIds = {
        apiServer       = azurerm_subnet.api.id
        systemNode      = azurerm_subnet.system.id
        userNode        = azurerm_subnet.user.id
        privateEndpoint = azurerm_subnet.private_endpoint.id
      }
    }
    identities = { for name, identity in azurerm_user_assigned_identity.identity : name => {
      name = identity.name, id = identity.id, clientId = identity.client_id, principalId = identity.principal_id
    } }
    monitoring = {
      logAnalyticsId          = try(azurerm_log_analytics_workspace.logs[0].id, "")
      azureMonitorWorkspaceId = try(azurerm_monitor_workspace.metrics[0].id, "")
      prometheusQueryEndpoint = try(azurerm_monitor_workspace.metrics[0].query_endpoint, "")
      grafanaId               = try(azurerm_dashboard_grafana.dashboard[0].id, "")
      grafanaEndpoint         = try(azurerm_dashboard_grafana.dashboard[0].endpoint, "")
    }
    readiness = {
      namespace         = "aiks-readiness"
      serviceAccount    = "readiness"
      clientId          = azurerm_user_assigned_identity.identity["readiness"].client_id
      tenantId          = data.azurerm_client_config.current.tenant_id
      vaultUri          = azurerm_key_vault.readiness.vault_uri
      markerKeyName     = azapi_resource.marker.name
      gatewayClassName  = "approuting-istio"
      internalGateway   = var.config.environment == "production"
      managedPrometheus = var.config.observability.managedPrometheus
    }
  }
}