"""The release manifest example describes the committed synthetic fixtures exactly.

Release paths map to `testdata/synthetic-speech/`: `audio/` and `croissant.json` keep
their names, and `manifests/manifest.jsonl` is the fixture `manifest.jsonl`. The content
fingerprint is the SHA-256 of the lines `<sha256>  <path>` (sha256sum format), one per
file, sorted by path, each ending in a newline.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "testdata" / "synthetic-speech"
EXAMPLE = REPO / "manifests" / "release-manifest.example.json"


def source(path: str) -> Path:
    if path == "manifests/manifest.jsonl":
        return FIXTURES / "manifest.jsonl"
    return FIXTURES / path


def fingerprint(files: list[dict]) -> str:
    lines = "".join(f"{item['sha256']}  {item['path']}\n" for item in sorted(files, key=lambda item: item["path"]))
    return hashlib.sha256(lines.encode()).hexdigest()


def test_every_listed_file_matches_the_fixture():
    release = json.loads(EXAMPLE.read_text())
    for item in release["files"]:
        path = source(item["path"])
        assert path.is_file(), item["path"]
        assert path.stat().st_size == item["size"], item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], item["path"]


def test_every_valid_clip_is_released():
    release = json.loads(EXAMPLE.read_text())
    listed = {item["path"] for item in release["files"]}
    clips = {f"audio/{path.name}" for path in (FIXTURES / "audio").glob("*.wav")}
    assert clips <= listed


def test_fingerprint_is_recomputed_from_the_file_list():
    release = json.loads(EXAMPLE.read_text())
    assert release["integrity"]["content_fingerprint"] == fingerprint(release["files"])
    assert release["fingerprint"] == release["integrity"]["content_fingerprint"]


def test_fingerprint_changes_when_a_file_changes():
    release = json.loads(EXAMPLE.read_text())
    changed = [dict(item) for item in release["files"]]
    changed[0]["sha256"] = "0" * 64
    assert fingerprint(changed) != release["fingerprint"]
