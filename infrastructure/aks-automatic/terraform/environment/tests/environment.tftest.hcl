mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      subscription_id = "00000000-0000-0000-0000-000000000000"
      tenant_id       = "11111111-1111-4111-8111-111111111111"
      object_id       = "22222222-2222-4222-8222-222222222222"
    }
  }
}
mock_provider "azapi" {}

override_module {
  target = module.cluster
  outputs = {
    cluster = {
      id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.ContainerService/managedClusters/test"
      name = "test", fqdn = "test.example.invalid"
    }
    registry = {
      id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.ContainerRegistry/registries/test"
      name = "test", loginServer = "test.azurecr.io"
    }
    oidc_issuer       = "https://issuer.example.invalid/"
    kubelet_object_id = "33333333-3333-4333-8333-333333333333"
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
    condition     = length(azurerm_role_assignment.cluster_admin) == 2 && azurerm_role_assignment.vault_reader.role_definition_name == "Key Vault Reader" && azurerm_role_assignment.pull.role_definition_name == "AcrPull"
    error_message = "Role bindings must match the Bicep contract."
  }
  assert {
    condition     = azurerm_federated_identity_credential.readiness.subject == "system:serviceaccount:aiks-readiness:readiness" && azapi_resource.marker.body.properties.attributes.exportable == false
    error_message = "Readiness identity/key contract changed."
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