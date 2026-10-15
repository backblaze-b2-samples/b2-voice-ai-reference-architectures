# B2 reference architectures for voice and speech AI

Reference architectures for storing, versioning and delivering voice and speech AI data on Backblaze B2: training datasets, generated audio and voice agent session recordings. Current B2 product behavior is documented at [backblaze.com/docs](https://www.backblaze.com/docs/cloud-storage).

## Architectures

<!-- catalog:architectures:start -->
| Architecture | Purpose | Resources |
|---|---|---|
| [Voice training data lifecycle](architectures/voice-training-data-lifecycle/architecture.md) | Collect speech with consent, validate and review it, and publish immutable dataset releases that NeMo, Lhotse, Hugging Face Datasets, WebDataset and MosaicML Streaming read from B2. | [deployment](infrastructure/b2-provisioning/README.md) |
| [Batch voice generation and dubbing](architectures/batch-voice-generation-and-dubbing/architecture.md) | Send sources to dubbing, speech synthesis and transcription providers through presigned GET URLs, keep every output as an expiring candidate, and promote reviewed outputs to immutable deliveries with lineage. | [deployment](infrastructure/b2-provisioning/README.md) |
| [Voice agent session archive](architectures/voice-agent-session-archive/architecture.md) | Archive finished voice agent sessions from LiveKit Egress or a session adapter, evaluate each session for quality and safety, retain it per class under Object Lock, and select only passed, consented sessions for review, analytics or datasets. | [deployment](infrastructure/livekit-egress/README.md) |
<!-- catalog:architectures:end -->

## Where state lives

Architecture definitions live in `catalog/architectures.yaml`. The tables on this page and the files under `docs/`, `skill/references/` and `llms.txt` are generated from it by `tools/generate_catalog.py`.

## Clean up

The tooling in this repository creates no buckets, application keys or lifecycle rules.

## Known limitations

Isolation between tenants is not covered: the layouts assume a single organization.

## Partner integration options

<!-- catalog:integrations:start -->
| Tool | Role | How it connects to B2 | Connection type | Architectures |
|---|---|---|---|---|
| [NVIDIA NeMo](https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/datasets.html) | Speech model training on NeMo JSONL manifests | Reads releases through its Lhotse data loader, or from a local copy; its own object-store support is documented for AIStore | Application copies data to or from B2 | [Voice training data lifecycle](architectures/voice-training-data-lifecycle/architecture.md) |
| [Lhotse](https://github.com/lhotse-speech/lhotse) | Speech data loading from Lhotse Shar shards | Shard sources given as pipe commands with an S3 client and the B2 endpoint, or as s3 URIs opened through smart_open | Provider uses an S3-compatible endpoint | [Voice training data lifecycle](architectures/voice-training-data-lifecycle/architecture.md) |
| [WebDataset](https://github.com/webdataset/webdataset) | Tar-shard data loading | Shard URLs as pipe commands with an S3 client and the B2 endpoint, or presigned https URLs read with curl | Provider uses an S3-compatible endpoint | [Voice training data lifecycle](architectures/voice-training-data-lifecycle/architecture.md) |
| [Hugging Face Datasets](https://huggingface.co/docs/datasets/package_reference/loading_methods) | Dataset loading and streaming from JSON or WebDataset files | storage_options passed to the s3fs backend with the B2 endpoint_url | Provider uses an S3-compatible endpoint | [Voice training data lifecycle](architectures/voice-training-data-lifecycle/architecture.md) |
| [MosaicML Streaming](https://docs.mosaicml.com/projects/streaming/en/stable/how_to_guides/configure_cloud_storage_credentials.html) | Streaming MDS shards into a local cache during training | S3_ENDPOINT_URL set to the B2 S3 endpoint; remote s3 path and local cache directory | Provider uses an S3-compatible endpoint | [Voice training data lifecycle](architectures/voice-training-data-lifecycle/architecture.md) |
| [Label Studio](https://labelstud.io/guide/storage_s3) | Transcript and label review | Amazon S3 storage type with the S3 endpoint field set to B2; source storage with pre-signed URLs and target storage for annotations | Provider uses an S3-compatible endpoint | [Voice training data lifecycle](architectures/voice-training-data-lifecycle/architecture.md) |
| [MLflow](https://mlflow.org/docs/latest/self-hosting/architecture/artifact-store/) | Experiment tracking and artifact storage | Artifact root on an s3 path with MLFLOW_S3_ENDPOINT_URL set to the B2 S3 endpoint | Provider uses an S3-compatible endpoint | [Voice training data lifecycle](architectures/voice-training-data-lifecycle/architecture.md) |
| [DVC](https://doc.dvc.org/user-guide/data-management/remote-storage/amazon-s3) | Data versioning pinned by Git commits | S3 remote with endpointurl set to the B2 S3 endpoint | Provider uses an S3-compatible endpoint | [Voice training data lifecycle](architectures/voice-training-data-lifecycle/architecture.md) |
| [ElevenLabs Dubbing](https://elevenlabs.io/docs/api-reference/dubbing/create-project) | Dubbing into other languages, with speaker detection and voice cloning | Reads the source from a presigned GET URL passed as source_url; the application downloads the dubbed audio and subtitles from the signed output URLs and writes them to B2 | Application copies data to or from B2 | [Batch voice generation and dubbing](architectures/batch-voice-generation-and-dubbing/architecture.md) |
| [Deepgram](https://developers.deepgram.com/docs/pre-recorded-audio) | Pre-recorded transcription, diarization and English audio intelligence | Reads the source from a presigned GET URL in the url field; the transcript returns in the response or by callback and the application writes it to B2 | Application copies data to or from B2 | [Batch voice generation and dubbing](architectures/batch-voice-generation-and-dubbing/architecture.md) |
| [AssemblyAI](https://www.assemblyai.com/docs/api-reference/transcripts/submit) | Batch transcription, speaker labels and transcript analysis | Reads the source from a presigned GET URL passed as audio_url; the application fetches the transcript after the webhook or by polling and writes it to B2 | Application copies data to or from B2 | [Batch voice generation and dubbing](architectures/batch-voice-generation-and-dubbing/architecture.md) |
| [Cloudflare CDN](https://www.backblaze.com/docs/cloud-storage-deliver-private-backblaze-b2-content-through-cloudflare-cdn) | Private delivery of approved outputs | A Cloudflare Worker signs each request to the private releases bucket's S3 endpoint with an application key scoped to deliveries | Provider uses an S3-compatible endpoint | [Batch voice generation and dubbing](architectures/batch-voice-generation-and-dubbing/architecture.md) |
| [LiveKit Egress](https://docs.livekit.io/home/egress/outputs/) | Recording of voice agent rooms and tracks | S3 output with the B2 endpoint, force_path_style and a key limited to sessions/ in the incoming bucket; template in infrastructure/livekit-egress | Provider uses an S3-compatible endpoint | [Voice agent session archive](architectures/voice-agent-session-archive/architecture.md) |
| [Pipecat](https://docs.pipecat.ai/server/utilities/audio/audio-buffer-processor) | Voice agent pipelines that buffer user and bot audio | AudioBufferProcessor hands mixed or per-track PCM to the application, which converts it and uploads it to B2 | Application copies data to or from B2 | [Voice agent session archive](architectures/voice-agent-session-archive/architecture.md) |
| [Twilio Voice](https://www.twilio.com/docs/voice/api/recording) | Call and conference recordings | The recording status callback triggers the adapter, which downloads the media with HTTP Basic authentication and writes it to B2; Twilio external storage supports AWS S3 only | Application copies data to or from B2 | [Voice agent session archive](architectures/voice-agent-session-archive/architecture.md) |
| [Daily](https://docs.daily.co/reference/rest-api/recordings/get-recording-link) | Cloud and raw-tracks recordings of calls | The recording.ready-to-download webhook triggers the adapter, which downloads the file through the recording access link API and writes it to B2; Daily custom storage supports Amazon S3 only, with OCI by request | Application copies data to or from B2 | [Voice agent session archive](architectures/voice-agent-session-archive/architecture.md) |
| [Vapi](https://docs.vapi.ai/security-and-privacy/retrieve-call-artifacts) | Hosted voice agent call recordings and logs | After the end-of-call-report event, the adapter fetches recordings from the authenticated artifact endpoints, which redirect to short-lived signed URLs, and writes them to B2 | Application copies data to or from B2 | [Voice agent session archive](architectures/voice-agent-session-archive/architecture.md) |
<!-- catalog:integrations:end -->
