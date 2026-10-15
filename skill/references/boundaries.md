# Boundaries: what B2 is not in a voice system

B2 is the durable data and artifact layer around voice applications. When a requirement falls in the left column, B2 is not the component that meets it; the right column says what does. Every architecture lists these boundaries for its own workflow in its `architecture.md`.

| Boundary | B2 is not | What does it instead |
|---|---|---|
| Real-time voice transport | A carrier of live audio or synthesized speech streamed to a listener | The voice platform or WebRTC stack: LiveKit, Pipecat, Twilio, Daily, Vapi, or the provider's streaming API |
| Low-latency conversational session store | A store for state read or updated on every turn | The agent's memory, a cache or the application database |
| Message queue | A scheduler, retry mechanism or ordered queue; Event Notifications are delivered at least once and the webhook must answer quickly | A queue or job runner outside B2 that the webhook writes to |
| Transcription or voice generation model | A model that transcribes, redacts, dubs or synthesizes | ASR, alignment and redaction models, hosted providers (ElevenLabs, Deepgram, AssemblyAI) or self-hosted models |
| Workflow orchestrator | A sequencer of validation, review, generation or promotion steps | An orchestrator or the application's job runner |
| Metadata catalog | A searchable index of speakers, clips, sessions or jobs; listing a prefix is not a query | A database or data catalog loaded from the manifests |
| Vector database | An index for embedding similarity, deduplication or speaker search | A vector database; embedding files can be stored in B2 as artifacts |
| Local GPU cache or high-throughput training storage | The per-step random-read store behind GPUs | Local NVMe or a cluster cache filled from a release; B2 Overdrive only when sustained read throughput is the measured constraint |

## How to apply this in an answer

- Name the boundary in one sentence and say what takes over, rather than adding a B2 feature to cover it.
- A live voice agent is boundary 1 and 2 at once: audio and per-turn state stay in the platform and the agent; the finished recording reaches B2 after the session.
- Event Notifications start work. They are not a queue: verify the signature on the raw body, record the `eventId`, answer, and hand the work to a queue outside B2.
- "Search the sessions where the caller was frustrated" is boundary 6: index the evaluation and transcript manifests in a database.
- "Train directly from B2 on every step" is boundary 8: shard the release, copy or stream it into a local cache, and read from the cache. Consider Overdrive only for sustained high-throughput reads.
- When a hosted provider only writes to its own storage or to AWS S3 (Twilio, Daily) or returns results through its API (ElevenLabs, Deepgram, AssemblyAI, Vapi), say that the application copies to B2. It is not a native integration.

Sources: [architecture catalog](architecture-catalog.md), and the Boundaries sections of the [training data](../../architectures/voice-training-data-lifecycle/architecture.md#boundaries), [generation and dubbing](../../architectures/batch-voice-generation-and-dubbing/architecture.md#boundaries) and [session archive](../../architectures/voice-agent-session-archive/architecture.md#boundaries) architectures.
