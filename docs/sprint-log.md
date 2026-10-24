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

**2026-10-19** — CRC-14 closed (merged as #3): the Makefile and `dev.ps1` expose the
same targets, and a drift test pins that they stay in step. Its blind spot, found
two days later: it compares target names, not behaviour, so it could not see
`dev.ps1` discarding a program's exit code (see 10-21). The clean-clone proof from
the Sprint 2 "Next" line is not recorded anywhere; treat it as not done.

## Sprint 3 — week of 2026-10-19

**2026-10-19 → 10-21** — CRC-15 merged (#4): bronze ingest of `blocks`,
`transactions` and `token_transfers` for a fixed 7-day window (2026-10-01..07),
one command (`.\dev.ps1 ingest`). Decisions worth keeping:
- Columns are pruned at ingest, which breaks bronze-as-raw on purpose. ADR 0006
  records why: the source is free and replayable, and the footer-measured saving is
  1,334 → 713 MB/day (-47%), almost all of it `transactions` (`input` calldata is
  540 MB). `token_transfers` saves 5% and is pruned anyway, for one code path.
- A partition is complete only when its `_SUCCESS` JSON marker exists, written
  last. Re-ingest deletes the marker first. The marker carries row count, source
  last-modified and the column list, so a partition can be absent, current or
  stale (re-delivered by AWS, or ingested under older columns).
- Files stream through `iter_batches`; row counts are compared three ways per file
  (source footer, rows streamed, destination footer).
- 38 new tests, 81 in total, no network. Checked by mutating the code: one
  mutation (data deleted before the marker) survived the first test, which
  interrupted the wrong moment, and the test was rewritten.

Found along the way: `value` is `float64` in the public dataset, so wei above
about 0.009 ETH is already rounded at source and cannot be recovered at ingest.
PRIMER corrected; P3 reconciliation must use counts and coverage, not summed
`value`. `BRIEF.md`, `PLANNED.md` and the beginners' guide still say
`DECIMAL(38,0)`, and nothing yet says what P3 reconciles on (BRIEF AC3 says "zero
unexplained variance", the PRIMER glossary says "same totals").
Also found: `dev.ps1` threw away a program's exit code (fixed in CRC-15, since
`ingest` uses 1 = partitions failed, 2 = bucket unreachable), and an edit script
truncated an untracked file (CH-005; CH-004 extended with the recurring "expected
value derived from the thing under test" pattern).

**2026-10-24** — Honest state of CRC-15: it merged with the full-window run
unfinished. Bronze holds 6 of 21 partitions (`blocks` 3 of 7, `transactions` 2 of
7, `token_transfers` 1 of 7); the rest are missing and one run was interrupted
mid-`transactions`. So the 7-day end-to-end result, a second-pass skip count, and
peak memory are all unmeasured; the memory question is recorded in CRC-17's notes.
The squash title was malformed for the third time (`Feature/crc 15 ... (#4)`)
despite a reminder, so a reminder is not a control. A CI story is needed: `make
verify` on every PR plus a Conventional Commits title check. No
`.github/workflows` exists yet.
Next: finish the 21-partition run, CI story, CRC-16.
