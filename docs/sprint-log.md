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

## Sprint 2 — week of 2026-10-05

**2026-10-05 → 10-09** — CRC-13 done: typed settings module, five-service
end-to-end smoke test, 15 unit tests. Three review rounds, because the first two
attempts to verify the *failure* path produced no usable evidence — one run was
interrupted, one was timed with a stale stopwatch. Verified both ways in the end:
5/5 against a healthy stack, exit 1 with five named failures against a stopped
one, 12.76s. Three defects found that the passing path never showed. Logged as
CH-004.
Next: CRC-14 (Makefile + dev.ps1), then the clean-clone proof.

