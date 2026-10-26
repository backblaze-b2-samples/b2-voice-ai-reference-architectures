# LiveKit Egress output to B2

[LiveKit Egress](https://docs.livekit.io/home/egress/outputs/) can write a recording to any S3-compatible bucket. This folder holds a request template that points that output at a B2 bucket, and a script that fills it from environment variables.

B2 is not in the live audio path here. LiveKit carries the real-time audio; Egress writes the finished recording to the incoming zone when the session is recorded, and the rest of the [session archive](../../examples/README.md) works from that object.

| File | Purpose |
|---|---|
| [`egress-request.template.json`](egress-request.template.json) | Audio-only room recording, written as OGG to `sessions/` in the incoming bucket |
| [`render_request.py`](render_request.py) | Fills the template from environment variables and prints the request |

## How the B2 output is configured

The `s3` block of the output has these settings:

| Field | Value | Why |
|---|---|---|
| `endpoint` | `https://s3.<B2_REGION>.backblazeb2.com` | The S3-compatible endpoint of the bucket's region, derived from `B2_REGION` so there is a single setting to keep in sync. LiveKit requires it to start with `https://`. |
| `force_path_style` | `true` | LiveKit's documentation says to set it when using non-AWS storage. |
| `region` | `<B2_REGION>`, for example `us-west-004` | Included so requests are signed for the bucket's region. LiveKit marks it optional when `endpoint` is set. |
| `bucket` | `<B2_INCOMING_BUCKET>` | The incoming zone bucket of the architecture. |
| `access_key`, `secret` | `<B2_APPLICATION_KEY_ID>`, `<B2_APPLICATION_KEY>` | A scoped application key, never the master key. |

The output `filepath` is `sessions/{room_name}/{time}`. `{room_name}` and `{time}` are LiveKit filepath templates and are filled by LiveKit when the recording starts.

## Use it

1. Create the incoming bucket and a key limited to the `sessions/` prefix with [`b2-provisioning`](../b2-provisioning/README.md) (the key named `voice-sessions-incoming-sessions`). Keep the key ID and key it prints.
2. Set the environment:

   ```bash
   export B2_REGION=us-west-004
   export B2_INCOMING_BUCKET=<your-prefix>-voice-sessions-incoming
   export B2_APPLICATION_KEY_ID=<key-id>
   export B2_APPLICATION_KEY=<key>
   ```

3. Print the request for a room and pass it to the Egress API of your LiveKit SDK or CLI:

   ```bash
   python infrastructure/livekit-egress/render_request.py --room <room-name> > request.json
   ```

   The file contains the key, so delete it after use and keep it out of version control.

## Notes and limits

- **Key scope.** The key admits one prefix, so every `filepath` must start with `sessions/`. A file path outside the prefix is rejected by B2.
- **Manifest.** Egress also uploads a JSON manifest unless `disable_manifest` is set in the file output. Check where your Egress version writes it and keep it under the same prefix, or disable it.
- **Request shape.** The template uses the room composite request with `file_outputs`. LiveKit documents `StartEgress` as the newer request; there the storage settings move into a `storage` block (on the request or on an output) that carries the same `s3` fields, so the `s3` block of this template can be moved there unchanged.
- **Self-hosted Egress.** The service configuration accepts an `s3:` section with `endpoint`, `region`, `bucket` and credentials as a default for all requests. `force_path_style` is documented for the per-request `s3` block, so this template sets it per request.
- **After upload.** Subscribe to [Event Notifications](../../examples/README.md#event-notifications-incoming-zone) on the `sessions/` prefix to start post-processing once the object exists.

## Validation status

The request fields were read from LiveKit's Egress documentation on 2026-10-15: [outputs](https://docs.livekit.io/home/egress/outputs/), [API reference](https://docs.livekit.io/home/egress/api/) and [self-hosting](https://docs.livekit.io/transport/self-hosting/egress/). `tests/test_infrastructure.py` checks the filled template (endpoint derived from the region, `force_path_style`, bucket and key placeholders). Running it against a live Egress service and a B2 bucket is outside the validation scope.
