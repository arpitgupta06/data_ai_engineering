# ADR 0004 — The AI product is a wallet risk & fund-tracing copilot

**Status:** Accepted · 2026-09-04

## Context
The original context defined a stack and a set of concerns but no product. Without
a defined consumer, the warehouse model has no anchor, and governance concerns
(RBAC, guardrails, evaluation) have nothing to be governed *for*. An existing
personal project, DataTalk, already covers text-to-SQL, so a generic analytics
agent would duplicate it.

## Decision
The consumer is an AML-style **wallet risk and fund-tracing copilot**: it traces
funds across hops, scores wallet risk from labels and behaviour, answers
investigative questions, and produces an audit-ready finding with cited
transaction hashes.

## Consequences
- Requires multi-tool orchestration — SQL, graph traversal, and RAG over policy
  documents — which is a meaningfully harder agent design than text-to-SQL.
- Gives SCD2 labels, RBAC, masking, guardrails and evaluation a concrete purpose.
- Groundedness and refusal behaviour become measurable acceptance criteria.
- Mirrors the `Risk_Fraud_Regulatory_Intelligence` brief in `hack2skill2`, so the
  work is portable to that framing.

## Rejected alternatives
- *Whale/anomaly monitoring agent* — a better showcase for streaming specifically,
  but a thinner reasoning problem. Retained as a possible Phase 8 extension.
- *Analytics/market intelligence agent* — overlaps DataTalk.
