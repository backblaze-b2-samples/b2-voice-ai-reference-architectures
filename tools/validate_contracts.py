#!/usr/bin/env python3
"""Strictly validate the catalog, every architecture it lists, schemas and manifests.

With no arguments the whole repository is validated:

* every JSON Schema is valid and carries this repository's `$id` prefix;
* every `manifests/<name>.example.json` validates against `schemas/<name>.schema.json`;
* the synthetic fixtures under `testdata/synthetic-speech/` are checked: every manifest
  line, consent record and session manifest against its schema, every clip against the
  audio rules, the declared duration against the WAV header, sizes and hashes against
  the files, and every consent reference against an active consent record;
* the catalog validates against its schema and every entry is checked: the three
  architecture files, the required sections, a Mermaid diagram, boundary coverage,
  dated evidence, a validation status that claims no more than its evidence, and the
  session evaluation step of the voice agent session archive;
* the README has its required sections;
* every application folder `applications/<name>/` has the shape that `AGENTS.md` describes:
  the project files, a `.env.example` with the `B2_*` names and no endpoint variable, no
  `package.json`, an upper bound on every requirement and dependency, copies in `shared/`
  and `schemas/` that equal their sources, an entry in the `applications` job matrix of
  `.github/workflows/ci.yml`, a section in `applications/README.md`, a README that warns
  that a lifecycle or CORS command replaces rules, and no bucket configuration calls in
  `src/`;
* `.github/workflows/ci.yml` grants only `contents: read` and does not use `pull_request_target`;
* every non-null `links.application` is `applications/<name>/README.md` of an application
  folder that passes the rules above;
* no text file contains the em dash character.

Options:
  --capability-map FILE      check one row per required B2 capability and no price figure
  --require-applications N   require at least N catalog entries that link a valid application
"""
from __future__ import annotations

import argparse
import json
import hashlib
import re
import sys
import tomllib
from datetime import datetime
from pathlib import Path

import jsonschema
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio_checks  # noqa: E402
import generate_catalog  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ID_PREFIX = "urn:backblaze:b2-voice-ai-reference-architectures:"
ARCHITECTURE_FILES = ("architecture.md", "decisions.md", "object-layout.md")
SESSION_ARCHIVE_ID = "voice-agent-session-archive"
FIXTURE_DIR = Path("testdata") / "synthetic-speech"
SESSION_DURATION_SLACK_SECONDS = 1.0
EM_DASH = chr(0x2014)
TEXT_SUFFIXES = {".md", ".py", ".yml", ".yaml", ".json", ".jsonl", ".txt", ".cff", ".sh", ".ts", ".toml", ".cfg", ".ini"}
SKIP_DIRS = {".git", ".venv", "node_modules", ".pytest_cache", "__pycache__"}

APPLICATIONS_DIR = "applications"
CI_WORKFLOW = Path(".github") / "workflows" / "ci.yml"
APPLICATION_SECTIONS = ("What it shows", "Architecture", "Configurations it ships", "How to run", "Known limitations")
APPLICATION_README_SECTIONS = ("B2's role", "Setup", "Tests", "Known limitations")
APPLICATION_FILES = ("pyproject.toml", "requirements.txt", "requirements-dev.txt", ".gitignore", ".env.example", "README.md")
APPLICATION_SOURCE_FILES = ("cli.py", "config.py", "storage.py")
APPLICATION_TEST_FILES = ("conftest.py", "test_live.py")
APPLICATION_GITIGNORE = (".env", "*.egg-info/", ".venv/", "__pycache__/", ".pytest_cache/")
APPLICATION_ENV_NAMES = ("B2_APPLICATION_KEY_ID", "B2_APPLICATION_KEY", "B2_BUCKET_NAME", "B2_REGION")
ENDPOINT_VARIABLE = re.compile(r"\bB2_(?:ENDPOINT|S3_ENDPOINT|KEY_ID)\b")
PUBLIC_URL_BASE_COMMENTED = re.compile(r"^\s*#\s*B2_PUBLIC_URL_BASE=", re.MULTILINE)
# Text that must not appear in an application's `src/`: bucket configuration and shortcuts to it.
BUCKET_CONFIGURATION = (
    "put_bucket_", "put_object_lock_configuration", "create_bucket", "delete_bucket", "b2_update_bucket",
    "b2_set_bucket_notification_rules", ".Bucket(", "subprocess", "os.system", "bucket update", "bucket create",
    "notification-rule", "--lifecycle-rules", "--cors-rules", "update-bucket",
)

# Catalog boundary id -> (label, text that must appear in the Boundaries section).
BOUNDARIES = {
    "real-time-voice-transport": ("Real-time voice transport", "real time voice transport"),
    "low-latency-session-store": ("Low-latency conversational session store", "low latency conversational session store"),
    "message-queue": ("Message queue", "message queue"),
    "transcription-or-voice-model": ("Transcription or voice generation model", "transcription or voice generation model"),
    "workflow-orchestrator": ("Workflow orchestrator", "workflow orchestrator"),
    "metadata-catalog": ("Metadata catalog", "metadata catalog"),
    "vector-database": ("Vector database", "vector database"),
    "gpu-cache-or-training-storage": ("Local GPU cache or high-throughput training storage", "gpu cache"),
}

# Required capability-map rows: (label to write, regex matched against the normalized first cell).
CAPABILITIES = [
    ("S3-compatible API", r"^s3 compatible api"),
    ("Presigned URLs and CORS", r"^presigned urls"),
    ("Multipart upload with retry, resume, complete and abort", r"^multipart upload"),
    ("Application keys with limited scope", r"^application keys"),
    ("Event Notifications with a signed, idempotent webhook", r"^event notifications"),
    ("Versions and lifecycle rules", r"^(object )?versions"),
    ("Server-side encryption", r"^server side encryption"),
    ("Object Lock", r"^object lock"),
    ("Cloud Replication", r"^cloud replication"),
    ("Included egress", r"^included egress"),
    ("Delivery through compute partners", r"^(delivery through )?compute partners?"),
    ("Private delivery through a CDN", r"^private (cdn|delivery)"),
    ("Standard B2", r"^standard b2"),
    ("B2 Overdrive", r"^(b2 )?overdrive"),
    ("Local processing or GPU cache", r"^(local processing|local gpu cache|gpu cache)"),
    ("Selection criteria and trade-offs", r"^selection criteria"),
]

README_SECTIONS = ("Where state lives", "Clean up", "Known limitations", "Partner integration options")
PRICE = re.compile(
    r"\$\s*\d|\b\d[\d.,]*\s*(usd|dollars?|cents?)\b|\d\s*\u00a2|\b\d[\d.,]*\s*(/|per|a)\s*(gb|tb)\b",
    re.IGNORECASE,
)
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
SAMPLE_ORG_URL = "https://github.com/backblaze-b2-samples/"
TEST_LIKE = ("tests/", "examples/", "infrastructure/", "applications/")
DOC_LIKE = ("architectures/", "docs/", "skill/")


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def sections(text: str) -> list[tuple[int, str, str]]:
    """Return (level, title, body) for each heading outside fenced code; the body includes subsections."""
    lines = text.splitlines()
    heads: list[tuple[int, int, str]] = []
    fenced = False
    for index, line in enumerate(lines):
        if FENCE.match(line):
            fenced = not fenced
            continue
        match = None if fenced else HEADING.match(line)
        if match:
            heads.append((index, len(match.group(1)), match.group(2)))
    result = []
    for position, (index, level, title) in enumerate(heads):
        end = len(lines)
        for later_index, later_level, _ in heads[position + 1:]:
            if later_level <= level:
                end = later_index
                break
        result.append((level, title, "\n".join(lines[index + 1:end])))
    return result


def find_section(found: list[tuple[int, str, str]], name: str) -> str | None:
    wanted = normalize(name)
    for _, title, body in found:
        if normalize(title).startswith(wanted):
            return body
    return None


def has_mermaid(text: str) -> bool:
    return re.search(r"^```mermaid[ \t]*\n(?:[ \t]*\n)*[ \t]*(?!```)\S", text, re.MULTILINE) is not None


def is_documentation(item: str) -> bool:
    if item.startswith(SAMPLE_ORG_URL):
        return False
    if item.startswith(("http://", "https://")):
        return True
    return item.startswith(DOC_LIKE)


def is_test_like(item: str) -> bool:
    return item.startswith(SAMPLE_ORG_URL) or item.startswith(TEST_LIKE)


def check_status(entry: dict) -> list[str]:
    label = entry["id"]
    status = entry["validation"]["status"]
    evidence = entry["validation"]["evidence"]
    errors = []
    if all(is_documentation(item) for item in evidence) and status != "documentation-reviewed":
        errors.append(f"{label}: status {status} but all evidence is documentation; use documentation-reviewed")
    if status in {"source-reviewed-and-automated-tests", "end-to-end-environment-tested"} and not any(
        is_test_like(item) for item in evidence
    ):
        errors.append(f"{label}: status {status} needs evidence under tests/, examples/, infrastructure/ or applications/, or a sample repository")
    if status == "source-reviewed" and all(is_documentation(item) for item in evidence):
        errors.append(f"{label}: status source-reviewed needs evidence other than documentation")
    return errors


def check_architecture(root: Path, entry: dict) -> list[str]:
    errors: list[str] = []
    arch_id = entry["id"]
    folder = root / "architectures" / arch_id
    if entry["slug"] != arch_id:
        errors.append(f"{arch_id}: slug {entry['slug']} must equal the id")
    expected_link = f"architectures/{arch_id}/architecture.md"
    if entry["links"]["architecture"] != expected_link:
        errors.append(f"{arch_id}: links.architecture must be {expected_link}")
    texts: dict[str, str] = {}
    for name in ARCHITECTURE_FILES:
        path = folder / name
        if not path.is_file() or not path.read_text().strip():
            errors.append(f"{arch_id}: missing or empty architectures/{arch_id}/{name}")
        else:
            texts[name] = path.read_text()
    if errors and "architecture.md" not in texts:
        return errors + check_status(entry)
    found = [item for text in texts.values() for item in sections(text)]
    for name in ("Boundaries", "Where B2 does not fit", "Evidence", "Object layout", "Security"):
        if find_section(found, name) is None:
            errors.append(f"{arch_id}: missing section '{name}'")
    if "architecture.md" in texts and not has_mermaid(texts["architecture.md"]):
        errors.append(f"{arch_id}: architecture.md has no Mermaid diagram")
    boundaries = find_section(found, "Boundaries")
    if boundaries is not None:
        covered = normalize(boundaries)
        for boundary in entry["boundaries"]:
            label, needle = BOUNDARIES[boundary]
            if needle not in covered:
                errors.append(f"{arch_id}: Boundaries section does not cover '{label}'")
    evidence = find_section(found, "Evidence")
    if evidence is not None and not DATE.search(evidence):
        errors.append(f"{arch_id}: Evidence section has no dated entry (YYYY-MM-DD)")
    errors.extend(check_status(entry))
    if arch_id == SESSION_ARCHIVE_ID:
        evaluation = find_section(found, "Session evaluation")
        if evaluation is None:
            evaluation = find_section(found, "Session quality and safety evaluation")
        if evaluation is None:
            errors.append(f"{arch_id}: missing section 'Session evaluation'")
        else:
            words = normalize(evaluation)
            for word in ("quality", "safety"):
                if word not in words:
                    errors.append(f"{arch_id}: Session evaluation section does not mention {word}")
    return errors


def check_schemas(root: Path) -> list[str]:
    errors = []
    for path in sorted((root / "schemas").glob("*.json")):
        try:
            schema = json.loads(path.read_text())
            jsonschema.Draft202012Validator.check_schema(schema)
        except (ValueError, jsonschema.SchemaError) as error:
            errors.append(f"schemas/{path.name}: invalid schema: {str(error).splitlines()[0]}")
            continue
        if not str(schema.get("$id", "")).startswith(SCHEMA_ID_PREFIX):
            errors.append(f"schemas/{path.name}: $id must start with {SCHEMA_ID_PREFIX}")
        if not schema.get("title"):
            errors.append(f"schemas/{path.name}: missing title")
    return errors


def check_manifest_examples(root: Path) -> list[str]:
    errors = []
    for path in sorted((root / "manifests").glob("*.example.json")):
        name = path.name[: -len(".example.json")]
        schema_path = root / "schemas" / f"{name}.schema.json"
        if not schema_path.is_file():
            errors.append(f"manifests/{path.name}: no schemas/{name}.schema.json")
            continue
        try:
            instance = json.loads(path.read_text())
            schema = json.loads(schema_path.read_text())
            jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(instance)
        except (ValueError, jsonschema.ValidationError) as error:
            errors.append(f"manifests/{path.name}: {str(error).splitlines()[0]}")
            continue
        if name == "session-manifest":
            errors.extend(check_session_semantics(instance, f"manifests/{path.name}"))
        if name == "release-manifest":
            if instance["fingerprint"] != instance["integrity"]["content_fingerprint"]:
                errors.append(f"manifests/{path.name}: fingerprint differs from integrity.content_fingerprint")
            if instance["statistics"]["file_count"] != len(instance["files"]):
                errors.append(f"manifests/{path.name}: statistics.file_count does not match files")
            if instance["statistics"]["total_bytes"] != sum(item["size"] for item in instance["files"]):
                errors.append(f"manifests/{path.name}: statistics.total_bytes does not match files")
    return errors


def schema_validator(root: Path, name: str) -> jsonschema.Draft202012Validator:
    schema = json.loads((root / "schemas" / f"{name}.schema.json").read_text())
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


def first_error(validator: jsonschema.Draft202012Validator, instance: object) -> str | None:
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.path))
    if not errors:
        return None
    where = "/".join(map(str, errors[0].path)) or "<root>"
    return f"{where}: {errors[0].message}"


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def check_session_semantics(manifest: dict, label: str) -> list[str]:
    """Rules a JSON Schema cannot express for a session manifest."""
    errors = []
    elapsed = (parse_datetime(manifest["ended_at"]) - parse_datetime(manifest["started_at"])).total_seconds()
    if elapsed < 0:
        errors.append(f"{label}: ended_at is before started_at")
    elif abs(elapsed - manifest["duration_seconds"]) > SESSION_DURATION_SLACK_SECONDS:
        errors.append(f"{label}: duration_seconds differs from ended_at minus started_at")
    participants = {item["participant_id"] for item in manifest["participants"]}
    for recording in manifest["recordings"]:
        owner = recording.get("participant_id")
        if owner is not None and owner not in participants:
            errors.append(f"{label}: recording {recording['path']} names unknown participant {owner}")
    return errors


def check_clip(base: Path, line: dict, label: str) -> list[str]:
    """Check one manifest line against its audio file: the rules, the duration and the hash."""
    path = base / line["audio_filepath"]
    if not path.is_file():
        return [f"{label}: {line['audio_filepath']} does not exist"]
    errors = [f"{label}: {line['audio_filepath']} {defect}" for defect in audio_checks.clip_defects(path, line["duration"])]
    if "sha256" in line and hashlib.sha256(path.read_bytes()).hexdigest() != line["sha256"]:
        errors.append(f"{label}: {line['audio_filepath']} sha256 does not match the file")
    return errors


def load_consents(base: Path, validator: jsonschema.Draft202012Validator) -> tuple[dict[str, dict], list[str]]:
    consents: dict[str, dict] = {}
    errors = []
    for path in sorted((base / "consents").glob("*.json")):
        label = f"{FIXTURE_DIR.as_posix()}/consents/{path.name}"
        try:
            record = json.loads(path.read_text())
        except ValueError as error:
            errors.append(f"{label}: {error}")
            continue
        problem = first_error(validator, record)
        if problem:
            errors.append(f"{label}: {problem}")
            continue
        if record["consent_id"] != path.stem:
            errors.append(f"{label}: consent_id must equal the file name")
        consents[record["consent_id"]] = record
    return consents, errors


def check_consent_reference(consents: dict[str, dict], consent_id: str, label: str, use: str | None = None,
                            speaker: str | None = None) -> list[str]:
    record = consents.get(consent_id)
    if record is None:
        return [f"{label}: consent_id {consent_id} has no record"]
    errors = []
    if record["status"] != "active":
        errors.append(f"{label}: consent {consent_id} is {record['status']}")
    if use is not None and use not in record["permitted_uses"]:
        errors.append(f"{label}: consent {consent_id} does not permit {use}")
    if speaker is not None and record["subject"]["speaker_id"] != speaker:
        errors.append(f"{label}: consent {consent_id} belongs to {record['subject']['speaker_id']}, not {speaker}")
    return errors


def check_fixtures(root: Path) -> list[str]:
    """Validate testdata/synthetic-speech: schemas, audio rules, durations, hashes and consent."""
    base = root / FIXTURE_DIR
    if not base.is_dir():
        return []
    prefix = FIXTURE_DIR.as_posix()
    errors: list[str] = []
    line_validator = schema_validator(root, "dataset-line")
    consents, consent_errors = load_consents(base, schema_validator(root, "consent-record"))
    errors.extend(consent_errors)

    manifest = base / "manifest.jsonl"
    if not manifest.is_file():
        errors.append(f"{prefix}/manifest.jsonl is missing")
    else:
        for number, text in enumerate(manifest.read_text().splitlines(), 1):
            label = f"{prefix}/manifest.jsonl:{number}"
            try:
                line = json.loads(text)
            except ValueError as error:
                errors.append(f"{label}: {error}")
                continue
            problem = first_error(line_validator, line)
            if problem:
                errors.append(f"{label}: {problem}")
                continue
            errors.extend(check_clip(base, line, label))
            if "consent_id" in line:
                use = "training" if line.get("split", "train") == "train" else "evaluation"
                errors.extend(check_consent_reference(consents, line["consent_id"], label, use, line.get("speaker")))

    session_path = base / "session" / "session-manifest.json"
    if session_path.is_file():
        label = f"{prefix}/session/session-manifest.json"
        try:
            session = json.loads(session_path.read_text())
        except ValueError as error:
            return errors + [f"{label}: {error}"]
        problem = first_error(schema_validator(root, "session-manifest"), session)
        if problem:
            errors.append(f"{label}: {problem}")
        else:
            errors.extend(check_session_semantics(session, label))
            for recording in session["recordings"]:
                path = session_path.parent / recording["path"]
                name = f"{label}: {recording['path']}"
                if not path.is_file():
                    errors.append(f"{name} does not exist")
                    continue
                if path.stat().st_size != recording["size"]:
                    errors.append(f"{name} size does not match the file")
                if hashlib.sha256(path.read_bytes()).hexdigest() != recording["sha256"]:
                    errors.append(f"{name} sha256 does not match the file")
                if path.suffix == ".wav":
                    errors.extend(f"{name} {defect}" for defect in audio_checks.clip_defects(path, recording.get("duration_seconds")))
                    info = audio_checks.read_wav_info(path)
                    if recording.get("sample_rate_hz") not in (None, info.sample_rate):
                        errors.append(f"{name} sample_rate_hz does not match the WAV header")
                    if recording.get("channels") not in (None, info.channels):
                        errors.append(f"{name} channels does not match the WAV header")
            for consent_id in session["consent_ids"]:
                errors.extend(check_consent_reference(consents, consent_id, label))

    croissant = base / "croissant.json"
    if croissant.is_file():
        try:
            document = json.loads(croissant.read_text())
        except ValueError as error:
            return errors + [f"{prefix}/croissant.json: {error}"]
        for item in document.get("distribution", []):
            target = base / item.get("contentUrl", "")
            if not target.is_file():
                errors.append(f"{prefix}/croissant.json: {item.get('contentUrl')} does not exist")
            elif hashlib.sha256(target.read_bytes()).hexdigest() != item.get("sha256"):
                errors.append(f"{prefix}/croissant.json: sha256 of {item.get('contentUrl')} does not match the file")
    return errors


def check_readme(root: Path) -> list[str]:
    readme = root / "README.md"
    if not readme.is_file():
        return ["README.md is missing"]
    found = sections(readme.read_text())
    errors = []
    for name in README_SECTIONS:
        if find_section(found, name) is None:
            errors.append(f"README.md: missing section '{name}'")
    limitations = find_section(found, "Known limitations")
    if limitations is not None:
        words = normalize(limitations)
        if "tenant" not in words or "isolation" not in words:
            errors.append("README.md: Known limitations must state that isolation between tenants is not covered")
    return errors


def check_no_em_dash(root: Path) -> list[str]:
    errors = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts) or not path.is_file():
            continue
        if path.suffix in TEXT_SUFFIXES or path.name in {"llms.txt", "LICENSE"}:
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            if EM_DASH in text:
                errors.append(f"{relative}: contains the em dash character")
    return errors


def check_capability_map(root: Path, file: Path) -> list[str]:
    path = file if file.is_absolute() else root / file
    if not path.is_file():
        return [f"{file}: capability map not found"]
    rows: list[tuple[str, str]] = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells or set("".join(cells)) <= set("-: "):
            continue
        rows.append((normalize(cells[0]), stripped))
    errors = []
    for label, pattern in CAPABILITIES:
        matching = [row for first, row in rows if re.match(pattern, first)]
        if not matching:
            errors.append(f"{file}: no row for '{label}'")
        elif len(matching) > 1:
            errors.append(f"{file}: {len(matching)} rows for '{label}', expected one")
        elif "backblaze.com" not in matching[0]:
            errors.append(f"{file}: row '{label}' has no Backblaze documentation link")
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if PRICE.search(line):
            errors.append(f"{file}:{number}: contains a price figure; link the pricing page instead")
    return errors


def unbounded_requirements(lines: list[str]) -> list[str]:
    """Requirement lines (from a requirements file or a dependency list) that carry no upper bound."""
    found = []
    for line in lines:
        requirement = line.split("#", 1)[0].strip()
        if not requirement or requirement.startswith("-"):
            continue
        if not re.search(r"<|==|~=", requirement):
            found.append(requirement)
    return found


def load_ci(root: Path) -> tuple[dict | None, list[str]]:
    path = root / CI_WORKFLOW
    if not path.is_file():
        return None, [f"{CI_WORKFLOW.as_posix()} is missing"]
    try:
        workflow = yaml.safe_load(path.read_text())
    except yaml.YAMLError as error:
        return None, [f"{CI_WORKFLOW.as_posix()}: invalid YAML: {str(error).splitlines()[0]}"]
    if not isinstance(workflow, dict):
        return None, [f"{CI_WORKFLOW.as_posix()}: expected a mapping"]
    return workflow, []


def check_ci_policy(workflow: dict | None) -> list[str]:
    """The workflow grants read-only repository access and never runs on `pull_request_target`."""
    if workflow is None:
        return []
    label = CI_WORKFLOW.as_posix()
    errors = []
    if workflow.get("permissions") != {"contents": "read"}:
        errors.append(f"{label}: top-level permissions must be exactly {{contents: read}}")
    # YAML 1.1 reads the key `on` as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    if "pull_request_target" in json.dumps(triggers, default=str):
        errors.append(f"{label}: must not use pull_request_target")
    return errors


def ci_matrix(workflow: dict | None) -> list[str] | None:
    """The `app` values of the `applications` job matrix, or None when the job or matrix is missing."""
    try:
        values = workflow["jobs"]["applications"]["strategy"]["matrix"]["app"]
    except (KeyError, TypeError):
        return None
    return [str(value) for value in values] if isinstance(values, list) else None


def application_folders(root: Path) -> list[Path]:
    base = root / APPLICATIONS_DIR
    if not base.is_dir():
        return []
    return sorted(path for path in base.iterdir()
                  if path.is_dir() and not path.name.startswith(".") and path.name not in SKIP_DIRS)


def check_application_pyproject(folder: Path, label: str, package: str) -> list[str]:
    path = folder / "pyproject.toml"
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        return [f"{label}: pyproject.toml cannot be read: {str(error).splitlines()[0]}"]
    errors = []
    project = data.get("project", {})
    if project.get("name") != folder.name:
        errors.append(f"{label}: pyproject.toml project name must be {folder.name}")
    if not project.get("requires-python"):
        errors.append(f"{label}: pyproject.toml needs requires-python")
    if "setuptools" not in str(data.get("build-system", {}).get("build-backend", "")):
        errors.append(f"{label}: pyproject.toml [build-system] must use setuptools")
    command = project.get("scripts", {}).get(folder.name)
    if not isinstance(command, str) or not command.startswith(f"{package}.cli:"):
        errors.append(f"{label}: [project.scripts] needs {folder.name} = \"{package}.cli:<function>\"")
    package_data = data.get("tool", {}).get("setuptools", {}).get("package-data", {})
    if not any("schemas/*.json" in patterns for patterns in package_data.values() if isinstance(patterns, list)):
        errors.append(f"{label}: [tool.setuptools.package-data] must include schemas/*.json")
    pytest_options = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    if pytest_options.get("testpaths") != ["tests"]:
        errors.append(f"{label}: [tool.pytest.ini_options] testpaths must be [\"tests\"]")
    if pytest_options.get("pythonpath") != ["src"]:
        errors.append(f"{label}: [tool.pytest.ini_options] pythonpath must be [\"src\"]")
    if not any(str(marker).startswith("live") for marker in pytest_options.get("markers", [])):
        errors.append(f"{label}: [tool.pytest.ini_options] must declare the live marker")
    for requirement in unbounded_requirements(list(project.get("dependencies", []))):
        errors.append(f"{label}: pyproject.toml dependency {requirement} has no upper bound")
    return errors


def check_application_copies(root: Path, folder: Path, label: str, package_dir: Path) -> list[str]:
    """Code under `shared/` and schemas under `schemas/` must be unchanged copies of their sources."""
    errors = []
    shared = package_dir / "shared"
    if shared.is_dir():
        if not (shared / "__init__.py").is_file():
            errors.append(f"{label}: src/{package_dir.name}/shared/__init__.py is missing")
        for copy in sorted(shared.glob("*.py")):
            if copy.name == "__init__.py":
                continue
            sources = [root / "examples" / "python" / copy.name, root / "tools" / copy.name]
            source = next((item for item in sources if item.is_file()), None)
            if source is None:
                errors.append(f"{label}: shared/{copy.name} has no source in examples/python/ or tools/")
            elif source.read_bytes() != copy.read_bytes():
                errors.append(f"{label}: shared/{copy.name} differs from {source.relative_to(root).as_posix()}; copy it again")
    schemas = package_dir / "schemas"
    if schemas.is_dir():
        for copy in sorted(schemas.glob("*.json")):
            source = root / "schemas" / copy.name
            if not source.is_file():
                errors.append(f"{label}: schemas/{copy.name} has no source in schemas/")
            elif source.read_bytes() != copy.read_bytes():
                errors.append(f"{label}: schemas/{copy.name} differs from schemas/{copy.name} at the repository root; copy it again")
    return errors


def check_application(root: Path, folder: Path, matrix: list[str] | None, readme_sections: list[tuple[int, str, str]]) -> list[str]:
    """Check one `applications/<name>/` folder against the rules in AGENTS.md."""
    name = folder.name
    label = f"{APPLICATIONS_DIR}/{name}"
    package = name.replace("-", "_")
    errors = [f"{label}: missing {item}" for item in APPLICATION_FILES if not (folder / item).is_file()]
    package_dir = folder / "src" / package
    if not package_dir.is_dir():
        errors.append(f"{label}: missing src/{package}/")
    else:
        errors.extend(f"{label}: missing src/{package}/{item}" for item in APPLICATION_SOURCE_FILES
                      if not (package_dir / item).is_file())
        errors.extend(check_application_copies(root, folder, label, package_dir))
        for path in sorted(package_dir.rglob("*.py")):
            text = path.read_text()
            for pattern in BUCKET_CONFIGURATION:
                if pattern in text:
                    errors.append(f"{label}: {path.relative_to(folder).as_posix()} contains '{pattern}'; "
                                  "bucket configuration ships as templates and commands, never as application code")
    errors.extend(f"{label}: missing tests/{item}" for item in APPLICATION_TEST_FILES
                  if not (folder / "tests" / item).is_file())
    if list(folder.rglob("package.json")):
        errors.append(f"{label}: contains a package.json; applications are Python command-line tools")
    if (folder / "pyproject.toml").is_file():
        errors.extend(check_application_pyproject(folder, label, package))
    for requirements in ("requirements.txt", "requirements-dev.txt"):
        path = folder / requirements
        if path.is_file():
            errors.extend(f"{label}: {requirements}: {item} has no upper bound"
                          for item in unbounded_requirements(path.read_text().splitlines()))
    environment = folder / ".env.example"
    if environment.is_file():
        text = environment.read_text()
        for variable in APPLICATION_ENV_NAMES:
            if not re.search(rf"^{variable}=", text, re.MULTILINE):
                errors.append(f"{label}: .env.example must set {variable}")
        if not PUBLIC_URL_BASE_COMMENTED.search(text):
            errors.append(f"{label}: .env.example must carry a commented # B2_PUBLIC_URL_BASE= line")
        if ENDPOINT_VARIABLE.search(text):
            errors.append(f"{label}: .env.example must not define an endpoint or key id alias "
                          "(B2_ENDPOINT, B2_S3_ENDPOINT, B2_KEY_ID); the endpoint is derived from B2_REGION")
    gitignore = folder / ".gitignore"
    if gitignore.is_file():
        entries = {line.strip() for line in gitignore.read_text().splitlines()}
        errors.extend(f"{label}: .gitignore must list {entry}" for entry in APPLICATION_GITIGNORE if entry not in entries)
    readme = folder / "README.md"
    if readme.is_file():
        text = readme.read_text()
        found = sections(text)
        errors.extend(f"{label}: README.md missing section '{item}'" for item in APPLICATION_README_SECTIONS
                      if find_section(found, item) is None)
        if ("--lifecycle-rule" in text or "put-bucket-cors" in text) and not re.search(r"\breplaces\b", text, re.IGNORECASE):
            errors.append(f"{label}: README.md shows a lifecycle or CORS command; say that it replaces all of the bucket's rules")
    if matrix is None or name not in matrix:
        errors.append(f"{label}: not listed in the applications job matrix of {CI_WORKFLOW.as_posix()}")
    entry = next((body for level, title, body in readme_sections
                  if level == 2 and normalize(title) == normalize(name)), None)
    if entry is None:
        errors.append(f"{label}: applications/README.md has no section '## {name}'")
    else:
        inner = sections(entry)
        errors.extend(f"{label}: applications/README.md section '{name}' missing '{item}'"
                      for item in APPLICATION_SECTIONS if find_section(inner, item) is None)
    return errors


def application_target(link: str) -> str | None:
    """The folder name of a `links.application` value shaped `applications/<name>/README.md`."""
    match = re.fullmatch(rf"{APPLICATIONS_DIR}/([A-Za-z0-9][A-Za-z0-9_-]*)/README\.md", link)
    return match.group(1) if match else None


def check_applications(root: Path, catalog: dict, minimum: int | None, workflow: dict | None) -> list[str]:
    """Check every application folder, the catalog links to them and the minimum count."""
    readme = root / APPLICATIONS_DIR / "README.md"
    readme_sections = sections(readme.read_text()) if readme.is_file() else []
    matrix = ci_matrix(workflow)
    errors: list[str] = []
    problems: dict[str, list[str]] = {}
    for folder in application_folders(root):
        problems[folder.name] = check_application(root, folder, matrix, readme_sections)
        errors.extend(problems[folder.name])
    valid_links = 0
    for entry in catalog["architectures"]:
        link = entry["links"]["application"]
        if not link:
            continue
        target = application_target(link)
        if target is None or target not in problems:
            errors.append(f"{entry['id']}: links.application must be applications/<name>/README.md of an application folder, got {link}")
        elif problems[target]:
            errors.append(f"{entry['id']}: links.application points to {target}, which does not pass the application rules")
        else:
            valid_links += 1
    if minimum is not None and valid_links < minimum:
        errors.append(f"{valid_links} catalog entries link a valid application, expected at least {minimum}")
    return errors


def validate_repository(root: Path = ROOT, capability_map: Path | None = None, require_applications: int | None = None) -> list[str]:
    errors = check_schemas(root) + check_manifest_examples(root) + check_fixtures(root)
    try:
        catalog = generate_catalog.load_catalog(root)
    except (OSError, ValueError, jsonschema.SchemaError) as error:
        return errors + [str(error)]
    seen: set[str] = set()
    for entry in catalog["architectures"]:
        if entry["id"] in seen:
            errors.append(f"{entry['id']}: duplicate id")
        seen.add(entry["id"])
        errors.extend(check_architecture(root, entry))
    errors.extend(check_readme(root))
    workflow, ci_errors = load_ci(root)
    errors.extend(ci_errors)
    errors.extend(check_ci_policy(workflow))
    errors.extend(check_applications(root, catalog, require_applications, workflow))
    errors.extend(check_no_em_dash(root))
    if capability_map is not None:
        errors.extend(check_capability_map(root, capability_map))
    return errors


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--capability-map", type=Path, help="Markdown capability map to check")
    parser.add_argument("--require-applications", type=int,
                        help="minimum number of catalog entries that link a valid application")
    args = parser.parse_args(argv)
    errors = validate_repository(root, args.capability_map, args.require_applications)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("catalog and contracts valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
