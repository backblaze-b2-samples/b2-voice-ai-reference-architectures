---
name: b2-voice-speech-ai
description: "Decide where Backblaze B2 belongs in voice and speech AI systems and how to lay it out: speech training datasets, batch voice generation and dubbing, voice agent session recordings, call analytics. Use when the user mentions B2 or Backblaze together with voice agents, LiveKit, Pipecat, Twilio, Daily, Vapi, ElevenLabs, Deepgram, AssemblyAI, ASR or TTS training data, recordings, transcripts, consent, or dataset releases. Keeps B2 out of the real-time audio path and chooses zones, prefixes, lifecycle, retention and consent handling."
compatibility: "Works with any agent that can read Markdown files. Optional: the b2 command line tool, version 4 or later, and Python 3.10 or later to run the repository validators. Delegates B2 basics to the shared b2-cloud-storage skill when it is installed."
allowed-tools: "Read Grep Glob Bash(b2:*) Bash(python3:*) Bash(python:*)"
---

# B2 for voice and speech AI

This skill decides where Backblaze B2 belongs in a voice or speech AI design and how to lay it out. It adds the voice-specific decisions on top of the B2 basics. It does not replace the Backblaze documentation: product behavior and limits are authoritative at <https://www.backblaze.com/docs/cloud-storage>, and prices at <https://www.backblaze.com/cloud-storage/pricing>. Never quote a price or an egress allowance; link the pricing page.

Paths below are relative to the root of the `b2-voice-ai-reference-architectures` repository. When this skill is installed on its own, read the same files from that repository.

## What this skill covers and what it delegates

**Delegate to the shared B2 skill (`b2-cloud-storage`)** when it is installed:

- deriving the S3-compatible endpoint from a bucket's region;
- creating application keys and choosing their capabilities;
- signing presigned URLs;
- listing, auditing and cleaning up buckets;
- reading or changing lifecycle rules on a live bucket.

When the shared skill is not installed, use the B2 documentation and the [capability map](../docs/b2-capabilities.md), and the working examples in `examples/python/` and `examples/typescript/`. In every case: the endpoint is `https://s3.<region>.backblazeb2.com`, built from `B2_REGION`; credentials and bucket names come from the environment (`B2_APPLICATION_KEY_ID`, `B2_APPLICATION_KEY`, `B2_REGION`, `B2_INCOMING_BUCKET`, `B2_WORK_BUCKET`, `B2_RELEASES_BUCKET`, `B2_PROTECTED_BUCKET`); nothing is hardcoded or committed.

**This skill decides:**

1. whether B2 belongs in the design at all (never in the real-time audio path);
2. which architecture applies;
3. zones and prefixes;
4. lifecycle, retention and Object Lock;
5. consent handling;
6. which boundary applies when a requirement does not fit B2;
7. standard B2 or B2 Overdrive.

## Decision procedure

Work through these in order and state the result of each in the answer.

### 1. Is the data needed during a live conversation?

If audio, state or a lookup is needed within a turn of a live call, it does not go through B2. The voice platform (LiveKit, Pipecat, Twilio, Daily, Vapi or similar) carries the audio; the agent's memory, a cache or an application database carries state. B2 receives the finished recording afterwards. Say this explicitly whenever a design mentions a live agent. See [boundaries](references/boundaries.md).

### 2. Which architecture applies?

| The user is working on | Architecture | Read |
|---|---|---|
| Collecting, validating, reviewing and releasing speech training data | `voice-training-data-lifecycle` | [architecture](../architectures/voice-training-data-lifecycle/architecture.md) |
| Dubbing, speech synthesis, voice conversion, batch transcription, call analytics with a provider or a self-hosted model | `batch-voice-generation-and-dubbing` | [architecture](../architectures/batch-voice-generation-and-dubbing/architecture.md) |
| Keeping, evaluating and reusing recordings of voice agent sessions | `voice-agent-session-archive` | [architecture](../architectures/voice-agent-session-archive/architecture.md) |

The list of all three with integrations is in [references/architecture-catalog.md](references/architecture-catalog.md) and in the [category page](../docs/voice-speech-ai.md). If a workflow combines two (for example sessions that become training data), use both and connect them through the selection step described in the session archive.

### 3. Zones and prefixes

Use one bucket per zone, with purpose prefixes inside it. Lifecycle, Object Lock, replication, encryption defaults and notification rules are per bucket, and an application key admits one prefix. Details: [zones, lifecycle and keys](references/zones-lifecycle-keys.md).

| Zone | Variable | Prefixes |
|---|---|---|
| Incoming | `B2_INCOMING_BUCKET` | `contributions/`, `sessions/`, `source/` |
| Work | `B2_WORK_BUCKET` | `candidates/`, `scratch/`, `evaluations/` |
| Releases | `B2_RELEASES_BUCKET` | `datasets/`, `deliveries/` |
| Protected | `B2_PROTECTED_BUCKET` | `consents/`, `sessions/` |

A small deployment may use one bucket with these prefixes and must then scope every rule by prefix; say so. Do not add per-tenant prefixes: isolation between tenants is not covered by these layouts, and a design that needs it needs separate buckets and keys per tenant, which is outside this material.

### 4. Lifecycle and retention

- Incoming and work data expire by lifecycle after processing. Choose the period longer than the longest expected processing and review time, and from the user's retention policy; day counts in the templates are examples.
- Releases and deliveries are immutable per version. Write each version once, write its manifest last, and never rewrite it.
- Consent records and retained sessions go in the protected bucket under Object Lock. Lifecycle cannot delete a locked version, so a retention job deletes after retention ends.
- Applying a lifecycle configuration replaces all of a bucket's rules. Always read and merge the current rules first, and say so before giving a command.
- Cancel unfinished multipart uploads with a lifecycle rule in every zone.

### 5. Consent

- Check consent before processing, generating or selecting voice data. Speech is personal data.
- Every clip or session carries a consent id. Speaker ids in object names and metadata are pseudonymous; object names, bucket names and metadata are not encrypted and never carry names, emails or phone numbers.
- A withdrawal is a new version of the consent record. Selection into a dataset reads the current consent again.
- Storage can exclude a withdrawn speaker from later releases. It cannot remove what a trained model already learned; do not imply otherwise.
- Voice agent sessions pass a quality and safety evaluation before they can be selected for review, analytics or model improvement.

See [consent and retention](references/consent-and-retention.md).

### 6. Which boundary applies?

When a requirement does not fit B2, name the boundary and what does that job: real-time voice transport, conversational session store, message queue, transcription or generation model, workflow orchestrator, metadata catalog, vector database, local GPU cache or high-throughput training storage. The table is in [references/boundaries.md](references/boundaries.md).

### 7. Standard B2 or Overdrive?

Standard B2 for every zone and every workload by default. B2 Overdrive only for sustained high-throughput reads, such as repeated full-dataset passes across a GPU cluster where aggregate read throughput is the measured constraint. Training jobs still read through a local cache. Do not offer Overdrive for recordings, generated audio, archives, small datasets or bursty reads.

### 8. Presigned URLs, keys and providers

- Hosted providers (ElevenLabs, Deepgram, AssemblyAI) get a presigned GET URL for one source object and never a key. Set the lifetime longer than the provider's download window; some providers do not document when they fetch, so say what is unverified. The application writes the result to B2.
- Twilio, Daily and Vapi do not write to B2: an adapter downloads the recording and uploads it to B2. LiveKit Egress writes to B2 itself through its S3 output with the B2 endpoint and `force_path_style`.
- Browsers upload with presigned PUT and, for large files, multipart with resume; the bucket needs CORS for the origin and the `ETag` header exposed. Presigned POST is not supported.
- One application key per component, limited to one bucket and one prefix, with the narrowest capabilities. No master or administrative key in a runtime service.
- Presigned URLs and delivery manifests are bearer credentials: short lifetimes, never logged.

## Answer format

When recommending a design, state in this order: where B2 is and is not in the flow; the architecture; the zones and prefixes; lifecycle and retention; consent handling; the evidence basis.

State the evidence basis. All three architectures are `source-reviewed-and-automated-tests`: they rest on provider and B2 documentation read on the dates in each architecture's Evidence section and on application tests that run in CI against an S3-compatible emulator. Each application's opt-in live check was run once on 2026-10-16 against a real B2 test bucket and covers storage operations only (authentication and endpoint derivation, put, get, head, list, presigned PUT and GET, server-side copy, manifest write order, delete, multipart resume, presigned URL expiry). On 2026-10-26 one manual walkthrough of each application's commands (not `tests/test_live.py`) was run against a real B2 test bucket with Object Lock and SSE-B2: `voice-dataset-ingest` through `release` with per-object GOVERNANCE retention, `voice-generation-artifacts` through `submit`, `promote` and `deliver`, and `voice-session-archive` through `register-session`, `handle-webhook`, `serve` and `evaluate`. They are one-off manual runs, not repeatable tests. Bucket configuration, Event Notification delivery, LiveKit Egress and the named providers are outside the scope of the live runs. Do not describe them as production-proven or as tested end to end.

## References

- [Boundaries](references/boundaries.md): the eight things B2 is not, and what does each job.
- [Zones, lifecycle and keys](references/zones-lifecycle-keys.md): buckets, prefixes, lifecycle templates, Object Lock and scoped keys.
- [Consent and retention](references/consent-and-retention.md): consent records, withdrawal, retention classes and session evaluation.
- [Architecture catalog](references/architecture-catalog.md): generated list of the architectures, integrations and evidence.
- [Category page](../docs/voice-speech-ai.md): scope, when to use, when not to use, FAQ.
- [B2 capability map](../docs/b2-capabilities.md): what each B2 capability requires and limits.
- [Configuration templates](../examples/README.md): lifecycle, CORS, Event Notifications, Object Lock, replication, encryption and scoped key commands.
- [Provisioning helper](../infrastructure/b2-provisioning/README.md) and [LiveKit Egress template](../infrastructure/livekit-egress/README.md).
