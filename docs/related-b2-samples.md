# Related B2 sample applications

This page links existing, public sample applications that store audio, speech or transcript data on Backblaze B2 and that meet the B2 integration conventions listed below. They are independent repositories. None of them implements one of the architectures in this repository, and this repository does not modify them. They are useful as working code for individual pieces: ingesting audio into a bucket, running a speech model next to it, and writing results back as objects.

Most of these samples run open-source models locally and use B2 as the storage layer. They do not cover real-time voice transport, telephony or hosted speech providers; see the [category page](voice-speech-ai.md) for where B2 fits in those systems.

## Samples

Each row records the date on which a fresh shallow clone of the repository passed the checks described in the next section.

### Transcription and speaker labeling

| Sample | What it shows | Checked |
|---|---|---|
| [whisperx-diarized-archive](https://github.com/backblaze-b2-samples/whisperx-diarized-archive) | A transcript archive for an audio and video library: WhisperX transcription, pyannote speaker diarization, sentence-transformers embeddings, and keyword and semantic search. | 2026-10-15 |
| [whisper-subtitle-pipeline](https://github.com/backblaze-b2-samples/whisper-subtitle-pipeline) | Batch subtitling and translation of videos with faster-whisper; source media and SRT, VTT and JSON captions are kept in one bucket. | 2026-10-15 |
| [speechbrain-call-center-analytics](https://github.com/backblaze-b2-samples/speechbrain-call-center-analytics) | On-device SpeechBrain analysis of call recordings: transcription, diarization and sentiment, with inputs and results stored on B2. | 2026-10-15 |
| [ai-meeting-notes](https://github.com/backblaze-b2-samples/ai-meeting-notes) | Recorded or uploaded meetings transcribed with speaker diarization, then summarized into decisions and action items, searchable across past meetings. | 2026-10-15 |
| [ai-shorts-generator](https://github.com/backblaze-b2-samples/ai-shorts-generator) | A long video transcribed with Whisper, cut into vertical clips with burned-in captions; sources, transcripts and clips are stored on B2. | 2026-10-15 |

### Speech datasets and audio processing

| Sample | What it shows | Checked |
|---|---|---|
| [whisper-dataset-builder](https://github.com/backblaze-b2-samples/whisper-dataset-builder) | Long-form audio segmented with pyannote voice activity detection and transcribed with faster-whisper, written back to B2 as clip and transcript pairs in LJSpeech or Hugging Face audiofolder layout. | 2026-10-15 |
| [deepfilternet-batch-speech-enhancement](https://github.com/backblaze-b2-samples/deepfilternet-batch-speech-enhancement) | Batch denoising of speech recordings with DeepFilterNet into 48 kHz WAV, with sources, outputs and per-batch JSONL logs on B2. | 2026-10-15 |
| [audiosr-batch](https://github.com/backblaze-b2-samples/audiosr-batch) | Batch audio super-resolution with AudioSR to 48 kHz WAV, keeping originals and enhanced renditions side by side in a bucket. | 2026-10-15 |
| [demucs-stem-archive](https://github.com/backblaze-b2-samples/demucs-stem-archive) | Audio split into vocals, drums, bass and other stems with Demucs; every track and stem is archived on B2 and streamed from the bucket. | 2026-10-15 |

### Audio analysis and catalogs

| Sample | What it shows | Checked |
|---|---|---|
| [panns-audioset-tagging](https://github.com/backblaze-b2-samples/panns-audioset-tagging) | Local audio tagging with PANNs (527 AudioSet labels and a 2048-dimension embedding per clip); clips, tags and dataset manifests are stored on B2. | 2026-10-15 |
| [dejavu-audio-dedup](https://github.com/backblaze-b2-samples/dejavu-audio-dedup) | Deduplication of an audio catalog before ingest using Dejavu acoustic fingerprinting; raw audio and fingerprint artifacts are stored on B2. | 2026-10-15 |
| [birdnet-passive-acoustic-monitoring](https://github.com/backblaze-b2-samples/birdnet-passive-acoustic-monitoring) | Bird species detected in field recordings with BirdNET-Analyzer; raw audio, detections and event clips are stored on B2. | 2026-10-15 |

## What was checked

On the date shown, each repository was cloned fresh with `--depth 1` and passed four static checks on its tracked files:

1. The environment variable names in `.env.example` follow the standard B2 names.
2. No deprecated alias for those names is read.
3. The S3-compatible API is the default; the B2 native API is not called from source.
4. Each S3 client is constructed with a custom user agent.

These checks read files only. They do not show that a sample builds, runs or has no defects, and the contents of the repositories can change after the date shown. Check the repository itself before relying on it.

## When to use this page

- You want working code for one stage of a speech pipeline (transcription, diarization, dataset building, audio enhancement or tagging) that already writes to B2 over the S3-compatible API.
- You are comparing how different applications lay out source audio, derived artifacts and logs in a bucket.

## When not to use this page

- You need an architecture with zones, retention classes, consent records or release manifests. Use the [architecture index](architecture-index.md).
- You need real-time voice transport, a telephony integration or a hosted speech provider. These samples do not cover them.

## FAQ

**Are these the architectures in this repository?**
No. They are separate sample applications. The architectures are described under [`architectures/`](../architectures/), and the [application entries](../applications/README.md) list only applications that implement one of them.

**Is this a complete list of voice samples?**
No. It lists the repositories that met the conventions above on the date shown. Other repositories in the same organization may also handle audio or speech.

**Can I copy code from these samples?**
They are public repositories with their own licenses; read the license in each repository before reusing code.
