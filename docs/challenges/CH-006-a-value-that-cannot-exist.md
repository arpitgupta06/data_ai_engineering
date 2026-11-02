# CH-006 · A transfer amount larger than the type that holds it

| | |
|---|---|
| **Phase** | 1 — Cold path |
| **Jira** | CRC-16 |
| **ADR** | none |
| **Category** | data-quality |
| **Status** | Mitigated |
| **Surfaced** | 2026-11-02 |
| **Closed** | — |

## Context

CRC-16 profiles every bronze partition and writes the result to `chainrisk-artifacts`,
so that later phases can tell "the data changed" from "my code changed". The first
profile of the 7-day window was the first time anyone had looked at the whole of
`token_transfers.value`, the transfer amount.

## The problem

The profile's `max_value` for `token_transfers.value` was `1.3579883643756192e+202`.

An ERC-20 amount is a `uint256`, so the largest amount any transfer can carry is
2^256 − 1, about 1.16 × 10^77. A value of 10^202 is not a big transfer. It is a number
the type cannot hold, so it cannot be an amount.

Across the window, 205 of 25,762,006 rows are above 2^256. Another 25,777 are above
1e30, across 552 tokens; those are not impossible, only implausible.

## Why it was hard

**The first reading was wrong in two ways, and both were plausible.**

1. *The units.* The number was first described as "10^184 ETH". `value` is in the
   token's raw base units, and decimals differ per token, so there is no unit in which
   that sentence is true. This is the reason bronze keeps `token_address` next to
   `value` at all.
2. *The cause.* It was then called "deliberately poisoned": a token contract can mint
   any supply, so a spam token explains a huge number. It does not explain a number
   above 2^256, which no contract can emit. The word "adversarial" was reached for
   because it made a better sentence, not because anything showed intent.

**The column is `float64`.** A float prints 1.9e+202 without complaint, so nothing in
the data looks broken. The profile only surfaced it because min and max were recorded
per partition.

**Two different things hide under "too big".** Rows above 2^256 are impossible and
need no judgement to reject. Rows between roughly 1e30 and 2^256 are possible, since
meme tokens do mint trillions of units, and no bound derived from the data separates
them from legitimate supply. They need different remedies.

## What the raw logs showed

The first hypothesis was an ERC-721 mix-up: ERC-20 and ERC-721 share the event
signature, and an ETL that treats every `Transfer` as a token transfer would write a
`tokenId` into `value`. It was tested against the source's `logs` table, the raw
events these rows were decoded from, by reading one offending event per token.

It was wrong. All five events have the standard `Transfer` topic0 but not the standard
layout. Instead of three topics and one 32-byte data word, they have one topic and
96 bytes of data (one has two topics and 64 bytes): `from`, `to` and the amount sit in
`data`, because the contract declares them non-indexed. The signature hash does not
include whether a parameter is indexed, so these events look like transfers to anything
filtering on topic0.

The stored `value` equals the **whole `data` field read as one integer**, not its last
32-byte word. For four of the five events the leading data word times 2^512 (or 2^256
for the two-topic event) reproduces the stored `value` exactly, to float64 precision:

| Token | Leading data word × shift | Stored `value` |
|---|---|---|
| `0xda9f4301…d80a` | 0xfe850577…da92 × 2^512 | 1.9482217072326007e+202 |
| `0x06012c8c…266d` | 0xfa893e03…6d90 × 2^512 | 1.9177298924529765e+202 |
| `0xf7a6e15d…f643` | 0xd66bb05f…e074 × 2^512 | 1.6412840511393435e+202 |
| `0x30774174…dd8f` (2 topics) | 0xdac17f95…1ec7 × 2^256 | 1.4460986235339944e+125 |

The fifth (`0x0b6ce2e4…34bc`) has a zero leading word, so it is consistent with the
same mechanism but not an exact check.

That fits what the profile showed: the extremes repeat (34 distinct values across 197
rows of one contract) and the rows concentrate in a handful of tokens, which looks like
a systematic decoding fault and not scattered corruption. Whether the repeats share a
sender was not checked.

## Options considered

| Option | Pros | Cons | Cost of being wrong |
|---|---|---|---|
| Drop the rows at bronze | Clean data from the first layer | Bronze stops matching the source; ADR 0006 prunes columns, it does not edit values | Silent loss; nobody can ask what was dropped |
| Recompute `value` from the raw `logs` | Gives the true amount | Needs the source logs table; even with a block filter one event took 2 to 3 minutes to find | Large, slow ingest for 205 rows |
| Keep bronze faithful, count the rows in the profile, quarantine in silver | Bronze stays a copy; the count is a baseline that moves if the source is fixed; the rule is mechanical | Silver rule is not built yet; the true amount is lost for those rows | Until silver exists, a sum over `value` is wrong |
| A per-token sanity bound | Would also catch the 1e30 tail | Needs per-token decimals and a policy; nothing in the data supports a number | A wrong bound either hides real transfers or lets spam through |

## Decision

Keep bronze faithful. Record the count in the profile, driven by a `BOUNDS` mapping of
column name to allowed range (`value` → 0 … 2^256), so another hard limit is a data
entry and not a schema change. Silver is to quarantine `value > 2^256` as undecodable,
a rule needing no per-token judgement. The 1e30 tail is a separate policy question
for silver and is not decided here.

What we gave up: the real amount for those 205 rows, which only the raw logs hold.

## How we validated it

- The 2^256 ceiling and the "whole data field" mechanism were checked against five raw
  events, one per affected token, by recomputing the stored value from the log's data.
  Four match exactly; one is consistent but unverified.
- `out_of_range_count` has unit tests, including that the ceiling itself is allowed and
  that rows are counted, never dropped or changed. A mutation of the bound fails a test.
- Not checked: the other 200 rows individually (they are assumed to share the
  mechanism, on the strength of the five); the 552 tokens in the 1e30 tail; and whether
  the fault lies in the dataset's ETL or only in how it handles this event layout.

## Outcome

The 205 rows are counted per partition in the profile. Silver does not exist yet, so
nothing quarantines them: any sum or "largest transfer" query over `value` is wrong
until it does. Not yet reported to the dataset's maintainers, so there is no response
to record.

## What I'd do differently

Read `value` as a raw base-unit integer in the first sentence, not afterwards. The
unit error came from restating a number without asking what it was measured in, and the
"deliberately poisoned" framing came from preferring the more dramatic of two
explanations. The useful step was neither: it was noticing that the number exceeds the
type, which turned an argument about plausibility into a fact.

The wider lesson is that a column typed `float64` hides exactly the evidence that
would have exposed this. The profile recorded a minimum and a maximum, and that was
enough.

## Found on the way

Two more properties of the source, noticed while querying and not yet investigated
beyond a count. Neither is part of this challenge's decision.

- Every `token_transfers.from_address` and `to_address` is the 66-character, 32-byte
  padded form (`0x000…fe85…`), whereas `transactions` addresses are 42 characters.
  A join between the two matches nothing until one side is normalised.
- `transactions.to_address` is the literal string `'None'` on all 11,535
  contract-creation transactions, not a null. `null_count` and `empty_count` both read
  zero for it. Together with the empty string in `receipt_contract_address`, that makes
  two different spellings of "no value" in one table.

---

## 90-second answer

> I was building a profile of the data I had landed: row counts, null rates, and the
> minimum and maximum of each numeric column. The maximum transfer amount in one table
> was a number with 202 digits.
>
> My first explanations were wrong. I described it in the wrong units, and then I called
> it adversarial, a spam token minting a huge supply. Neither held up. The useful
> observation was that an amount in this system is a 256-bit integer, so there is a hard
> ceiling about 77 digits long, and 205 rows were above it. That meant they were not
> large, they were impossible, and an impossible value needs no judgement to reject.
>
> I then checked the raw event logs for one example per token. My second theory, an
> NFT-versus-token mix-up, was also wrong. The real cause was that those contracts
> declare the sender and receiver as non-indexed, so they sit in the data field, and the
> source had read the whole data field as the amount instead of its last word. I
> confirmed it by recomputing the stored number from the log, which matched exactly for
> four of the five.
>
> The decision was to keep the raw layer faithful, count the impossible rows in the
> profile with a configurable range so the next hard limit is a config entry, and
> quarantine them downstream. I could not responsibly claim more than the five events I
> had looked at, so the record says that, and says what is still open: the downstream
> rule does not exist yet, and I have not reported it to the dataset owners.
