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

## Telemetry

- Logs: [events and fields]
- Metrics: [measurements and dimensions]
- Traces: [operations and correlations]
- Alerts/dashboards: [operator signals]

## Constraints and dependencies

[Security, identity, privacy, networking, compatibility, prerequisites, and dependencies.]

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

[APIs, manifests, schemas, configuration, versioning, and compatibility.]

## Infrastructure and deployment

[Azure/AKS resources, Kubernetes objects, dependencies, rollout, rollback, and cleanup.]

## Security and identity

[Trust boundaries, workload identity, RBAC, secrets, network controls, and data handling.]

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

[Constraints, expected surfaces, interfaces, and dependencies without over-prescribing code.]

## Acceptance criteria

- [ ] [Observable criterion]

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

Closes #[feature-issue]
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
- [ ] Applicable tests are included
- [ ] Required CI is configured
- [ ] All current PR CI checks are green
- [ ] Documentation is updated

Closes #[tracking-issue]
```