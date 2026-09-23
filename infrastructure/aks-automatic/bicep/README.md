# AKS Automatic Bicep Foundation

This subscription-scoped implementation consumes the shared validated environment
configuration. It creates Azure Kubernetes Service (AKS) Automatic and its supporting
resources. It does not install the readiness application or implement the unfinished
`aiks infra deploy` lifecycle. Those remain issues #9 and #11 respectively.

## Status and Evidence

Offline compilation, parameter mapping, command construction, resource inventory,
role assignments, security assertions, output shape, and dashboard structure are
covered by automated tests. No Azure deployment has been executed for this work.
Runtime idempotence, resource-provider acceptance, private connectivity, identity
propagation, collector ingestion, dashboard queries, and alert delivery remain
unverified. A successful Bicep build does not establish any of those properties.
On 2026-09-23, the user approved moving issue #7's live deployment and idempotence
evidence to #11 (execution) and #12 (final acceptance evidence). Issue #7 may merge
once its static checks and reviews pass. The full foundation still requires live
dev and private production-shaped validation with both engines before completion.
This deferral does not authorize Azure deployment or deletion.

## Prerequisites

- Python 3.12 or newer and the project installed with `pip install -e '.[dev]'`.
- Bicep 0.46.1, the version pinned in continuous integration (CI). The create-only
  resource decorator requires at least 0.38.3.
- Azure CLI and an explicitly selected subscription for native Azure operations.
- Subscription permission to create the environment resource group and its resources,
  plus permission to create role assignments at their resource scopes. Contributor
  alone cannot create role assignments. Use appropriately scoped Role Based Access
  Control Administrator permissions rather than granting workloads Owner.
- A real Microsoft Entra administrator group object ID and operator IPv4 allowlists.
  The example values are documentation placeholders, not deployable credentials.
- Register Microsoft.ContainerService, Microsoft.Network, Microsoft.ManagedIdentity,
  Microsoft.ContainerRegistry, Microsoft.KeyVault, Microsoft.OperationalInsights,
  Microsoft.Insights, Microsoft.Monitor, Microsoft.Dashboard, and
  Microsoft.AlertsManagement. Verify Automatic regional availability, quota, and
  subscription feature requirements before deployment.
- Production validation requires an operator path to the private API, registry, and
  vault endpoints, with private DNS forwarding/resolution configured for that path.

## Inputs and Independent Use

Seven parameters match the YAML contract: `environment`, `location`, `prefix`,
`adminGroupObjectId`, `network`, `observability`, and `tags`. Terraform backend and
local Kubernetes settings are intentionally excluded. Generate parameters from
validated YAML; the nested Bicep object parameters are not a replacement for the
configuration validator.

```python
from pathlib import Path
from aiks.config import load_environment_config
from aiks.engines.bicep import write_parameters

config = load_environment_config(Path("infrastructure/aks-automatic/config/dev.example.yaml"))
write_parameters(config, Path("dev.parameters.json"))
```

Review and replace the example group and allowlist values before using Azure.
The checked-in template is independent of Python once the parameter file exists:

```sh
bicep build infrastructure/aks-automatic/bicep/main.bicep --outfile /tmp/aiks.json
az deployment sub what-if --subscription "$SUBSCRIPTION_ID" --location westus3 \
  --name aiks-dev \
  --template-file infrastructure/aks-automatic/bicep/main.bicep \
  --parameters @dev.parameters.json
```

`what-if` calls Azure, but does not deploy resources. Review every create, modify,
delete, and unresolved expression. Do not treat noisy runtime references as approval
to proceed. An authorized operator can use the same arguments with `create` in
place of `what-if`; this repository's CI never does so. No deployment is authorized
by merely generating these files or invoking the adapter's command builders.

`deployment_command()` returns an argument tuple for the shared `run_command()`
boundary. It supports `validate`, `what-if`, and `create`, with an explicit
subscription. `template_path()` locates modules in a source checkout or installed
wheel; keep its context open for the entire native-tool invocation.

## Ownership and Versions

| Module | Responsibility | Pinned resource API versions |
| --- | --- | --- |
| main / naming / foundation | Subscription resource group, deterministic names, composition and outputs | Resources 2025-04-01 |
| identity / bindings | Cluster/readiness identities, federation, registry pull and vault metadata access | ManagedIdentity 2024-11-30; Authorization 2022-04-01 |
| network | Virtual network, subnets, API private DNS, network/DNS permissions | Network 2025-01-01; privateDnsZones 2024-06-01 |
| aks | Automatic profile, managed Gateway API, Entra access, upgrades and monitoring addons | ContainerService 2026-04-01 |
| registry | Premium registry, legacy role mode, dev subnet/IP rules | ContainerRegistry 2026-03-01-preview |
| vault | RBAC vault and server-generated, nonexportable readiness marker key | KeyVault 2024-11-01 |
| private-endpoint | Production registry/vault private endpoints, zones and links | Network 2025-01-01; privateDnsZones 2024-06-01 |
| monitoring / grafana-access | Custom workspaces, Grafana, collection rules and reader/admin roles | OperationalInsights 2023-09-01; Monitor 2023-04-03; Dashboard 2024-10-01; dataCollectionRules 2023-03-11 |
| monitoring-bindings | Associations, diagnostics, action groups and baseline alerts | Associations 2023-03-11; diagnostics 2016-09-01; actionGroups 2023-01-01; activityLogAlerts 2020-10-01; scheduledQueryRules 2023-12-01; prometheusRuleGroups 2023-03-01 |

The registry preview is the only authorized exception, approved 2026-09-23:
stable 2025-11-01 supports the role-assignment mode but not subnet rules. The
preview uses `virtualNetworkSubnetResourceId`, not the historical `id` field.
See the [official registry change log](https://learn.microsoft.com/en-us/azure/templates/microsoft.containerregistry/change-log/registries).
Read-only registry references use stable 2025-11-01. Legacy stable diagnostics
uses the fixed name `service` and `timeGrain`, rather than newer preview fields.

Resource-group names include a deterministic subscription/environment/prefix hash.
Registry and vault names use the same inputs with service-specific length constraints.
Change the prefix to deliberately create a separate environment; never share a
resource group between engines.

The virtual network and subnets use `@onlyIfNotExists()`. Existing resources are
left untouched, preserving AKS-managed delegations and policies on repeat runs.
This also means changing their CIDRs or service-endpoint configuration is an
explicit migration, not an in-place update performed by this template. Compare
actual network state with the configuration before every apply; reject mismatches.
The later lifecycle verifier must enforce this drift check. Network creation is
serialized, and AKS starts only after the network/DNS role-assignment module completes.
Completion ordering cannot eliminate Azure role-propagation delays.

Platform administrators receive AKS Cluster User, AKS RBAC Cluster Admin, and
Grafana Admin at the respective resources. The cluster identity receives Network
Contributor on its virtual network and Private DNS Zone Contributor on its API
zone. The generated kubelet receives AcrPull at the registry. Readiness receives
Key Vault Reader at the vault, with federation restricted to
`system:serviceaccount:aiks-readiness:readiness`. Grafana's identity receives
Monitoring Reader at the Azure Monitor workspace. No subscription-wide workload
role or vault secret-reading permission is granted.

## Outputs and Monitoring

The `result` deployment output follows `FoundationOutputs` in
[src/aiks/outputs.py](../../../src/aiks/outputs.py). Validate the value under
`properties.outputs.result.value` with that model before consuming it. Disabled
monitoring resources have empty strings. Credentials, kubeconfig, vault key material,
and storage keys are not outputs. The marker is created server-side and is not
rotated on every redeployment.

Import [dashboards/foundation.json](dashboards/foundation.json) in Grafana using
the platform administrator account. Select the managed Prometheus datasource and
cluster. Dashboard import is a data-plane operation, not an ARM resource in this
template; automated import belongs in the later lifecycle orchestration. Panels
show readiness, requests/use, node-count and pending-pod provisioning indicators,
readiness latency/failures, HTTP signals, and alert state. They have not been
rendered or validated against live samples.

The readiness chart must expose its ServiceMonitor and the `aiks_readiness_*` and
`aiks_http_*` series used by these panels. Issue #9 must reconcile the precise
metric names with this asset. The collector must include `kube_node_status_capacity`
and the other queried kube-state-metrics series; minimal ingestion defaults may
require an explicit keep-list. Validate every query and managed rule-group health.
Absence of application metrics before #9 is installed is not successful monitoring.

Verify custom collection-rule associations and ingestion in the configured
workspaces; reject unexpected default workspaces rather than accepting duplicate
ingestion. Collector publishing authorization is service-managed in the official
onboarding template; runtime validation must confirm effective access. Resource
Health severity is recorded in the description/webhook properties because Activity
Log alerts do not expose the native severity field used by metric/log rules.

## Timing and Cleanup

The architecture targets complete deployment and readiness within 30 minutes,
excluding documented capacity/private-network incidents. This implementation has
no measured deployment duration yet. Record native command duration and preserve
redacted failure evidence; do not retry a destructive command blindly.

`destroy_command()` only constructs deletion arguments. It requires the exact
environment confirmation, an explicit production override, and a resource-group
ID matching the subscription/prefix/environment naming contract. Before executing,
the lifecycle caller must verify the group against trusted deployment outputs and
Azure ownership tags and remove the readiness Helm release. The builder alone is
not a live ownership check. Native deletion bypasses these Python safeguards.

Production vault purge protection retains a soft-deleted vault for 90 days and
reserves its name. Report this residual and recovery window; never attempt to
bypass purge protection. Subscription deployment history and independently managed
Terraform state are not removed by environment resource-group deletion.

## Local Checks

```sh
AIKS_REQUIRE_BICEP=1 .venv/bin/python -m pytest tests/test_bicep.py --no-cov
.venv/bin/ruff check src tests
.venv/bin/mypy src
```

The compiled-template security assertions are the scoped policy gate for this
work item, not a substitute for the cross-engine scans and runtime evidence in
issues #10-#12. CI fails when Bicep is missing or compilation emits diagnostics.