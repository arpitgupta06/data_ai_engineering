# Ways of Working

The process contract for this repository. Agreed 2026-09-14.

Roles: **Arpit** is the engineer. **Claude** acts as tech lead and reviewer — no PR
merges without a review, the same as it would work on a real team.

---

## 1. Tooling

| Concern | Tool |
|---|---|
| Work tracking | Jira Cloud, project key **CRC** (Chain Risk Copilot) |
| Source control | GitHub, public repo, single `main` branch |
| CI/CD | GitHub Actions |
| Decisions | ADRs in [`docs/adr/`](adr/) |

## 2. Cadence — 1-week sprints

Sprint runs **Monday → Sunday**. Four ceremonies, but only the ones that earn
their keep solo:

| Ceremony | When | What it actually is here |
|---|---|---|
| Sprint planning | Monday, ~30 min | Pick the sprint goal, pull stories until capacity is full, confirm every one meets Definition of Ready |
| Daily check-in | Each working session | Two lines appended to `docs/sprint-log.md`: what moved, what's blocked. Not a standup — there's nobody to stand up with |
| Sprint review | Sunday, ~20 min | Demo the increment. Record a terminal run or write a short "here's what works now" note. If you can't demo it, it isn't done |
| Retrospective | Sunday, ~15 min | What slowed you down, one process change to try, and **what challenges this sprint surfaced** — anything that cost over half a day or changed the design gets an entry in [`docs/challenges/`](challenges/). Highest-return ceremony for a solo builder; do not skip it |

**Be honest about velocity.** The first two sprints will overrun. Record actual
points completed, don't adjust the estimates afterwards to look better. Estimation
only becomes a skill if you keep the evidence of being wrong.

## 3. Work breakdown

```
Epic      one roadmap phase          e.g. "Phase 0 — Foundations"
 └ Story  a user-visible outcome     "Local data plane stands up in one command"
    └ Subtask  one branch, one PR    "Redpanda service with dual listeners"
```

A story that cannot be demoed is a task, not a story. A subtask that takes more
than ~2 days is too big — split it.

**Story points** (Fibonacci: 1, 2, 3, 5, 8): relative effort including
uncertainty, not hours. 8 means "too big, probably split it."

## 4. Branching — trunk-based

`main` is the only long-lived branch. It is protected and always releasable.

```
feature/CRC-12-redpanda-dual-listeners     new capability
fix/CRC-19-minio-init-race                 defect
chore/CRC-08-makefile-entrypoint           tooling, deps, config
docs/CRC-31-phase1-runbook                 documentation only
spike/CRC-22-rpc-provider-comparison       timeboxed investigation, throwaway
```

Rules:

- Branch from the latest `main`, always. Never branch from a branch.
- **One story, one branch, one PR.** Subtasks are a checklist inside the story,
  not separate branches. Lifetime target: **under a week**; split the story if
  it runs longer.
- Rebase on `main` before opening the PR — keep history linear.
- Delete the branch on merge.
- Never commit directly to `main`. The one exception is the initial bootstrap
  commit, which predates this document.

> Revised at Sprint 2 planning (2026-10-03). The original rule was one branch per
> *subtask*. Sprint 1 produced a single branch carrying five subtasks, because the
> subtasks were edits to one file and splitting them would have been theatre. The
> rule was changed to match how the work actually divides, rather than left to be
> quietly ignored — a process nobody follows is worse than a looser one everybody does.

## 5. Commits — Conventional Commits + Jira key

```
<type>(<scope>): <subject> (CRC-NN)

feat(infra): add redpanda with dual kafka listeners (CRC-12)
fix(ingest): handle empty s3 partition without raising (CRC-19)
chore(ci): cache uv dependencies between runs (CRC-10)
docs(adr): record decision on finality depth (CRC-22)
test(dbt): add uniqueness test on dim_address surrogate key (CRC-41)
refactor(agent): extract tool registry from graph builder (CRC-58)
```

Types: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`.
Scopes used here: `infra`, `ingest`, `stream`, `dbt`, `dags`, `agent`, `ci`, `tf`, `docs`.

Subject: imperative mood, lower case, no trailing period, under 72 characters.
Body: explain **why**, not what — the diff already shows what.

## 6. Pull requests

- Title matches the commit convention, including the Jira key.
- Fill in the PR template. "See ticket" is not a description.
- Keep it under ~400 changed lines where you can. Large PRs get reviewed badly.
- CI must be green before review is requested. Don't ask for review on a red build.
- Squash merge, so `main` gets one clean commit per subtask.

**Self-review first.** Before requesting review, read your own diff on GitHub —
not in your editor. The different presentation catches a surprising amount:
leftover debug prints, commented-out code, a secret in a fixture, a file you
didn't mean to add.

## 7. Definition of Ready

A story may enter a sprint only when:

- [ ] The outcome is written from a user's or operator's point of view
- [ ] Acceptance criteria are testable — each one is pass/fail, not a matter of opinion
- [ ] Dependencies are identified and either resolved or explicitly accepted as a risk
- [ ] It is estimated
- [ ] It is small enough to finish within the sprint

## 8. Definition of Done

A subtask is done when **all** of these hold:

- [ ] Acceptance criteria demonstrably met
- [ ] Automated test covering the new behaviour, and it fails without the change
- [ ] CI green: lint, type check, tests, and — for infra changes — `docker compose config` validates
- [ ] No secrets in the diff, and no credentials outside `.env` / GitHub Secrets
- [ ] Documentation updated in the same PR: README, runbook, or docstrings
- [ ] An ADR written if a non-obvious decision was made
- [ ] A challenge log entry written if this surfaced a non-trivial problem — see [`docs/challenges/`](challenges/)
- [ ] PR reviewed and approved
- [ ] Jira ticket moved to Done with a one-line note on what shipped
- [ ] Branch deleted

"It works on my machine" is not Done. Done means **a clean clone reproduces it.**

## 9. Board

```
Backlog → To Do → In Progress → In Review → Done
```

**WIP limit: 2 items in `In Progress`.** Exceeding it means you are
context-switching instead of finishing. A blocked item moves back to `To Do` with
a comment naming the blocker — it does not sit in progress accumulating age.

## 10. Branch protection on `main`

Configure in GitHub → Settings → Branches → Add rule for `main`:

- [ ] Require a pull request before merging
- [ ] Require status checks to pass — add each CI job once it exists
- [ ] Require branches to be up to date before merging
- [ ] Require conversation resolution before merging
- [ ] Require linear history
- [ ] Do not allow bypassing the above settings

Solo, "require approvals" would block you entirely, so leave approvals at 0 and
treat Claude's review as the approval gate. Everything else stays on — being
unable to merge a red build is the point.
