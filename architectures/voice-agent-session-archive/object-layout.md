# Object layout: voice agent session archive

One bucket per zone, with purpose prefixes inside each bucket. Lifecycle rules, Object Lock, replication, default encryption and Event Notification rules are configured per bucket, and an application key admits a single prefix, so separate buckets keep each policy and each key simple. There is no per-tenant prefix: the layout assumes a single organization.

Bucket names follow [`infrastructure/b2-provisioning/`](../../infrastructure/b2-provisioning/README.md): `<bucket-prefix>-voice-sessions-<zone>`. The environment variables are the ones the [examples](../../examples/README.md#code-examples) and the [LiveKit Egress template](../../infrastructure/livekit-egress/README.md) read.

| Zone | Bucket | Variable | Object Lock | Lifecycle template |
|---|---|---|---|---|
| Incoming | `<bucket-prefix>-voice-sessions-incoming` | `B2_INCOMING_BUCKET` | No | [`lifecycle-incoming.json`](../../examples/lifecycle-incoming.json) |
| Work | `<bucket-prefix>-voice-sessions-work` | `B2_WORK_BUCKET` | No | [`lifecycle-work.json`](../../examples/lifecycle-work.json) |
| Releases | `<bucket-prefix>-voice-sessions-releases` | `B2_RELEASES_BUCKET` | Optional, governance mode, set at creation | [`lifecycle-releases.json`](../../examples/lifecycle-releases.json) |
| Protected | `<bucket-prefix>-voice-sessions-protected` | `B2_PROTECTED_BUCKET` | Yes, governance default retention | [`lifecycle-protected.json`](../../examples/lifecycle-protected.json) |

All four buckets are private and use SSE-B2 as the default encryption.

## Incoming bucket

```text
sessions/
  <session_id>/
    <time>.ogg                  # LiveKit Egress output (filepath sessions/{room_name}/{time}, room name = session id)
    audio/<participant_id>.wav  # tracks uploaded by an adapter (Pipecat, Twilio, Daily, Vapi)
    transcript.json             # transcript, where the platform or the operator produces one
    events/events.jsonl         # session events: turns, tool calls, end reason
    session-manifest.json       # written last; evaluation.status is pending here
```

- **Writes:** LiveKit Egress, through the key in the Egress request, and the session adapters. Both use a key limited to `sessions/` with `writeFiles` (and `listFiles`, `readFiles` where the writer verifies the upload).
- **Reads:** the evaluator.
- **Events:** the `evaluate-sessions` Event Notification rule on `sessions/` for `b2:ObjectCreated:*` ([template](../../examples/event-notifications.json)). The receiver acts only on `session-manifest.json`.
- **Lifecycle:** objects under `sessions/` are hidden after the example period and deleted the next day; unfinished large uploads are cancelled. The template also carries rules for `contributions/` and `source/`, which this architecture does not use; they match nothing in this bucket. Set the period longer than the time the evaluator and archiver may need, including retries.
- **Paths in the manifest:** recording paths are relative to `sessions/<session_id>/`, so the same manifest stays valid when the session is copied to the protected bucket.

## Work bucket

```text
evaluations/
  <session_id>/
    <evaluator-version>.json    # quality and safety results of one evaluator run
scratch/
  <session_id>/...              # decoded audio and intermediate files of the evaluator
```

- **Writes:** the evaluator, with one key per prefix.
- **Reads:** the evaluator, triage staff through the application, and the selection job.
- **Lifecycle:** `scratch/` is hidden after a short period and then deleted; in `evaluations/`, current versions are kept and superseded versions are deleted after the example period; unfinished large uploads are cancelled ([`lifecycle-work.json`](../../examples/lifecycle-work.json)). The template's `candidates/` rule matches nothing in this bucket.

## Protected bucket

```text
sessions/
  <session_id>/
    <recording files>           # same relative paths as in the incoming bucket
    session-manifest.json       # with the completed evaluation block; written last
consents/
  <consent_id>.json             # consent and license record; a withdrawal is a new version
```

- **Writes:** the evaluator (or a separate archiver) on `sessions/`, only for the `standard`, `extended` and `legal-hold` retention classes; the consent service on `consents/`.
- **Reads:** the selection job, the application that issues presigned GET URLs for retrieval, and auditors (with `readFileRetentions` and `readFileLegalHolds`).
- **Object Lock:** enabled at bucket creation, with governance-mode default retention ([template](../../examples/object-lock.json)). The archiver extends retention per object to the class's period and sets a legal hold for `legal-hold`.
- **Lifecycle:** only cancels unfinished large uploads. A retention job deletes session versions after `retention.delete_after` once their retention has ended, because lifecycle rules cannot delete locked versions and cannot tell classes apart.
- **CORS:** allow `GET` and `HEAD` from the review tool's origin only, when sessions are played in the browser from presigned URLs.
- **Replication:** optional, to a bucket in another region ([template](../../examples/replication.json)). The destination must also use Object Lock, and deletes are not replicated, so a removal must be applied to the replica as well.

## Releases bucket

```text
datasets/
  <dataset>/
    <version>/
      audio/<session_id>/<segment>.wav
      manifest.jsonl            # one line per segment, with session and consent ids
      release-manifest.json     # written last
```

- **Writes:** the selection job, with a key limited to `datasets/`. A `<version>` folder is written once; any change is a new version.
- **Reads:** training and evaluation jobs with read-only, expiring keys.
- **Lifecycle:** current versions are never hidden by lifecycle; hidden versions are deleted after a grace period, which lets a release be hidden and then removed after a consent withdrawal.
- **Object Lock:** optional. Governance mode keeps the ability to remove a release after a withdrawal.

## Keys

`provision.py` creates one key per purpose prefix (for example `voice-sessions-incoming-sessions`) with `listFiles`, `readFiles` and `writeFiles`. Narrow them per component:

| Component | Bucket and prefix | Capabilities |
|---|---|---|
| LiveKit Egress | incoming, `sessions/` | `writeFiles` |
| Session adapter | incoming, `sessions/` | `listFiles`, `readFiles`, `writeFiles` |
| Evaluator | incoming, `sessions/` | `listFiles`, `readFiles` |
| Evaluator | protected, `consents/` | `listFiles`, `readFiles` |
| Evaluator | work, `evaluations/` and `scratch/` (two keys) | `listFiles`, `readFiles`, `writeFiles` |
| Evaluator or archiver | protected, `sessions/` | `listFiles`, `readFiles`, `writeFiles`, `writeFileRetentions`, `writeFileLegalHolds` |
| Retrieval service | protected, `sessions/` | `readFiles` (signs presigned GET URLs) |
| Selection job | protected, `sessions/` and `consents/` (two keys) | `listFiles`, `readFiles` |
| Selection job | releases, `datasets/` | `listFiles`, `readFiles`, `writeFiles` |
| Retention job | protected, `sessions/` | `listFiles`, `deleteFiles`, `readFileRetentions` |
| Training job | releases, `datasets/` | `listFiles`, `readFiles`, with an expiry |
| Consent service | protected, `consents/` | `listFiles`, `readFiles`, `writeFiles` |
| Auditor | protected, `sessions/` and `consents/` (two keys) | `listFiles`, `readFiles`, `readFileRetentions`, `readFileLegalHolds` |

Removing a session after a consent withdrawal, before its retention ends, needs a separate key with `bypassGovernance`, held only by the component that applies withdrawals. A presigned URL carries the capabilities of the key that signed it, limited to one operation on one object.

## Clean up

`python infrastructure/b2-provisioning/provision.py --bucket-prefix <your-prefix> --architecture voice-agent-session-archive --cleanup` prints the commands that remove these keys and buckets. Read the notes in the [provisioning README](../../infrastructure/b2-provisioning/README.md#clean-up) first: deletion is irreversible, and versions under compliance retention or a legal hold cannot be deleted.
