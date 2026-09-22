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
| CLI and environment configuration contract is not landed | [#6](https://github.com/toddysm/aiks/issues/6) is open and has no implementation pull request. |
| Bicep AKS Automatic foundation is not implemented | Tracked by [#7](https://github.com/toddysm/aiks/issues/7). |
| Terraform foundation and Azure state lifecycle are not implemented | Tracked by [#8](https://github.com/toddysm/aiks/issues/8). |
| Readiness application and Helm lifecycle are not implemented | Tracked by [#9](https://github.com/toddysm/aiks/issues/9). |
| Infrastructure-as-code parity and comprehensive static CI are incomplete | Tracked by [#10](https://github.com/toddysm/aiks/issues/10). Existing CodeQL automation covers only part of this work. |
| Azure lifecycle verification and protected cleanup are not implemented | Tracked by [#11](https://github.com/toddysm/aiks/issues/11). |
| Foundation documentation and acceptance evidence are incomplete | Tracked by [#12](https://github.com/toddysm/aiks/issues/12). |