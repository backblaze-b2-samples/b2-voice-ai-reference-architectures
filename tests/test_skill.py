"""Tests for the agent skill, its validator, the category page and the llms.txt entries."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml

import validate_skill

REPO = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO / "skill"
SKILL = SKILL_DIR / "SKILL.md"
CATEGORY_PAGE = REPO / "docs" / "voice-speech-ai.md"
EM_DASH = chr(0x2014)


@pytest.fixture
def skill_copy(tmp_path: Path) -> Path:
    """A copy of the skill folder that tests can break."""
    target = tmp_path / "skill"
    shutil.copytree(SKILL_DIR, target)
    # The skill links to sibling folders of the repository; link them so the copy resolves.
    for name in ("architectures", "docs", "examples", "infrastructure", "schemas"):
        (tmp_path / name).symlink_to(REPO / name)
    return target / "SKILL.md"


def frontmatter(path: Path) -> dict:
    text = path.read_text()
    front, _ = validate_skill.split_frontmatter(text)
    return yaml.safe_load(front)


def rewrite_frontmatter(path: Path, **changes) -> None:
    text = path.read_text()
    front, body = validate_skill.split_frontmatter(text)
    data = yaml.safe_load(front)
    for key, value in changes.items():
        if value is None:
            data.pop(key.replace("_", "-"), None)
        else:
            data[key.replace("_", "-")] = value
    path.write_text("---\n" + yaml.safe_dump(data, sort_keys=False, width=10_000) + "---" + body)


# The repository's skill ----------------------------------------------------------------------


def test_repository_skill_is_valid():
    assert validate_skill.validate(SKILL) == []


def test_repository_skill_has_the_fields_the_shared_validator_requires():
    data = frontmatter(SKILL)
    for field in ("name", "description", "compatibility", "allowed-tools"):
        assert field in data
    assert data["name"] == "b2-voice-speech-ai"
    assert isinstance(data["allowed-tools"], str)
    assert len(data["description"]) >= 40


def test_frontmatter_parses_like_the_shared_validator():
    """The shared validator closes the frontmatter at the first --- after the opening one."""
    text = SKILL.read_text()
    assert text.startswith("---")
    closing = text.find("---", 3)
    parsed = yaml.safe_load(text[3:closing])
    assert parsed["name"] == "b2-voice-speech-ai"
    assert text[closing - 1] == "\n"


def test_skill_delegates_b2_basics_to_the_shared_skill():
    body = SKILL.read_text()
    assert "b2-cloud-storage" in body
    for topic in ("endpoint", "application keys", "presigned"):
        assert topic in body.lower()


def test_skill_states_the_voice_decisions():
    body = " ".join(SKILL.read_text().lower().split())
    assert "never in the real-time audio path" in body
    for topic in ("zones and prefixes", "lifecycle", "consent", "boundary", "overdrive only for sustained"):
        assert topic in body


def test_every_reference_is_linked_and_resolves():
    references = sorted((SKILL_DIR / "references").glob("*.md"))
    names = {path.name for path in references}
    assert {"architecture-catalog.md", "boundaries.md", "zones-lifecycle-keys.md", "consent-and-retention.md"} <= names
    linked = {resolved for _, resolved in validate_skill.local_links(SKILL)}
    for path in references:
        assert path.resolve() in linked


def test_skill_points_to_each_architecture_and_the_category_page():
    links = {target for target, _ in validate_skill.local_links(SKILL)}
    for architecture in (
        "voice-training-data-lifecycle",
        "batch-voice-generation-and-dubbing",
        "voice-agent-session-archive",
    ):
        assert f"../architectures/{architecture}/architecture.md" in links
    assert "../docs/voice-speech-ai.md" in links
    assert "../docs/b2-capabilities.md" in links


def test_boundaries_reference_lists_the_eight_boundaries():
    text = (SKILL_DIR / "references" / "boundaries.md").read_text().lower()
    for label in (
        "real-time voice transport",
        "low-latency conversational session store",
        "message queue",
        "transcription or voice generation model",
        "workflow orchestrator",
        "metadata catalog",
        "vector database",
        "local gpu cache",
    ):
        assert label in text


def test_skill_files_have_no_em_dash_or_price_figure():
    for path in [SKILL, *(SKILL_DIR / "references").glob("*.md")]:
        text = path.read_text()
        assert EM_DASH not in text, path
        assert not validate_skill.PRICE.search(text), path


# The validator's failure cases ---------------------------------------------------------------


@pytest.mark.parametrize("field", ["name", "description", "compatibility", "allowed_tools"])
def test_missing_frontmatter_field_fails(skill_copy: Path, field: str):
    rewrite_frontmatter(skill_copy, **{field: None})
    errors = validate_skill.validate(skill_copy)
    assert any(field.replace("_", "-") in error for error in errors)


def test_allowed_tools_must_be_a_string(skill_copy: Path):
    rewrite_frontmatter(skill_copy, allowed_tools=["Read", "Grep"])
    assert any("allowed-tools" in error for error in validate_skill.validate(skill_copy))


def test_short_description_fails(skill_copy: Path):
    rewrite_frontmatter(skill_copy, description="Too short.")
    assert any("description" in error for error in validate_skill.validate(skill_copy))


def test_overlong_description_fails(skill_copy: Path):
    rewrite_frontmatter(skill_copy, description="B2 voice. " * 150)
    assert any("longer than" in error for error in validate_skill.validate(skill_copy))


@pytest.mark.parametrize("name", ["B2_Voice", "b2-voice-speech", "-b2", "b2--voice"])
def test_wrong_name_fails(skill_copy: Path, name: str):
    rewrite_frontmatter(skill_copy, name=name)
    assert any("name" in error for error in validate_skill.validate(skill_copy))


def test_triple_hyphen_inside_a_value_fails(skill_copy: Path):
    rewrite_frontmatter(skill_copy, compatibility="Any agent --- with files.")
    assert any("---" in error for error in validate_skill.validate(skill_copy))


def test_missing_frontmatter_fails(skill_copy: Path):
    skill_copy.write_text("# No frontmatter\n")
    assert any("frontmatter" in error for error in validate_skill.validate(skill_copy))


def test_unterminated_frontmatter_fails(skill_copy: Path):
    skill_copy.write_text("---\nname: b2-voice-speech-ai\n")
    assert any("frontmatter" in error for error in validate_skill.validate(skill_copy))


@pytest.mark.parametrize(
    "needle,replacement,expected",
    [
        ("b2-cloud-storage", "the shared skill", "shared B2 skill"),
        ("never in the real-time audio path", "outside the hot path", "real-time audio path"),
        ("Consent", "Permissions", "consent"),
    ],
)
def test_missing_topic_fails(skill_copy: Path, needle: str, replacement: str, expected: str):
    text = skill_copy.read_text()
    assert needle in text
    skill_copy.write_text(text.replace(needle, replacement).replace(needle.lower(), replacement.lower()))
    assert any(expected in error for error in validate_skill.validate(skill_copy))


def test_em_dash_in_a_reference_fails(skill_copy: Path):
    reference = skill_copy.parent / "references" / "boundaries.md"
    reference.write_text(reference.read_text() + f"\nA note {EM_DASH} with a dash.\n")
    assert any("em dash" in error for error in validate_skill.validate(skill_copy))


def test_price_figure_in_a_reference_fails(skill_copy: Path):
    reference = skill_copy.parent / "references" / "zones-lifecycle-keys.md"
    reference.write_text(reference.read_text() + "\nStorage costs $6 per month.\n")
    assert any("price" in error for error in validate_skill.validate(skill_copy))


def test_broken_link_fails(skill_copy: Path):
    skill_copy.write_text(skill_copy.read_text() + "\nSee [missing](references/missing.md).\n")
    assert any("missing link target" in error for error in validate_skill.validate(skill_copy))


def test_unlinked_reference_fails(skill_copy: Path):
    (skill_copy.parent / "references" / "orphan.md").write_text("# Orphan\n")
    assert any("orphan.md" in error for error in validate_skill.validate(skill_copy))


def test_missing_file_fails(tmp_path: Path):
    assert validate_skill.validate(tmp_path / "SKILL.md")


def test_main_exit_codes(skill_copy: Path, capsys):
    assert validate_skill.main([str(skill_copy)]) == 0
    assert validate_skill.main([]) == 0
    skill_copy.write_text("# broken\n")
    assert validate_skill.main([str(skill_copy)]) == 1
    assert validate_skill.main(["a", "b"]) == 2
    capsys.readouterr()


def test_a_lone_skill_file_is_validated_without_references(tmp_path: Path):
    """The published SKILL.md is also checked on its own, away from the repository."""
    lone = tmp_path / "SKILL.md"
    shutil.copy(SKILL, lone)
    assert validate_skill.validate(lone) == []


# The category page ---------------------------------------------------------------------------


def headings(path: Path) -> list[str]:
    return re.findall(r"^## (.+)$", path.read_text(), flags=re.MULTILINE)


def test_category_page_has_the_required_sections():
    found = headings(CATEGORY_PAGE)
    for section in ("When to use", "When not to use", "FAQ", "Architecture index"):
        assert section in found
    assert any(h.startswith("Standard B2 and B2 Overdrive") for h in found)
    assert any(h.startswith("Where B2 fits") for h in found)
    assert any(h.startswith("Where B2 does not fit") for h in found)


def test_category_page_faq_is_question_shaped():
    text = CATEGORY_PAGE.read_text()
    faq = text.split("## FAQ", 1)[1]
    questions = re.findall(r"^\*\*(.+\?)\*\*$", faq, flags=re.MULTILINE)
    assert len(questions) >= 8


def test_category_page_limits_overdrive_to_sustained_reads():
    text = " ".join(CATEGORY_PAGE.read_text().split())
    assert "only to sustained high-throughput reads" in text


def test_category_page_indexes_every_catalog_architecture():
    catalog = yaml.safe_load((REPO / "catalog" / "architectures.yaml").read_text())
    text = CATEGORY_PAGE.read_text()
    for architecture in catalog["architectures"]:
        assert architecture["links"]["architecture"].replace("architectures/", "../architectures/") in text


def test_category_page_has_no_em_dash_or_price_figure():
    text = CATEGORY_PAGE.read_text()
    assert EM_DASH not in text
    assert not validate_skill.PRICE.search(text)


# llms.txt and the applications page ----------------------------------------------------------


def test_llms_txt_lists_the_category_page_and_the_skill():
    llms = (REPO / "llms.txt").read_text()
    assert "(docs/voice-speech-ai.md)" in llms
    assert "(skill/SKILL.md)" in llms


def test_llms_txt_omits_them_when_the_files_are_absent(tmp_path: Path):
    import generate_catalog

    catalog = generate_catalog.load_catalog(REPO)
    root = tmp_path
    (root / "schemas").mkdir()
    rendered = generate_catalog.render_llms(catalog, root)
    assert "docs/voice-speech-ai.md" not in rendered
    assert "skill/SKILL.md" not in rendered
    (root / "docs").mkdir()
    (root / "docs" / "voice-speech-ai.md").write_text("# Page\n")
    (root / "skill").mkdir()
    (root / "skill" / "SKILL.md").write_text("# Skill\n")
    rendered = generate_catalog.render_llms(catalog, root)
    assert "(docs/voice-speech-ai.md)" in rendered
    assert "(skill/SKILL.md)" in rendered


def test_applications_page_describes_the_applications_and_their_rules():
    text = (REPO / "applications" / "README.md").read_text()
    for part in ("What it shows", "Architecture", "Configurations it ships", "How to run", "Known limitations"):
        assert part in text
    assert "../AGENTS.md#applications" in text
    assert "emulated S3" in text
    assert EM_DASH not in text
