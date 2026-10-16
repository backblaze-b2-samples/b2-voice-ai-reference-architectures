"""The LiveKit Egress request that writes a session's recording straight to B2.

The request is printed with placeholders where the credentials go, always: this command never
reads or prints a key. Fill the placeholders in the server-side code that calls the Egress API,
with a separate application key that has only `writeFiles` and is limited to `incoming/sessions/`
of this bucket. Never reuse the key this application runs with.

The S3 output uses the endpoint derived from `B2_REGION` and `force_path_style`, which LiveKit's
documentation asks for with S3-compatible storage. The file path puts the recording inside the
session folder, `incoming/sessions/<room>/<session_id>/`, where `register-session` wrote the
session's registration. `{time}` is a LiveKit file path template that LiveKit fills in.
"""
from __future__ import annotations

from typing import Any

from . import storage
from .config import Target

KEY_ID_PLACEHOLDER = "<key-id-of-the-egress-key>"
KEY_PLACEHOLDER = "<application-key-of-the-egress-key>"


def build_request(target: Target, room: str, session_id: str) -> dict[str, Any]:
    storage.check_id(room, "room", storage.ROOM_PATTERN)
    storage.check_id(session_id, "session id")
    return {
        "room_name": room,
        "audio_only": True,
        "audio_mixing": "DUAL_CHANNEL_AGENT",
        "file_outputs": [
            {
                "file_type": "OGG",
                "filepath": storage.session_prefix(room, session_id) + "{time}.ogg",
                "disable_manifest": True,
                "s3": {
                    "access_key": KEY_ID_PLACEHOLDER,
                    "secret": KEY_PLACEHOLDER,
                    "region": target.region,
                    "endpoint": target.endpoint,
                    "bucket": target.bucket,
                    "force_path_style": True,
                },
            }
        ],
    }
