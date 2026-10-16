"""Submit a generation job: run the worker and keep every output as an expiring candidate.

`submit` reads the source, signs a short-lived GET URL for it (what a hosted provider would
receive), runs the worker, checks every output with `shared/audio_checks.py` and writes the outputs to
`work/candidates/<job_id>/<attempt>/<language>/`: one mix per language and one stem per speaker. The
job manifest is written last, so a job exists only when all of its outputs are stored. A candidate
is never overwritten: a new attempt is a new attempt id.
"""
from __future__ import annotations

import hashlib
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from botocore.exceptions import ClientError

from . import contracts, storage
from .errors import ConfigError, Conflict, Rejected
from .shared import audio_checks
from .worker import Worker, WorkerRequest

JOB_KINDS = ("dubbing", "text-to-speech")  # the kinds whose output is audio
DEFAULT_SOURCE_EXPIRES = 900
DEFAULT_CANDIDATE_DAYS = 15  # 14 days to hide plus 1 day to delete, as in the lifecycle template
MAX_CANDIDATE_DAYS = 365
MANIFEST_NAME = "job-manifest.json"


def check_unique(values: Sequence[str], label: str, pattern: Any) -> tuple[str, ...]:
    if not values:
        raise Rejected(f"give at least one {label}")
    for value in values:
        storage.check_id(value, label, pattern)
    if len(set(values)) != len(values):
        raise Rejected(f"each {label} may be given once")
    return tuple(values)


def check_seconds(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= contracts.MAX_EXPIRES_SECONDS:
        raise Rejected(f"{label} must be a whole number of seconds from 1 to {contracts.MAX_EXPIRES_SECONDS}")
    return value


def new_job_id(now: datetime) -> str:
    return f"gen-{now:%Y%m%d}-{uuid.uuid4().hex[:6]}"


def check_outputs(outputs: Sequence[Any]) -> None:
    """Every output must be a usable clip: 16 kHz mono 16-bit PCM, long enough, audible, not clipped."""
    with tempfile.TemporaryDirectory() as folder:
        for index, output in enumerate(outputs):
            path = Path(folder) / f"{index}.wav"
            path.write_bytes(output.data)
            defects = audio_checks.clip_defects(path)
            if defects:
                raise ConfigError(f"the worker's output {output.name} failed the audio checks: {', '.join(defects)}")


def submit_job(client: Any, bucket: str, worker: Worker, *, source_id: str, target_languages: Sequence[str],
               speakers: Sequence[str], source_language: str = "en", kind: str = "dubbing",
               job_id: str | None = None, attempt: str = "a1", source_expires: int = DEFAULT_SOURCE_EXPIRES,
               candidate_days: int = DEFAULT_CANDIDATE_DAYS, consent_ids: Sequence[str] = (),
               now: datetime | None = None) -> dict[str, Any]:
    """Run one job and store its candidates and its manifest. Returns a summary."""
    moment = now or contracts.utc_now()
    job_id = storage.check_id(job_id or new_job_id(moment), "job id")
    storage.check_id(attempt, "attempt")
    storage.check_id(source_id, "source id")
    languages = check_unique(target_languages, "target language", storage.LANG_PATTERN)
    names = check_unique(speakers, "speaker", storage.SPEAKER_PATTERN)
    storage.check_id(source_language, "source language", storage.LANG_PATTERN)
    consents = check_unique(consent_ids, "consent id", storage.ID_PATTERN) if consent_ids else ()
    if kind not in JOB_KINDS:
        raise Rejected(f"kind must be one of {', '.join(JOB_KINDS)}")
    check_seconds(source_expires, "--source-expires")
    if not isinstance(candidate_days, int) or isinstance(candidate_days, bool) or not 1 <= candidate_days <= MAX_CANDIDATE_DAYS:
        raise Rejected(f"--candidate-days must be a whole number of days from 1 to {MAX_CANDIDATE_DAYS}")

    prefix = storage.candidate_prefix(job_id, attempt)
    if storage.list_keys(client, bucket, prefix):
        raise Conflict(f"attempt {attempt} of job {job_id} already exists; candidates are written once, use a new attempt")

    key = storage.source_key(source_id)
    try:
        source, version_id = storage.get_object(client, bucket, key)
    except ClientError as error:
        if storage.is_missing(error):
            raise Rejected(f"no source {key} in the bucket; upload it first (see synth-source --put)") from error
        raise
    source_sha256 = hashlib.sha256(source).hexdigest()

    request = WorkerRequest(job_id=job_id, kind=kind, source_url=storage.presign_get(client, bucket, key, source_expires),
                            source_sha256=source_sha256, source_language=source_language,
                            target_languages=languages, speakers=names)
    outputs = worker.run(request)
    check_outputs(outputs)

    expires_at = contracts.stamp(contracts.in_days(moment, candidate_days))
    records: list[dict[str, Any]] = []
    for output in outputs:
        storage.put_bytes(client, bucket, f"{prefix}{output.name}", output.data, "audio/wav")
        record: dict[str, Any] = {"kind": output.kind, "key": f"{prefix}{output.name}", "language": output.language,
                                  "size": len(output.data), "sha256": hashlib.sha256(output.data).hexdigest(),
                                  "expires_at": expires_at}
        if output.speaker_id:
            record["speaker_id"] = output.speaker_id
        records.append(record)

    source_record: dict[str, Any] = {"key": key, "sha256": source_sha256,
                                     "delivery": {"method": "presigned-get", "expires_in_seconds": source_expires}}
    if version_id and version_id != "null":
        source_record["version_id"] = version_id
    manifest: dict[str, Any] = {
        "schema_version": "1.0.0",
        "job_id": job_id,
        "kind": kind,
        "created_at": contracts.stamp(moment),
        "status": "succeeded",
        "provider": {"name": worker.worker_id, "mode": worker.mode},
        "input": source_record,
        "parameters": {"source_language": source_language, "target_languages": list(languages), "speakers": list(names)},
        "outputs": records,
        "review": {"status": "pending"},
    }
    if consents:
        manifest["consent_ids"] = list(consents)
    contracts.validate(contracts.JOB_SCHEMA, manifest, "the job manifest")
    manifest_key = f"{prefix}{MANIFEST_NAME}"
    storage.put_json(client, bucket, manifest_key, manifest)  # last: the job exists now
    return {"job_id": job_id, "attempt": attempt, "worker": worker.worker_id, "manifest": manifest_key,
            "outputs": [{"kind": item["kind"], "key": item["key"], "sha256": item["sha256"]} for item in records],
            "candidates_expire_at": expires_at}
