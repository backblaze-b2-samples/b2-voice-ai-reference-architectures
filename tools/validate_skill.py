#!/usr/bin/env python3
"""Validate the agent skill: frontmatter, body rules and reference files.

Usage:
  python tools/validate_skill.py [SKILL.md]

Without an argument the skill at skill/SKILL.md is validated. The checks:

* the frontmatter has `name`, `description`, `compatibility` and `allowed-tools`
  (the fields the shared B2 skill's validator requires), all strings, with
  `allowed-tools` a string and a `name` made of lowercase letters, digits and hyphens;
* the frontmatter parses the same way under the shared validator's rule (the first
  `---` after the opening delimiter closes it), so no value may contain `---`;
* the skill delegates B2 basics to the shared `b2-cloud-storage` skill, states that B2
  is never in the real-time audio path, and covers zones and prefixes, lifecycle,
  consent and boundaries;
* when the skill sits in a repository checkout (a `references/` folder next to it):
  every local link resolves, every file in `references/` is linked from SKILL.md, and no
  skill file contains a price figure or the em dash character.

The exit status is 0 when the skill is valid and 1 otherwise.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILL = ROOT / "skill" / "SKILL.md"

SKILL_NAME = "b2-voice-speech-ai"
SHARED_SKILL = "b2-cloud-storage"
REQUIRED_FIELDS = ("name", "description", "compatibility", "allowed-tools")
NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MIN_DESCRIPTION = 40
MAX_DESCRIPTION = 1024
MAX_COMPATIBILITY = 500
EM_DASH = chr(0x2014)
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
FENCE = re.compile(r"^\s*(```|~~~)")
PRICE = re.compile(r"\$\s?\d")

# Body topics the skill must cover: (label, regex searched in the lowercased body).
REQUIRED_TOPICS = (
    ("delegation to the shared B2 skill", re.escape(SHARED_SKILL)),
    ("endpoint derivation", r"endpoint"),
    ("presigned URLs", r"presigned"),
    ("application keys", r"application key|keys?\b"),
    ("B2 outside the real-time audio path", r"never in the real[- ]time audio path|real[- ]time audio path"),
    ("zones and prefixes", r"zones? and prefixes"),
    ("lifecycle", r"lifecycle"),
    ("consent", r"consent"),
    ("boundaries", r"boundar"),
    ("Overdrive limited to sustained reads", r"overdrive only for sustained"),
)


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return (frontmatter text, body) using the shared validator's rule."""
    if not text.startswith("---"):
        return None, text
    closing = text.find("---", 3)
    if closing == -1:
        return None, text
    return text[3:closing], text[closing + 3:]


def frontmatter_errors(text: str) -> tuple[dict, list[str]]:
    front, _ = split_frontmatter(text)
    if front is None:
        return {}, ["SKILL.md: missing or unterminated frontmatter"]
    try:
        parsed = yaml.safe_load(front)
    except yaml.YAMLError as error:
        return {}, [f"SKILL.md: frontmatter is not valid YAML: {error}"]
    if not isinstance(parsed, dict):
        return {}, ["SKILL.md: frontmatter is not a mapping"]
    errors: list[str] = []
    # The closing delimiter must be a line of its own, so that the shared rule and a
    # line-based reader agree on where the frontmatter ends.
    closing = text.find("---", 3)
    first_line = text.split("\n", 1)[0].strip()
    at_line_start = text[closing - 1] == "\n"
    rest = text[closing + 3:].split("\n", 1)[0]
    if first_line != "---" or not at_line_start or rest.strip():
        errors.append(
            "SKILL.md: the frontmatter must open and close with a line containing only ---, "
            "and no value may contain ---"
        )
    for field in REQUIRED_FIELDS:
        if field not in parsed:
            errors.append(f"SKILL.md: missing frontmatter field {field!r}")
        elif not isinstance(parsed[field], str) or not parsed[field].strip():
            errors.append(f"SKILL.md: frontmatter field {field!r} must be a non-empty string")
    name = parsed.get("name")
    if isinstance(name, str):
        if not NAME_PATTERN.match(name) or len(name) > 64:
            errors.append("SKILL.md: name must be lowercase letters, digits and hyphens, at most 64 characters")
        elif name != SKILL_NAME:
            errors.append(f"SKILL.md: name must be {SKILL_NAME!r}, got {name!r}")
    description = parsed.get("description")
    if isinstance(description, str):
        if len(description) < MIN_DESCRIPTION:
            errors.append(f"SKILL.md: description is shorter than {MIN_DESCRIPTION} characters")
        if len(description) > MAX_DESCRIPTION:
            errors.append(f"SKILL.md: description is longer than {MAX_DESCRIPTION} characters")
    compatibility = parsed.get("compatibility")
    if isinstance(compatibility, str) and len(compatibility) > MAX_COMPATIBILITY:
        errors.append(f"SKILL.md: compatibility is longer than {MAX_COMPATIBILITY} characters")
    return parsed, errors


def without_code_blocks(text: str) -> str:
    kept: list[str] = []
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            kept.append(line)
    return "\n".join(kept)


def local_links(path: Path) -> list[tuple[str, Path]]:
    links = []
    for match in LINK.finditer(without_code_blocks(path.read_text())):
        raw = match.group(1).strip()
        if raw.startswith("<") and raw.endswith(">"):
            raw = raw[1:-1]
        target = raw.split(maxsplit=1)[0]
        if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
            continue
        links.append((target, (path.parent / target.split("#", 1)[0]).resolve()))
    return links


def body_errors(body: str) -> list[str]:
    lowered = " ".join(body.lower().split())
    errors = []
    for label, pattern in REQUIRED_TOPICS:
        if not re.search(pattern, lowered):
            errors.append(f"SKILL.md: the skill does not cover {label}")
    return errors


def repository_errors(skill: Path) -> list[str]:
    """Checks that need the skill's own folder: references, links, prices, em dash."""
    folder = skill.parent
    references = folder / "references"
    if not references.is_dir():
        return []
    errors: list[str] = []
    files = [skill, *sorted(references.rglob("*.md"))]
    linked: set[Path] = set()
    for path in files:
        relative = path.relative_to(folder)
        text = path.read_text()
        if EM_DASH in text:
            errors.append(f"{relative}: contains the em dash character")
        if PRICE.search(without_code_blocks(text)):
            errors.append(f"{relative}: contains a price figure; link the pricing page instead")
        for target, resolved in local_links(path):
            if not resolved.exists():
                errors.append(f"{relative}: missing link target {target}")
            elif path == skill:
                linked.add(resolved)
    for path in sorted(references.rglob("*.md")):
        if path.resolve() not in linked:
            errors.append(f"SKILL.md: does not link references/{path.relative_to(references)}")
    return errors


def validate(skill: Path) -> list[str]:
    if not skill.is_file():
        return [f"{skill}: not found"]
    text = skill.read_text()
    _, errors = frontmatter_errors(text)
    _, body = split_frontmatter(text)
    errors += body_errors(body)
    errors += repository_errors(skill)
    return errors


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) > 1:
        print("usage: validate_skill.py [SKILL.md]", file=sys.stderr)
        return 2
    skill = Path(args[0]) if args else DEFAULT_SKILL
    errors = validate(skill)
    if errors:
        print("\n".join(errors))
        return 1
    print(f"{skill}: skill valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
