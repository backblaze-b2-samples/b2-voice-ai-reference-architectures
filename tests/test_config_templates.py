"""Shape checks for the B2 configuration templates in examples/.

The rules mirror the limits in the Backblaze B2 documentation linked from
docs/b2-capabilities.md. No B2 account is needed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples"
README = EXAMPLES / "README.md"

ZONES = {"incoming", "work", "releases", "protected"}
LIFECYCLE_FIELDS = {
    "fileNamePrefix",
    "daysFromUploadingToHiding",
    "daysFromHidingToDeleting",
    "daysFromStartingToCancelingUnfinishedLargeFiles",
}
DAY_FIELDS = LIFECYCLE_FIELDS - {"fileNamePrefix"}
EVENT_TYPE = re.compile(r"^b2:(ObjectCreated|ObjectDeleted|HideMarkerCreated|MultipartUploadCreated):(\*|[A-Za-z]+)$")
PLACEHOLDER = re.compile(r"^<[a-z0-9-]+>$")


def load(name: str):
    return json.loads((EXAMPLES / name).read_text())


def lifecycle_files() -> list[Path]:
    return sorted(EXAMPLES.glob("lifecycle-*.json"))


# Lifecycle rules


def test_one_lifecycle_template_per_zone():
    zones = {path.stem.removeprefix("lifecycle-") for path in lifecycle_files()}
    assert zones == ZONES


@pytest.mark.parametrize("path", lifecycle_files(), ids=lambda p: p.name)
def test_lifecycle_rule_shape(path):
    rules = json.loads(path.read_text())
    assert isinstance(rules, list) and 1 <= len(rules) <= 100
    prefixes = [rule["fileNamePrefix"] for rule in rules]
    assert len(prefixes) == len(set(prefixes)), "duplicate prefixes"
    for rule in rules:
        assert set(rule) == LIFECYCLE_FIELDS
        assert isinstance(rule["fileNamePrefix"], str)
        values = [rule[field] for field in DAY_FIELDS]
        assert any(value is not None for value in values), "all day values are null"
        for value in values:
            assert value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 1)


@pytest.mark.parametrize("path", lifecycle_files(), ids=lambda p: p.name)
def test_every_zone_cancels_unfinished_large_files(path):
    rules = json.loads(path.read_text())
    assert any(rule["fileNamePrefix"] == "" and rule["daysFromStartingToCancelingUnfinishedLargeFiles"] for rule in rules)


@pytest.mark.parametrize("path", lifecycle_files(), ids=lambda p: p.name)
def test_bucket_wide_rules_never_hide_current_versions(path):
    for rule in json.loads(path.read_text()):
        if rule["fileNamePrefix"] == "":
            assert rule["daysFromUploadingToHiding"] is None


def test_protected_and_release_zones_never_hide_current_versions():
    for zone in ("protected", "releases"):
        for rule in load(f"lifecycle-{zone}.json"):
            assert rule["daysFromUploadingToHiding"] is None


# CORS (S3 PutBucketCors format)


def test_cors_shape():
    rules = load("cors.s3.json")["CORSRules"]
    assert 1 <= len(rules) <= 100
    for rule in rules:
        assert set(rule) <= {"ID", "AllowedOrigins", "AllowedMethods", "AllowedHeaders", "ExposeHeaders", "MaxAgeSeconds"}
        origins = rule["AllowedOrigins"]
        assert origins and all(origin for origin in origins), "empty origins are rejected"
        assert "*" not in origins or origins == ["*"]
        assert all(origin == "*" or origin.startswith("https://") for origin in origins)
        assert set(rule["AllowedMethods"]) <= {"GET", "HEAD", "PUT", "DELETE"}
        assert 0 <= rule["MaxAgeSeconds"] <= 86400
        assert len(json.dumps(rule).encode()) < 1000


def test_cors_supports_presigned_put_and_browser_multipart():
    rule = load("cors.s3.json")["CORSRules"][0]
    assert "PUT" in rule["AllowedMethods"] and "GET" in rule["AllowedMethods"]
    assert "ETag" in rule["ExposeHeaders"]


# Event Notifications (b2_set_bucket_notification_rules shape)


def test_event_notification_shape():
    rules = load("event-notifications.json")["eventNotificationRules"]
    assert 1 <= len(rules) <= 25
    names = [rule["name"] for rule in rules]
    assert len(names) == len(set(names))
    for rule in rules:
        assert {"name", "eventTypes", "isEnabled", "objectNamePrefix", "targetConfiguration"} <= set(rule)
        assert rule["eventTypes"] and all(EVENT_TYPE.match(event) for event in rule["eventTypes"])
        assert len({event.split(":")[1] for event in rule["eventTypes"]}) == 1, "one event category per rule"
        assert 1 <= rule.get("maxEventsPerBatch", 1) <= 50
        target = rule["targetConfiguration"]
        assert target["targetType"] == "webhook"
        assert target["url"].startswith("https://")
        assert "backblaze" not in target["url"]
        for header in target.get("customHeaders", []):
            assert not header["name"].lower().startswith("x-bz-")


def test_event_notification_prefixes_do_not_overlap_per_event_type():
    rules = load("event-notifications.json")["eventNotificationRules"]
    for i, first in enumerate(rules):
        for second in rules[i + 1:]:
            if set(first["eventTypes"]) & set(second["eventTypes"]):
                a, b = first["objectNamePrefix"], second["objectNamePrefix"]
                assert not (a.startswith(b) or b.startswith(a)), f"{first['name']} overlaps {second['name']}"


def test_event_notification_template_holds_no_signing_secret():
    for rule in load("event-notifications.json")["eventNotificationRules"]:
        assert "hmacSha256SigningSecret" not in rule["targetConfiguration"]


# Object Lock, replication, encryption


def test_object_lock_shape():
    lock = load("object-lock.json")
    assert lock["fileLockEnabled"] is True
    retention = lock["defaultRetention"]
    assert retention["mode"] in {"governance", "compliance"}
    assert isinstance(retention["period"]["duration"], int) and retention["period"]["duration"] >= 1
    assert retention["period"]["unit"] in {"days", "years"}


def test_replication_shape():
    source = load("replication.json")["asReplicationSource"]
    rules = source["replicationRules"]
    assert 1 <= len(rules) <= 2
    assert len({rule["replicationRuleName"] for rule in rules}) == len(rules)
    for rule in rules:
        assert set(rule) == {"replicationRuleName", "destinationBucketId", "fileNamePrefix", "includeExistingFiles",
                             "isEnabled", "priority"}
        assert 1 <= rule["priority"] <= 2147483647
        assert isinstance(rule["includeExistingFiles"], bool) and isinstance(rule["isEnabled"], bool)
        assert PLACEHOLDER.match(rule["destinationBucketId"]), "use a placeholder, not a real bucket id"
    assert PLACEHOLDER.match(source["sourceApplicationKeyId"]), "use a placeholder, not a real key id"


def test_encryption_shape():
    assert load("encryption.json") == {"defaultServerSideEncryption": {"mode": "SSE-B2", "algorithm": "AES256"}}


# README


def test_readme_lists_every_template():
    text = README.read_text()
    for path in sorted(EXAMPLES.glob("*.json")):
        assert f"`{path.name}`" in text, f"{path.name} is not described in examples/README.md"


def test_readme_warns_before_applying_lifecycle_rules():
    text = README.read_text()
    first_command = text.index("--lifecycle-rules")
    warning = text.find("replaces ALL of a bucket's existing lifecycle rules")
    assert 0 <= warning < first_command


def test_readme_commands_reference_existing_templates():
    text = README.read_text()
    for name in re.findall(r"examples/([a-z0-9.-]+\.json)", text):
        assert (EXAMPLES / name).is_file(), name


def test_readme_keys_are_scoped():
    text = README.read_text()
    commands = [line for line in text.splitlines() if line.startswith("b2 key create")]
    assert commands
    for command in commands:
        assert "--bucket" in command and "--name-prefix" in command
        assert "--all-capabilities" not in command
