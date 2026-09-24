# Readiness workload

The Python service and [shared Helm chart](../charts/readiness/Chart.yaml) validate
application scheduling, routing, health probes, workload identity, and metrics.
Local kind lifecycle validation is automated. Live AKS behavior, image pushes,
identity access, and managed monitoring must still be validated under
[#11](https://github.com/toddysm/aiks/issues/11), with final evidence in #12.

## Prerequisites

Use Python 3.12+, Docker, kind 0.33.0, Helm 3 or 4 (tested with 4.2.4), and kubectl.
Docker must be running. Install the source checkout for image building:

```sh
python -m pip install -e '.[dev,readiness]'
```

The container uses a digest-pinned Python 3.12 Alpine image, user/group 10001,
read-only root filesystem, dropped capabilities, and a runtime-default seccomp
profile. It needs no embedded credentials. A mounted ConfigMap supplies service
settings; only the projected Azure workload token is used for AKS identity checks.
The build context excludes local state, kubeconfig, provider caches, and local
environment files. Image building requires the source checkout; installed wheels
include the chart and can deploy an existing image.

## Configuration

Start from an environment example and use a distinct `local.kindClusterName`.
Configuration files and generated results should live under ignored `.aiks/`.
Existing environment files remain valid. Optional settings are:

```yaml
local:
  kindClusterName: aiks-readiness
  nodeImage: kindest/node:v1.35.8@sha256:07b2536e30b803ed61d1677a79df6115f798ce64c80f9e22f6ed45afd09323c0
  gatewayChartVersion: v1.9.1
workload:
  image: aiks-readiness:local
  replicas: 1
  ready: true
  timeoutSeconds: 300
  packageIndexUrl: https://pypi.org/simple
```

`packageIndexUrl` is a nonsecret HTTPS build dependency source. Credentials,
queries, and fragments are rejected; private-index credentials are not supported.
The default image can be built with `workload build`, or replaced with an existing
local image. On AKS, an image in the configured registry referenced by
`@sha256:<digest>` skips publishing; a local image is tagged, pushed, and resolved
to an immutable registry digest. No mutable tag is used by the AKS Deployment.

## Local lifecycle

```sh
aiks workload build --config .aiks/dev.yaml --target kind
aiks local create --config .aiks/dev.yaml
aiks workload install --config .aiks/dev.yaml --target kind
aiks workload verify --config .aiks/dev.yaml --target kind --json-output .aiks/verification.json
aiks workload upgrade --config .aiks/dev.yaml --target kind
aiks workload rollback --config .aiks/dev.yaml --target kind
aiks workload uninstall --config .aiks/dev.yaml --target kind
aiks local delete --config .aiks/dev.yaml
```

Change `workload.replicas` or other nonsecret settings before upgrade. Rollback
defaults to the previous Helm revision; use `--revision` to select one explicitly.
Uninstall requires typing `aiks-readiness`; deletion requires the exact kind cluster
name. Local create refuses to adopt an unowned cluster. An ownership receipt stores
the configuration identity and cluster namespace UID; subsequent operations reject
a replaced cluster. The private kubeconfig and receipt are below
`.aiks/local/<cluster-name>` and never overwrite the operator's default kubeconfig.

Envoy Gateway is installed from its pinned upstream Helm chart, including its
Gateway definitions. The readiness chart creates a local GatewayClass and
ClusterIP-backed proxy. Local verification makes requests from a workload pod
through that proxy, not directly to the application Service; this also works on
Docker Desktop where cluster addresses are not host-routable. `local delete` removes
the whole owned test cluster and its controller, while uninstall removes the
readiness release and its dedicated workload namespace.

The Helm release record lives in `aiks-system`; workload resources live in
`aiks-readiness`. Do not put unrelated workloads in that owned namespace.

## Existing AKS target

Use an already provisioned cluster and the normalized `result` output from either
infrastructure engine. Obtain an Entra-backed kubeconfig through your authorized
operator workflow; #11 will integrate that step. Every AKS command requires explicit
files/context and checks the kubeconfig server against the foundation cluster name:

```sh
aiks workload install --config .aiks/dev.yaml --target aks \
  --kubeconfig .aiks/aks-kubeconfig --context my-aks --outputs .aiks/foundation.json
aiks workload verify --config .aiks/dev.yaml --target aks \
  --kubeconfig .aiks/aks-kubeconfig --context my-aks --outputs .aiks/foundation.json
```

Upgrade, rollback, and uninstall accept the same target options. Mutations prompt
for the environment; production additionally requires `--allow-production-change`.
Uninstall also requires the workload namespace. Installing/upgrading from a local
image performs a real registry push, so review the displayed target before confirming.
The operator needs the existing cluster-admin and registry push permissions. No
subscription selection, cluster provisioning, role grants, or Azure resource deletion
is performed by these commands.

AKS uses `approuting-istio` without enabling a service mesh. Production uses the
Gateway infrastructure annotation for an internal Azure LoadBalancer. Verification
requires current Accepted/Programmed/ResolvedRefs conditions, an assigned address,
HTTP success through the route, and identity/metrics success. Production also checks
the generated Service's internal annotation and private frontend addresses. Azure
control-plane verification of frontend bindings remains part of #11. The operator
must have the required private network and DNS access before attempting verification.

## Service and metrics

| Endpoint | Behavior |
| --- | --- |
| `/livez` | Process liveness; independent of external services. |
| `/readyz` | 200 when configured ready, 503 otherwise. |
| `/identityz` | Explicit `skipped` on kind; reads only marker-key metadata with projected workload identity on AKS. |
| `/metrics` | Prometheus readiness, failure, request-count, and latency series. |

Metrics match the existing foundation dashboard: `aiks_readiness_info`,
`aiks_readiness_duration_seconds`, `aiks_readiness_failures_total`,
`aiks_http_requests_total`, and `aiks_http_request_duration_seconds`.
Labels include environment, cluster, namespace, workload, and target. HTTP paths
are restricted to known endpoint labels plus `other` to bound cardinality.
Azure errors return a generic failure, never exception details or key material.
No distributed tracing or product usage telemetry is added.

The Azure `ServiceMonitor` is conditional and never rendered for kind. The service
account name, namespace, client ID, and projected-token label match the foundation's
federated identity contract. Managed Prometheus scraping and actual Key Vault access
still require the operator-run AKS validation.

## Validation and recovery

```sh
python -m pytest
helm lint infrastructure/aks-automatic/charts/readiness
python scripts/check_readiness_chart.py
python scripts/test_readiness_lifecycle.py
```

The chart checker needs kubeconform 0.8.0 and HTTPS access to pinned upstream custom
schemas. It validates all standard and custom objects without missing-schema skips.
The lifecycle script creates a unique `aiks-test-9-*` cluster, checks replica rollback,
and deletes only its own cluster even on failure. `AIKS_TEST_PACKAGE_INDEX` can select
an approved, credential-free HTTPS package mirror for that test build.

For interrupted local creation, inspect only the configured kind cluster and its
receipt. A cluster without a valid receipt is not adopted or automatically deleted;
confirm ownership before manual cleanup. Helm upgrades use atomic rollback semantics.
On timeouts, inspect pod events, Gateway conditions, and image availability in the
explicit context. Identity failures on AKS require checking federation, token projection,
vault permissions, and private connectivity; no credential fallback is attempted.