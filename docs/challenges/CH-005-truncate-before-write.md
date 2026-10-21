# CH-005 · A script truncated an untracked file before it wrote

| | |
|---|---|
| **Phase** | 1 — Cold path |
| **Jira** | CRC-15 |
| **ADR** | none |
| **Category** | ops |
| **Status** | Mitigated |
| **Surfaced** | 2026-10-20 |
| **Closed** | — |

## Context

Mid-way through CRC-15 the bronze ingest module, `src/chainrisk/ingest/bronze.py`,
held the column selections, the two S3 clients and the schema-drift check. It had
been created and edited across the session but not yet committed. The task at hand
was adding the `_SUCCESS` marker helpers.

## The problem

An edit script rewrote the file with Python's `open(path, "w")`. The new text
contained box-drawing characters (`──`) in a comment. On Windows the default text
encoding is cp1252, which cannot encode them, so the script died with:

```
UnicodeEncodeError: 'charmap' codec can't encode characters in position 4775-4776
```

The file was already empty: 0 bytes afterwards. It was untracked,
so Git had no copy to restore.

## Why it was hard

**The failure order is the trap.** `open(path, "w")` truncates the file at the moment
it opens, before a single byte is written. The encode error came after. So the
operation that failed was the write, but the damage was done by the open. The
traceback pointed at the encoding, and the file looked like a casualty of that.

**The follow-up tools reported success.** The same command went on to run `ruff` and
`mypy`, which passed, because an empty module is valid Python. Nothing in the output
suggested the file was gone until its size was checked.

**Recovery depended on luck.** The full file content happened to be in the working
session, so it could be rewritten. With no such copy, the file would have been lost.

## Options considered

| Option | Pros | Cons | Cost of being wrong |
|---|---|---|---|
| Commit early and often | Gives every file a recovery point; fixes the whole class of loss | Needs discipline; early commits are rough | Without it, the next truncation loses real work |
| Always pass `encoding="utf-8"` | Removes this specific trigger | Does not help with any other failure mid-write | A different mid-write failure truncates the file the same way |
| Write to a temp file, then `os.replace` | Atomic on one filesystem; a failure leaves the original untouched | More code per edit | Small; it only matters when something fails |
| Use the editor's Write/Edit tools instead of ad hoc scripts | Already encoding-safe | Not always practical for bulk edits | None |

## Decision

Treat the three fixes as layers, in order of value: commit early, pass an explicit
encoding, and write via a temp file with `os.replace`. Edits to source files go
through the editor tools where possible.

What we gave up: speed. The one-line script was the quickest way to make the edit,
and each safeguard adds a step.

## How we validated it

Partly. After the incident the file was rewritten and checked with `ruff`,
`mypy --strict` and a round-trip of the marker helpers against a real local
filesystem. Subsequent edit scripts passed `encoding="utf-8"`. The commit-early
practice has not been applied yet: the file is still untracked.

## Outcome

The file was recovered by rewriting it from the session. No work was lost. The
safeguards are in place only for the encoding; committing early and temp-file
writes are still to be adopted, which is why the status is Mitigated.

## What I'd do differently

Commit the file before running anything that rewrites it. An untracked file has no
safety net at all, and that single habit would have made the other two fixes
irrelevant for this incident.

The deeper lesson is that the pipeline and the tooling were held to different
standards in the same session. The ingest design makes a partition either complete
or absent by writing the data first and the `_SUCCESS` marker last, so a failure
never leaves a half-state visible. The edit script did the opposite: it destroyed the
original before the replacement existed. The principle was already understood; it
needed applying to the tooling as well.

---

## 90-second answer

> We were building an ingest pipeline whose central rule is that a partition is
> either complete or absent. You write the data, then a marker file last, so a crash
> can never leave something that looks finished but is half there.
>
> In the same session I lost a source file to exactly the failure that rule prevents.
> A script opened the file for writing, which empties it immediately, and then crashed
> on a character the Windows default encoding could not handle. The file was empty,
> it was not yet committed, so Git could not restore it. The linters passed, because
> an empty file is valid Python. I only found out by checking its size.
>
> I rebuilt it from the session, so nothing was lost, but that was luck. The fixes, in
> order of value: commit early so every file has a recovery point, always state the
> encoding explicitly on Windows, and write to a temporary file and swap it in
> atomically so a failure leaves the original alone.
>
> The third is the same idea as the marker: do the risky part somewhere nobody is
> reading, and commit with a single atomic step. I applied it to the pipeline and not
> to my own tooling, which is the part I would change.
