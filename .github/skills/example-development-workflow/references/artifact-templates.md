# Artifact Templates

Adapt these templates to the repository and omit a section only when it is explicitly not applicable. Preserve issue and PR links so every decision and implementation item is traceable.

## Feature Issue

```markdown
# Summary

[One paragraph describing the requested functionality and intended outcome.]

## Problem and users

[Problem, affected users or operators, and current limitation.]

## Goals

- [Measurable goal]

## Non-goals

- [Explicit exclusion]

## Requirements

- [Functional requirement]

## Scenarios

### Primary
[Expected end-to-end path.]

### Alternate and failure
[Edge cases, errors, recovery, and cleanup.]

## Acceptance criteria

- [ ] [Observable criterion]

## Success metrics

- [Business, adoption, performance, reliability, or cost metric and target]

## Implementation standards

- Infrastructure: [Both Bicep and Terraform, matching inputs/outputs, and CI validation; or `Not applicable: no infrastructure deliverable`.]
- Kubernetes: [One Helm chart with local Kubernetes and AKS values plus deployment, validation, upgrade, rollback, and cleanup acceptance criteria; or `Not applicable: no Kubernetes deliverable`.]
- Languages: [Python for supporting code; Bash/zsh with an explicit shell declaration for shell scripts; Python or JavaScript for deployed example applications.]
- Configuration: [Externalized YAML/JSON for all application, deployment, environment-specific, and runtime configuration.]
- Identity and secrets: [Selected mechanism and rank. Explain why every higher-ranked option is not viable. Confirm no secrets enter source-controlled configuration.]

## Telemetry

- Logs: [events and fields]
- Metrics: [measurements and dimensions]
- Traces: [operations and correlations]
- Alerts/dashboards: [operator signals]

## Constraints and dependencies

[Security, identity, privacy, networking, compatibility, prerequisites, and dependencies. Identify any implementation-standard category with no corresponding deliverable. Treat any other deviation as an exception requiring documented impact, mitigation, and explicit approval.]

## Testing and delivery

[Required test levels, CI, documentation, rollout, rollback, and cleanup.]

## Decisions and open questions

- Decision: [Agreed decision and rationale]
- Open: [Only explicitly accepted unresolved questions]
```

## Architecture Design

```markdown
# [Functionality] Design

- Status: Proposed
- Feature issue: #[number]
- Area: [infrastructure | platform | inference | agents | samples | cross-cutting]

## Context

[Problem, users, current state, and why this design is needed.]

## Goals and non-goals

[Trace to the approved feature issue.]

## Requirements and scenarios

[Functional requirements, acceptance criteria, primary path, failures, and cleanup.]

## Proposed architecture

[Components and responsibilities. Include a Mermaid diagram when useful.]

## Control and data flows

[Requests, events, model/data movement, state, and failure handling.]

## Interfaces and configuration

[APIs, schemas, versioning, compatibility, and configuration ownership. Externalize all application, deployment, environment-specific, and runtime configuration as YAML or JSON.]

## Infrastructure and deployment

[For every infrastructure deliverable, define equivalent Bicep and Terraform implementations with matching inputs/outputs and GitHub Actions validation. For every Kubernetes deliverable, define one Helm chart and environment-specific values for both local Kubernetes and AKS, including deployment, validation, upgrade, rollback, and cleanup. State explicitly when either deliverable category is absent.]

## Application and automation languages

[Choose Python or JavaScript for deployed example applications. Use Python for supporting code and Bash/zsh only when shell is the appropriate interface. Record choices and rationale, and declare every shell script's required shell explicitly.]

## Security and identity

[Trust boundaries, RBAC, network controls, and data handling. For each workload, name the selected mechanism and rank: managed identity/Workload ID; Kubernetes Secrets sourced from Azure Key Vault through Secrets Store CSI Driver; runtime retrieval from Azure Key Vault; environment variables for local testing only. Explain why every higher-ranked option is not viable and confirm that no secret values enter source-controlled configuration.]

## Observability

[Logs, metrics, traces, dashboards, alerts, correlation, and success signals.]

## Reliability, scale, performance, and cost

[Targets, limits, recovery behavior, capacity assumptions, and cost controls.]

## Test and CI strategy

[Unit, integration, deployment, end-to-end, failure, and GitHub Actions coverage.]

## Alternatives considered

[Meaningful alternatives and why they were not selected.]

## Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| [Risk] | [Impact] | [Mitigation] |

## Delivery plan

[Incremental implementation order and definition of done.]

## Decisions and open questions

- Decision: [Decision and rationale]
- Open: [Question, owner, and resolution point]
```

After design approval, change `Status: Proposed` to `Status: Accepted` before merging the design PR.

## Tracking Issue

```markdown
# Outcome

[Implementation outcome represented by this tracker.]

## Source

- Feature issue: #[number]
- Accepted design: [repository link]
- Design PR: #[number]

## Work items

- [ ] #[work-item-number] [Title]

## Dependency order

1. #[number] - [Reason it comes first]

## Definition of done

- [ ] All work-item acceptance criteria pass
- [ ] Applicable tests run locally and in GitHub Actions
- [ ] Every infrastructure deliverable has equivalent Bicep and Terraform implementations with matching inputs/outputs and passing CI, or the tracker records that no infrastructure deliverable exists
- [ ] Every Kubernetes deliverable uses one Helm chart and passes local Kubernetes and AKS lifecycle validation, or the tracker records that no Kubernetes deliverable exists
- [ ] Supporting code, shell scripts, and deployed applications use the required languages
- [ ] All configuration is externalized as YAML/JSON and each workload records its identity or secret mechanism, rank, and higher-ranked exclusions
- [ ] Required telemetry and documentation are delivered
- [ ] Copilot review is complete and every thread is resolved
- [ ] All PR CI checks are green
- [ ] Implementation PR is merged

## Status

[Current phase, active item, and blockers.]
```

## Work-Item Issue

```markdown
# Objective

[One coherent and independently verifiable result.]

## Traceability

- Tracked by: #[tracking-issue]
- Design: [repository link]

## In scope

- [Behavior or artifact]

## Out of scope

- [Explicit exclusion]

## Implementation notes

[Constraints, expected surfaces, interfaces, dependencies, and applicable implementation standards without over-prescribing code.]

## Acceptance criteria

- [ ] [Observable criterion]
- [ ] If this item delivers infrastructure: equivalent Bicep and Terraform inputs/outputs plus passing CI validation
- [ ] If this item delivers Kubernetes resources: the same Helm chart passes deployment, validation, upgrade, rollback, and cleanup on local Kubernetes and AKS
- [ ] Supporting code, shell scripts, and deployed applications use the required languages, with Bash/zsh declared explicitly for scripts
- [ ] All configuration is externalized as YAML/JSON; the selected identity or secret rank and every higher-ranked exclusion are recorded

## Tests and CI

- [Applicable automated tests and GitHub workflow]
- [Manual validation and reason, only when automation is not applicable]

## Telemetry and documentation

[Required signals and docs, or confirmed reason they are not applicable.]

## Dependencies

- [Blocks/is blocked by issue or external dependency]
```

## Design PR

```markdown
## Summary

[What the design proposes and why.]

## Design

- [Path to the design document]

## Validation

- [Markdown, link, diagram, or documentation checks performed]
- [Evidence that each implementation-standard category is designed in full or explicitly absent]

Closes #<feature-issue>
```

## Implementation PR

```markdown
## Summary

[Delivered behavior and user/operator outcome.]

## Design

- [Accepted design link]
- Design PR: #[number]

## Work items

- #[number] - [Result]

## Testing

- [Local test command and result]
- [GitHub Actions workflow/check]

## Telemetry and operations

[Signals, dashboards, alerts, rollout, rollback, and cleanup delivered.]

## Review readiness

- [ ] Implementation matches the accepted design
- [ ] Every infrastructure deliverable has equivalent Bicep and Terraform inputs/outputs and passing GitHub Actions validation, or the PR identifies that no infrastructure deliverable exists
- [ ] Every Kubernetes deliverable uses one Helm chart and passes deployment, validation, upgrade, rollback, and cleanup on local Kubernetes and AKS, or the PR identifies that no Kubernetes deliverable exists
- [ ] Supporting code is Python; shell scripts declare Bash/zsh; deployed applications are Python or JavaScript
- [ ] All configuration is externalized as YAML/JSON; each workload records its identity or secret mechanism, rank, and higher-ranked exclusions
- [ ] Applicable tests are included
- [ ] Required CI is configured
- [ ] All current PR CI checks are green
- [ ] Documentation is updated

Closes #<tracking-issue>
```