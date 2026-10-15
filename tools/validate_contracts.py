#!/usr/bin/env python3
"""Strictly validate the catalog, every architecture it lists, schemas and manifests.

With no arguments the whole repository is validated:

* every JSON Schema is valid and carries this repository's `$id` prefix;
* every `manifests/<name>.example.json` validates against `schemas/<name>.schema.json`;
* the catalog validates against its schema and every entry is checked: the three
  architecture files, the required sections, a Mermaid diagram, boundary coverage,
  dated evidence, a validation status that claims no more than its evidence, and the
  session evaluation step of the voice agent session archive;
* the README has its required sections;
* no text file contains the em dash character.

Options:
  --capability-map FILE   check one row per required B2 capability and no price figure
  --require-applications N   require at least N public `links.application` repositories
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_catalog  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ID_PREFIX = "urn:backblaze:b2-voice-ai-reference-architectures:"
ARCHITECTURE_FILES = ("architecture.md", "decisions.md", "object-layout.md")
SESSION_ARCHIVE_ID = "voice-agent-session-archive"
EM_DASH = chr(0x2014)
TEXT_SUFFIXES = {".md", ".py", ".yml", ".yaml", ".json", ".jsonl", ".txt", ".cff", ".sh", ".ts", ".toml", ".cfg", ".ini"}
SKIP_DIRS = {".git", ".venv", "node_modules", ".pytest_cache", "__pycache__"}

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
GITHUB_REPO = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
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
        if name == "release-manifest":
            if instance["fingerprint"] != instance["integrity"]["content_fingerprint"]:
                errors.append(f"manifests/{path.name}: fingerprint differs from integrity.content_fingerprint")
            if instance["statistics"]["file_count"] != len(instance["files"]):
                errors.append(f"manifests/{path.name}: statistics.file_count does not match files")
            if instance["statistics"]["total_bytes"] != sum(item["size"] for item in instance["files"]):
                errors.append(f"manifests/{path.name}: statistics.total_bytes does not match files")
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


def is_public_repository(url: str) -> bool:
    """True when the GitHub repository answers an unauthenticated API request."""
    match = GITHUB_REPO.match(url)
    if not match:
        return False
    request = urllib.request.Request(
        f"https://api.github.com/repos/{match.group(1)}/{match.group(2)}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "b2-voice-ai-reference-architectures"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError):
        return False


def check_applications(catalog: dict, minimum: int, public=is_public_repository) -> list[str]:
    urls = sorted({
        entry["links"]["application"]
        for entry in catalog["architectures"]
        if entry["links"]["application"] and entry["links"]["application"].startswith("https://")
    })
    public_urls = [url for url in urls if public(url)]
    if len(public_urls) < minimum:
        return [f"{len(public_urls)} public links.application repositories, expected at least {minimum}"]
    return []


def validate_repository(root: Path = ROOT, capability_map: Path | None = None, require_applications: int | None = None,
                        public=is_public_repository) -> list[str]:
    errors = check_schemas(root) + check_manifest_examples(root)
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
    errors.extend(check_no_em_dash(root))
    if capability_map is not None:
        errors.extend(check_capability_map(root, capability_map))
    if require_applications is not None:
        errors.extend(check_applications(catalog, require_applications, public))
    return errors


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--capability-map", type=Path, help="Markdown capability map to check")
    parser.add_argument("--require-applications", type=int, help="minimum number of public links.application repositories")
    args = parser.parse_args(argv)
    errors = validate_repository(root, args.capability_map, args.require_applications)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("catalog and contracts valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
