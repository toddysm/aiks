# Project State

Capability inventory for this project, maintained by My Feature Engineer. This
file tracks *what the project has and is building*; the GitHub Project board
(when one is available) tracks *transient work in flight*.

## Implemented

| Capability | Feature slug | Tracking issue | Design doc | Docs | Landed |
| --- | --- | --- | --- | --- | --- |
| Repository scaffold and project overview | `project-scaffold` | — | — | [Project overview](../README.md) | 2026-08-04 |
| Example development workflow and static security automation | `example-development-workflow` | — | — | [Workflow skill](../.github/skills/example-development-workflow/SKILL.md) | 2026-08-05 |
| Accepted AKS Automatic foundation architecture | `aks-automatic-foundation-design` | [#3](https://github.com/toddysm/aiks/issues/3) | [Architecture design](architecture/infrastructure/aks-automatic-foundation.md) | [Architecture design](architecture/infrastructure/aks-automatic-foundation.md) | 2026-08-05 |
| CLI and environment configuration contract, logging, tests, and Python quality automation | `aks-automatic-cli-contract` | [#6](https://github.com/toddysm/aiks/issues/6) | [Architecture design](architecture/infrastructure/aks-automatic-foundation.md) | [CLI usage](../infrastructure/aks-automatic/README.md) | 2026-09-23 |
| Bicep foundation modules, engine adapter, and offline policy validation | `aks-automatic-bicep` | [#7](https://github.com/toddysm/aiks/issues/7) | [Architecture design](architecture/infrastructure/aks-automatic-foundation.md) | [Bicep usage and limits](../infrastructure/aks-automatic/bicep/README.md) | 2026-09-23 |
| Terraform foundation, guarded state commands, and offline validation | `aks-automatic-terraform` | [#8](https://github.com/toddysm/aiks/issues/8) | [Architecture design](architecture/infrastructure/aks-automatic-foundation.md) | [Terraform usage and limits](../infrastructure/aks-automatic/terraform/README.md) | 2026-09-23 |
| Readiness service, shared Helm chart, workload commands, and tested local lifecycle | `aks-automatic-readiness` | [#9](https://github.com/toddysm/aiks/issues/9) | [Architecture design](architecture/infrastructure/aks-automatic-foundation.md) | [Readiness operator guide](../infrastructure/aks-automatic/readiness-app/README.md) | 2026-09-24 |
| Executable cross-engine parity, negative policy fixtures, and comprehensive static checks | `aks-automatic-parity` | [#10](https://github.com/toddysm/aiks/issues/10) | [Architecture design](architecture/infrastructure/aks-automatic-foundation.md) | [Parity and static validation](../infrastructure/aks-automatic/parity/README.md) | 2026-09-24 |

> NOTE: The first two rows are inferred from the repository and merged pull
> requests [#1](https://github.com/toddysm/aiks/pull/1) and
> [#2](https://github.com/toddysm/aiks/pull/2); verify their capability names.
> The architecture row is backed by closed issue #3 and merged pull request
> [#4](https://github.com/toddysm/aiks/pull/4).

## In progress

| Capability | Feature slug | Tracking issue | Design doc | Docs |
| --- | --- | --- | --- | --- |
| AKS Automatic infrastructure foundation | `aks-automatic-foundation` | [#5](https://github.com/toddysm/aiks/issues/5) | [Architecture design](architecture/infrastructure/aks-automatic-foundation.md) | — |

## Planned

| Capability | Feature slug | Tracking issue |
| --- | --- | --- |
| — | — | — |

## Deferred / Won't do

| Capability | Feature slug | Tracking issue | Reason |
| --- | --- | --- | --- |
| — | — | — | — |

## Known gaps & debt

| Gap | Notes |
| --- | --- |
| Bicep foundation is not live deployment-validated | [PR #20](https://github.com/toddysm/aiks/pull/20) supplies the modules, adapters, and offline checks for #7. The registry-only `2026-03-01-preview` exception and deferral of live deployment/idempotence evidence to #11/#12 were approved on 2026-09-23. No Azure deployment was performed; full lifecycle orchestration and final feature acceptance remain outstanding. |
| Terraform foundation and state lifecycle are not live deployment-validated | [PR #21](https://github.com/toddysm/aiks/pull/21) supplies #8's environment, guarded backend commands, tests, and offline CI. The user approved moving live deployment/idempotence and backend lifecycle evidence to #11/#12 on 2026-09-23. No Azure deployment was performed; full foundation acceptance remains outstanding. |
| Readiness workload is not live AKS-validated | [#9](https://github.com/toddysm/aiks/issues/9) provides the service, shared chart, commands, schema/image checks, and verified kind lifecycle. Live registry pushes, workload identity, managed scraping, and private AKS routing remain assigned to #11/#12; no Azure operations were performed. |
| Offline parity does not prove live equivalence | [#10](https://github.com/toddysm/aiks/issues/10) supplies input/output contracts, compiled Bicep and real-module Terraform mock-plan assertions, negative fixtures, and static gates. Live Azure resource snapshots, repeated deployment, and service behavior still require #11/#12. Earlier Terraform names can require replacement after the documented naming correction. |
| Azure lifecycle implementation awaits live acceptance | [#11](https://github.com/toddysm/aiks/issues/11) implements guarded deployment, verification, alert exercises, and protected cleanup with mocked tests. The [lifecycle guide](../infrastructure/aks-automatic/lifecycle/README.md) describes commands and safety boundaries. Required live dev/private-production runs for both engines, including the #7/#8 deferrals, remain unmet and block merging/completing #11. |
| Foundation documentation and acceptance evidence are incomplete | [#12](https://github.com/toddysm/aiks/issues/12) owns the sanitized final cross-engine evidence, including the live validation deferred from #7/#8. Full feature completion remains gated on that evidence. |
