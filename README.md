# B2 reference architectures for voice and speech AI

Reference architectures for storing, versioning and delivering voice and speech AI data on Backblaze B2: training datasets, generated audio and voice agent session recordings. Current B2 product behavior is documented at [backblaze.com/docs](https://www.backblaze.com/docs/cloud-storage).

## Architectures

<!-- catalog:architectures:start -->
| Architecture | Purpose | Resources |
|---|---|---|
| [Voice training data lifecycle](architectures/voice-training-data-lifecycle/architecture.md) | Collect speech with consent, validate and review it, and publish immutable dataset releases that NeMo, Lhotse, Hugging Face Datasets, WebDataset and MosaicML Streaming read from B2. | [deployment](infrastructure/b2-provisioning/README.md) |
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
<!-- catalog:integrations:end -->
