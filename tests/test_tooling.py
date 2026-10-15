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
    shutil.copy(REPO / "README.md", tmp_path / "README.md")
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

def test_require_applications_counts_public_links(repo: Repo):
    repo.add_architecture("voice-training-data-lifecycle")
    repo.add_architecture("batch-voice-generation-and-dubbing")
    catalog = repo.catalog()
    catalog["architectures"][0]["links"]["application"] = "https://github.com/backblaze-b2-samples/one"
    catalog["architectures"][1]["links"]["application"] = "https://github.com/backblaze-b2-samples/two"
    repo.save(catalog)
    assert repo.errors(require_applications=2, public=lambda url: True) == []
    assert has(repo.errors(require_applications=3, public=lambda url: True), "expected at least 3")
    assert has(repo.errors(require_applications=2, public=lambda url: url.endswith("one")), "1 public")


def test_local_application_paths_do_not_count(repo: Repo):
    repo.add_architecture()
    repo.write("applications/demo/README.md", "# Demo\n")
    catalog = repo.catalog()
    catalog["architectures"][0]["links"]["application"] = "applications/demo/README.md"
    repo.save(catalog)
    assert has(repo.errors(require_applications=1, public=lambda url: True), "0 public")


def test_is_public_repository_rejects_non_github_urls():
    assert validate_contracts.is_public_repository("https://example.com/a/b") is False


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
