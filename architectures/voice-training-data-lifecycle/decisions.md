# Decision record: voice training data lifecycle

Each decision states the context, the choice, the alternatives considered and the consequences. Dates are when the decision was recorded.

## D1. One bucket per zone

- **Date:** 2026-10-15
- **Context:** Lifecycle rules, Object Lock, replication, default encryption and Event Notification rules are bucket settings, and an application key admits a single name prefix.
- **Choice:** Four buckets (incoming, work, releases, protected) with purpose prefixes inside each.
- **Alternatives:** One bucket with zone prefixes. Simpler to create, but Object Lock would apply to the whole bucket or nothing, lifecycle rules would share one list of at most 100 rules, and a key could not cover two zones without covering the whole bucket.
- **Consequences:** More buckets to provision; `provision.py` prints them. A small project may collapse zones into one bucket if it states so and accepts the coarser controls.

## D2. Consent recorded before upload, as an immutable record

- **Date:** 2026-10-15
- **Context:** A clip without a provable consent cannot be used, and consent can be withdrawn later.
- **Choice:** The consent service writes the record to the protected bucket, under Object Lock in governance mode, before issuing any upload URL. A withdrawal is a new version of the record, never an edit.
- **Alternatives:** Consent fields only inside the upload sidecar. Cheaper, but the sidecar expires with the incoming zone and can be overwritten.
- **Consequences:** Every clip, line and release points to a `consent_id` that can be audited. Governance mode allows an administrator with `bypassGovernance` to correct a mistaken record; compliance mode would not.

## D3. Validation decides once and writes the decision

- **Date:** 2026-10-15
- **Context:** Event Notifications are delivered at least once, and the webhook must answer quickly.
- **Choice:** The webhook verifies the signature, records the `eventId` and queues the work outside B2. The worker writes accepted clips to `candidates/` and rejected ones, with defect codes, to `evaluations/validation/`.
- **Alternatives:** Polling the incoming prefix. Works without Event Notifications access but adds delay and list operations.
- **Consequences:** Needs Event Notifications enabled for the account and a queue outside B2. Polling stays a valid fallback.

## D4. Releases are immutable folders with a manifest written last

- **Date:** 2026-10-15
- **Context:** Training runs must name exactly the data they used, and readers must not see a half-written release.
- **Choice:** `datasets/<dataset>/<version>/` is written once, and `release-manifest.json` is the last object. It lists every file with size and SHA-256 and a content fingerprint over the sorted file list.
- **Alternatives:** Overwriting a `latest/` folder. Simpler for readers, but results cannot be reproduced and partial writes are visible.
- **Consequences:** Storage grows with each version; the releases lifecycle removes hidden versions after a grace period. Readers resolve a release id instead of a path.

## D5. Several shard formats, one manifest

- **Date:** 2026-10-15
- **Context:** Speech training code uses different loaders: NeMo manifests, Lhotse Shar, WebDataset tars, MosaicML Streaming MDS shards and Hugging Face Datasets.
- **Choice:** The NeMo JSONL manifest is the source of truth for clip metadata. The builder derives the shard formats the consumers need from it, and the release manifest lists them all.
- **Alternatives:** One shard format for every consumer. Less storage, but forces loaders to convert or read through adapters.
- **Consequences:** Storage for each extra format. Produce only the formats that are read.

## D6. Read through a local cache; Overdrive only for sustained reads

- **Date:** 2026-10-15
- **Context:** Training reads are sequential within shards but repeated across epochs, and GPUs stall on slow random reads.
- **Choice:** Jobs copy or stream shards into local NVMe or a cluster cache; B2 stays the durable source. B2 Overdrive is considered only when sustained aggregate read throughput is the measured bottleneck.
- **Alternatives:** Reading every sample straight from B2 on every step; or using Overdrive by default. The first is slower; the second fits only large, sustained read workloads and comes with a storage commitment.
- **Consequences:** Jobs need local disk sized for the working set and must invalidate the cache by release id, not by timestamp.

## D7. Label Studio with separate source and target connections

- **Date:** 2026-10-15
- **Context:** Reviewers need to listen to candidates and correct transcripts; a key admits one prefix.
- **Choice:** Source storage on `candidates/` with a read-only key and pre-signed URLs; target storage on `evaluations/review/` with a write key.
- **Alternatives:** One key for the whole work bucket. Simpler, but Label Studio could then overwrite candidates or delete evaluations.
- **Consequences:** The work bucket needs a CORS rule for the Label Studio origin when pre-signed URLs are on. Turning pre-signed URLs off removes that need, and the audio then flows through the Label Studio server.

## D8. Releases zone not locked by default

- **Date:** 2026-10-15
- **Context:** A consent withdrawal may require removing a release, while released data also needs protection from accidental deletion.
- **Choice:** No Object Lock on the releases bucket by default; protection comes from scoped keys without `deleteFiles` and from versioning. Where a contract requires immutability, enable governance mode at bucket creation.
- **Alternatives:** Compliance-mode lock on every release. Strongest protection, but nobody can remove a release before retention ends, even after a withdrawal.
- **Consequences:** Removing a release is possible with an administrative key. If replication is used, the removal must be repeated on the replica, because deletes and hide markers are not replicated.

## D9. MLflow artifacts in the work bucket, DVC remote in the releases bucket

- **Date:** 2026-10-15
- **Context:** Experiment artifacts are many and change often; DVC data is content-addressed and pinned by Git commits.
- **Choice:** MLflow artifact root at `evaluations/mlflow/` in the work bucket; DVC remote at `datasets/dvc/` in the releases bucket, beside but separate from the release folders.
- **Alternatives:** A fifth bucket for experiment artifacts. Cleaner separation of policies, at the cost of one more bucket and key set.
- **Consequences:** Superseded MLflow artifact versions follow the `evaluations/` lifecycle. Model artifacts that must be kept long term should be promoted to their own release or registry.
