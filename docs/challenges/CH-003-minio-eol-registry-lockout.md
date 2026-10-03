# CH-003 · A core dependency went EOL mid-sprint and locked us out of its images

| | |
|---|---|
| **Phase** | 0 — Foundations |
| **Jira** | CRC-6 (CRC-9 subtask) |
| **ADR** | [0005 — MinIO community EOL](../adr/0005-minio-community-eol.md) |
| **Category** | ops, security, cost |
| **Status** | Resolved |
| **Surfaced** | 2026-09-29 |
| **Closed** | 2026-10-01 |

## Context

Sprint 1's only committed story was CRC-6: bring up the local data plane —
Redpanda, MinIO and Postgres — with one command. MinIO was the S3-compatible
object store standing in for the bronze layer, chosen back in ADR 0003.

## The problem

`docker compose up -d` failed at the image pull:

```
quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z
  → 401 UNAUTHORIZED
```

Each attempted fix uncovered a wider failure:

| Attempt | Result |
|---|---|
| Switch to Docker Hub `minio/minio:RELEASE.2025-09-07T16-13-09Z` | `pull access denied … repository does not exist` |
| `minio/mc:RELEASE.2025-08-13T08-35-41Z` | `pull access denied` |
| `minio/mc:RELEASE.2025-08-13T08-35-41Z-cpuv1` and `minio/mc:latest` | `denied: requested access to the resource is denied` — repository-level, not tag-level |

The underlying cause: **MinIO's community edition had reached end of life.**
`github.com/minio/minio` was archived on 25 Apr 2026 with the banner "THIS
REPOSITORY IS NO LONGER MAINTAINED"; the community edition moved to source-only
distribution; `minio/mc` on Docker Hub was flagged Archived; and quay.io's MinIO
namespace now serves the commercial, license-gated AIStor product instead.

A component chosen two weeks earlier had become unobtainable.

## Why it was hard

Three things compounded.

**The failure mode was misleading.** `401 UNAUTHORIZED` reads like a credentials
problem. The instinct is to `docker login` or check a proxy — both dead ends.
Registries return 401 and "repository does not exist" for *policy* decisions, so
the error says nothing about the actual cause.

**Documentation outlived reality.** Docker Hub's tag API still listed
`minio/minio:RELEASE.2025-09-07T16-13-09Z` as active with a digest and
architectures, while the registry refused to serve it. Two successive fixes were
pinned from documentation rather than verified against the registry, and both
failed.

**The obvious replacement was worse than the original.** `bitnamilegacy/minio-client`
has a shell and solved the immediate problem — but it lives in Broadcom's frozen
archive namespace: explicitly unmaintained, no security updates, and flagged for
possible withdrawal. It would have swapped an archived image for one inside an
archive *organisation*, tagged `:latest` on a repository that will never change.

## Options considered

| Option | Pros | Cons | Cost of being wrong |
|---|---|---|---|
| **A. `bitnamilegacy/minio-client`** | Has a shell; works immediately | Frozen archive org, no patches, may be withdrawn | Same failure again, later, with less warning |
| **B. MinIO AIStor free tier** | Vendor-supported, actively developed | License file, registry gating, enterprise packaging | Heavy coupling to a commercial product for a throwaway local component |
| **C. LocalStack / SeaweedFS / Garage** | Actively maintained; LocalStack emulates more of AWS | Larger change; new operational surface; no Phase 0 benefit | Sprint time spent learning a tool that Phase 9 deletes |
| **D. Chainguard builds, pinned by digest** | Built from source, free, anonymously pullable, maintained | Only `:latest` published free; digests may be garbage-collected; distroless (no shell) | Reproducibility expires at a time we don't control |
| **E. Drop the client image; create buckets with `boto3`** | Removes the dependency entirely; code is testable; overlaps the Sprint 2 smoke test | `docker compose up` alone no longer creates buckets — changes an acceptance criterion | Mild divergence between "compose up" and "stack ready" |

## Decision

**Option D**, with E retained as the standing fallback.

The distroless objection to Chainguard's client turned out not to be an
objection. `mc` reads aliases from `MC_HOST_<alias>`, and `mc mb` accepts
several targets — so the bootstrap became a single command needing no shell:

```yaml
environment:
  MC_HOST_local: http://${MINIO_ROOT_USER:?…}:${MINIO_ROOT_PASSWORD:?…}@minio:9000
command: ["mb", "--ignore-existing", "local/chainrisk-bronze", "local/chainrisk-artifacts"]
```

**What it cost:** reproducibility with an expiry date outside our control,
credentials visible in `docker compose config`, and a dependency on a third
party continuing to build software its own authors abandoned. All three are
recorded in ADR 0005 with explicit triggers to revisit.

## How we validated it

All nine of CRC-6's acceptance criteria were re-run from a clean `down -v`. The
decisive one was reaching Kafka from the **host** rather than from inside
Docker's network, which returned `brokers: ['localhost:19092']` — proving the
advertised listener address was right, not merely that a connection opened.

## Outcome

The stack comes up healthy from a clean clone: six containers, both buckets
created, `wal_level = logical`, Schema Registry answering, Kafka reachable from
the host. Roughly two of Sprint 1's five committed points were consumed by this
rather than by planned work.

Idempotency was confirmed afterwards, but only by inspecting the volume: `mc`
prints "Bucket created successfully" whether or not the bucket existed, because
`--ignore-existing` suppresses the error rather than the message. The bucket
directories' mtimes were unchanged across two further `up` cycles. A third
instance of the same lesson in one challenge — the tool's claim is not evidence.

## What I'd do differently

**Verify the artifact before pinning it.** Both failed fixes came from reading a
tag off a documentation page or a registry's web UI. One `docker manifest inspect`
would have caught each in seconds. Documentation describes intent; the registry
describes reality, and only one of them is what `docker pull` talks to.

**Read the error for what it is, not what it resembles.** `401` and "repository
does not exist" are the same response a registry gives for a deliberate policy
change. Treating them as auth failures sent the first investigation sideways.

**Check the liveness of a dependency when choosing it, not when it breaks.**
MinIO's archival was two weeks old when it was selected. An archived repository,
a source-only distribution notice, or a vendor pushing a commercial fork are all
visible before adoption — and all cheaper to see then than mid-sprint.

The generalisable form: a stack's real dependency is the **interface**, not the
implementation. This project depends on the S3 API; MinIO is one implementation
and Chainguard's build is one packaging of it. That framing is what made the
decision tractable — and it is the same reasoning behind treating Redpanda as an
implementation of the Kafka protocol rather than as a product choice.

---

## 90-second answer

> Two weeks into a build, the local object store I'd picked stopped being
> installable. `docker compose up` failed with a 401 from the registry, which
> looks like a credentials problem — it wasn't. MinIO had archived its community
> edition, moved to source-only distribution, and pulled its images from both
> Docker Hub and quay.io to push a commercial product instead. The client tool's
> image was gone too.
>
> My first two fixes both failed, and they failed for the same reason: I pinned
> tags I'd read off documentation pages rather than verified against the
> registry. The tag API still listed an image the registry refused to serve.
> That's the habit I changed — now I run `docker manifest inspect` before
> pinning anything.
>
> The tempting fix was a Bitnami image that still had a shell, but that lives in
> their frozen legacy namespace — unmaintained, no security patches, flagged for
> removal. Replacing an archived image with one from an archive org is a worse
> dependency than the one you started with.
>
> What I actually did was step back and ask what the stack depends on. Not
> MinIO — the S3 API. So I moved to Chainguard's build, which is compiled from
> source and still maintained, pinned it by digest because they only publish
> `latest`, and recorded in an ADR that the digest can be garbage-collected, with
> four explicit triggers for revisiting it.
>
> It cost about two of five sprint points. The thing I'd do earlier next time is
> check whether a dependency is still alive at the point I choose it — an
> archived repo is visible before adoption, and much cheaper to see then.
