# Terraform foundation

Implementation for [work item #8](https://github.com/toddysm/aiks/issues/8), pending
live acceptance. The environment root declares the foundation; the separate
bootstrap root and `aiks state` commands manage its backend. Offline tests use
mocked providers/processes. No live deployment or repeat-deployment evidence has
been collected, and this implementation must not be treated as production-validated.

## Operator prerequisites

Use Python 3.12+, Terraform 1.15.8, and an authenticated Azure CLI user session.
State mutation commands require macOS or Linux for local POSIX file locking.
The current session supplies the subscription, tenant, and signed-in operator.
Service-principal bootstrap is not supported by the signed-in-user discovery.
Register the required Azure resource providers and grant the operator resource
creation permissions plus scoped role-assignment permission before proceeding.
No automatic provider registration or account-key fallback is performed.

Replace documentation-only names, addresses, and identity values in the selected
environment YAML. Backend access uses `terraform.allowedIpRanges` and
`terraform.allowedSubnetIds`; if the former is empty, `network.paasAllowedIpRanges`
is used. At least one backend rule is required. Existing allowed subnets must have
the Microsoft.Storage service endpoint enabled. The private production operator
must also have connectivity and DNS resolution for the cluster, registry, and vault.
This bootstrap creates a restricted public storage endpoint, not private operator connectivity.

## State commands

These commands perform real Azure operations. Bootstrap asks for confirmation
after showing the subscription and backend resource group. Status is read-only.

```sh
aiks state bootstrap --config path/to/dev.yaml --json-output bootstrap-result.json
aiks state status --config path/to/dev.yaml --json-output backend-status.json
aiks state destroy --config path/to/dev.yaml --json-output cleanup-result.json
```

Destroy requires the typed environment and exact state resource-group name.
Production additionally requires `--allow-production-destroy`. An active lease,
any non-bootstrap blob, an existing environment, unrecognized resource, extra
container, or unverified ownership blocks cleanup. The operator must stop all
concurrent deployments before cleanup; an acquired bootstrap-blob lease does not
lock the entire storage account against other clients creating new state keys.

Bootstrap first applies the dedicated backend root using local state, then migrates
that state to `bootstrap.tfstate`. Main environments use
`<prefix>-<environment>/environment.tfstate`. The environment root never owns the
backend. After destroying an environment, inspect and intentionally retire its empty
remote state key before `aiks state destroy`; this command never deletes those keys
for you or assumes an empty-looking state belongs to this operation.

Repeated bootstrap updates permit the configured environment's exact state key
alongside `bootstrap.tfstate`. Unknown keys and active leases still block updates;
environment state without bootstrap state requires explicit recovery. This does not
relax the bootstrap-only key inventory required for deletion.

## Native Terraform environment

The command-line environment lifecycle (`aiks infra deploy/plan/destroy`) remains
owned by #11. For independently operated Terraform, first generate validated inputs
and backend configuration, using your actual configuration path:

```python
from pathlib import Path
from aiks.config import load_environment_config
from aiks.engines.terraform import backend, variables, write_json

config = load_environment_config(Path("path/to/dev.yaml"))
work = Path(".aiks/native")
work.mkdir(parents=True, exist_ok=True, mode=0o700)
write_json(work / "environment.tfvars.json", variables(config))
write_json(work / "backend.json", backend(config))
```

Set `ARM_SUBSCRIPTION_ID` from the intended current Azure session. Run from the
repository root after bootstrap and review the plan before applying:

```sh
terraform -chdir=infrastructure/aks-automatic/terraform/environment init -backend-config="$PWD/.aiks/native/backend.json"
terraform -chdir=infrastructure/aks-automatic/terraform/environment plan -var-file="$PWD/.aiks/native/environment.tfvars.json" -out="$PWD/.aiks/native/environment.tfplan"
terraform -chdir=infrastructure/aks-automatic/terraform/environment apply "$PWD/.aiks/native/environment.tfplan"
terraform -chdir=infrastructure/aks-automatic/terraform/environment output -json result
```

Native Terraform bypasses `aiks` confirmation guards. Use its explicit destroy-plan
workflow only after independently checking subscription, environment, and backend
scope. Never run Terraform destroy on the bootstrap root: use guarded state cleanup.

## Recovery and security

`.aiks/state/<backend-hash>` is ignored by git and restricted to the local user.
It contains state, inputs, saved plans, recovery copies, and cleanup receipts.
Recovery files are mode `0600`; state, plans, and provider caches are never packaged.
Treat all state as sensitive: native provider-computed fields can include credentials
even when shared-key authentication is disabled. No state contents are logged.

A mismatch between local and remote bootstrap state stops migration. Preserve both
copies, compare lineage/serial/resource identities privately, and resolve the correct
source before retrying. An interrupted first bootstrap can resume from its retained
local state; if the remote backend declaration exists but the remote state is absent,
the command stops for explicit recovery rather than overwriting data. An active remote
lease must be investigated, not automatically broken. On interrupted cleanup, inspect
the group and recovery copy before manually releasing an orphaned bootstrap lease.

Cleanup acquires the bootstrap lease, downloads and validates the state, deletes the
state group outside Terraform, verifies absence, and writes `cleanup-receipt.json`.
`--delete-recovery-copy` removes the newly created copy only after successful deletion;
older copies and local state remain your responsibility. File removal is not guaranteed
secure erasure on solid-state or backed-up storage. Environment Key Vault soft deletion,
and production purge protection, are expected residuals distinct from backend deletion.

Recognized read-side role-propagation failures retry at most four attempts, with
one-, two-, and four-second delays. Other errors stop with a redacted result.
Verify identity, scoped role, and network path before retrying; mutations are not
automatically retried.

## Resource ownership and limits

AzAPI solely owns the cluster, registry, and marker-key child. AzureRM owns the network,
identities, role assignments, vault, endpoints, monitoring, alerts, and state storage.
System-subnet delegation is the only ignored lifecycle field; all other declared network
properties remain Terraform-managed. Custom cluster network changes trigger replacement.
Terraform uses Grafana 12 because AzureRM 5.x accepts 12/13; Bicep currently pins 11.
The existing portable dashboard asset is shared, but import, metrics, and version
compatibility require live verification. Alert definitions and access profiles mirror
Bicep semantically; native-provider service API versions may differ from Bicep's pins.

The operator's environment configuration is validated in Python before mapping. Direct
Terraform callers must use these validated inputs; Terraform does not duplicate every
Python topology validation. The normalized `result` contains only the shared nonsecret
output schema. AzAPI response exports are explicitly selected, never wildcard responses.

## Provider compatibility checkpoint

Verified on 2026-09-23 with Terraform 1.15.8, AzureRM 5.6.0, and AzAPI 2.12.0.
The lock file includes checksums for `darwin_arm64` and `linux_amd64`.

The original design required the native `azurerm_kubernetes_automatic_cluster`
and limited AzAPI to missing Gateway/monitoring profiles, the marker-key child,
and identity reads. The installed provider cannot represent the full contract:

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

### Pinned provider review checks

The review checks use the installed, locked versions rather than older provider
behavior. AzureRM 5.6.0's
[container implementation](https://github.com/hashicorp/terraform-provider-azurerm/blob/v5.6.0/internal/services/storage/storage_container_resource.go)
creates containers through `Storage.ResourceManager.BlobContainers` and records
`commonids.NewStorageContainerID(...).ID()` in state. The container is therefore
created through the management plane before granting the operator its container-scoped
data role. Its `id` is the management resource ID; `url` is the separate data endpoint,
and the installed schema has no `resource_manager_id` attribute.

AzAPI 2.12.0's installed `azapi_resource` schema declares both
`response_export_values` and `replace_triggers_external_values` as dynamic.
Mapped response exports are documented as alias-to-JMESPath queries, and replacement
triggers can contain objects. The existing selected exports and subnet-object trigger
are intentional. Provider-schema tests, native validation, and profile assertions run
in continuous integration to detect changes in these contracts. These offline checks
are not evidence of live resource deployment.

Run from the repository root. These commands download signed provider binaries
but do not authenticate to Azure, initialize remote state, or create resources.

```sh
terraform -chdir=infrastructure/aks-automatic/terraform/environment init -backend=false -input=false -lockfile=readonly
terraform -chdir=infrastructure/aks-automatic/terraform/environment fmt -check
terraform -chdir=infrastructure/aks-automatic/terraform/environment validate
terraform -chdir=infrastructure/aks-automatic/terraform/environment test
terraform -chdir=infrastructure/aks-automatic/terraform/bootstrap init -backend=false -input=false -lockfile=readonly
terraform -chdir=infrastructure/aks-automatic/terraform/bootstrap test
terraform -chdir=infrastructure/aks-automatic/terraform/bootstrap providers schema -json |
  jq '.provider_schemas["registry.terraform.io/hashicorp/azurerm"].resource_schemas |
    {automatic_cluster: (.azurerm_kubernetes_automatic_cluster.block |
      {attributes: (.attributes | keys), blocks: (.block_types | keys)}),
     registry_network_rules: .azurerm_container_registry.block.attributes.network_rule_set.type}'
```

Also run module profile tests, TFLint on each root/module, Trivy configuration
scanning, and the Python test/lint/type suites. These tests do not establish
live deployment/idempotence acceptance. The full foundation remains incomplete
until operator-run evidence satisfies #8 and the lifecycle/evidence work in #11/#12.

## Approved ownership exception

On 2026-09-23 the user approved AzAPI ownership of the Automatic cluster and registry, leaving
other supported resources with AzureRM. One owner per resource avoids adding
competing updates; it does not waive repeat-deployment verification.

The [updated design](../../../docs/architecture/infrastructure/aks-automatic-foundation.md)
pins cluster API `2026-04-01` and registry API `2026-03-01-preview` (the existing
registry-only preview exception). Implementation is in progress. No Azure
deployment or deletion has been performed or authorized for #8.