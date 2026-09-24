mock_provider "azurerm" {
  override_during = plan
  mock_data "azurerm_client_config" {
    defaults = {
      subscription_id = "00000000-0000-0000-0000-000000000000"
      tenant_id       = "11111111-1111-4111-8111-111111111111"
      object_id       = "22222222-2222-4222-8222-222222222222"
    }
  }
  mock_resource "azurerm_resource_group" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test" }
  }
  mock_resource "azurerm_virtual_network" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Network/virtualNetworks/test" }
  }
  mock_resource "azurerm_log_analytics_workspace" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.OperationalInsights/workspaces/logs" }
  }
  mock_resource "azurerm_monitor_workspace" {
    defaults = {
      id                                  = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Monitor/accounts/metrics"
      default_data_collection_endpoint_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Insights/dataCollectionEndpoints/metrics"
      query_endpoint                      = "https://metrics.example.invalid/"
    }
  }
  mock_resource "azurerm_dashboard_grafana" {
    defaults = {
      id       = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Dashboard/grafana/dashboard"
      endpoint = "https://grafana.example.invalid/"
      identity = { principal_id = "44444444-4444-4444-8444-444444444444", tenant_id = "11111111-1111-4111-8111-111111111111" }
    }
  }
  mock_resource "azurerm_key_vault" {
    defaults = {
      id        = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.KeyVault/vaults/readiness"
      vault_uri = "https://aiks-parity.vault.azure.net/"
    }
  }
  mock_resource "azurerm_private_dns_zone" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Network/privateDnsZones/test.invalid" }
  }
  mock_resource "azurerm_monitor_action_group" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Insights/actionGroups/operators" }
  }
}
mock_provider "azapi" {}

override_resource {
  target          = azurerm_subnet.api
  override_during = plan
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Network/virtualNetworks/test/subnets/api-server" }
}
override_resource {
  target          = azurerm_subnet.system
  override_during = plan
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Network/virtualNetworks/test/subnets/system-node" }
}
override_resource {
  target          = azurerm_subnet.user
  override_during = plan
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Network/virtualNetworks/test/subnets/user-node" }
}
override_resource {
  target          = azurerm_subnet.private_endpoint
  override_during = plan
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Network/virtualNetworks/test/subnets/private-endpoint" }
}
override_resource {
  target          = azurerm_monitor_data_collection_rule.containers[0]
  override_during = plan
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Insights/dataCollectionRules/containers" }
}
override_resource {
  target          = azurerm_monitor_data_collection_rule.prometheus[0]
  override_during = plan
  values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Insights/dataCollectionRules/prometheus" }
}

override_resource {
  target          = azurerm_user_assigned_identity.identity["cluster"]
  override_during = plan
  values = {
    id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.ManagedIdentity/userAssignedIdentities/cluster"
    client_id    = "55555555-5555-4555-8555-555555555555"
    principal_id = "66666666-6666-4666-8666-666666666666"
  }
}
override_resource {
  target          = azurerm_user_assigned_identity.identity["readiness"]
  override_during = plan
  values = {
    id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.ManagedIdentity/userAssignedIdentities/readiness"
    client_id    = "77777777-7777-4777-8777-777777777777"
    principal_id = "88888888-8888-4888-8888-888888888888"
  }
}

override_resource {
  target          = module.cluster.azapi_resource.cluster
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.ContainerService/managedClusters/test"
    output = {
      fqdn       = "test.example.invalid", privateFqdn = "private.example.invalid"
      oidcIssuer = "https://issuer.example.invalid/", kubeletObjectId = "33333333-3333-4333-8333-333333333333"
    }
  }
}

override_resource {
  target          = module.cluster.azapi_resource.registry
  override_during = plan
  values = {
    id     = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.ContainerRegistry/registries/test"
    output = { loginServer = "test.azurecr.io" }
  }
}

run "development" {
  command = plan
  variables {
    config = merge(yamldecode(file("../../config/dev.example.yaml")).spec, {
      prefix             = yamldecode(file("../../config/dev.example.yaml")).spec.naming.prefix
      adminGroupObjectId = yamldecode(file("../../config/dev.example.yaml")).spec.identity.adminGroupObjectId
    })
  }
  assert {
    condition     = azurerm_key_vault.readiness.rbac_authorization_enabled && azurerm_key_vault.readiness.network_acls[0].default_action == "Deny" && length(azurerm_private_endpoint.service) == 0
    error_message = "Development must retain restricted RBAC vault access without production endpoints."
  }
  assert {
    condition     = alltrue([for subnet in [azurerm_subnet.system, azurerm_subnet.user] : toset([for endpoint in subnet.service_endpoint : endpoint.service]) == toset(["Microsoft.ContainerRegistry", "Microsoft.KeyVault"])])
    error_message = "Both development node subnets require registry and vault service endpoints."
  }
  assert {
    condition     = length(azurerm_role_assignment.cluster_admin) == 2 && azurerm_role_assignment.vault_reader.role_definition_name == "Key Vault Reader" && azurerm_role_assignment.pull.role_definition_name == "AcrPull"
    error_message = "Role bindings must match the Bicep contract."
  }
  assert {
    condition     = azurerm_federated_identity_credential.readiness.subject == "system:serviceaccount:aiks-readiness:readiness" && azapi_resource.marker.body.properties.attributes.exportable == false
    error_message = "Readiness identity/key contract changed."
  }
  assert {
    condition     = azurerm_resource_group.environment.name == "rg-${var.config.prefix}-${var.config.environment}-${substr(provider::azapi::unique_string([data.azurerm_client_config.current.subscription_id, var.config.environment, var.config.prefix]), 0, 8)}"
    error_message = "Non-global resource naming must use the same ARM-compatible seed as Bicep."
  }
}

run "production" {
  command = plan
  variables {
    config = merge(yamldecode(file("../../config/production.example.yaml")).spec, {
      prefix             = yamldecode(file("../../config/production.example.yaml")).spec.naming.prefix
      adminGroupObjectId = yamldecode(file("../../config/production.example.yaml")).spec.identity.adminGroupObjectId
    })
  }
  assert {
    condition     = !azurerm_key_vault.readiness.public_network_access_enabled && azurerm_key_vault.readiness.purge_protection_enabled && length(azurerm_private_endpoint.service) == 2 && length(azurerm_private_dns_zone.api) == 1
    error_message = "Production needs private endpoints, private API DNS, and purge protection."
  }
  assert {
    condition     = length(azurerm_monitor_alert_prometheus_rule_group.signals[0].rule) == 5 && length(azurerm_monitor_scheduled_query_rules_alert_v2.pipeline) == 1 && length(azurerm_monitor_activity_log_alert.health) == 1
    error_message = "Required operational alert coverage is missing."
  }
  assert {
    condition     = !azurerm_log_analytics_workspace.logs[0].local_authentication_enabled && !azurerm_dashboard_grafana.dashboard[0].api_key_enabled && azurerm_role_assignment.grafana_reader[0].role_definition_name == "Monitoring Reader"
    error_message = "Monitoring must use identity-based access."
  }
}

run "reject_unmonitored_production" {
  command = plan
  variables {
    config = merge(yamldecode(file("../../config/production.example.yaml")).spec, {
      prefix             = "aiks-production"
      adminGroupObjectId = "11111111-1111-4111-8111-111111111111"
      observability      = merge(yamldecode(file("../../config/production.example.yaml")).spec.observability, { containerInsights = false })
    })
  }
  expect_failures = [var.config]
}