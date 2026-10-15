# Decision record: voice agent session archive

Each decision states the context, the choice, the alternatives considered and the consequences. Dates are when the decision was recorded.

## D1. One bucket per zone

- **Date:** 2026-10-15
- **Context:** Fresh captures, evaluation results, retained sessions, consent records and dataset releases need different lifecycle, protection and access, and these are bucket settings; an application key admits a single name prefix.
- **Choice:** Four buckets (incoming, work, releases, protected) with purpose prefixes inside each. `sessions/` exists in both the incoming and the protected bucket: the first expires, the second is retained under Object Lock.
- **Alternatives:** One bucket with zone prefixes. Simpler to create, but Object Lock for retained sessions would apply to every capture, and lifecycle for fresh captures would share one rule list with retained sessions.
- **Consequences:** More buckets to provision; `provision.py` prints them. A small project may collapse zones into one bucket if it states so and accepts the coarser controls.

## D2. LiveKit Egress writes to B2 directly; other platforms go through an adapter

- **Date:** 2026-10-15
- **Context:** LiveKit Egress documents an S3 output with a custom endpoint and lists Backblaze as S3-compatible. Twilio and Daily document custom storage only on AWS S3 (Daily also OCI by request), Vapi documents a fixed list of storage providers that does not include B2, and Pipecat documents no storage upload at all.
- **Choice:** LiveKit Egress is the main path, with the request template in `infrastructure/livekit-egress/`. For Pipecat, Twilio, Daily and Vapi, a session adapter receives the completion signal, downloads the recording through the platform's API and uploads it to B2. The catalog marks Egress as `native-s3-output` and the others as `application-copy`.
- **Alternatives:** Letting Twilio or Daily write to an AWS S3 bucket and replicating from there. It adds a second cloud account and a second copy, and the copy to B2 still has to be built.
- **Consequences:** The adapter pays the transfer of each recording once and holds the platform's API credentials. The repository ships notes for these adapters, not code.

## D3. Session manifest written last, at the end of the session

- **Date:** 2026-10-15
- **Context:** Recordings, tracks, transcripts and event logs arrive at different times, and Event Notifications fire for every object.
- **Choice:** The adapter or the `egress_ended` handler writes `session-manifest.json` only after every file it lists exists and has been verified. The webhook receiver queues evaluation only for manifest objects.
- **Alternatives:** Starting evaluation on the first recording object. It would evaluate incomplete sessions and need a way to wait for the rest.
- **Consequences:** A session without a manifest is incomplete; the incoming lifecycle removes it. If a platform's completion signal is lost, the adapter's periodic check of open sessions writes the manifest late or reports the session as incomplete.

## D4. Evaluation before any selection

- **Date:** 2026-10-15
- **Context:** Sessions can be unusable (silent, clipped, truncated) or unsafe to reuse (no active consent, sensitive identifiers, content that violates policy). Once a session reaches reviewers, analytics or a training set, it is hard to take back.
- **Choice:** Every session gets an evaluation with quality and safety groups. Only `passed` sessions can carry uses in `eligible_for`, and only uses that consent permits. The schema enforces the status rules, and the selection job re-checks consent at selection time.
- **Alternatives:** Filtering at selection time only. Each consumer would need its own copy of the rules, and nothing would record why a session was admitted.
- **Consequences:** Evaluation adds a step and a component before sessions are usable. Results are kept per evaluator version, so a rule change is a re-evaluation with a new result, not an edit.

## D5. Retention by class, enforced in the protected zone

- **Date:** 2026-10-15
- **Context:** Some sessions are needed only briefly, others for a contractual or regulatory period, and some are under legal hold.
- **Choice:** `short` sessions stay only in the incoming bucket and expire with it. `standard` and `extended` sessions are copied to the protected bucket with Object Lock retention set per object for the class; `legal-hold` sessions also get a legal hold. A retention job deletes versions after `delete_after` once retention has ended.
- **Alternatives:** A lifecycle rule per class on the protected bucket. Lifecycle rules match by prefix, not by class, and cannot delete versions under retention.
- **Consequences:** Deletion in the protected zone is an explicit job, not a lifecycle rule. Governance mode keeps the ability to remove a session after a consent withdrawal with a key that has `bypassGovernance`.

## D6. Presigned retrieval through the application

- **Date:** 2026-10-15
- **Context:** Support staff, reviewers and auditors need to listen to individual sessions; sessions are personal data.
- **Choice:** The application authorizes each person, records the access and issues short-lived presigned GET URLs for one session's files. No reviewer tool holds a B2 key, and no bucket is public.
- **Alternatives:** A read key in the review tool. Simpler, but any user of the tool could read every session.
- **Consequences:** Access control and audit live in the application. CORS on the protected bucket allows only the review tool's origin.

## D7. Selection into datasets conditioned on consent

- **Date:** 2026-10-15
- **Context:** Sessions are a source of training data, but consent can be withdrawn or expire after the session was evaluated.
- **Choice:** The selection job takes only `passed` sessions with `model-improvement` in `eligible_for`, reads the latest consent version again, and builds versioned dataset releases that list the session and consent ids of every file, or hands the selected material to the voice training data lifecycle as contributions.
- **Alternatives:** Copying all evaluated sessions into a training bucket. It would bypass the consent check at the moment of use and make withdrawal hard to apply.
- **Consequences:** A withdrawal is applied by excluding the speaker from later releases and hiding affected releases; models already trained are outside storage.
