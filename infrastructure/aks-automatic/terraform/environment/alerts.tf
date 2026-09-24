resource "azurerm_monitor_action_group" "operators" {
  count               = length(var.config.observability.actionGroupReceivers) > 0 ? 1 : 0
  name                = "alerts-${local.name}"
  resource_group_name = azurerm_resource_group.environment.name
  short_name          = substr(local.name, 0, 12)
  tags                = local.tags
  dynamic "email_receiver" {
    for_each = var.config.observability.actionGroupReceivers
    content {
      name                    = email_receiver.value.name
      email_address           = email_receiver.value.emailAddress
      use_common_alert_schema = true
    }
  }
}

locals {
  action_group_ids = concat(var.config.observability.actionGroupResourceIds, azurerm_monitor_action_group.operators[*].id)
  alerts_enabled   = var.config.environment == "production" || length(var.config.observability.actionGroupResourceIds) > 0 || length(var.config.observability.actionGroupReceivers) > 0
  signals = {
    ReadinessUnavailable   = { expression = "absent(aiks_readiness_info{status=\"ready\"}) or min(aiks_readiness_info{status=\"ready\"}) != 1", severity = 1, window = "PT5M" }
    FailedPods             = { expression = "sum(kube_pod_status_phase{phase=\"Failed\"}) > 0", severity = 2, window = "PT5M" }
    NodeNotReady           = { expression = "kube_node_status_condition{condition=\"Ready\",status=\"true\"} == 0", severity = 1, window = "PT5M" }
    CpuRequestsPressure    = { expression = "sum by(node) (kube_pod_container_resource_requests{resource=\"cpu\"}) / sum by(node) (kube_node_status_capacity{resource=\"cpu\"}) > 0.85", severity = 2, window = "PT15M" }
    MemoryRequestsPressure = { expression = "sum by(node) (kube_pod_container_resource_requests{resource=\"memory\"}) / sum by(node) (kube_node_status_capacity{resource=\"memory\"}) > 0.85", severity = 2, window = "PT15M" }
  }
}

resource "azurerm_monitor_activity_log_alert" "health" {
  count               = local.alerts_enabled ? 1 : 0
  name                = "health-${local.name}"
  resource_group_name = azurerm_resource_group.environment.name
  location            = "global"
  scopes              = [module.cluster.cluster.id]
  description         = "Severity 1: AKS Resource Health reports unavailable or degraded."
  tags                = local.tags
  criteria {
    category    = "ResourceHealth"
    resource_id = module.cluster.cluster.id
    resource_health { current = ["Unavailable", "Degraded"] }
  }
  dynamic "action" {
    for_each = local.action_group_ids
    content {
      action_group_id    = action.value
      webhook_properties = { severity = "1", environment = var.config.environment }
    }
  }
}

resource "azurerm_monitor_alert_prometheus_rule_group" "signals" {
  count               = var.config.observability.managedPrometheus && local.alerts_enabled ? 1 : 0
  name                = "signals-${local.name}"
  resource_group_name = azurerm_resource_group.environment.name
  location            = var.config.location
  cluster_name        = module.cluster.cluster.name
  scopes              = [azurerm_monitor_workspace.metrics[0].id]
  interval            = "PT1M"
  tags                = local.tags
  dynamic "rule" {
    for_each = local.signals
    content {
      alert      = rule.key
      expression = rule.value.expression
      severity   = rule.value.severity
      for        = rule.value.window
      enabled    = true
      labels     = { environment = var.config.environment, cluster = module.cluster.cluster.name }
      dynamic "action" {
        for_each = local.action_group_ids
        content { action_group_id = action.value }
      }
      alert_resolution {
        auto_resolved   = true
        time_to_resolve = "PT5M"
      }
    }
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "pipeline" {
  count                   = var.config.observability.containerInsights && local.alerts_enabled ? 1 : 0
  name                    = "pipeline-${local.name}"
  resource_group_name     = azurerm_resource_group.environment.name
  location                = var.config.location
  display_name            = "AKS monitoring pipeline heartbeat missing"
  scopes                  = [azurerm_log_analytics_workspace.logs[0].id]
  severity                = 2
  evaluation_frequency    = "PT5M"
  window_duration         = "PT15M"
  skip_query_validation   = true
  auto_mitigation_enabled = true
  tags                    = local.tags
  criteria {
    query                   = "KubePodInventory | where ClusterId =~ \"${module.cluster.cluster.id}\""
    time_aggregation_method = "Count"
    operator                = "LessThan"
    threshold               = 1
    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }
  action { action_groups = local.action_group_ids }
}