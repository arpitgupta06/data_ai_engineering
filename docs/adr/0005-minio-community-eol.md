# ADR 0005 — MinIO community EOL: Chainguard build, pinned by digest

**Status:** Accepted · 2026-10-01 · Supersedes nothing · Relates to [ADR 0003](0003-local-first-environment.md)

## Context

Phase 0 needs an S3-compatible object store running locally to stand in for the
bronze layer. MinIO was chosen for this in ADR 0003 — small, S3-compatible, and
the de facto local S3 stand-in.

During Phase 0 (CRC-6), MinIO's community edition reached end of life and its
container images stopped being anonymously pullable. Observed directly:

| Reference | Result |
|---|---|
| `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z` | `401 UNAUTHORIZED` |
| `minio/minio:RELEASE.2025-09-07T16-13-09Z` (Docker Hub) | `pull access denied … repository does not exist` |
| `minio/mc:RELEASE.2025-08-13T08-35-41Z-cpuv1` | `denied: requested access to the resource is denied` |
| `minio/mc:latest` | same |

Supporting evidence:

- `github.com/minio/minio` was archived on 25 Apr 2026; its README states
  "THIS REPOSITORY IS NO LONGER MAINTAINED".
- The community edition is now distributed as **source only** — no prebuilt
  binaries. The final community release is `RELEASE.2025-09-07T16-13-09Z`.
- `minio/mc` on Docker Hub is flagged **Archived**; its public tag API returns 404.
- quay.io now serves `quay.io/minio/aistor/minio` — the commercial AIStor
  product, which requires a license file mounted at `/minio.license`.

The stack could not start. This was a hard blocker, not a preference.

## Decision

Use **Chainguard's builds of MinIO and the MinIO client, pinned by digest.**

```yaml
minio:       cgr.dev/chainguard/minio@sha256:…
minio-init:  cgr.dev/chainguard/minio-client@sha256:…
```

Chainguard builds MinIO from source, which — now that the community edition is
source-only — makes them one of the few parties still producing a usable build.

Two secondary decisions follow from the image choice:

1. **Digest pinning, not tags.** Chainguard's free tier publishes only `:latest`
   and rebuilds continuously, so a tag reference would silently change build to
   build. A digest also protects the MinIO healthcheck, which relies on `sh`
   being bash in that image in order to use `/dev/tcp` — an undocumented property
   that a rebuild could remove.
2. **`MC_HOST_<alias>` instead of `mc alias set`.** Chainguard's client image is
   distroless: no shell, `mc` is the entrypoint. Supplying the alias through the
   environment collapses the bucket bootstrap into a single `mc mb` invocation
   with several targets, so no shell is required.

### What this cost

- **Reproducibility has an expiry we do not control.** Free-tier Chainguard
  images may garbage-collect older digests. The pin is correct today and will
  one day stop resolving.
- **Credentials appear in rendered config.** `MC_HOST_local` is interpolated by
  Compose rather than deferred with `$$`, so the value shows in
  `docker compose config` output. Acceptable for a local stack with dev
  credentials; it would not be acceptable anywhere else.
- **A third party now sits between us and an unmaintained upstream.** We depend
  on Chainguard continuing to build a project its own authors have abandoned.

## Options considered

| Option | Why rejected |
|---|---|
| `minio/minio` or `quay.io/minio/minio`, pinned | Not pullable. Not a choice. |
| `bitnamilegacy/minio-client` | Broadcom's frozen archive namespace — explicitly unmaintained, no security updates, may be withdrawn. Replacing an archived image with one from an archive *org* is a worse dependency, and `:latest` on a frozen repo is a contradiction. |
| MinIO AIStor free tier | License file, registry gating, enterprise packaging. Heavy for a local stand-in. |
| LocalStack, SeaweedFS, Garage | All maintained and credible. Rejected for now: a larger change, new operational surface to learn, and no Phase 0 benefit over a working S3 API. Remain the fallback if a trigger below fires. |
| Create buckets with `boto3`, no client image at all | Genuinely attractive — removes the dependency rather than replacing it, and the code overlaps the Sprint 2 smoke test. Rejected only because `MC_HOST_` made the container approach work with one registry and no shell. Documented in CH-003 as the standing fallback. |
| Real AWS S3 dev bucket | Most faithful to production, but breaks the local-first principle of ADR 0003 and costs money. |

## Validation

CRC-6's nine acceptance criteria were re-run against the pinned images from a
clean `down -v`:

- All services reach `healthy`
- Kafka reachable from the host: `brokers: ['localhost:19092']`
- Schema Registry answers on `:18081`
- Both buckets created; MinIO console and S3 API reachable
- `wal_level = logical`
- No credentials in tracked files; every image pinned
- `down -v` removes all three named volumes

Idempotency confirmed 2026-10-03. With both buckets already present on the volume
(directory mtimes `Oct 1 12:13`), two further `up` cycles left those timestamps
unchanged — the buckets were not recreated. `mc` nonetheless printed "Bucket
created successfully" on every run: `--ignore-existing` suppresses the *error*,
not the *output*. The log line is therefore not evidence of what happened; the
filesystem is.

## Consequences

MinIO remains a **local-only** component. Phase 9 promotes the cold path to real
AWS S3, at which point MinIO leaves the architecture entirely. The contract this
project depends on is the S3 API, not MinIO — the same reasoning as ADR 0003's
treatment of Redpanda as an implementation of the Kafka protocol.

## Triggers to revisit

Any one of these invalidates the decision:

1. A fresh clone fails to pull either digest (Chainguard garbage collection).
2. The stack must be exposed beyond localhost, or hold anything sensitive.
3. An S3 feature is needed that the frozen MinIO build does not implement.
4. Chainguard stops building MinIO.

On any trigger: move to LocalStack, or drop the bucket bootstrap to `boto3` and
let Phase 9's real S3 arrive sooner.

## Sources

- `github.com/minio/minio` — repository archived 25 Apr 2026, README banner
- Docker Hub `minio/mc` repository page — marked Archived
- Observed `docker pull` / `docker manifest inspect` output, 2026-09-29 to 2026-10-01
