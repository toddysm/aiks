---
name: example-development-workflow
description: "Guide AI on AKS example functionality from discovery through delivery. Use when asked to create, add, design, or implement an example or feature and manage its full GitHub lifecycle: requirements interviews, feature issues, architecture designs under docs/architecture, design PRs, tracking and work-item issues, implementation, tests and GitHub CI, Copilot review comments, check failures, merge, and branch cleanup."
---

# Example Development Workflow

Run this workflow as a state machine. Finish each phase before starting the next:

1. Discover and agree on the functionality.
2. File the feature issue.
3. Design, review, and merge the design PR.
4. File and approve the implementation breakdown.
5. Implement and close each work item on one feature branch.
6. Open the implementation PR.
7. Complete Copilot review and CI remediation.
8. Merge, clean up, report, and stop.

Read [artifact templates](./references/artifact-templates.md) before creating issues, design documents, or PRs.

## Operating Rules

- Use repository GitHub tools when available; otherwise use authenticated `gh` commands.
- Treat GitHub issues and PRs as the durable workflow record. Keep links and status current.
- Ask focused questions in small batches and adapt follow-up questions to the answers.
- Never infer approval from silence. Require explicit approval at each named gate.
- Never expose credentials or route secrets through chat. Let authentication prompts run directly in the terminal or system UI.
- Honor repository conventions, configured Git identity and signing, branch protection, and merge strategy.
- Keep unrelated changes out of every branch. Do not rewrite or discard user changes.
- Before deleting a branch, verify its PR is merged, its commits are reachable from the remote default branch, it has no local-only commits, no worktree uses it, and the current worktree is clean. Use non-force deletion and pause if any check fails.
- Pause and explain a blocker when authentication, permissions, required infrastructure, or an external service prevents safe progress.

## Phase 1: Discover and Agree

Inspect only enough existing documentation and nearby examples to make the interview repository-aware. Then interview the user until all applicable areas below are resolved:

- problem, intended users, and desired outcome
- goals and explicit non-goals
- functional requirements and acceptance criteria
- primary, alternate, failure, and cleanup scenarios
- repository area: `infrastructure`, `platform`, `inference`, `agents`, `samples`, or cross-cutting
- dependencies, prerequisites, interfaces, and compatibility constraints
- identity, security, privacy, networking, and data-handling requirements
- scale, performance, reliability, cost, and operational expectations
- business or adoption metrics that define success
- logs, metrics, traces, dashboards, and alerts needed to observe success and failures
- testing levels, GitHub CI expectations, documentation, rollout, rollback, and cleanup

Mark an item `Not applicable` only after confirming why. Keep unresolved material decisions visible as open questions; do not silently choose defaults.

Summarize the agreed problem, scope, scenarios, acceptance criteria, metrics, telemetry, and constraints. Ask the user to confirm that summary.

**Scope approval gate:** Do not create an issue, branch, design, or code until the user explicitly approves the functionality summary.

## Phase 2: File the Feature Issue

Verify the GitHub repository, default branch, authentication, and issue permissions. Create one feature issue using the feature issue template. Include the complete approved scope rather than referring only to chat history.

Record the issue number and URL. Use this feature issue as the design's source requirement. Do not close it manually; the design PR must contain `Closes #<feature-issue>` so merging that PR closes it.

## Phase 3: Design the Functionality

### Choose the design location

Store designs below `docs/architecture/` and create only the relevant area folders:

| Area | Design path |
| --- | --- |
| Infrastructure | `docs/architecture/infrastructure/<slug>.md` |
| Platform | `docs/architecture/platform/<slug>.md` |
| Inference | `docs/architecture/inference/<slug>.md` |
| Agents | `docs/architecture/agents/<slug>.md` |
| End-to-end samples | `docs/architecture/samples/<slug>.md` |
| Multiple areas | `docs/architecture/cross-cutting/<slug>.md` |

Use a stable lowercase hyphenated slug. Split a design into additional area documents only when separate ownership or substantial detail makes that clearer; link the documents to each other and to the feature issue.

### Write and publish the design

1. Sync the default branch and create `design/<feature-issue>-<slug>` from it.
2. Write the design using the design template. Trace requirements and acceptance criteria back to the feature issue.
3. Include diagrams when they clarify components or flows, using Mermaid in Markdown.
4. Validate Markdown, links, diagrams, and any repository documentation checks.
5. Commit with the configured identity and signing policy, push the branch, and create a design PR.
6. Link the design file and feature issue in the PR body and add `Closes #<feature-issue>`.
7. Ask the user to review the design PR. Address requested changes on the same branch.
8. Merge the design PR only after the user explicitly approves it and all PR checks pass. Delete its branch using the branch-cleanup safeguards and sync the default branch.

The accepted design in the default branch is the implementation source of truth. Do not begin breakdown or implementation from an unmerged design.

## Phase 4: Break Down the Design

Create the tracking issue first using the tracking template. Then derive work items from the accepted design and create one GitHub issue per independently verifiable implementation piece. The ordered tracking issue plus its linked work-item issues is the durable design breakdown.

Each work item must:

- deliver a coherent, testable result rather than a file-by-file task
- link to the tracking issue and accepted design
- define in-scope and out-of-scope behavior
- include acceptance criteria, applicable tests, dependencies, and telemetry work
- be small enough to implement and validate before moving to the next item

Link work items as native GitHub sub-issues when supported. Otherwise, add a Markdown task list such as `- [ ] #123` to the tracking issue. Also add `Tracked by #<tracking-issue>` to every work item so linkage is bidirectional. Order the tracker by dependency and include CI setup or extension as a work item when the repository cannot yet test the functionality automatically.

Present the user with the tracking issue, ordered work-item table, dependencies, and definition of done.

**Breakdown approval gate:** Ask whether the filed breakdown is acceptable. Do not create an implementation branch or change implementation files until the user explicitly approves it. If rejected, revise the issues and repeat this gate.

## Phase 5: Implement the Work Items

After breakdown approval, sync the default branch and create one branch named `feature/<tracking-issue>-<slug>`. Implement every work item on this same branch in dependency order.

For each work item:

1. Restate its acceptance criteria and inspect the owning implementation and test surfaces.
2. Implement the smallest complete change that satisfies the item and accepted design.
3. Add or update unit, integration, deployment, or end-to-end tests as applicable. If no automated test is applicable, document the reason and a reproducible manual validation in the issue.
4. Add or update GitHub Actions under `.github/workflows/` so applicable tests run automatically for pull requests. Reuse existing workflows and repository commands before introducing new tooling.
5. Run the narrowest behavior check after the first edit, repair locally, then run the broader applicable test and lint suite.
6. Commit the work-item change using repository conventions and push the shared feature branch.
7. Comment on the work item with the commit link, files or behavior delivered, and validation evidence.
8. Close the work item as completed only when its acceptance criteria pass. Reopen it if later review or CI proves it incomplete.
9. Update the tracking issue and move to the next open work item.

Never close a work item merely because code was written. Do not move forward while its applicable validation is failing.

Per-item closure may rely on focused local validation when PR CI does not run until Phase 6. Record that deferral in the issue, run any branch-triggered CI that is available, and reopen the item if final PR CI exposes a defect in its result.

## Phase 6: Open the Implementation PR

After all work items are closed:

1. Compare the branch with the accepted design and tracking issue for omissions.
2. Run the complete applicable local test, lint, build, manifest, and documentation validation suite.
3. Push the final branch state and open one implementation PR into the default branch using the implementation PR template.
4. Link the accepted design and all work items. Add `Closes #<tracking-issue>` so the merge closes the tracker.
5. Ensure GitHub Copilot review is requested or automatic review is enabled. If the agent cannot request it, ask the user to request the review and pause.

## Phase 7: Wait for Review and CI

Do not merge immediately after opening the PR. Wait until GitHub Copilot's review has completed and all PR CI has reached a terminal state. Do not treat an absent review as approval; if completion cannot be established, ask the user rather than guessing.

### Address Copilot review

Refresh review threads and handle each unresolved actionable comment in turn:

1. Understand the reported risk and verify it against the design and code.
2. Implement the correction on the existing feature branch.
3. Add or adjust a regression test when applicable.
4. Run focused validation, then relevant broader checks.
5. Commit and push the fix.
6. Reply with the change and validation evidence, then resolve the thread only after the fix is present remotely.

Re-fetch review threads after every push. Continue until Copilot has completed its review and every Copilot thread is resolved. For a comment that does not require a code change, explain the rationale respectfully in the thread and resolve it only after that rationale is recorded.

### Repair CI

Inspect every failed PR check and its logs, whether required or optional. Reproduce locally when practical, fix the root cause, add regression coverage when applicable, and push the repair. Continue until all CI checks are green. Do not bypass, disable, or weaken a check merely to make the PR pass.

If a failure is demonstrably external or flaky, record the evidence, rerun it through supported GitHub controls, and pause for explicit user direction if it remains unresolved. Do not merge while it is failing.

## Phase 8: Merge and Finish

Merge only when all of these statements are true:

- GitHub reports the PR as mergeable.
- GitHub Copilot review is complete.
- Every Copilot review thread is resolved after a fix or recorded rationale.
- All PR CI checks are green.
- The implementation still conforms to the accepted design.

Use the repository's configured merge strategy. After merge, follow the branch-cleanup safeguards to delete the remote feature branch, switch to the default branch, fast-forward from the remote, delete the local feature branch, and verify a clean synchronized worktree.

Report the merged PR, tracking issue, completed work items, tests, CI status, and resulting default-branch commit. Then stop and wait for the user.
