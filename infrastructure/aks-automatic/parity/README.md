# Foundation Parity and Static Validation

This gate checks the Bicep and Terraform implementations of the
[accepted foundation design](../../../docs/architecture/infrastructure/aks-automatic-foundation.md).
It does not authenticate to Azure, provision resources, or replace live acceptance
under [#11](https://github.com/toddysm/aiks/issues/11) and
[#12](https://github.com/toddysm/aiks/issues/12).

## Contract

[contract.json](contract.json) declares shared semantic inputs, operational-only
settings, normalized output groups, Azure resource inventory, role identifiers,
resource properties, and binding assertions. Terraform backend resources are
excluded because Bicep does not need a state backend. Local Kubernetes and workload
settings belong to the shared readiness workflow, not either infrastructure engine.

The gate combines these independent checks:

- Both adapters must expose exactly the declared inputs and preserve their values.
- The Bicep compiler's complete policy-bearing output must match
  [bicep.snapshot.json](bicep.snapshot.json). Only compiler metadata is removed;
  expressions, conditions, outputs, scopes, and module wiring remain checked.
- Provider-mocked Terraform plans evaluate the real environment and cluster module.
  Only computed resource identifiers and service responses are mocked. No whole
  module is replaced. The fixture uses distinct identity, subnet, workspace, and
  collection-rule identifiers to detect miswired bindings.
- Development and production example inventories must match exact resource counts.
  Terraform inline private DNS zone groups are counted as Azure child resources.
  Unknown resource types, conditions, or repetition expressions fail closed.
- Cluster and registry properties, network inputs, restricted development access,
  private production access, identities, role definitions and scopes, monitoring
  bindings, diagnostics, alert expressions, and readiness outputs are checked.
- Terraform outputs validate against the shared Python output model, whose full
  schema is pinned in [outputs.schema.json](outputs.schema.json).
- Negative fixtures remove/add resources, alter roles and principals, expose
  endpoints, broaden allowlists, break monitoring, and change output fields.

The condition reader recognizes only the existing compiled condition vocabulary;
it is not a general Azure Resource Manager (ARM) evaluator. The snapshot prevents
silent changes to the expressions and variables behind those conditions. The
inventory expectations describe the two checked-in examples, not every optional
configuration combination. Isolated cluster tests additionally cover custom
networks and disabled monitoring. Live ARM defaults, service-created resources,
permissions, data ingestion, and repeated-deployment convergence require #11.

## Local Checks

Install the package with development and readiness extras, then use Terraform
1.15.8 and Bicep 0.46.1. All commands below run from the repository root.

```bash
python -m pip install -e '.[dev,readiness]'
for root in environment bootstrap modules/cluster; do
  terraform -chdir="infrastructure/aks-automatic/terraform/$root" init \
    -backend=false -input=false -lockfile=readonly
done
AIKS_REQUIRE_PARITY=1 AIKS_REQUIRE_BICEP=1 AIKS_REQUIRE_TERRAFORM=1 \
  python -m pytest
ruff check src tests scripts/check_repository.py
ruff format --check src tests scripts/check_repository.py
mypy src
bandit -q -r src
pip-audit
python scripts/check_repository.py
actionlint
npx --yes markdownlint-cli2@0.20.0
git ls-files -z '*.md' | xargs -0 lychee --offline --include-fragments --no-progress
gitleaks git --redact --no-banner --log-opts="--all"
```

Use actionlint 1.7.12, lychee 0.24.2, and Gitleaks 8.30.1. Python quality-tool
versions are pinned in [pyproject.toml](../../../pyproject.toml). The dedicated
workflows also pin Terraform, Bicep, TFLint, Trivy, Helm, kind, and kubeconform;
downloaded standalone binaries are checksum-verified. GitHub Actions use explicit
supported release majors. Provider lock files are read-only on initialization,
and provider/schema artifacts are never restored from an unkeyed cache.

The local parity suite takes about 7 seconds with initialized tools on the tested
development machine. Download time and runner speed dominate cold runs.

## Automation Matrix

| Check | Workflow | Typical cold-run budget |
| --- | --- | --- |
| Python tests, coverage, typing, lint, dependency audit, schema drift, Bicep policy | [Python quality](../../../.github/workflows/python-tests.yml) | 3-8 minutes |
| Terraform format, validation, lint, locked schemas, mock tests, cross-engine parity, infrastructure security | [Terraform quality](../../../.github/workflows/terraform-tests.yml) | 3-8 minutes |
| Rendered Helm schemas, real isolated kind lifecycle, readiness image vulnerability and secret scan | [Readiness quality](../../../.github/workflows/readiness-tests.yml) | 5-20 minutes |
| Markdown, local links/fragments, duplicate structured keys, workflow semantics/policy, secret history and scanner negative fixture | [Repository quality](../../../.github/workflows/repository-checks.yml) | 2-6 minutes |
| Python/workflow security analysis and structured syntax | [CodeQL](../../../.github/workflows/codeql.yml) | 2-8 minutes |

Budgets are estimates, not performance guarantees. Networked package and security
database downloads are allowed; Azure credentials and deployments are not.
Repository links and fragments are checked offline for deterministic pull-request
results. External web URLs are not availability-checked by that gate.

All jobs must pass before this work item's pull request is merged, including
the added repository gate. No branch-protection bypass or settings change is
part of this implementation. The review loop checks every reported check, not
only the subset configured as required by GitHub.

The structured-data checker skips only raw Helm templates, which are validated
after rendering by the readiness workflow. It rejects duplicate keys and checks
declared JSON Schemas. Workflow checks reject privileged pull-request triggers,
unexpected write permissions, unpinned actions, and explicit cloud deployment
commands. These checks are regression guards, not a sandbox against arbitrary
code execution by a malicious workflow change; code review remains required.

## Updating the Contract

Do not refresh a snapshot simply to silence a failure. Review the source change,
verify its semantic effect in both engines, update the corresponding contract
assertions and negative tests, and obtain approval for any design exception.
Then regenerate the compiler artifact with the pinned compiler:

```bash
set -o pipefail
bicep build infrastructure/aks-automatic/bicep/main.bicep --stdout \
  | jq 'walk(if type == "object" then del(.metadata) else . end)' \
  > infrastructure/aks-automatic/parity/bicep.snapshot.json
```

Review the generated diff. Changing a provider/tool version also requires locked
initialization, provider-schema tests, the full parity suite, and the existing
security checks. Do not regenerate locks with `-upgrade` in automation.

## Naming Compatibility

Terraform now uses AzAPI's ARM-compatible `unique_string` with Bicep's seed order
for environment resource names. Cluster identity, private endpoint/link,
delegation, and connection names are aligned. Both engines use Grafana major 12;
registry, vault, and Grafana names may differ because they need global uniqueness.

**Earlier Terraform deployments can require resource replacement after this naming
correction.** This is not a state migration. Inspect the plan before any apply;
use the original deployed revision for cleanup or plan an explicitly reviewed
migration. The backend name and state keys are unchanged. Backend cleanup now
checks all matching environment-group name prefixes, including old and new hashes,
both before and after acquiring the bootstrap lease. It requires group-list
visibility and refuses unverifiable inventories or any remaining matching group.

## Troubleshooting

- Missing compiler/provider: initialize with the checked-in locks and use the
  `AIKS_REQUIRE_*` flags above. Automation fails instead of silently skipping.
- Inventory mismatch: inspect the resource type and extra/missing counts, then
  check conditional monitoring, private endpoints, and inline child resources.
- Binding mismatch: compare the declared source and target, not generated mock
  identifiers. Never substitute unknown values with empty strings to pass.
- Snapshot mismatch: check compiler version first, then inspect source expressions.
- Schema drift: regenerate the configuration/output schema only after reviewing
  the model change and both engine output contracts.
- Security finding: update the affected component or fix the policy. Do not add
  broad suppressions. The existing backend-specific Trivy exception remains scoped
  to its documented deny-default network policy.

Gitleaks retains all default rules. Its sole added exception matches the public
Key Vault Reader role identifier exactly, only in the parity contract and only
for the generic-key rule. The scanner test requires both generic-key and GitHub
token findings; neither rule nor the contract file is excluded from scanning.
