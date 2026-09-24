resource "azurerm_log_analytics_workspace" "logs" {
  count                        = var.config.observability.containerInsights ? 1 : 0
  name                         = "log-${local.name}"
  resource_group_name          = azurerm_resource_group.environment.name
  location                     = var.config.location
  sku                          = "PerGB2018"
  retention_in_days            = var.config.observability.logRetentionDays
  local_authentication_enabled = false
  tags                         = local.tags
}

resource "azurerm_monitor_workspace" "metrics" {
  count               = var.config.observability.managedPrometheus ? 1 : 0
  name                = "amw-${local.name}"
  resource_group_name = azurerm_resource_group.environment.name
  location            = var.config.location
  tags                = local.tags
}

resource "azurerm_dashboard_grafana" "dashboard" {
  count                             = var.config.observability.managedGrafana ? 1 : 0
  name                              = "grafana-${local.suffix}"
  resource_group_name               = azurerm_resource_group.environment.name
  location                          = var.config.location
  grafana_major_version             = "12"
  sku                               = "Standard"
  api_key_enabled                   = false
  deterministic_outbound_ip_enabled = true
  zone_redundancy_enabled           = true
  tags                              = local.tags
  identity { type = "SystemAssigned" }
  dynamic "azure_monitor_workspace_integrations" {
    for_each = azurerm_monitor_workspace.metrics
    content { resource_id = azure_monitor_workspace_integrations.value.id }
  }
}

resource "azurerm_role_assignment" "grafana_admin" {
  count                = var.config.observability.managedGrafana ? 1 : 0
  scope                = azurerm_dashboard_grafana.dashboard[0].id
  role_definition_name = "Grafana Admin"
  principal_id         = var.config.adminGroupObjectId
  principal_type       = "Group"
}

resource "azurerm_role_assignment" "grafana_reader" {
  count                = var.config.observability.managedGrafana && var.config.observability.managedPrometheus ? 1 : 0
  scope                = azurerm_monitor_workspace.metrics[0].id
  role_definition_name = "Monitoring Reader"
  principal_id         = azurerm_dashboard_grafana.dashboard[0].identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_monitor_data_collection_rule" "containers" {
  count               = var.config.observability.containerInsights ? 1 : 0
  name                = "ci-${local.name}"
  resource_group_name = azurerm_resource_group.environment.name
  location            = var.config.location
  kind                = "Linux"
  tags                = local.tags
  destinations {
    log_analytics {
      name                  = "logs"
      workspace_resource_id = azurerm_log_analytics_workspace.logs[0].id
    }
  }
  data_flow {
    streams      = ["Microsoft-ContainerInsights-Group-Default"]
    destinations = ["logs"]
  }
  data_sources {
    extension {
      name           = "ContainerInsightsExtension"
      streams        = ["Microsoft-ContainerInsights-Group-Default"]
      extension_name = "ContainerInsights"
      extension_json = jsonencode({ dataCollectionSettings = { interval = "1m", namespaceFilteringMode = "Off", enableContainerLogV2 = true } })
    }
  }
}

resource "azurerm_monitor_data_collection_rule" "prometheus" {
  count                       = var.config.observability.managedPrometheus ? 1 : 0
  name                        = "prom-${local.name}"
  resource_group_name         = azurerm_resource_group.environment.name
  location                    = var.config.location
  kind                        = "Linux"
  data_collection_endpoint_id = azurerm_monitor_workspace.metrics[0].default_data_collection_endpoint_id
  tags                        = local.tags
  destinations {
    monitor_account {
      name               = "metrics"
      monitor_account_id = azurerm_monitor_workspace.metrics[0].id
    }
  }
  data_flow {
    streams      = ["Microsoft-PrometheusMetrics"]
    destinations = ["metrics"]
  }
  data_sources {
    prometheus_forwarder {
      name    = "prometheus"
      streams = ["Microsoft-PrometheusMetrics"]
    }
  }
}

resource "azurerm_monitor_data_collection_rule_association" "containers" {
  count                   = var.config.observability.containerInsights ? 1 : 0
  name                    = "ContainerInsightsExtension"
  target_resource_id      = module.cluster.cluster.id
  data_collection_rule_id = azurerm_monitor_data_collection_rule.containers[0].id
}

resource "azurerm_monitor_data_collection_rule_association" "prometheus" {
  count                   = var.config.observability.managedPrometheus ? 1 : 0
  name                    = "prometheus-${local.name}"
  target_resource_id      = module.cluster.cluster.id
  data_collection_rule_id = azurerm_monitor_data_collection_rule.prometheus[0].id
}

resource "azurerm_monitor_diagnostic_setting" "cluster" {
  count                      = var.config.observability.containerInsights ? 1 : 0
  name                       = "service"
  target_resource_id         = module.cluster.cluster.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.logs[0].id
  dynamic "enabled_log" {
    for_each = var.config.environment == "production" ? toset(["kube-audit", "kube-audit-admin", "kube-apiserver", "kube-controller-manager", "kube-scheduler", "cluster-autoscaler", "cloud-controller-manager", "guard"]) : toset(["kube-apiserver", "kube-controller-manager", "guard"])
    content { category = enabled_log.value }
  }
  enabled_metric { category = "AllMetrics" }
}