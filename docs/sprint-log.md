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
- 38 new test cases (34 test functions, expanded by parametrization) in
  `test_bronze_ingest.py`, 81 in total, no network. Checked by mutating the code: one
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
unfinished. *Resolved 2026-10-26, below.* Bronze holds 6 of 21 partitions (`blocks` 3 of 7, `transactions` 2 of
7, `token_transfers` 1 of 7); the rest are missing and one run was interrupted
mid-`transactions`. So the 7-day end-to-end result, a second-pass skip count, and
peak memory are all unmeasured; the memory question is recorded in CRC-17's notes.
The squash title was malformed for the third time (`Feature/crc 15 ... (#4)`)
despite a reminder, so a reminder is not a control. A CI story is needed: `make
verify` on every PR plus a Conventional Commits title check. No
`.github/workflows` exists yet.
Next: finish the 21-partition run, CI story, CRC-16.

**2026-10-26** — CRC-15 evidence completed. The 21-partition run finished (it was
run by hand, so its wall time and peak memory were not taken). Checked read-only
against the source: all 21 partitions have a `_SUCCESS` marker, no data without a
marker, and for every one the marker rows == bronze footer rows == source footer rows
(0 mismatches; written columns equal `COLUMNS[table]`). Window totals: `blocks`
50,218 rows, `transactions` 12,695,803, `token_transfers` 25,762,006. A second
pass skipped all 21 with nothing rewritten: `0 ingested, 21 skipped, 0 failed`,
11.7 s, exit 0.
The recovery path was exercised at real size: `transactions` 2026-10-04 (an empty
file) and `token_transfers` 2026-10-02 (351 MB), both left by interrupted runs with
no marker, are now complete and match the source (1,526,358 and 4,145,489 rows).
I did not watch that run, so what is evidenced is the before and after states, not
the clearing itself.
First-run wall time for the whole window can no longer be obtained, since every run
now skips. Instead, one partition was re-ingested on purpose: `transactions`
2026-10-01 (2,036,041 rows, the largest), marker deleted so it read as absent and
went through the recovery path. Result: 70.3 s wall time (network-bound, so it
varies with the link), process peak working set 557 MiB against a 50 MiB
interpreter baseline, Arrow memory pool peak 358 MiB. Peak is the OS-tracked
counter read in-process (`GetProcessMemoryInfo`), not sampled. The partition
came back with marker rows == bronze footer rows == source footer rows, and the
other 20 still matched (0 mismatches). That is the per-task memory envelope CRC-17
needs; it is from one run on one date.
Next: CI story (CRC-18), CRC-16 profiling; replace the "not measured" comment in
`bronze.py` with this figure (rides along with CRC-16 or CRC-18).
