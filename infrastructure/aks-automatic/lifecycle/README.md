# Azure Foundation Lifecycle

The `aiks infra` commands connect the existing Bicep/Terraform foundations to
preflight, preview, readiness installation, observed-resource verification, and
protected cleanup. This implementation is under
[#11](https://github.com/toddysm/aiks/issues/11). **Live acceptance is required
before that issue or its pull request can complete.** Mocked tests and static
checks are not deployment evidence.

## Prerequisites

- Install the package with `python -m pip install -e '.[dev,readiness]'` in the
  repository checkout, or install a built distribution for runtime-only use.
- Install Azure CLI 2.86.0+, kubectl 1.35+, kubelogin 0.2+, Helm 3.17+ or 4.x,
  and either Bicep 0.46.1+ or Terraform 1.15.8+ within their current major version.
  Exact supported tool policy and the reviewed Automatic region catalog are in
  [platform.json](../config/platform.json).
- Sign in interactively and select the intended subscription yourself before
  running cloud commands. Never place static credentials in configuration.
- Have permission to create subscription-level deployments/resource groups,
  foundation resources, and role assignments. Be able to use the configured
  platform administrator group for Kubernetes access, push to the environment
  registry, and query monitoring data.
- Register the providers named in the platform policy. Preflight reports missing
  registrations but does not register them or request quota on your behalf.
- For Terraform, complete the separate
  [backend bootstrap workflow](../terraform/README.md) first. Environment commands
  do not bootstrap or delete the backend implicitly.
- Build the readiness image with `aiks workload build --config <config>` before
  deployment, or configure an existing immutable image digest in the environment
  registry. Local builds require Docker and the source checkout. Deployment can
  authenticate to the registry and push the configured local image.

Use an ignored operator configuration copied from the dev or production example.
The checked-in examples contain illustrative identifiers and development IP
allowlists, not valid operator credentials or network permissions.

## Private Connectivity

Before creating a private cluster, the operator must already have a connected
network and functioning private name resolution. Configure representative existing
private hosts with a reachable TCP port 443:

```yaml
spec:
  lifecycle:
    privateProbeHosts:
      - existing-private-probe.example.internal
    deploymentTimeoutSeconds: 1800
    verificationTimeoutSeconds: 300
    connectionTimeoutSeconds: 10
    pollIntervalSeconds: 10
    alertTimeoutSeconds: 900
    minimumAvailableCores: 4
```

Merge this fragment into the full configuration, not a separate minimal file.
Probe hosts must be plain names, without URL credentials, paths, or query strings.
The preflight canary proves an existing private path; it cannot prove the DNS
record of a cluster that does not exist yet. Post-deployment verification checks
the real API, registry, and vault endpoints and their private address bindings.
The tool never provisions the operator's private connectivity.

## Commands

```bash
aiks infra validate --config .aiks/dev.yaml --engine bicep
aiks infra preflight --config .aiks/dev.yaml --engine bicep
aiks infra plan --config .aiks/dev.yaml --engine bicep --json-output .aiks/plan-result.json
aiks infra deploy --config .aiks/dev.yaml --engine bicep --json-output .aiks/deploy-result.json
aiks infra verify --config .aiks/dev.yaml --json-output .aiks/verify-result.json
aiks infra destroy --config .aiks/dev.yaml --engine bicep --json-output .aiks/destroy-result.json
```

Use `--engine terraform` for the Terraform path. Verification can discover the
engine from the owned resource group's tags, or accept an explicit `--engine`.
`validate` retains its configuration-only, cloud-free behavior. `preflight` and
`plan` contact Azure but do not create environment resources. Planning initializes
the existing Terraform backend and reads state when that engine is selected.

Deployment requires typing the environment name. Production also requires
`--allow-production-deploy`. Destruction requires typing the environment and,
for production, `--allow-production-destroy`. Alert exercises have their own
interruption confirmation and production override.

The active subscription is captured once and pinned to subsequent Azure commands.
The checked Bicep executable compiles the template explicitly; Azure CLI receives
that compiled artifact rather than choosing its own compiler implicitly.
The runtime does not accept injected ARM credentials or Terraform command flags.
Always inspect the active account and the preview before authorizing a mutation.
Preflight quota and region checks are availability signals, not a capacity
reservation or a guarantee that an allocation will succeed.

## Ownership and Artifacts

Each environment has a private, locked workspace under `.aiks/infra/<hash>`.
The synchronous command pins that directory through verified no-follow directory
descriptors and temporarily uses it as the process working directory. The caller's
directory is restored on success or failure. Do not run lifecycle methods in
parallel threads within one Python process; use separate CLI processes instead.
Relative tool/configuration paths are captured before entering the workspace.
The group carries `aiks-owner`, `aiks-engine`, and a random `aiks-instance` tag.
Existing untagged environments and cross-engine adoption are refused. Old
standalone engine deployments need a separately reviewed adoption/migration;
changing configuration is not authorization to take ownership.

Deployment writes a private intent before applying and an ownership receipt after
the created group and outputs are verified. Normal cleanup requires the receipt
and matching live instance. A replaced group is refused even if its name matches.
Environment commands never target the backend resource group.

Raw previews, plans, outputs, state metadata, and the explicit kubeconfig stay in
the ignored private workspace. **Do not commit these files.** Only summarized
operation results and sanitized type-count inventories are suitable starting
points for acceptance evidence; audit them before sharing. The default kubeconfig
is not modified, and local cluster-admin credentials are not requested.

Normal deployment refuses Terraform replacement/deletion actions and unverifiable
or destructive Bicep preview changes, including nested removals/array replacements.
Bicep scalar modifications are conservatively limited to tags and log retention;
other property modifications require separate review. Review any intended migration separately.
Repeat `plan` after a successful deployment to record whether `noOp` is true, then
repeat deployment and verification. Do not label unexplained differences clean.

## Verification

Verification inspects the Automatic cluster profile, configured address ranges,
identity and federation, exact resource-scoped role assignments, registry/vault
restrictions, collection rules and associations, diagnostics, Grafana linkage,
fresh Container Insights records, and readiness samples in managed Prometheus.
It also verifies readiness through the shared Helm workflow, private endpoint
approval/address bindings, the production Gateway's Azure frontend, and a bounded
Resource Graph inventory. Telemetry-only propagation is retried up to the
configured bound; security drift fails immediately.

Unexpected core resource counts, monitoring duplicates, unknown resource types,
incomplete graph results, foreign output references, and incomplete role evidence
fail validation. These are conservative checks: a service API shape change must
be investigated, not bypassed. Inherited subscription access is outside the exact
environment role inventory and still needs operator security review.

## Alert Exercise

The production frontend check rejects public inbound or unclassified frontends
even when the Gateway itself has a valid private address. Explicit outbound-only
frontends may remain for cluster egress; they do not expose the ingress service.

```bash
aiks infra exercise-alerts --config .aiks/production.yaml --engine terraform \
  --allow-production-change --json-output .aiks/alert-result.json
```

This deliberately interrupts only the readiness deployment. It records the replica
count, scales to zero, waits for a fresh `ReadinessUnavailable` alert, restores
replicas in `finally`, verifies readiness, and waits for resolution. The result
does not claim notification delivery: the configured action-group receiver must
confirm the notification and correlate it with this exercise. Record that
confirmation separately in the sanitized acceptance report.

## Cleanup and Recovery

Normal cleanup removes the readiness release before infrastructure, then checks
resource-group deletion. Terraform applies an inspected destroy plan and removes
only its verified-empty environment state key under a blob lease. The separate
backend, its bootstrap state, and storage retention remain intact.

Soft-deleted vaults are expected retained artifacts, especially with production
purge protection. Cleanup reports them without attempting purge. Backend blob
versions and soft-deleted state are also retained according to backend policy.
Use `aiks state destroy` only after independent backend cleanup prerequisites pass.

For a failed partial deployment lacking complete outputs:

```bash
aiks infra destroy --config .aiks/dev.yaml --engine bicep --allow-partial-cleanup
```

Partial cleanup requires a matching pre-apply intent and instance tag. It refuses
any listed resource whose ownership tags cannot be verified, including untagged
service-generated resources. That refusal requires operator inspection; it is not
permission to bypass ownership checks. The whole owned group is removed, so any
readiness resources in a partial cluster disappear with the cluster. Review
soft-deleted vaults and retained backend versions afterward.

## Acceptance Matrix

The connected operator must run sequential isolated environments for both engines
and both dev/private-production postures, with separate authorization and cost
boundaries. Required evidence includes initial/repeated preview and deployment,
workload install/upgrade/rollback/uninstall, identity/image pull, private routing,
monitoring ingestion/dashboards, alert fire/notification/resolution, timings,
ownership refusals, partial recovery, deletion and retained-artifact reports.

Terraform additionally requires backend bootstrap/migration, updates with deployed
environment state, permission propagation, interruption/recovery, lease refusal,
and protected backend cleanup. Those deferred #7/#8 requirements remain mandatory.
[#12](https://github.com/toddysm/aiks/issues/12) owns the audited final reports.
No live acceptance result is implied by this guide or by passing mocked tests.
