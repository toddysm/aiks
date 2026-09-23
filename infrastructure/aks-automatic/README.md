# AKS Automatic Foundation

This directory contains the configuration contract and, as later work items land, the Bicep,
Terraform, readiness application, and Helm implementations for the accepted
[AKS Automatic foundation design](../../docs/architecture/infrastructure/aks-automatic-foundation.md).

## Python CLI

Use Python 3.12 or later in an isolated virtual environment:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/aiks --help
```

The CLI uses Click and provides these command groups:

- `aiks config`: configuration schema operations
- `aiks infra`: Bicep and Terraform environment lifecycle
- `aiks state`: Terraform Azure Storage backend lifecycle
- `aiks local`: local kind cluster lifecycle
- `aiks workload`: readiness Helm release lifecycle

Commands owned by later implementation issues are visible but fail with the GitHub issue that
tracks their implementation. They never report a deployment as successful before it exists.

## Configuration

The Pydantic model in `src/aiks/config.py` is the source of truth. It accepts versioned YAML and
generates [`config/schema.json`](config/schema.json) for editor and CI validation.

Configuration precedence is intentionally narrow:

1. The selected YAML file supplies all nonsecret environment settings.
2. Explicit CLI options select operations and targets but do not override environment posture.
3. The current Azure CLI session supplies subscription, tenant, and user authentication in later
   work items.
4. Environment variables are reserved for ignored local testing and never carry committed Azure
   credentials.

Generate and compare the schema:

```bash
.venv/bin/aiks config schema --output "$TMPDIR/aiks-schema.json"
cmp "$TMPDIR/aiks-schema.json" infrastructure/aks-automatic/config/schema.json
```

The configuration loader rejects undeclared fields, secret-shaped keys, unrestricted API ranges,
overlapping network ranges, and environment postures that do not meet the accepted dev or
production baseline. Example UUIDs, IP ranges, email addresses, and names are documentation-only
values and must be replaced before deployment.

## Results and redaction

Commands emit a concise human result and can write a JSON result containing:

- operation and phase
- success status and duration
- locally generated correlation ID
- nonsecret operation context

The result boundary redacts credential-shaped keys, bearer tokens, storage account keys, SAS
signatures, and connection strings before writing logs or JSON. The subprocess adapter invokes
tools with argument arrays and never enables a shell.

The CLI uses Python standard logging on local stderr. A package-level filter redacts message
arguments and structured context before handlers emit a record. No file, network, or remote
telemetry handler is configured.

The CLI sends no product usage telemetry. Correlation IDs and operation summaries remain local
unless an operator explicitly attaches a sanitized report to GitHub.

## Destroy safeguards

Destroy protection is operational CLI behavior, not environment configuration. YAML cannot
disable or weaken it. Every environment and Terraform state destroy command requires the operator
to type the selected environment name. Production additionally requires the explicit
`--allow-production-destroy` flag before prompting. Environment destruction never removes the
Terraform backend; state cleanup remains a separate guarded command.

The commands currently stop at tracked placeholders. Issues #8 and #11 implement the state and
environment deletion behavior while retaining these safeguards.

## Issue #6 validation

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src
.venv/bin/bandit -q -r src
.venv/bin/pip-audit

schema_output="$(mktemp)"
.venv/bin/aiks config schema --output "$schema_output"
cmp "$schema_output" infrastructure/aks-automatic/config/schema.json
rm "$schema_output"

_AIKS_COMPLETE=zsh_source .venv/bin/aiks | grep -q '#compdef aiks'
```

Azure deployment and Helm lifecycle commands are implemented by subsequent work items in
[tracker #5](https://github.com/toddysm/aiks/issues/5).