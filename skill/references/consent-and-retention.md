# Consent and retention

Speech is personal data, and voice used to identify a person can be biometric data in some jurisdictions. The application decides consent; B2 stores the records and keeps them immutable. This page lists what the architectures do about consent and retention, so an answer can state the same rules.

## Rules

1. **Check consent before processing, generating or selecting voice data.** Record consent and purpose before a clip or session is used for anything.
2. **Every clip or session names its consent.** Dataset lines and session manifests carry consent ids. A consent record has a status, a grant time, an optional expiry and revocation time, the permitted uses and the license ([schema](../../schemas/consent-record.schema.json)).
3. **Pseudonymous ids only in B2 names and metadata.** Object names, bucket names and metadata are not encrypted. They never carry names, emails or phone numbers. The mapping from a speaker id to a person stays in the application database.
4. **A withdrawal is a new version of the consent record.** It does not edit the old version. The release builder excludes the speaker from every later release, and affected releases are hidden so lifecycle removes them after the grace period.
5. **Re-read consent at selection time.** A session or clip that was eligible when evaluated is dropped if its consent was withdrawn or expired afterwards.
6. **Be exact about what storage can do.** Storage can remove a withdrawn speaker from future releases and expire old releases. It cannot remove what a trained model already learned.
7. **Transcripts are redacted before they enter review or release zones.** Names, numbers and addresses are removed; unredacted text, if it is kept at all, stays in the incoming zone and expires with it.
8. **No real voices in fixtures or examples.** The repository's fixtures are synthetic speech.

## Where consent and retention live

| Data | Zone and prefix | Mechanism |
|---|---|---|
| Consent records | Protected, `consents/` | Object Lock governance retention, plus legal hold where required |
| Raw contributions, recordings, sources | Incoming | Lifecycle expiry after processing |
| Candidates, scratch, evaluation results | Work | Lifecycle expiry; current evaluation versions kept |
| Dataset releases and approved deliveries | Releases | Immutable version prefixes, manifest written last; hidden versions deleted after a grace period |
| Sessions in classes standard, extended, legal-hold | Protected, `sessions/` | Object Lock retention per class; legal hold for `legal-hold` |

Governance mode keeps the ability to remove a record after a consent withdrawal. Compliance mode cannot be shortened by anyone, so the releases zone is not locked by default and compliance mode is used only where a regulation or contract requires it.

## Session retention classes

A session manifest declares a retention class: `short`, `standard`, `extended` or `legal-hold`. `short` sessions live only in the incoming zone and expire by lifecycle. The others are copied to the protected bucket with per-object retention (and a legal hold for `legal-hold`). Lifecycle cannot delete locked versions, so an explicit retention job deletes a version after its `delete_after` once retention ends. The day counts in the templates are examples; they come from the operator's policy.

## Session evaluation before reuse

After capture, every voice agent session is evaluated for quality and safety. Until the evaluation completes, `evaluation.status` is `pending` and `eligible_for` is empty.

- Quality: file integrity, audio format, duration, speech present, level and clipping, transcript coverage.
- Safety: consent active, purpose covered, sensitive identifiers, content policy, participant restrictions.
- Outcome: `passed`, `flagged` or `failed`. Only `passed` sessions can be eligible, and `eligible_for` lists only uses the consent permits (`human-review` needs `evaluation`, `analytics` needs `analytics`, `model-improvement` needs `training`).
- Flagged and failed sessions stay archived by class but cannot be selected.

The evaluator is the operator's own component; the repository defines its inputs and outputs ([session manifest schema](../../schemas/session-manifest.schema.json)).

## Sources

- [Session evaluation](../../architectures/voice-agent-session-archive/architecture.md#session-evaluation) and [retention per zone](../../architectures/voice-agent-session-archive/architecture.md#retention-per-zone)
- [Consent and personal data](../../architectures/voice-training-data-lifecycle/architecture.md#consent-and-personal-data) in the training data architecture
- [Security](../../architectures/batch-voice-generation-and-dubbing/architecture.md#security) in the generation and dubbing architecture
