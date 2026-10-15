# Decision record: batch voice generation and dubbing

Each decision states the context, the choice, the alternatives considered and the consequences. Dates are when the decision was recorded.

## D1. One bucket per zone

- **Date:** 2026-10-15
- **Context:** Sources, candidates, deliveries and consent records need different lifecycle, protection and access, and these are bucket settings; an application key admits a single name prefix.
- **Choice:** Four buckets (incoming, work, releases, protected) with purpose prefixes inside each.
- **Alternatives:** One bucket with zone prefixes. Simpler to create, but candidates and deliveries would share one lifecycle list, Object Lock for consent records would apply to the whole bucket or nothing, and a key could not cover two zones without covering the whole bucket.
- **Consequences:** More buckets to provision; `provision.py` prints them. A small project may collapse zones into one bucket if it states so and accepts the coarser controls.

## D2. Presigned GET as the provider's input, with a lifetime chosen per provider

- **Date:** 2026-10-15
- **Context:** Hosted providers accept a URL to fetch the input. Giving them a B2 key would expose more than one object for longer than one job, and uploading the file to each provider duplicates the transfer in the application.
- **Choice:** The runner signs one presigned GET URL per job, for one object version, immediately before submitting, with a lifetime longer than the provider's queue and download time. The lifetime and, where documented, the provider's download window are recorded in the job manifest. A download failure leads to a new URL and a resubmission.
- **Alternatives:** Uploading the file to the provider (ElevenLabs `file`, AssemblyAI upload endpoint, Deepgram binary body). Avoids URL expiry entirely, at the cost of the application streaming every file. It stays the fallback when a provider's fetch time cannot be bounded.
- **Consequences:** A URL that is too short fails jobs; one that is too long widens the window for a leaked URL. Neither ElevenLabs nor Deepgram documents its fetch time, so the lifetime is a setting to tune from observed failures.

## D3. Application copy for hosted providers

- **Date:** 2026-10-15
- **Context:** The hosted providers return results through their APIs or through short-lived signed URLs; none is configured with a B2 bucket as an output destination.
- **Choice:** The runner downloads each output on completion, checks its size, computes its SHA-256 and writes it to `candidates/<job_id>/` with a scoped key. The catalog marks these integrations as `application-copy`.
- **Alternatives:** Deepgram's documented presigned PUT callback, which writes the transcript straight to an S3 presigned URL. It removes one copy, but it has not been tested with B2, the runner still needs the completion signal to write the job manifest, and Deepgram's retries would rewrite the same object.
- **Consequences:** The runner pays the transfer of each output once. Provider download URLs expire (one hour for ElevenLabs outputs), so the copy runs promptly after completion and re-reads the provider when a URL has expired.

## D4. Callbacks where available, polling as the fallback

- **Date:** 2026-10-15
- **Context:** Jobs take from seconds to many minutes. All three providers document a completion callback or webhook, and all three allow reading the job's status.
- **Choice:** Use the provider's callback or webhook to start collection, verified by signature or configured credentials, and keep a periodic poll of jobs still open after their expected duration.
- **Alternatives:** Polling only. Simpler and needs no public endpoint, but adds delay and requests per job.
- **Consequences:** The completion handler must be idempotent, because ElevenLabs, Deepgram and AssemblyAI may each deliver more than once, and a poll may observe a completion the callback already handled.

## D5. Candidates expire; approved outputs are promoted by copy

- **Date:** 2026-10-15
- **Context:** Generation produces many outputs that are never used, and an approved output must not disappear when candidate storage is cleaned.
- **Choice:** Every output starts in `candidates/`, which a lifecycle rule expires. Approval copies the outputs to `deliveries/<release>/<version>/` in the releases bucket, and the job manifest with its promotion and lineage is written there last.
- **Alternatives:** Approving in place by moving objects between prefixes in the same bucket. Saves a copy but mixes expiring and kept data under one lifecycle and one key.
- **Consequences:** Approved outputs are stored twice until the candidate expires. A delivery never depends on the candidate's lifetime.

## D6. Immutable delivery versions with lineage

- **Date:** 2026-10-15
- **Context:** Recipients and downstream jobs must know exactly which source and which job produced a file, and corrections are common in dubbing.
- **Choice:** A delivery version is written once. The promoted job manifest records the source version's SHA-256, the job id and the hashes of the approved outputs. A correction is a new job and a new delivery version.
- **Alternatives:** Overwriting a `latest/` folder. Simpler for recipients, but previous deliveries cannot be reproduced and partial writes are visible.
- **Consequences:** Storage grows with each version; the releases lifecycle removes hidden versions after a grace period. Recipients resolve a release id instead of a path.

## D7. Short-lived URLs for few recipients, a private CDN for wide distribution

- **Date:** 2026-10-15
- **Context:** Some deliveries go to one reviewer or partner, others are played many times.
- **Choice:** Presigned GET URLs per recipient by default; a CDN worker that signs requests to the private releases bucket when the same files are fetched repeatedly.
- **Alternatives:** A public bucket. Simplest, but every object becomes readable by anyone with its name, and access cannot be revoked per delivery.
- **Consequences:** The CDN adds a worker and its own key, scoped to `deliveries/`; per-user authorization lives in the CDN or the application.

## D8. Per-speaker stems as separate outputs

- **Date:** 2026-10-15
- **Context:** Reviewers judge each dubbed voice separately, and editors remix without regenerating.
- **Choice:** Stems are stored as one file per speaker and language next to the mix and listed in the manifest with `kind: stem` and `speaker_id`.
- **Alternatives:** Keeping only the mix. Less storage, but a problem in one voice forces a full re-dub.
- **Consequences:** Stems exist only where the provider or worker produces them; for ElevenLabs they come from a Dubbing Studio export uploaded by the application.

## D9. Transcription and call analytics in the same pipeline

- **Date:** 2026-10-15
- **Context:** Batch transcription and analysis of recorded calls use the same submit, collect, review and promote steps as dubbing, with transcripts as outputs.
- **Choice:** The job kinds `transcription` and `call-analytics` share the manifest, the layout and the review path. The provider's JSON is stored unchanged beside derived files.
- **Alternatives:** A separate architecture for transcription. More focused, but it would duplicate the layout, keys and promotion rules.
- **Consequences:** Cross-call queries need a database or warehouse loaded from the stored files; B2 does not answer them.
