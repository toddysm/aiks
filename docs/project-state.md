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
| Bicep AKS Automatic foundation is in progress | [#7](https://github.com/toddysm/aiks/issues/7) has a composed subscription template, command adapters, offline policy tests, and [module documentation](../infrastructure/aks-automatic/bicep/README.md) in [PR #20](https://github.com/toddysm/aiks/pull/20). The registry-only `2026-03-01-preview` exception was approved on 2026-09-23. This work is not merged or deployment-tested. On the same date, the user deferred its live deployment/idempotence evidence to #11/#12; static checks and review remain required for #7. |
| Terraform foundation and Azure state lifecycle are not implemented | Tracked by [#8](https://github.com/toddysm/aiks/issues/8). |
| Readiness application and Helm lifecycle are not implemented | Tracked by [#9](https://github.com/toddysm/aiks/issues/9). |
| Infrastructure-as-code parity and comprehensive static CI are incomplete | Tracked by [#10](https://github.com/toddysm/aiks/issues/10). Existing CodeQL automation covers only part of this work. |
| Azure lifecycle verification and protected cleanup are not implemented | [#11](https://github.com/toddysm/aiks/issues/11) also owns the live deployment and repeat-deployment tests deferred from #7 on 2026-09-23. |
| Foundation documentation and acceptance evidence are incomplete | [#12](https://github.com/toddysm/aiks/issues/12) owns the sanitized final cross-engine evidence, including the live validation deferred from #7. Full feature completion remains gated on that evidence. |