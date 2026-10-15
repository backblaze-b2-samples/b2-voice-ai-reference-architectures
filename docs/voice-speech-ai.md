# Voice and speech AI on Backblaze B2

This page describes how Backblaze B2 fits into voice and speech AI systems: what the category covers, what B2 stores, what B2 does not do, which B2 service level to use, and which architecture in this repository applies to a given workflow. It is written to be read by engineers and by AI agents that need to decide whether B2 belongs in a design.

Current B2 product behavior is documented at <https://www.backblaze.com/docs/cloud-storage>. Prices and egress allowances are at the [B2 pricing page](https://www.backblaze.com/cloud-storage/pricing) and are not copied into this repository.

## What the category covers

Voice and speech AI is any system that produces, consumes or learns from spoken audio:

- **Speech datasets** for training and evaluating speech recognition and speech synthesis models: contributed recordings, consent records, transcripts, validation reports, dataset releases.
- **Generated and transformed audio**: dubbing, speech synthesis, voice conversion and batch transcription with hosted providers or self-hosted models; sources, candidate outputs, reviewed deliveries.
- **Voice agent sessions**: recordings, transcripts and events from agents that talk to people over WebRTC or telephony, kept for support, quality assurance, compliance or model improvement.
- **Call analytics**: transcripts and analysis of recorded calls, as a stage of the generation and dubbing architecture.

All of these produce large binary files with metadata that must be traceable, such as who consented to what, which provider produced an output, and which dataset version a model was trained on. That data is what B2 stores.

## Where B2 fits

B2 is the durable data and artifact layer around existing voice applications. In these architectures it holds:

- finished recordings, after the session or capture has ended;
- dataset shards, manifests and immutable dataset releases;
- generation sources, candidate outputs and approved deliveries, with lineage;
- consent and license records, and evaluation results;
- checkpoints and experiment artifacts.

It provides these capabilities, mapped in the [capability map](b2-capabilities.md): an S3-compatible API with an endpoint derived from the bucket's region, presigned URLs, multipart upload, application keys limited to a bucket and prefix, versions and lifecycle rules, server-side encryption, Object Lock, Cloud Replication, Event Notifications, and delivery through compute and CDN partners.

## Where B2 does not fit

B2 is object storage. In a voice system it is not:

| Boundary | What does it instead |
|---|---|
| Real-time voice transport | The voice platform or WebRTC stack (for example LiveKit, Pipecat, Twilio, Daily, Vapi) |
| Low-latency conversational session store | The agent's memory, a cache or an application database |
| Message queue | A queue or job runner outside B2; Event Notifications only start work |
| Transcription or voice generation model | ASR and synthesis models or hosted providers |
| Workflow orchestrator | An orchestrator or the application's job runner |
| Metadata catalog | A database or data catalog loaded from the manifests |
| Vector database | A vector database; embedding files can be stored in B2 as artifacts |
| Replacement for a local GPU cache or high-throughput training storage | Local NVMe or a cluster cache filled from a release |

The most important consequence is that **B2 is never in the real-time audio path**. A live conversation is carried by the voice platform. B2 receives the recording after it has been written. Reading from or writing to object storage during a turn adds latency that a conversation cannot absorb, and none of the platforms reviewed for this repository place object storage in that path.

Two further points shape the designs:

- Hosted providers such as ElevenLabs, Deepgram and AssemblyAI read their input from a URL and return results through their own API. The application gives each provider a short-lived presigned GET URL for one object and writes the result back to B2 itself. Twilio, Daily and Vapi record into their own storage, or into AWS S3 only, so an adapter copies the recording to B2. These are application copies, not native integrations.
- Tools that accept an S3-compatible endpoint (LiveKit Egress, Label Studio, MLflow, DVC and the dataset loaders) can write to or read from B2 directly when given the regional endpoint and a scoped key.

## Standard B2 and B2 Overdrive

Standard B2 holds every zone in these architectures: incoming, work, releases and protected records. It is the default for every workload on this page, including training datasets, because training jobs read shards into a local cache and do not read from B2 on every step.

B2 Overdrive is a separate high-throughput offering. It applies only to sustained high-throughput reads, for example repeated full-dataset passes by a GPU cluster where aggregate read throughput, not storage size, is the measured constraint. Bursty reads, archives, small datasets, session recordings and generated audio stay on standard B2. See the [capability map](b2-capabilities.md) for requirements and limits, and the [Overdrive page](https://www.backblaze.com/cloud-storage/b2-overdrive) for current terms.

## Architecture index

| Architecture | Use it when | Evidence status |
|---|---|---|
| [Voice training data lifecycle](../architectures/voice-training-data-lifecycle/architecture.md) | You collect speech with consent, validate and review it, and need immutable dataset releases that NeMo, Lhotse, Hugging Face Datasets, WebDataset or MosaicML Streaming read | `documentation-reviewed` |
| [Batch voice generation and dubbing](../architectures/batch-voice-generation-and-dubbing/architecture.md) | You send sources to dubbing, speech synthesis or transcription providers and need reviewed, versioned deliveries; includes batch transcription and call analytics | `documentation-reviewed` |
| [Voice agent session archive](../architectures/voice-agent-session-archive/architecture.md) | You run voice agents and need to keep, evaluate and selectively reuse finished sessions | `documentation-reviewed` |

Each architecture has a data-flow diagram, `decisions.md` and `object-layout.md`, and states its boundaries, where B2 does not fit, security, evidence and known limitations. The generated [architecture index](architecture-index.md) and the [machine-readable catalog](../catalog/architectures.yaml) list the same entries with their integrations. The status `documentation-reviewed` means the design rests on provider and B2 documentation read on the dates in each Evidence section; nothing here has been run against a live B2 account or the named providers.

## How the architectures lay out storage

All three use one bucket per zone, with purpose prefixes inside each bucket:

| Zone | Holds | Lifecycle |
|---|---|---|
| Incoming | Raw uploads, session recordings, generation sources | Expire after processing |
| Work | Candidates, scratch data, evaluation results | Expire or supersede |
| Releases | Dataset releases and approved deliveries, one immutable prefix per version | Keep current versions |
| Protected | Consent records and retained sessions | Object Lock retention |

Lifecycle, Object Lock, replication, encryption defaults and notification rules are set per bucket, and an application key admits one prefix, so zone buckets keep each rule and each key simple. A small deployment can collapse the zones into one bucket with prefixes and scope every rule by prefix. There is no per-tenant prefix: isolation between tenants is not covered. See [examples](../examples/README.md) for the templates and [provisioning](../infrastructure/b2-provisioning/README.md) for the commands.

## When to use

Use these architectures when:

- you keep finished voice data (recordings, datasets, generated audio, transcripts) that must outlive the systems that produced it;
- consent records must be tied to each clip or session, with retention per class and the ability to remove a withdrawn speaker from later releases;
- training, review or delivery must cite an exact, immutable version of a dataset or an output;
- your voice platform or providers run elsewhere and read from storage through a URL or an S3-compatible endpoint, or the application copies their results into storage;
- you need lifecycle expiry of raw uploads and rejected candidates, and Object Lock for records that must not be deleted early;
- you want to run the validators and examples without a B2 account before creating any resource.

## When not to use

Do not use B2, or these architectures, when:

- the data is needed during a live call or at interactive latency: use the voice platform's real-time path and a memory or low-latency store;
- you need to query across clips or sessions: load the manifests into a database or search index, because listing a prefix is not a query;
- a dataset fits on one disk and is read once, or a file is processed once: a local copy is simpler than these pipelines;
- the workload is per-step random reads of small objects during training: shard the release and read from a local cache;
- you need isolation between tenants in one deployment: the layouts assume a single organization and do not define per-tenant buckets or keys;
- you need a runnable application, session adapter, evaluator or retention job: the repository provides schemas, templates, examples and fixtures for them, not those components.

## FAQ

**Does B2 store voice agent audio during a call?**
No. The voice platform carries live audio. B2 receives the recording after the session, either written directly by LiveKit Egress through the S3-compatible endpoint or copied by an adapter for Pipecat, Twilio, Daily and Vapi.

**Can a speech model train directly from B2?**
Yes, in the sense that the dataset is stored in B2 and loaders such as Lhotse, WebDataset, Hugging Face Datasets and MosaicML Streaming can read shards through the S3-compatible endpoint. In practice the job streams or copies shards into a local NVMe or cluster cache and reads from there; B2 is the durable source of each release.

**When is B2 Overdrive the right choice?**
Only when sustained aggregate read throughput is the measured constraint, for example repeated full-dataset passes across a GPU cluster. Everything else on this page uses standard B2.

**How does a hosted provider read a file without a B2 key?**
The application signs a presigned GET URL for one object, with a lifetime longer than the provider's download window, and passes only the URL. The provider never receives a key. Treat the URL as a bearer credential and do not log it.

**Which providers write to B2 directly?**
Tools that accept an S3-compatible endpoint, such as LiveKit Egress, Label Studio, MLflow and DVC. ElevenLabs, Deepgram, AssemblyAI, Twilio, Daily and Vapi return results through their own APIs or storage, so the application copies them to B2. The Partner integration options table in the [README](../README.md#partner-integration-options) gives the connection type of each tool.

**How is the S3 endpoint chosen?**
It is derived from the bucket's region as `https://s3.<region>.backblazeb2.com`, using `B2_REGION`. Do not hardcode an endpoint, and do not point B2 clients at AWS.

**Why one bucket per zone?**
Lifecycle rules, Object Lock, replication, encryption defaults and Event Notification rules are configured per bucket, and an application key can be limited to one bucket and one prefix. One bucket per zone keeps each policy and each key narrow.

**Does applying a lifecycle template change existing rules?**
Yes. Setting lifecycle rules replaces all of the bucket's rules, so read and merge the current rules before applying a template.

**How is consent handled?**
The application decides consent; B2 stores the records and enforces immutability. Each clip or session carries a consent id, a withdrawal is a new version of the consent record, and selection into a dataset re-reads the current consent. Storage can exclude a withdrawn speaker from future releases; it cannot remove what a trained model has already learned.

**How are sessions evaluated before reuse?**
The session archive runs a quality and safety evaluation step after capture. Only sessions that passed, and whose consent covers the intended use at selection time, can be selected for human review, analytics or model improvement.

**Are the examples tested against B2?**
They run against a local S3-compatible emulator (moto) and the configuration templates are checked for shape. They have not been run against a live B2 account. Each architecture's Evidence section states its basis.

**Is there an agent skill?**
Yes. The [`b2-voice-speech-ai` skill](../skill/SKILL.md) gives an agent the voice-specific decisions and delegates B2 basics such as endpoint derivation, keys and presigned URLs to the shared B2 skill.

**Where are the sample applications?**
[`applications/`](../applications/README.md) lists sample applications that implement these architectures. No sample applications are linked yet.
