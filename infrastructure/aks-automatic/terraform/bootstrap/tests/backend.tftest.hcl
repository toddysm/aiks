mock_provider "azurerm" {}

variables {
  config = {
    environment        = "dev", location = "westus3", owner = "owner"
    resource_group     = "aiks-tfstate-dev", storage_account = "staiksdev0001", container = "tfstate"
    operator_object_id = "11111111-1111-4111-8111-111111111111"
    allowed_ip_ranges  = ["203.0.113.10/32"], allowed_subnet_ids = []
  }
}

run "identity_only_backend" {
  command = plan
  assert {
    condition     = !azurerm_storage_account.backend.shared_access_key_enabled && !azurerm_storage_account.backend.allow_nested_items_to_be_public && azurerm_storage_account.backend.min_tls_version == "TLS1_2" && azurerm_storage_account.backend.network_rules[0].default_action == "Deny"
    error_message = "Backend must disallow shared keys/public blobs and restrict network access."
  }
  assert {
    condition     = azurerm_storage_account.backend.blob_properties[0].versioning_enabled && azurerm_role_assignment.operator.role_definition_name == "Storage Blob Data Contributor" && output.backend.use_azuread_auth && output.backend.use_cli
    error_message = "Backend durability and Entra-only access are mandatory."
  }
}

run "reject_open_network" {
  command = plan
  variables {
    config = {
      environment        = "dev", location = "westus3", owner = "owner"
      resource_group     = "aiks-tfstate-dev", storage_account = "staiksdev0001", container = "tfstate"
      operator_object_id = "11111111-1111-4111-8111-111111111111"
      allowed_ip_ranges  = ["0.0.0.0/0"], allowed_subnet_ids = []
    }
  }
  expect_failures = [var.config]
}