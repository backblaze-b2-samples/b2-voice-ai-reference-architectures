"""Tests for the catalog tooling: every validator rule has a passing and a failing case."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest
import yaml

import check_markdown_links
import generate_catalog
import validate_contracts

REPO = Path(__file__).resolve().parents[1]
ENTRY_ID = "voice-training-data-lifecycle"
SESSION_ID = "voice-agent-session-archive"

ALL_SECTIONS = ["Boundaries", "Where B2 does not fit", "Evidence", "Security"]

# A minimal README with the required sections and generated-section markers. The tests use
# it instead of the repository's README, whose links point to files a temporary repo lacks.
README_FIXTURE = """# Fixture repository

## Architectures

<!-- catalog:architectures:start -->
<!-- catalog:architectures:end -->

## Where state lives

The catalog is the source of the generated tables.

## Clean up

Nothing is created.

## Known limitations

Isolation between tenants is not covered: the layouts assume a single organization.

## Partner integration options

<!-- catalog:integrations:start -->
<!-- catalog:integrations:end -->
"""


def architecture_md(sections=tuple(ALL_SECTIONS), mermaid=True, covered=("Real-time voice transport", "Message queue"),
                    dated=True, session_evaluation=False):
    parts = ["# Architecture", ""]
    if mermaid:
        parts += ["```mermaid", "flowchart LR", "  A --> B", "```", ""]
    bodies = {
        "Boundaries": "| Boundary | B2 role |\n|---|---|\n" + "\n".join(f"| {c} | Not provided by B2 |" for c in covered),
        "Where B2 does not fit": "- Sub-second conversational state.",
        "Evidence": "Reviewed 2026-10-15 against provider documentation." if dated else "Reviewed against provider documentation.",
        "Security": "Scoped keys and short-lived presigned URLs.",
    }
    for name in sections:
        parts += [f"## {name}", "", bodies[name], ""]
    if session_evaluation:
        parts += ["## Session evaluation", "", "Each session gets a quality and safety evaluation before selection.", ""]
    return "\n".join(parts)


def entry(arch_id=ENTRY_ID):
    return {
        "id": arch_id,
        "name": "Test architecture",
        "slug": arch_id,
        "summary": "Summary.",
        "audience": ["Platform engineers"],
        "problem": "Problem.",
        "workflow": ["Ingest", "Validate"],
        "boundaries": ["real-time-voice-transport", "message-queue"],
        "capabilities": ["S3-compatible API"],
        "integrations": [{
            "id": "tool-a", "name": "Tool A", "role": "Role", "connection": "S3 output",
            "connection_type": "native-s3-output", "url": "https://example.com/a", "evidence": "Docs reviewed 2026-10-15",
        }],
        "prerequisites": ["A bucket"],
        "expected_scale": {"profile": "Small", "notes": "Notes."},
        "validation": {
            "status": "documentation-reviewed", "date": "2026-10-15", "methods": ["Documentation review"],
            "evidence": ["https://example.com/docs", f"architectures/{arch_id}/architecture.md"],
        },
        "security_considerations": ["Scoped keys"],
        "compliance_considerations": ["Consent records"],
        "links": {
            "architecture": f"architectures/{arch_id}/architecture.md", "application": None,
            "deployment": None, "sample_data": None, "release_contract": None,
        },
    }


class Repo:
    def __init__(self, root: Path):
        self.root = root

    def write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def catalog(self) -> dict:
        return yaml.safe_load((self.root / "catalog" / "architectures.yaml").read_text())

    def save(self, catalog: dict) -> None:
        self.write("catalog/architectures.yaml", yaml.safe_dump(catalog, sort_keys=False))

    def add_architecture(self, arch_id=ENTRY_ID, **kwargs) -> dict:
        catalog = self.catalog()
        item = entry(arch_id)
        catalog["architectures"].append(item)
        self.save(catalog)
        folder = f"architectures/{arch_id}"
        self.write(f"{folder}/architecture.md", architecture_md(**kwargs))
        self.write(f"{folder}/decisions.md", "# Decisions\n\nOne decision.\n")
        self.write(f"{folder}/object-layout.md", "# Object layout\n\nOne bucket per zone.\n")
        return item

    def regenerate(self) -> None:
        assert generate_catalog.main([], root=self.root) == 0

    def errors(self, **kwargs) -> list[str]:
        return validate_contracts.validate_repository(self.root, **kwargs)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    shutil.copytree(REPO / "schemas", tmp_path / "schemas")
    (tmp_path / "README.md").write_text(README_FIXTURE)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(ci_text(None))
    # Start from the repository's catalog header with no entries, so each test adds only what it checks.
    catalog = yaml.safe_load((REPO / "catalog" / "architectures.yaml").read_text())
    catalog["architectures"] = []
    mini = Repo(tmp_path)
    mini.save(catalog)
    return mini


@pytest.fixture
def valid(repo: Repo) -> Repo:
    repo.add_architecture()
    repo.regenerate()
    return repo


def has(errors: list[str], text: str) -> bool:
    return any(text in error for error in errors)


# --- the repository itself and the passing cases ---------------------------------

def test_repository_is_valid():
    assert validate_contracts.validate_repository(REPO) == []


def test_repository_generated_files_are_current():
    assert generate_catalog.main(["--check"], root=REPO) == 0


def test_empty_catalog_passes(repo: Repo):
    assert repo.errors() == []


def test_valid_entry_passes(valid: Repo):
    assert valid.errors() == []


def test_session_archive_with_evaluation_passes(repo: Repo):
    repo.add_architecture(SESSION_ID, session_evaluation=True)
    assert repo.errors() == []


def test_boundary_enum_matches_validator_labels():
    schema = json.loads((REPO / "schemas" / "architecture-catalog.schema.json").read_text())
    enum = schema["$defs"]["architecture"]["properties"]["boundaries"]["items"]["enum"]
    assert sorted(enum) == sorted(validate_contracts.BOUNDARIES)


# --- catalog schema --------------------------------------------------------------

@pytest.mark.parametrize("field", ["boundaries", "integrations", "validation", "links"])
def test_missing_required_field_fails(repo: Repo, field: str):
    repo.add_architecture()
    catalog = repo.catalog()
    del catalog["architectures"][0][field]
    repo.save(catalog)
    assert has(repo.errors(), "catalog validation failed")


def test_unknown_boundary_fails(repo: Repo):
    repo.add_architecture()
    catalog = repo.catalog()
    catalog["architectures"][0]["boundaries"] = ["storage-engine"]
    repo.save(catalog)
    assert has(repo.errors(), "catalog validation failed")


def test_unknown_validation_status_fails(repo: Repo):
    repo.add_architecture()
    catalog = repo.catalog()
    catalog["architectures"][0]["validation"]["status"] = "production-proven"
    repo.save(catalog)
    assert has(repo.errors(), "catalog validation failed")


def test_missing_local_evidence_path_fails(repo: Repo):
    repo.add_architecture()
    catalog = repo.catalog()
    catalog["architectures"][0]["validation"]["evidence"].append("tests/missing.py")
    repo.save(catalog)
    assert has(repo.errors(), "missing local paths")


def test_duplicate_id_fails(valid: Repo):
    catalog = valid.catalog()
    catalog["architectures"].append(copy.deepcopy(catalog["architectures"][0]))
    valid.save(catalog)
    assert has(valid.errors(), "duplicate id")


def test_slug_must_equal_id(valid: Repo):
    catalog = valid.catalog()
    catalog["architectures"][0]["slug"] = "other"
    valid.save(catalog)
    assert has(valid.errors(), "slug other must equal the id")


def test_architecture_link_must_follow_folder(valid: Repo):
    valid.write("architectures/other/architecture.md", "x")
    catalog = valid.catalog()
    catalog["architectures"][0]["links"]["architecture"] = "architectures/other/architecture.md"
    valid.save(catalog)
    assert has(valid.errors(), "links.architecture must be")


# --- the three architecture files and their sections ------------------------------

@pytest.mark.parametrize("name", ["decisions.md", "object-layout.md"])
def test_missing_architecture_file_fails(valid: Repo, name: str):
    (valid.root / "architectures" / ENTRY_ID / name).unlink()
    assert has(valid.errors(), f"missing or empty architectures/{ENTRY_ID}/{name}")


@pytest.mark.parametrize("name", ["architecture.md", "decisions.md", "object-layout.md"])
def test_empty_architecture_file_fails(valid: Repo, name: str):
    (valid.root / "architectures" / ENTRY_ID / name).write_text("\n")
    assert has(valid.errors(), f"missing or empty architectures/{ENTRY_ID}/{name}")


def test_missing_architecture_md_fails(valid: Repo):
    (valid.root / "architectures" / ENTRY_ID / "architecture.md").unlink()
    assert has(valid.errors(), "missing local paths")


@pytest.mark.parametrize("section", ALL_SECTIONS)
def test_missing_section_fails(repo: Repo, section: str):
    repo.add_architecture(sections=[s for s in ALL_SECTIONS if s != section])
    assert has(repo.errors(), f"missing section '{section}'")


def test_missing_object_layout_section_fails(valid: Repo):
    valid.write(f"architectures/{ENTRY_ID}/object-layout.md", "# Layout\n\nText.\n")
    assert has(valid.errors(), "missing section 'Object layout'")


def test_headings_inside_code_fences_do_not_count(repo: Repo):
    repo.add_architecture(sections=[s for s in ALL_SECTIONS if s != "Security"])
    path = repo.root / "architectures" / ENTRY_ID / "architecture.md"
    path.write_text(path.read_text() + "\n```bash\n## Security\n```\n")
    assert has(repo.errors(), "missing section 'Security'")


def test_missing_mermaid_fails(repo: Repo):
    repo.add_architecture(mermaid=False)
    assert has(repo.errors(), "no Mermaid diagram")


def test_empty_mermaid_block_fails(repo: Repo):
    repo.add_architecture(mermaid=False)
    path = repo.root / "architectures" / ENTRY_ID / "architecture.md"
    path.write_text(path.read_text() + "\n```mermaid\n```\n")
    assert has(repo.errors(), "no Mermaid diagram")


def test_uncovered_boundary_fails(repo: Repo):
    repo.add_architecture(covered=("Real-time voice transport",))
    assert has(repo.errors(), "does not cover 'Message queue'")


def test_evidence_without_date_fails(repo: Repo):
    repo.add_architecture(dated=False)
    assert has(repo.errors(), "Evidence section has no dated entry")


# --- validation status ------------------------------------------------------------

def test_documentation_only_evidence_cannot_claim_more(valid: Repo):
    catalog = valid.catalog()
    catalog["architectures"][0]["validation"]["status"] = "source-reviewed-and-automated-tests"
    valid.save(catalog)
    assert has(valid.errors(), "all evidence is documentation")


def test_source_reviewed_needs_non_documentation_evidence(valid: Repo):
    catalog = valid.catalog()
    catalog["architectures"][0]["validation"]["status"] = "source-reviewed"
    valid.save(catalog)
    assert has(valid.errors(), "all evidence is documentation")


def test_automated_tests_status_needs_test_evidence(valid: Repo):
    valid.write("scripts/notes.md", "notes")
    catalog = valid.catalog()
    validation = catalog["architectures"][0]["validation"]
    validation["status"] = "source-reviewed-and-automated-tests"
    validation["evidence"].append("scripts/notes.md")
    valid.save(catalog)
    assert has(valid.errors(), "needs evidence under tests/")


def test_automated_tests_status_with_test_evidence_passes(valid: Repo):
    valid.write("tests/test_example.py", "def test_x():\n    pass\n")
    catalog = valid.catalog()
    validation = catalog["architectures"][0]["validation"]
    validation["status"] = "source-reviewed-and-automated-tests"
    validation["evidence"].append("tests/test_example.py")
    valid.save(catalog)
    assert valid.errors() == []


def test_sample_repository_url_counts_as_test_evidence(valid: Repo):
    catalog = valid.catalog()
    validation = catalog["architectures"][0]["validation"]
    validation["status"] = "source-reviewed-and-automated-tests"
    validation["evidence"].append("https://github.com/backblaze-b2-samples/example-sample")
    valid.save(catalog)
    assert valid.errors() == []


# --- session evaluation ------------------------------------------------------------

def test_session_archive_requires_evaluation_section(repo: Repo):
    repo.add_architecture(SESSION_ID)
    assert has(repo.errors(), "missing section 'Session evaluation'")


def test_session_evaluation_must_mention_quality_and_safety(repo: Repo):
    repo.add_architecture(SESSION_ID)
    path = repo.root / "architectures" / SESSION_ID / "architecture.md"
    path.write_text(path.read_text() + "\n## Session evaluation\n\nSessions are reviewed.\n")
    errors = repo.errors()
    assert has(errors, "does not mention quality") and has(errors, "does not mention safety")


# --- README ------------------------------------------------------------------------

@pytest.mark.parametrize("section", validate_contracts.README_SECTIONS)
def test_readme_missing_section_fails(valid: Repo, section: str):
    path = valid.root / "README.md"
    path.write_text(path.read_text().replace(f"## {section}", "## Other"))
    assert has(valid.errors(), f"missing section '{section}'")


def test_readme_known_limitations_must_name_tenant_isolation(valid: Repo):
    path = valid.root / "README.md"
    path.write_text(path.read_text().replace(
        "Isolation between tenants is not covered: the layouts assume a single organization.", "Nothing to note."))
    assert has(valid.errors(), "isolation between tenants")


def test_missing_readme_fails(repo: Repo):
    (repo.root / "README.md").unlink()
    assert has(repo.errors(), "README.md")


# --- schemas and manifest examples ---------------------------------------------------

def test_schema_id_prefix_is_enforced(repo: Repo):
    path = repo.root / "schemas" / "release-manifest.schema.json"
    schema = json.loads(path.read_text())
    schema["$id"] = "urn:example:release"
    path.write_text(json.dumps(schema))
    assert has(repo.errors(), "$id must start with")


def test_manifest_example_without_schema_fails(repo: Repo):
    repo.write("manifests/unknown.example.json", "{}")
    assert has(repo.errors(), "no schemas/unknown.schema.json")


def release_example() -> dict:
    sha = "a" * 64
    return {
        "schema_version": "1.0.0", "id": "voice@v1", "dataset": "voice", "version": "v1",
        "manifest_format": "nemo-jsonl", "modalities": ["audio"], "license": "CC-BY-4.0",
        "provenance": {"source_type": "synthetic", "generated_by": "tools/make_fixtures.py", "inputs": []},
        "quality": {"status": "passed", "validator": "tools/validate_contracts.py", "checks": ["schema"]},
        "statistics": {"file_count": 1, "total_bytes": 10},
        "integrity": {"algorithm": "sha256", "content_fingerprint": sha}, "fingerprint": sha,
        "files": [{"path": "audio/a.wav", "size": 10, "sha256": sha, "encoding_format": "audio/wav"}],
    }


def test_release_manifest_example_passes(repo: Repo):
    repo.write("manifests/release-manifest.example.json", json.dumps(release_example()))
    assert repo.errors() == []


@pytest.mark.parametrize("field,value,message", [
    ("fingerprint", "b" * 64, "fingerprint differs"),
    ("statistics", {"file_count": 2, "total_bytes": 10}, "file_count"),
    ("statistics", {"file_count": 1, "total_bytes": 11}, "total_bytes"),
])
def test_release_manifest_consistency_fails(repo: Repo, field: str, value, message: str):
    example = release_example()
    example[field] = value
    repo.write("manifests/release-manifest.example.json", json.dumps(example))
    assert has(repo.errors(), message)


def test_release_manifest_schema_violation_fails(repo: Repo):
    example = release_example()
    example["modalities"] = ["image"]
    repo.write("manifests/release-manifest.example.json", json.dumps(example))
    assert has(repo.errors(), "manifests/release-manifest.example.json")


# --- em dash -------------------------------------------------------------------------

def test_em_dash_in_text_fails(valid: Repo):
    valid.write("docs/note.md", "A sentence " + chr(0x2014) + " with a dash.\n")
    assert has(valid.errors(), "docs/note.md: contains the em dash")


def test_em_dash_in_dependency_folder_is_ignored(valid: Repo):
    valid.write(".venv/lib/note.md", chr(0x2014))
    valid.write("node_modules/pkg/readme.md", chr(0x2014))
    assert valid.errors() == []


# --- capability map ------------------------------------------------------------------

DOC = "https://www.backblaze.com/docs/cloud-storage"
MAP_LABELS = [label for label, _ in validate_contracts.CAPABILITIES]


def capability_map(skip=None, duplicate=None, extra="", no_link=None) -> str:
    lines = ["# Capabilities", "", "| Capability | What it does | Link |", "|---|---|---|"]
    for label in MAP_LABELS:
        if label == skip:
            continue
        link = "none" if label == no_link else f"[docs]({DOC})"
        lines.append(f"| **{label}** | Does a thing. | {link} |")
        if label == duplicate:
            lines.append(f"| {label} | Again. | [docs]({DOC}) |")
    return "\n".join(lines + ["", extra, ""])


def check_map(repo: Repo, text: str) -> list[str]:
    repo.write("docs/b2-capabilities.md", text)
    return repo.errors(capability_map=Path("docs/b2-capabilities.md"))


def test_capability_map_passes(repo: Repo):
    assert check_map(repo, capability_map()) == []


@pytest.mark.parametrize("label", MAP_LABELS)
def test_capability_map_missing_row_fails(repo: Repo, label: str):
    assert has(check_map(repo, capability_map(skip=label)), f"no row for '{label}'")


def test_capability_map_duplicate_row_fails(repo: Repo):
    assert has(check_map(repo, capability_map(duplicate="Object Lock")), "2 rows for 'Object Lock'")


def test_capability_map_row_needs_documentation_link(repo: Repo):
    assert has(check_map(repo, capability_map(no_link="Object Lock")), "no Backblaze documentation link")


@pytest.mark.parametrize("text", [
    "Storage costs $6 per month.", "It costs 6 USD.", "About 0.5 cents each.", "Egress at 10/GB.", "Priced at 6 per TB.",
])
def test_capability_map_price_figure_fails(repo: Repo, text: str):
    assert has(check_map(repo, capability_map(extra=text)), "price figure")


def test_capability_map_without_prices_may_link_pricing(repo: Repo):
    text = capability_map(extra="See [pricing](https://www.backblaze.com/cloud-storage/pricing) for current terms.")
    assert check_map(repo, text) == []


def test_missing_capability_map_file_fails(repo: Repo):
    assert has(repo.errors(capability_map=Path("docs/none.md")), "capability map not found")


# --- applications ----------------------------------------------------------------------

APP = "voice-dataset-ingest"
OTHER_APP = "voice-session-archive"
SHARED_SOURCE = "def multipart_upload():\n    return 'unchanged'\n"

PYPROJECT_TEMPLATE = """[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["boto3>=1.34,<2", "jsonschema>=4.23,<5", "python-dotenv>=1.0,<2"]

[project.scripts]
{name} = "{package}.cli:main"

[tool.setuptools.package-data]
{package} = ["schemas/*.json"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
markers = ["live: runs only against a real B2 account"]
"""

ENV_EXAMPLE = """B2_APPLICATION_KEY_ID=<key-id>
B2_APPLICATION_KEY=<application-key>
B2_BUCKET_NAME=<bucket-name>
B2_REGION=<region>
# B2_PUBLIC_URL_BASE=
"""

GITIGNORE = ".env\n*.egg-info/\n.venv/\n__pycache__/\n.pytest_cache/\n.local-state/\n"

APP_README = """# Application

## B2's role

It writes objects to one bucket.

## Setup

Install it.

## Tests

Run pytest.

## Known limitations

Not run against a live account.
"""

APPLICATION_SECTION = """## {name}

### What it shows

A workflow.

### Architecture

`{arch}`.

### Configurations it ships

Templates only.

### How to run

Run the command.

### Known limitations

Emulated S3 only.
"""


def ci_text(apps=(APP,), permissions="permissions:\n  contents: read\n", trigger="on: [push, pull_request]") -> str:
    text = f"name: ci\n{trigger}\n{permissions}jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo test\n"
    if apps is not None:
        text += (
            "  applications:\n    name: Application ${{ matrix.app }}\n    runs-on: ubuntu-latest\n"
            f"    strategy:\n      matrix:\n        app: [{', '.join(apps)}]\n    steps:\n      - run: echo app\n"
        )
    return text


def application_files(repo: Repo, name: str) -> dict[str, str | None]:
    package = name.replace("-", "_")
    base = f"applications/{name}"
    return {
        f"{base}/pyproject.toml": PYPROJECT_TEMPLATE.format(name=name, package=package),
        f"{base}/requirements.txt": "boto3>=1.34,<2\njsonschema>=4.23,<5\npython-dotenv>=1.0,<2\n",
        f"{base}/requirements-dev.txt": "-r requirements.txt\npytest>=8,<9\nmoto[server]>=5,<6\n",
        f"{base}/.gitignore": GITIGNORE,
        f"{base}/.env.example": ENV_EXAMPLE,
        f"{base}/README.md": APP_README,
        f"{base}/src/{package}/__init__.py": "",
        f"{base}/src/{package}/cli.py": "def main():\n    return 0\n",
        f"{base}/src/{package}/config.py": "CONFIG = {}\n",
        f"{base}/src/{package}/storage.py": "def make_client():\n    return None\n",
        f"{base}/src/{package}/shared/__init__.py": "",
        f"{base}/src/{package}/shared/multipart.py": SHARED_SOURCE,
        f"{base}/src/{package}/schemas/consent-record.schema.json":
            (repo.root / "schemas" / "consent-record.schema.json").read_text(),
        f"{base}/tests/conftest.py": "",
        f"{base}/tests/test_live.py": "",
    }


def add_application(repo: Repo, name: str = APP, arch: str = ENTRY_ID, files: dict | None = None,
                    matrix: list[str] | None = None, section: bool = True, link: bool = True) -> None:
    """Write a valid application, its CI matrix entry, its README section and its catalog link."""
    repo.write("examples/python/multipart.py", SHARED_SOURCE)
    for path, text in {**application_files(repo, name), **(files or {})}.items():
        if text is None:
            continue
        repo.write(path, text)
    names = sorted({*(application_matrix(repo)), *( [name] if matrix is None else matrix)})
    repo.write(".github/workflows/ci.yml", ci_text(names))
    readme = repo.root / "applications" / "README.md"
    text = readme.read_text() if readme.exists() else "# Sample applications\n\n"
    if section:
        text += APPLICATION_SECTION.format(name=name, arch=arch) + "\n"
    repo.write("applications/README.md", text)
    if link:
        if not any(item["id"] == arch for item in repo.catalog()["architectures"]):
            repo.add_architecture(arch, session_evaluation=(arch == SESSION_ID))
        catalog = repo.catalog()
        for item in catalog["architectures"]:
            if item["id"] == arch:
                item["links"]["application"] = f"applications/{name}/README.md"
        repo.save(catalog)
        repo.regenerate()


def application_matrix(repo: Repo) -> list[str]:
    workflow = yaml.safe_load((repo.root / ".github" / "workflows" / "ci.yml").read_text())
    return validate_contracts.ci_matrix(workflow) or []


def rewrite(repo: Repo, relative: str, old: str, new: str) -> None:
    text = (repo.root / relative).read_text()
    assert old in text, f"{old!r} not in {relative}"
    repo.write(relative, text.replace(old, new))


def application_errors(repo: Repo) -> list[str]:
    return [error for error in repo.errors() if error.startswith("applications/") or "links.application" in error]


@pytest.fixture
def app_repo(repo: Repo) -> Repo:
    add_application(repo)
    return repo


def test_valid_application_passes(app_repo: Repo):
    assert app_repo.errors() == []
    assert application_errors(app_repo) == []


@pytest.mark.parametrize("missing", [
    "pyproject.toml", "requirements.txt", "requirements-dev.txt", ".gitignore", ".env.example", "README.md",
    "src/voice_dataset_ingest/cli.py", "src/voice_dataset_ingest/config.py", "src/voice_dataset_ingest/storage.py",
    "tests/conftest.py", "tests/test_live.py",
])
def test_application_shape_is_required(repo: Repo, missing: str):
    add_application(repo, link=False)
    assert repo.errors() == []
    (repo.root / "applications" / APP / missing).unlink()
    assert has(repo.errors(), f"applications/{APP}: missing {missing}")


def test_application_shape_rejects_package_json_and_bad_project_files(repo: Repo):
    add_application(repo)
    repo.write(f"applications/{APP}/web/package.json", "{}\n")
    assert has(repo.errors(), "package.json")
    (repo.root / "applications" / APP / "web" / "package.json").unlink()
    assert repo.errors() == []
    rewrite(repo, f"applications/{APP}/pyproject.toml", 'name = "voice-dataset-ingest"', 'name = "other"')
    assert has(repo.errors(), "project name must be voice-dataset-ingest")
    rewrite(repo, f"applications/{APP}/pyproject.toml", 'testpaths = ["tests"]', 'testpaths = ["elsewhere"]')
    assert has(repo.errors(), "testpaths")
    repo.write(f"applications/{APP}/.gitignore", ".env\n")
    assert has(repo.errors(), ".gitignore must list")


def test_application_dependencies_are_bounded(repo: Repo):
    add_application(repo)
    assert repo.errors() == []
    for relative in ("requirements.txt", "requirements-dev.txt"):
        original = (repo.root / "applications" / APP / relative).read_text()
        repo.write(f"applications/{APP}/{relative}", original + "requests>=2\n")
        assert has(repo.errors(), f"{relative}: requests>=2 has no upper bound")
        repo.write(f"applications/{APP}/{relative}", original)
    assert repo.errors() == []
    rewrite(repo, f"applications/{APP}/pyproject.toml", '"boto3>=1.34,<2"', '"boto3>=1.34"')
    assert has(repo.errors(), "dependency boto3>=1.34 has no upper bound")


def test_application_shared_copy_must_match_source(repo: Repo):
    add_application(repo)
    assert repo.errors() == []
    shared = f"applications/{APP}/src/voice_dataset_ingest/shared/multipart.py"
    repo.write(shared, SHARED_SOURCE + "# edited in the copy\n")
    assert has(repo.errors(), "shared/multipart.py differs from examples/python/multipart.py")
    repo.write(shared, SHARED_SOURCE)
    assert repo.errors() == []
    repo.write(shared.replace("multipart.py", "invented.py"), "x = 1\n")
    assert has(repo.errors(), "shared/invented.py has no source")


def test_application_schema_copy_must_match_source(repo: Repo):
    add_application(repo)
    assert repo.errors() == []
    copy = f"applications/{APP}/src/voice_dataset_ingest/schemas/consent-record.schema.json"
    original = (repo.root / copy).read_text()
    repo.write(copy, original.replace('"title"', '"title ": "x", "_title"', 1))
    assert has(repo.errors(), "schemas/consent-record.schema.json differs")
    repo.write(copy, original)
    assert repo.errors() == []


def test_application_needs_ci_matrix_entry(repo: Repo):
    add_application(repo)
    assert repo.errors() == []
    repo.write(".github/workflows/ci.yml", ci_text([OTHER_APP]))
    assert has(repo.errors(), "not listed in the applications job matrix")
    repo.write(".github/workflows/ci.yml", ci_text(None))
    assert has(repo.errors(), "not listed in the applications job matrix")
    repo.write(".github/workflows/ci.yml", ci_text([APP, OTHER_APP]))
    assert repo.errors() == []


def test_application_needs_readme_section(repo: Repo):
    add_application(repo, section=False)
    assert has(repo.errors(), f"applications/README.md has no section '## {APP}'")
    repo.write("applications/README.md", "# Sample applications\n\n" + APPLICATION_SECTION.format(name=APP, arch=ENTRY_ID))
    assert repo.errors() == []
    rewrite(repo, "applications/README.md", "### How to run", "### Running")
    assert has(repo.errors(), "missing 'How to run'")
    repo.write("applications/README.md", "# Sample applications\n\n" + APPLICATION_SECTION.format(name=APP, arch=ENTRY_ID))
    rewrite(repo, f"applications/{APP}/README.md", "## Tests", "## Checks")
    assert has(repo.errors(), "README.md missing section 'Tests'")


def test_application_readme_warns_lifecycle_replaces(repo: Repo):
    add_application(repo)
    readme = f"applications/{APP}/README.md"
    command = "\n```bash\nb2 bucket update --lifecycle-rules '[]' \"$B2_BUCKET_NAME\"\n```\n"
    repo.write(readme, APP_README + command)
    assert has(repo.errors(), "say that it replaces all of the bucket's rules")
    repo.write(readme, APP_README + "\nThis command replaces all of the bucket's rules.\n" + command)
    assert repo.errors() == []
    repo.write(readme, APP_README + "\naws s3api put-bucket-cors --bucket x\n")
    assert has(repo.errors(), "say that it replaces")


def test_application_link_must_point_to_valid_application(repo: Repo):
    add_application(repo)
    assert repo.errors() == []
    for bad in ("https://github.com/backblaze-b2-samples/some-sample", "applications/README.md",
                f"applications/{APP}/pyproject.toml", f"applications/{APP}"):
        catalog = repo.catalog()
        catalog["architectures"][0]["links"]["application"] = bad
        repo.save(catalog)
        repo.regenerate()
        assert has(repo.errors(), "links.application must be applications/<name>/README.md"), bad
    catalog = repo.catalog()
    catalog["architectures"][0]["links"]["application"] = f"applications/{APP}/README.md"
    repo.save(catalog)
    repo.regenerate()
    assert repo.errors() == []
    (repo.root / "applications" / APP / "tests" / "conftest.py").unlink()
    assert has(repo.errors(), "which does not pass the application rules")
    # A link to a folder that does not exist is rejected by the catalog check itself.
    catalog = repo.catalog()
    catalog["architectures"][0]["links"]["application"] = "applications/missing/README.md"
    repo.save(catalog)
    assert has(repo.errors(), "missing local paths")


@pytest.mark.parametrize("pattern", validate_contracts.BUCKET_CONFIGURATION)
def test_application_source_has_no_bucket_configuration(repo: Repo, pattern: str):
    add_application(repo)
    assert repo.errors() == []
    module = f"applications/{APP}/src/voice_dataset_ingest/storage.py"
    repo.write(module, f"def configure(client):\n    return '{pattern}'\n")
    assert has(repo.errors(), f"contains '{pattern}'")
    repo.write(module, "def make_client():\n    return None\n")
    assert repo.errors() == []


def test_application_source_check_allows_configuration_in_tests_and_docs(repo: Repo):
    add_application(repo)
    repo.write(f"applications/{APP}/tests/test_never_configures.py", "FORBIDDEN = 'put_bucket_cors'\n")
    repo.write(f"applications/{APP}/README.md", APP_README + "\nThe application never calls put_bucket_cors.\n")
    assert repo.errors() == []


@pytest.mark.parametrize("variable", ["B2_ENDPOINT", "B2_S3_ENDPOINT", "B2_KEY_ID"])
def test_application_rejects_endpoint_variable(repo: Repo, variable: str):
    add_application(repo)
    assert repo.errors() == []
    environment = f"applications/{APP}/.env.example"
    repo.write(environment, ENV_EXAMPLE + f"{variable}=<value>\n")
    assert has(repo.errors(), "must not define an endpoint or key id alias")
    repo.write(environment, ENV_EXAMPLE.replace("B2_REGION=<region>\n", ""))
    assert has(repo.errors(), "must set B2_REGION")
    repo.write(environment, ENV_EXAMPLE.replace("# B2_PUBLIC_URL_BASE=", "B2_PUBLIC_URL_BASE="))
    assert has(repo.errors(), "commented # B2_PUBLIC_URL_BASE=")


@pytest.mark.parametrize("text, message", [
    (ci_text(None, permissions=""), "permissions must be exactly"),
    (ci_text(None, permissions="permissions: write-all\n"), "permissions must be exactly"),
    (ci_text(None, permissions="permissions:\n  contents: read\n  pull-requests: write\n"), "permissions must be exactly"),
    (ci_text(None, trigger="on:\n  pull_request_target:\n    branches: [main]"), "must not use pull_request_target"),
    (ci_text(None, trigger="on: [push, pull_request_target]"), "must not use pull_request_target"),
])
def test_ci_permissions_read_only_and_no_pull_request_target(repo: Repo, text: str, message: str):
    assert repo.errors() == []
    repo.write(".github/workflows/ci.yml", text)
    assert has(repo.errors(), message)
    repo.write(".github/workflows/ci.yml", ci_text(None))
    assert repo.errors() == []


def test_missing_ci_workflow_fails(repo: Repo):
    (repo.root / ".github" / "workflows" / "ci.yml").unlink()
    assert has(repo.errors(), "ci.yml is missing")


def test_require_applications_counts_valid_local_applications(repo: Repo):
    assert has(repo.errors(require_applications=1), "0 catalog entries link a valid application, expected at least 1")
    assert repo.errors(require_applications=0) == []
    add_application(repo, APP, ENTRY_ID)
    add_application(repo, OTHER_APP, SESSION_ID)
    assert repo.errors(require_applications=2) == []
    assert has(repo.errors(require_applications=3), "2 catalog entries link a valid application, expected at least 3")
    # A link to a folder that fails the rules does not count.
    (repo.root / "applications" / OTHER_APP / "tests" / "test_live.py").unlink()
    errors = repo.errors(require_applications=2)
    assert has(errors, "1 catalog entries link a valid application, expected at least 2")
    assert has(errors, "does not pass the application rules")
    # An application with no catalog link does not count either.
    catalog = repo.catalog()
    for item in catalog["architectures"]:
        item["links"]["application"] = None
    repo.save(catalog)
    repo.regenerate()
    assert has(repo.errors(require_applications=1), "0 catalog entries link a valid application")


# --- generate_catalog ---------------------------------------------------------------------

def test_check_fails_when_generated_files_are_missing(repo: Repo):
    assert generate_catalog.main(["--check"], root=repo.root) == 1


def test_check_passes_after_generation(valid: Repo):
    assert generate_catalog.main(["--check"], root=valid.root) == 0


@pytest.mark.parametrize("relative", ["README.md", "docs/architecture-index.md", "skill/references/architecture-catalog.md", "llms.txt"])
def test_check_detects_each_stale_file(valid: Repo, relative: str):
    path = valid.root / relative
    text = path.read_text()
    # README.md is hand-written outside the generated markers, so change what is inside them.
    path.write_text(text.replace(generate_catalog.INT_START, generate_catalog.INT_START + "\nstale") if relative == "README.md" else text + "\nstale\n")
    assert generate_catalog.main(["--check"], root=valid.root) == 1


def test_catalog_change_makes_generated_files_stale(valid: Repo):
    catalog = valid.catalog()
    catalog["architectures"][0]["summary"] = "A different summary."
    valid.save(catalog)
    assert generate_catalog.main(["--check"], root=valid.root) == 1
    valid.regenerate()
    assert generate_catalog.main(["--check"], root=valid.root) == 0


def test_partner_integration_options_are_generated(valid: Repo):
    index = (valid.root / "docs" / "architecture-index.md").read_text()
    readme = (valid.root / "README.md").read_text()
    assert "\n## Partner integration options\n" in index
    for text in (index, readme):
        assert "[Tool A](https://example.com/a)" in text
        assert "Provider uses an S3-compatible endpoint" in text


def test_partner_integration_lists_every_architecture_using_a_tool(repo: Repo):
    repo.add_architecture("voice-training-data-lifecycle")
    repo.add_architecture("batch-voice-generation-and-dubbing")
    repo.regenerate()
    row = next(line for line in (repo.root / "README.md").read_text().splitlines() if line.startswith("| [Tool A]"))
    assert row.count("architectures/") == 2


def test_llms_lists_optional_pages_only_when_they_exist(valid: Repo):
    assert "docs/voice-speech-ai.md" not in (valid.root / "llms.txt").read_text()
    valid.write("docs/voice-speech-ai.md", "# Voice and speech AI\n")
    valid.regenerate()
    assert "docs/voice-speech-ai.md" in (valid.root / "llms.txt").read_text()
    assert check_markdown_links.check(valid.root)[1] == []


def test_llms_lists_the_applications_page_only_when_it_exists(valid: Repo):
    assert "applications/README.md" not in (valid.root / "llms.txt").read_text()
    valid.write("applications/README.md", "# Sample applications\n")
    valid.regenerate()
    assert "[Sample applications](applications/README.md)" in (valid.root / "llms.txt").read_text()
    assert check_markdown_links.check(valid.root)[1] == []


def test_missing_readme_markers_fail_generation(repo: Repo):
    (repo.root / "README.md").write_text("# Title\n")
    assert generate_catalog.main([], root=repo.root) == 1


# --- check_markdown_links --------------------------------------------------------------------

def test_links_pass_when_targets_exist(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("[b](b.md#top) [home](../README.md) [web](https://example.com) [here](#x)")
    (tmp_path / "docs" / "b.md").write_text("# B")
    (tmp_path / "README.md").write_text("[a](docs/a.md)")
    checked, failures = check_markdown_links.check(tmp_path)
    assert failures == [] and checked == 3


def test_missing_link_target_fails(tmp_path: Path):
    (tmp_path / "README.md").write_text("[a](docs/missing.md)")
    assert check_markdown_links.main(tmp_path) == 1


def test_link_escaping_repository_fails(tmp_path: Path):
    (tmp_path / "README.md").write_text("[a](../outside.md)")
    assert any("escapes" in failure for failure in check_markdown_links.check(tmp_path)[1])


def test_links_in_dependency_folders_and_code_fences_are_skipped(tmp_path: Path):
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "x.md").write_text("[a](missing.md)")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "x.md").write_text("[a](missing.md)")
    (tmp_path / "README.md").write_text("```md\n[a](missing.md)\n```\n")
    assert check_markdown_links.check(tmp_path) == (0, [])


# --- check_markdown_links --external ---------------------------------------------------------

@pytest.fixture
def local_server():
    """A local HTTP server: /ok 200, /missing 404, /moved redirects to /ok, /nohead refuses HEAD, /slow stalls."""
    import http.server
    import threading
    import time

    seen: list[tuple[str, str, str | None]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _answer(self):
            seen.append((self.command, self.path, self.headers.get("Authorization")))
            if self.path == "/ok":
                self.send_response(200)
            elif self.path == "/moved":
                self.send_response(302)
                self.send_header("Location", "/ok")
            elif self.path == "/nohead" and self.command == "HEAD":
                self.send_response(405)
            elif self.path == "/nohead":
                self.send_response(200)
            elif self.path == "/slow":
                time.sleep(1.5)
                self.send_response(200)
            else:
                self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_HEAD = do_GET = _answer

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=False)
def no_proxy(monkeypatch):
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")


def test_external_links_pass_and_follow_redirects(tmp_path: Path, local_server, no_proxy):
    base, seen = local_server
    page = tmp_path / "page.md"
    page.write_text(f"[a]({base}/ok) [b]({base}/moved) [c]({base}/nohead) <{base}/ok> [local](x.md) [anchor](#top)")
    checked, failures = check_markdown_links.check_external(page, timeout=5)
    assert (checked, failures) == (3, [])
    assert ("GET", "/nohead", None) in seen
    assert all(auth is None for _, _, auth in seen)


def test_external_link_errors_are_reported(tmp_path: Path, local_server, no_proxy):
    base, _ = local_server
    page = tmp_path / "page.md"
    page.write_text(f"[a]({base}/ok) [b]({base}/missing)")
    checked, failures = check_markdown_links.check_external(page, timeout=5)
    assert checked == 2 and len(failures) == 1 and "/missing" in failures[0] and "404" in failures[0]


def test_external_link_timeout_and_refused_connection_fail(tmp_path: Path, local_server, no_proxy):
    base, _ = local_server
    page = tmp_path / "page.md"
    page.write_text(f"[slow]({base}/slow) [refused](http://127.0.0.1:1/x)")
    checked, failures = check_markdown_links.check_external(page, timeout=0.3)
    assert checked == 2 and len(failures) == 2


def test_external_links_in_code_fences_and_duplicates_are_ignored(tmp_path: Path):
    page = tmp_path / "page.md"
    page.write_text("```\n[a](http://127.0.0.1:1/never)\n```\n[b](https://example.com/x) [c](https://example.com/x)\n")
    assert check_markdown_links.external_links(page) == ["https://example.com/x"]


def test_external_cli_exit_codes(tmp_path: Path, local_server, no_proxy, capsys):
    base, _ = local_server
    good = tmp_path / "good.md"
    good.write_text(f"[a]({base}/ok)")
    bad = tmp_path / "bad.md"
    bad.write_text(f"[a]({base}/missing)")
    assert check_markdown_links.cli(["--external", str(good), "--timeout", "5"]) == 0
    assert check_markdown_links.cli(["--external", str(bad), "--timeout", "5"]) == 1
    assert check_markdown_links.cli(["--external", str(tmp_path / "absent.md")]) == 1
    assert "no such file" in capsys.readouterr().out
