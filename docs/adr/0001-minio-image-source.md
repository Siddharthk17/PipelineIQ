# ADR-0001: MinIO container image source

Date: 2026-11-04
Status: accepted

## Context

`docker-compose.yml` and CI both pull a MinIO S3-compatible server image.
In sequence, both previously obvious sources became unusable from automated
environments:

1. Docker Hub `minio/minio` — repository returns **404** (MinIO removed it
   from Hub); no tag can ever pull again.
2. `quay.io/minio/minio` — returns **401 for anonymous pulls**, which is
   exactly what GitHub Actions runners and fresh VMs do.

Each breakage killed the Docker Compose Smoke Test and Streaming E2E jobs
at image-pull time, with no application code involved.

## Decision

Pull MinIO from `pgsty/minio:${MINIO_IMAGE_TAG:-latest}` (default `latest`).
`pgsty/minio` is a repackaged MinIO mirror that exists on Docker Hub with a
working `latest` tag, and it is the same image already proven healthy on the
GCP deployment — so local, GCP, and CI converge on one source.

The tag stays operator-overridable via `MINIO_IMAGE_TAG` for pinning.

## Consequences

- Compose smoke + streaming E2E jobs pull reliably again.
- New dependency on an obscure third-party mirror. Mitigations: the tag is
  overridable, Dependabot's Docker updates cover `docker-compose.yml`, and
  the Trivy filesystem scan watches the repo. If `pgsty/minio` ever goes
  stale or disappears, switch the default tag (or source) in one line and
  record it here as ADR-0002.
- `k8s/minio/deployment.yaml` still references the dead `minio/minio` pin;
  it must be updated to `pgsty/minio` before the next Civo pull-through.
