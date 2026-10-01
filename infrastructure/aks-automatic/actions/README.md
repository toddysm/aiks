# Live Azure Acceptance in GitHub Actions

This is an opt-in, billable acceptance workflow, not a pull-request check.
Implementation is tracked by [#33](https://github.com/toddysm/aiks/issues/33).
It can test the unmerged lifecycle implementation in
[#24](https://github.com/toddysm/aiks/pull/24) without merging that pull request
or weakening its live-acceptance gate. Workflow code must first be reviewed and
merged to `main`; the deployment code is checked out at a separately approved
full commit SHA. No cloud run has been validated merely by merging this workflow.

## Authentication Without Client Secrets

Use a **user-assigned managed identity** with **OpenID Connect (OIDC) federation**.
This works without attaching the identity to the runner virtual machine and
without an application-registration client secret. A managed identity still has
a Microsoft Entra service-principal object internally; it does not require
managing a service-principal password or certificate.

Create the identity separately in the runner/operations resource group, outside
all disposable test groups. Configure these two federated identity credentials
on that identity, substituting the actual repository owner/name:

| Property | Test credential | Recovery credential |
| --- | --- | --- |
| Issuer | `https://token.actions.githubusercontent.com` | Same |
| Audience | `api://AzureADTokenExchange` | Same |
| Subject | `repo:OWNER/REPO:environment:azure-acceptance` | `repo:OWNER/REPO:environment:azure-acceptance-cleanup` |

Use exact subjects, not wildcard repository or branch trusts. The
`azure/login@v2` action exchanges the GitHub token for short-lived Azure tokens.
Only the worker has `id-token: write`; normal pull-request workflows do not.
The command-line interface uses that Azure CLI session, including Terraform and
kubelogin. Do not set `ARM_CLIENT_SECRET`, storage keys, `AZURE_CREDENTIALS`,
passwords, saved access tokens, or a static kubeconfig.

See Microsoft's [managed-identity OIDC setup guidance](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect).
No identity, federation, role assignment, runner, or GitHub environment is created
by these workflow files. Have an authorized administrator configure them.

## GitHub Settings

Create **Settings > Environments > azure-acceptance** and
**azure-acceptance-cleanup**. Restrict both to deployments from `main`; protect
`main` and require review of workflow/controller changes. Use required reviewers
for the test environment. Recovery must be preauthorized to run without another
human approval, or cancellation/timeout cleanup could wait indefinitely.

In **Environment secrets**, configure the following in both environments:

| Name | Value and handling |
| --- | --- |
| `AZURE_CLIENT_ID` | Managed identity **client ID**, not its principal/object ID |
| `AZURE_TENANT_ID` | Directory/tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Dedicated approved test subscription ID |
| `AIKS_CONFIG_BUNDLE` | JSON array of four complete operator configurations, described below |

The three IDs are identifiers, not passwords. Environment secrets provide
scoped access and log masking because this repository keeps account identifiers
out of public evidence. No client secret is needed. The configuration bundle
contains private addresses, group IDs and notification destinations, **not**
credentials. Do not commit the real bundle or include it in artifacts.

In **Environment variables**, configure these identically in both environments:

| Name | Purpose |
| --- | --- |
| `AIKS_APPROVED_SHA` | Reviewed, immutable 40-character commit containing the lifecycle commands |
| `AIKS_CAMPAIGN` | Unique lowercase approval ID, for example `acceptance-oct01` |
| `AIKS_RUN_ROOT` | Absolute persistent private directory, for example `/var/lib/aiks-acceptance` |
| `AIKS_MAX_HOURLY_USD` | Reviewed conservative hourly ceiling for all concurrently retained resources and runners |
| `AIKS_RETAINED_COST_USD` | Reviewed allowance for backend retention, disks, ingestion and other non-hourly costs |

Set the **repository variable** `AIKS_RECOVERY_ENABLED=true` only after recovery
has been reviewed, installed, and checked on the recovery runner. Both workflows
refuse an enabled test run without this opt-in. Do not put Azure identifiers in
repository-wide variables or use repository-wide Azure credential secrets.

Do not change either environment's campaign, commit, bundle, or cost settings
while a campaign needs recovery. The controller refuses mismatches rather than
adopting resources under new settings. Re-running the same campaign preserves
its original deadlines and does not rerun passed scenarios or failed mutations.
A new campaign identifier is a new spending authorization, not a retry mechanism.

## Runner and Authorization Prerequisites

Supply two dedicated Linux x64 runner agents: one with label `aiks-acceptance`
and one with `aiks-recovery`. Register them for this repository only. Never use
them for untrusted pull requests or unrelated repositories. They must share the
same durable `AIKS_RUN_ROOT`, owned by the same operating-system user with mode
`0700`; files use `0600`. This is **not** the GitHub checkout or runner-temporary
directory. Keep its disk encrypted and backed up privately. Do not delete the
volume until environment cleanup is verified.

The recovery agent needs independent job capacity while the test agent is busy.
Separate agents on one host share disk but not host-failure resilience. Separate
hosts require a private shared filesystem with tested POSIX locking and atomic
rename semantics. A GitHub runner registration credential is infrastructure
bootstrap material; keep it out of repository configuration and logs and use
short-lived registration tokens. It is not an Azure deployment credential.

Preinstall Azure CLI, Bicep, Terraform, Docker, Helm, kubectl and kubelogin at the
versions supported by the tested revision. Python 3.12 is installed by the
workflow. The container engine must build Linux amd64 images. The target package
is installed in an isolated environment from the approved checkout. Treat the
entire approved checkout and its dependencies as privileged executable code.

Both agents need existing private connectivity and name resolution to every
production API server, registry, vault and internal Gateway endpoint, plus
outbound access to GitHub and package/container registries. Each scenario creates
a different virtual network; merely placing a runner in some Azure network does
not establish connectivity to those new networks. Arrange routing and private
DNS links before accepting the production run; do not replace these checks with
public wildcard access. This workflow does not create that network connectivity.

Grant the managed identity only the permissions required by the selected
foundation and its existing Terraform backend. Subscription-level resource-group
creation, deployment operations and role-assignment operations are required by
the current foundation. Review its permission inventory with your administrator;
do not grant subscription Owner simply to make a check pass. Prefer a dedicated
test subscription and constrain role-assignment delegation where supported.

Azure control-plane access does not automatically grant Kubernetes access.
The selected administrator security group must authorize the managed identity
used by the runner, not only the human operator. An authorized directory owner
can explicitly add the managed identity's principal object to that group. Verify
this choice and membership propagation; the workflow does not change membership
or grant itself roles. Direct extra cluster assignments can violate the
foundation's exact role-inventory verification, so do not add them as a bypass.
Include the required registry publishing, monitoring **data** reads and
container-scoped Terraform state blob permissions in the reviewed access plan.

Authentication is renewed before each scenario and recovery. If authentication
expires during a scenario, it fails and the separately authenticated recovery
path attempts cleanup; renewal is not a promise that one login lasts 12 hours.
Azure CLI and Docker login files use each job's temporary directory, separate
from the shared recovery volume and from the other runner's session.

## Configuration Bundle

`AIKS_CONFIG_BUNDLE` is a JSON array in this fixed order:

```json
[
  {"engine": "bicep", "config": {"apiVersion": "aiks.io/v1alpha1", "kind": "AksAutomaticEnvironment", "metadata": {"name": "operator-dev"}, "spec": {"environment": "dev"}}},
  {"engine": "bicep", "config": {"spec": {"environment": "production"}}},
  {"engine": "terraform", "config": {"spec": {"environment": "dev"}}},
  {"engine": "terraform", "config": {"spec": {"environment": "production"}}}
]
```

This demonstrates the **wrapper only**, not deployable configurations. Supply
complete validated configurations based on the [development example](../config/dev.example.yaml)
and [production example](../config/production.example.yaml). Replace every
placeholder. Use the operator-selected administrator group, approved runner
source allowlists, nonoverlapping ranges, real private probe hosts and confirmed
notification recipients. The controller derives distinct resource prefixes and
metadata names from the campaign and scenario; it does not modify network,
identity, backend or security settings.

Terraform backends must already be separately bootstrapped and accessible.
The workflow never creates, migrates, purges or deletes the backend. Its state
keys are removed only through the foundation's guarded environment cleanup.
The separate backend acceptance item remains mandatory.

## Limits and Recovery

Authorization is **USD 100 aggregate and 12 hours**, including cleanup and runner
costs. The campaign clock starts when the first scenario is admitted, before
billable creation, and survives job retries. New test commands stop at hour 11,
reserving the final hour for cleanup. Individual commands have bounded timeouts;
child process groups are terminated on timeout or a recovery stop request.
Cleanup remains permitted after the deadline to reduce ongoing charges; a time
limit cannot instantly delete Azure resources.

Before running target code, admission requires:

```text
maximum hourly estimate * 12 + retained-cost allowance <= USD 80
```

The remaining 20 percent is headroom. Rates must include all four sequential
environments' retained resources, backend and both runners, and reflect reviewed
capacity/ingestion bounds. These operator-supplied estimates are **not live meter
readings or enforced capacity limits**. Do not dispatch if a defensible upper
estimate cannot fit this authorization. Stop and re-plan instead of silently
raising the limit. Azure budget alerts are delayed and do not stop consumption;
this workflow cannot guarantee a hard USD 100 billing cap. It does not claim an
actual-cost check passed.

Every scenario attempts guarded destruction in a `finally` path. A separate
recovery workflow also runs after workflow completion, on manual dispatch, and
at minutes 17 and 47 of each hour. Scheduled recovery acts only after the test
deadline or a recorded failure. It signals active commands to stop before taking
the campaign lock. A busy lock is not stolen; the running controller and a later
recovery attempt must finish cleanup.

Recovery uses the same private ownership receipts and exact approved code. It
never falls back to an unguarded resource-group delete or purges retained vaults.
Partial resources without verifiable ownership, a lost disk, a dead runner,
a delayed GitHub schedule, expired authorization or failed Azure deletion can
require operator recovery. **This is independent recovery, not guaranteed cleanup
under every infrastructure failure.** Monitor recovery failures and have an
operator available; closing VS Code does not stop Azure billing.

## Dispatch and Results

After setup, select **Actions > Azure acceptance > Run workflow**, use `main`
and enter exactly `AIKS_APPROVED_SHA`. Approve the protected environment.
The four scenarios execute sequentially; the first failure stops further tests.
The Mac and VS Code may then be disconnected: GitHub and the dedicated runners
own execution, not this chat session.

Each scenario performs preflight, image build, preview, deploy/verify, a required
no-op repeat preview, repeat deploy, replica-changing upgrade, rollback and
verification. Production additionally exercises alert fire/resolution. Guarded
destruction verifies environment removal and preserves expected backend/vault
retention. The workflow publishes only `acceptance-summary.json`, retained seven
days, with scenario statuses and explicit limitations. Full outputs, state,
kubeconfig and operation results remain private. Never upload the campaign root.

`automatedLifecyclePassed` is separate from `fullAcceptancePassed`, which remains
false. Notification receipt, backend bootstrap/recovery/leases, negative drift
and partial-failure fixtures, and audited cross-engine evidence still need the
tracked operator acceptance work. A green workflow does **not** close the parent
issue or authorize merging the lifecycle pull request by itself.
