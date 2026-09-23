# Terraform foundation

Status: provider-only scaffold for [work item #8](https://github.com/toddysm/aiks/issues/8).
The environment and state lifecycle are not implemented. Do not use this root
to deploy or destroy an environment. It currently has no resources, provider
authentication configuration, or backend configuration.

## Provider compatibility checkpoint

Verified on 2026-09-23 with Terraform 1.15.8, AzureRM 5.6.0, and AzAPI 2.12.0.
The lock file includes checksums for `darwin_arm64` and `linux_amd64`.

The [accepted design](../../../docs/architecture/infrastructure/aks-automatic-foundation.md)
requires the native `azurerm_kubernetes_automatic_cluster` and limits AzAPI to
missing Gateway/monitoring profiles, the marker-key child, and identity reads.
The installed provider cannot represent the full accepted contract:

| Required behavior | Verified limitation |
| --- | --- |
| Apply `network.podCidr`, `network.serviceCidr`, and `network.dnsServiceIp` when creating the cluster | The native Automatic resource has no network-profile inputs. Its creation request omits `networkProfile`. |
| Restrict development registry access using the configured node subnets as well as operator IP ranges | `azurerm_container_registry.network_rule_set` exposes `default_action` and `ip_rule`, but no virtual-network rule field. |

The [Bicep cluster](../bicep/modules/aks.bicep) and
[registry](../bicep/modules/registry.bicep) explicitly set those properties.
Relying on service defaults would not honor custom configuration or prove parity.
The administrator group is not a blocker: separate role assignments can implement
the same access model as Bicep.

Sources:

- [Pinned Automatic resource creation code](https://github.com/hashicorp/terraform-provider-azurerm/blob/v5.6.0/internal/services/containers/kubernetes_automatic_cluster_resource.go).
- [Pinned Automatic resource documentation](https://registry.terraform.io/providers/hashicorp/azurerm/5.6.0/docs/resources/kubernetes_automatic_cluster).
- [Pinned registry resource documentation](https://registry.terraform.io/providers/hashicorp/azurerm/5.6.0/docs/resources/container_registry).
- [Azure networking constraints](https://learn.microsoft.com/en-us/azure/aks/concepts-network-azure-cni-overlay#ip-address-planning).
  The documented service-range extension and limited pod-range expansion do not
  supply the missing initial network and DNS configuration through AzureRM.

## Reproduce offline

Run from the repository root. These commands download signed provider binaries
but do not authenticate to Azure, initialize remote state, or create resources.

```sh
terraform -chdir=infrastructure/aks-automatic/terraform/environment init -backend=false -input=false -lockfile=readonly
terraform -chdir=infrastructure/aks-automatic/terraform/environment fmt -check
terraform -chdir=infrastructure/aks-automatic/terraform/environment validate
terraform -chdir=infrastructure/aks-automatic/terraform/environment providers schema -json |
  jq '.provider_schemas["registry.terraform.io/hashicorp/azurerm"].resource_schemas |
    {automatic_cluster: (.azurerm_kubernetes_automatic_cluster.block |
      {attributes: (.attributes | keys), blocks: (.block_types | keys)}),
     registry_network_rules: .azurerm_container_registry.block.attributes.network_rule_set.type}'
```

Initialization, formatting, validation, and schema inspection pass for this
scaffold. They do not establish environment parity, backend safety, or live
deployment/idempotence acceptance.

## Decision required

Implementation is blocked on approval to revise resource ownership or on an
upstream AzureRM release that exposes the missing settings. The proposed
exception is AzAPI ownership of the Automatic cluster and registry, leaving
other supported resources with AzureRM. One owner per resource avoids adding
competing updates; it does not waive repeat-deployment verification.

This exception is not approved or implemented. The existing registry-only
preview API approval does not authorize broader AzAPI ownership. No Azure
deployment or deletion has been performed or authorized for #8.