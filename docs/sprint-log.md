# Sprint log

Two lines per working session. What moved, what's blocked. Append only.

---

## Sprint 1 — week of 2026-09-14

**2026-09-14** — Agreed ways of working: Jira (CRC), trunk-based branching, 1-week
sprints. Sprint 1 backlog written. Repo scaffold committed as CRC-2.
Next: Jira project + GitHub repo setup, then CRC-4.

**2026-09-28 → 10-01** — CRC-6 local data plane up and verified. Four days of the
sprint went to an unplanned blocker: MinIO community edition reached EOL and its
images stopped being anonymously pullable from both Docker Hub and quay.io.
Resolved by moving to Chainguard builds pinned by digest; `mc` bootstrap now uses
`MC_HOST_<alias>` so the distroless client needs no shell. Recorded as ADR 0005
and CH-003. All nine CRC-6 acceptance criteria verified, idempotency included —
proven by bucket directory mtimes, not by `mc` output, which reports "created"
either way.
Next: commit CRC-6, reconcile the board, Sunday retro.

