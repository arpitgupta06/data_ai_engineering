# Challenge log

A running record of the hard problems this project throws up, written while the
problem is fresh and structured so it can be retold in an interview.

Interviewers at architect level rarely ask *what did you build*. They ask **what
went wrong, what you considered, and why you chose what you chose**. The
difference between a weak answer and a strong one is almost never the difficulty
of the problem — it is whether you can name the options you rejected and what
each would have cost you. That is what this log captures.

---

## The rules

1. **Log it when it happens, not at the end.** The options you rejected are
   forgotten within a week. The log is worthless if written retrospectively.
2. **Record what actually happened.** If the outcome has not happened yet, the
   Outcome section says so. Never write an outcome you have not observed —
   an interviewer who probes one fabricated detail will discount everything
   else you said, and they do probe.
3. **A challenge is not a bug.** "Typo in a connection string" is not a
   challenge. It qualifies if it involved a genuine trade-off, a constraint that
   forced a design change, or a decision someone could reasonably have made
   differently.
4. **One challenge, one file.** `CH-NNN-short-slug.md`, numbered in order.
5. **Link it to the work.** Reference the Jira key and the ADR if the challenge
   produced a decision record.

## When to write an entry

Definition of Done includes: *if this task surfaced a non-trivial challenge, log
it before closing the ticket.* The sprint retro asks the same question — anything
that cost more than half a day, or changed the design, gets an entry.

## Index

| ID | Challenge | Phase | Category | Status |
|---|---|---|---|---|
| [CH-001](CH-001-dataset-is-not-real-time.md) | The "real-time" dataset is a nightly batch drop | Design | streaming, requirements | Mitigated |
| [CH-002](CH-002-cdc-scd-on-immutable-data.md) | CDC and SCD had nowhere honest to live | Design | modeling, governance | Resolved |
| [CH-003](CH-003-minio-eol-registry-lockout.md) | A core dependency went EOL mid-sprint and locked us out of its images | 0 | ops, security, cost | Resolved |

**Status:** `Open` (live problem) · `Mitigated` (working around it, residual risk)
· `Resolved` (closed, outcome observed)

**Categories:** `streaming` · `data-quality` · `modeling` · `cost` · `governance`
· `security` · `ai` · `ops` · `requirements` · `stakeholder`

## Planned challenges

[`PLANNED.md`](PLANNED.md) lists the architect-level challenges deliberately
engineered into each upcoming phase — so the hard parts are chosen rather than
stumbled into. The best entries in this log will still be the ones that surprise
us, but the planned ones guarantee a floor.

## Using these in an interview

Every entry ends with a **90-second answer** — the compressed spoken version.
Rehearse that, not the full document. Pick two or three entries per interview
that match the role: a streaming role wants CH-001, a governance-heavy role wants
CH-002.

Structure the spoken answer as: constraint → what was at stake → options → the
call and what it cost → how you knew it worked. Resist explaining the technology;
explain the judgement.
