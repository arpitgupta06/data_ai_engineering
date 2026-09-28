# CH-002 · CDC and SCD had nowhere honest to live

| | |
|---|---|
| **Phase** | Design (pre-Phase 0) |
| **Jira** | CRC-1 (epic) |
| **ADR** | [0002 — Where CDC and SCD actually live](../adr/0002-where-cdc-and-scd-live.md) |
| **Category** | modeling, governance |
| **Status** | Resolved |
| **Surfaced** | 2026-09-04 |
| **Closed** | 2026-09-04 |

## Context

The platform was required to demonstrate Change Data Capture and Slowly Changing
Dimensions — both standard expectations for a senior data engineering build, and
both things an interviewer will ask about.

The chosen domain was Ethereum blockchain data.

## The problem

Blockchain data is **append-only and immutable by construction**. A transaction
is never updated and never deleted. There is no `updated_at`, no soft delete, no
row that changes. CDC has nothing to capture. SCD has nothing that slowly changes.

Applying either pattern directly to chain facts would have meant inventing
mutation that does not exist — building a mechanism to demonstrate a technique
rather than because the data called for it.

## Why it was hard

The obvious resolutions were both bad. Force the patterns onto chain data and
produce something that any domain-literate reviewer would immediately identify as
contrived. Or drop the requirement and lose two of the most commonly interrogated
skills in the build.

The real difficulty was that the requirement was stated in terms of *techniques*
(CDC, SCD) rather than *outcomes*, and techniques without a problem to solve
always produce cargo cult.

## Options considered

| Option | Pros | Cons | Cost of being wrong |
|---|---|---|---|
| **A. Synthetic CDC feed over chain tables** | Demonstrates the mechanics; quick | Fabricated; teaches the tooling but none of the judgement. Falls apart under questioning | A portfolio piece that discredits the rest of the work |
| **B. Drop CDC and SCD entirely** | Honest | Loses two heavily-interviewed capabilities; requirement unmet | Weaker build, harder interviews |
| **C. Relocate each pattern to data that genuinely has that shape** | Both patterns earn their place; the *why* becomes defensible | Requires introducing an operational database earlier than a pure analytics build needs one | More components to build and run |

## Decision

**Option C.** Each pattern moved to where the data actually behaves that way:

- **SCD Type 2** → `dim_address` (entity labels, risk scores) and `dim_token`
  (symbol and decimals, which change when a proxy contract is upgraded). These
  change slowly and their history materially changes an investigation's answer.
- **CDC** → the operational Postgres behind the copilot: watchlists, alert rules,
  analyst annotations. Real inserts, updates and deletes, streamed via Debezium.
- **Late-arriving data and corrections** → chain **reorgs**, where an accepted
  block is orphaned and its transactions un-happen, plus the
  pending → confirmed → finalized lifecycle.

**What this cost:** an operational Postgres, Debezium, and a whole correctness
phase now exist in the architecture that a pure analytics build would not need.

## How we validated it

The SCD2 requirement was traced to a concrete business question it must answer:
*"what did we know about this address at the time we approved the transaction?"*
That is the question an auditor asks after an incident. A dimension that
overwrites labels cannot answer it; an SCD2 dimension can. The pattern is
therefore justified by the audit requirement, not by the wish to demonstrate SCD2.

The same test was applied to CDC and it failed against chain data — which is what
forced the relocation.

## Outcome

Resolved at design time. ADR 0002 records it, and the acceptance criteria for
Phase 4 now include an as-of query returning the label that was true on a past
date — so the claim is testable rather than asserted.

## What I'd do differently

Nothing about the outcome, but the trigger was luck. I noticed the mismatch
because the domain was one where immutability is famous. In a less obvious domain
— a CRM, a claims system — the same contrivance could pass unnoticed.

The generalisable habit: for each pattern a design mandates, name the specific
business question it answers before implementing it. If you cannot name one, the
pattern is decoration, and decoration in a data platform costs real money to run.

---

## 90-second answer

> The build was expected to demonstrate CDC and slowly changing dimensions. The
> domain was blockchain data — which is append-only and immutable by
> construction. Transactions are never updated or deleted. So CDC had nothing to
> capture and SCD had nothing that slowly changed.
>
> I could have faked it — built a synthetic change feed over the chain tables.
> That demonstrates the tooling, but anyone who knows the domain spots it
> immediately, and it teaches none of the judgement. Or I could have dropped both
> requirements, which is honest but loses two capabilities that come up in nearly
> every interview.
>
> What I did instead was ask, for each pattern, what business question it exists
> to answer. For SCD Type 2 the question is: what did we know about this address
> at the time we approved the transaction? That's what an auditor asks after an
> incident. Address labels do change slowly — an address unlabelled today may be
> attributed to a sanctioned group next month — and if you overwrite the label you
> can't answer the auditor. So SCD2 went onto the address and token dimensions,
> justified by the audit requirement.
>
> CDC went to the operational database behind the product — watchlists and
> analyst annotations — which genuinely has inserts, updates and deletes. And the
> late-arriving-data problem went to chain reorganisations, where a block is
> orphaned and its transactions un-happen. That's a far richer correction problem
> than a synthetic CDC feed.
>
> The habit I took from it: if you can't name the business question a pattern
> answers, the pattern is decoration — and decoration in a data platform costs
> real money to run.
