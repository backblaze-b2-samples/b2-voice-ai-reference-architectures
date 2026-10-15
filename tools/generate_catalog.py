#!/usr/bin/env python3
"""Validate the architecture catalog and render its human and agent indexes.

Generated files: the marked sections of README.md, docs/architecture-index.md,
skill/references/architecture-catalog.md and llms.txt. Run without arguments to
write them and with --check to fail when any of them is stale.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]

ARCH_START = "<!-- catalog:architectures:start -->"
ARCH_END = "<!-- catalog:architectures:end -->"
INT_START = "<!-- catalog:integrations:start -->"
INT_END = "<!-- catalog:integrations:end -->"

CONNECTION_TYPES = {
    "native-s3-output": "Provider uses an S3-compatible endpoint",
    "application-copy": "Application copies data to or from B2",
}

# Optional entries of llms.txt. Each is listed only when its file exists.
LLMS_START_HERE = [
    ("Voice and speech AI overview", "docs/voice-speech-ai.md",
     "What the architectures cover, where B2 fits and where it does not, when to use them, and FAQ."),
    ("Architecture index", "docs/architecture-index.md",
     "Human-readable index generated from the catalog."),
    ("Machine-readable catalog", "catalog/architectures.yaml",
     "Audiences, workflows, boundaries, capabilities, evidence, security and resource links."),
    ("B2 capability map", "docs/b2-capabilities.md",
     "What each B2 capability does, what it requires, its limitations and which architecture uses it."),
]
LLMS_RESOURCES = [
    ("B2 configuration examples", "examples/README.md",
     "CORS, lifecycle, Event Notifications, Object Lock, replication, encryption and scoped keys."),
    ("Infrastructure helpers", "infrastructure/README.md",
     "LiveKit Egress output to B2 and provisioning of per-zone buckets and scoped keys."),
    ("Sample applications", "applications/README.md",
     "Python command-line applications that implement the architectures, with tests that run against an emulated S3 server."),
    ("Related B2 sample applications", "docs/related-b2-samples.md",
     "Existing sample applications that exercise these workflows."),
]
LLMS_OPTIONAL = [
    ("Repository agent guide", "AGENTS.md",
     "Source hierarchy, generation commands and operational invariants."),
    ("Portable agent skill", "skill/SKILL.md",
     "Self-contained guidance for B2 voice and speech AI architecture work."),
]


def paths(root: Path) -> dict[str, Path]:
    return {
        "catalog": root / "catalog" / "architectures.yaml",
        "schema": root / "schemas" / "architecture-catalog.schema.json",
        "readme": root / "README.md",
        "doc_index": root / "docs" / "architecture-index.md",
        "skill_index": root / "skill" / "references" / "architecture-catalog.md",
        "llms": root / "llms.txt",
    }


def load_catalog(root: Path = ROOT) -> dict:
    p = paths(root)
    catalog = yaml.safe_load(p["catalog"].read_text())
    schema = json.loads(p["schema"].read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    )
    errors = sorted(validator.iter_errors(catalog), key=lambda error: list(error.path))
    if errors:
        details = "\n".join(
            f"- {'/'.join(map(str, error.path)) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ValueError(f"catalog validation failed:\n{details}")
    validate_local_links(catalog, root)
    return catalog


def validate_local_links(catalog: dict, root: Path) -> None:
    missing: list[str] = []
    for architecture in catalog["architectures"]:
        for value in architecture["links"].values():
            if value and not value.startswith(("http://", "https://")) and not (root / value).exists():
                missing.append(value)
        for value in architecture["validation"]["evidence"]:
            if not value.startswith(("http://", "https://")) and not (root / value).exists():
                missing.append(value)
    if missing:
        raise ValueError("catalog contains missing local paths: " + ", ".join(sorted(set(missing))))


def relative_link(target: str | None, base: Path, root: Path) -> str | None:
    if target is None or target.startswith(("http://", "https://")):
        return target
    return Path(os.path.relpath(root / target, base)).as_posix()


def markdown_link(label: str, target: str | None, base: Path, root: Path) -> str:
    return label if not target else f"[{label}]({relative_link(target, base, root)})"


def workflow_summary(architecture: dict) -> str:
    return " -> ".join(architecture["workflow"])


def render_readme_architectures(catalog: dict, root: Path) -> str:
    if not catalog["architectures"]:
        return "No architectures are listed."
    rows = [
        "| Architecture | Purpose | Resources |",
        "|---|---|---|",
    ]
    for architecture in catalog["architectures"]:
        links = architecture["links"]
        resources = [
            markdown_link(label, target, root, root)
            for label, target in (("application", links["application"]), ("deployment", links["deployment"]))
            if target
        ]
        rows.append(
            "| {name} | {summary} | {resources} |".format(
                name=markdown_link(architecture["name"], links["architecture"], root, root),
                summary=architecture["summary"],
                resources=" / ".join(resources),
            )
        )
    return "\n".join(rows)


def integrations(catalog: dict) -> list[tuple[dict, list[dict]]]:
    """Unique integrations in catalog order, with every architecture that uses each."""
    order: list[str] = []
    first: dict[str, dict] = {}
    used_by: dict[str, list[dict]] = {}
    for architecture in catalog["architectures"]:
        for integration in architecture["integrations"]:
            key = integration["id"]
            if key not in first:
                order.append(key)
                first[key] = integration
                used_by[key] = []
            used_by[key].append(architecture)
    return [(first[key], used_by[key]) for key in order]


def render_integrations(catalog: dict, base: Path, root: Path) -> str:
    if not catalog["architectures"]:
        return "No integrations are listed."
    rows = [
        "| Tool | Role | How it connects to B2 | Connection type | Architectures |",
        "|---|---|---|---|---|",
    ]
    for integration, architectures in integrations(catalog):
        names = "; ".join(
            markdown_link(a["name"], a["links"]["architecture"], base, root) for a in architectures
        )
        rows.append(
            f"| [{integration['name']}]({integration['url']}) | {integration['role']} | "
            f"{integration['connection']} | {CONNECTION_TYPES[integration['connection_type']]} | {names} |"
        )
    return "\n".join(rows)


def replace_section(text: str, start: str, end: str, body: str) -> str:
    if start not in text or end not in text:
        raise ValueError(f"missing generated section markers: {start} / {end}")
    prefix, remainder = text.split(start, 1)
    _, suffix = remainder.split(end, 1)
    return f"{prefix}{start}\n{body}\n{end}{suffix}"


def render_readme(catalog: dict, root: Path) -> str:
    readme = paths(root)["readme"]
    if not readme.exists():
        raise ValueError("README.md is missing")
    text = readme.read_text()
    text = replace_section(text, ARCH_START, ARCH_END, render_readme_architectures(catalog, root))
    return replace_section(text, INT_START, INT_END, render_integrations(catalog, root, root))


def render_docs_index(catalog: dict, root: Path) -> str:
    base = paths(root)["doc_index"].parent
    lines = [
        "<!-- Generated by tools/generate_catalog.py; edit catalog/architectures.yaml. -->",
        "# Voice and speech AI architecture index",
        "",
        "Canonical B2 product documentation: "
        f"<{catalog['canonical_documentation']}>.",
        "",
        "Use this index to choose a workflow. Each linked architecture states its evidence basis, "
        "what B2 is not in that workflow, and its security boundaries.",
    ]
    for architecture in catalog["architectures"]:
        lines.extend([
            "",
            f"## {markdown_link(architecture['name'], architecture['links']['architecture'], base, root)}",
            "",
            architecture["summary"],
            "",
            f"- **Audience:** {', '.join(architecture['audience'])}",
            f"- **Problem:** {architecture['problem']}",
            f"- **Workflow:** {workflow_summary(architecture)}",
            f"- **Expected scale:** {architecture['expected_scale']['profile']}. {architecture['expected_scale']['notes']}",
        ])
    lines.extend([
        "",
        "## Partner integration options",
        "",
        render_integrations(catalog, base, root),
        "",
        "## Machine-readable source",
        "",
        f"The catalog is available as [architectures.yaml]({relative_link('catalog/architectures.yaml', base, root)}) "
        f"and is validated by [JSON Schema]({relative_link('schemas/architecture-catalog.schema.json', base, root)}).",
        "",
    ])
    return "\n".join(lines)


def render_skill_index(catalog: dict, root: Path) -> str:
    lines = [
        "<!-- Generated by tools/generate_catalog.py; edit the repository catalog, then package this skill directory. -->",
        "# Architecture catalog",
        "",
        f"Evidence catalog date: {catalog['updated']}. For changing B2 product details, use "
        f"<{catalog['canonical_documentation']}> as the authority.",
    ]
    for architecture in catalog["architectures"]:
        lines.extend([
            "",
            f"## {architecture['name']}",
            "",
            architecture["summary"],
            "",
            f"**Use when:** {architecture['problem']}",
            "",
            f"**Workflow:** {workflow_summary(architecture)}",
            "",
            f"**B2 capabilities:** {', '.join(architecture['capabilities'])}.",
            "",
            "**Integrations:** " + "; ".join(
                f"[{item['name']}]({item['url']}) ({CONNECTION_TYPES[item['connection_type']].lower()}): {item['connection']}"
                for item in architecture["integrations"]
            ) + ".",
            "",
            f"**Scale:** {architecture['expected_scale']['profile']}. {architecture['expected_scale']['notes']}",
            "",
            "**Security:** " + "; ".join(architecture["security_considerations"]) + ".",
            "",
            "**Evidence basis:** " + "; ".join(architecture["validation"]["methods"]) + ".",
        ])
    lines.append("")
    return "\n".join(lines)


GENERATED_PAGES = {"docs/architecture-index.md"}


def existing(root: Path, entries: list[tuple[str, str, str]]) -> list[str]:
    return [
        f"- [{title}]({path}): {text}"
        for title, path, text in entries
        if path in GENERATED_PAGES or (root / path).exists()
    ]


def schema_lines(root: Path) -> list[str]:
    lines = []
    for schema in sorted((root / "schemas").glob("*.schema.json")):
        title = json.loads(schema.read_text()).get("title", schema.stem)
        lines.append(f"- [{title} schema](schemas/{schema.name}): JSON Schema.")
    return lines


def render_llms(catalog: dict, root: Path) -> str:
    lines = [
        "# B2 Reference Architectures for Voice and Speech AI",
        "",
        "> Designs, schemas and runnable examples for storing, versioning and delivering voice and speech AI data with Backblaze B2: training datasets, generated audio and voice agent sessions.",
        "",
        f"Canonical Backblaze product documentation: {catalog['canonical_documentation']}. "
        "Repository pages are maintained as clean Markdown; product documentation remains authoritative for changing service behavior. "
        "This community-convention file is a navigation aid, not an access-control policy or a search-ranking claim.",
        "",
        "## Start here",
        "",
        *existing(root, LLMS_START_HERE),
        "",
    ]
    if catalog["architectures"]:
        lines.extend(["## Architectures", ""])
        for architecture in catalog["architectures"]:
            lines.append(
                f"- [{architecture['name']}]({architecture['links']['architecture']}): {architecture['summary']}"
            )
        lines.append("")
    lines.extend(["## Contracts and schemas", "", *schema_lines(root), ""])
    resources = existing(root, LLMS_RESOURCES)
    if resources:
        lines.extend(["## Configuration and related resources", "", *resources, ""])
    lines.extend([
        "## Official product documentation",
        "",
        f"- [Backblaze AI and machine learning]({catalog['canonical_documentation']}): Canonical B2 documentation hub.",
        "- [Backblaze B2 documentation](https://www.backblaze.com/docs/cloud-storage): Current product behavior and setup.",
        "- [Backblaze B2 pricing](https://www.backblaze.com/cloud-storage/pricing): Current pricing and egress terms.",
        "",
    ])
    optional = existing(root, LLMS_OPTIONAL)
    if optional:
        lines.extend(["## Optional", "", *optional, ""])
    return "\n".join(lines)


def expected_outputs(catalog: dict, root: Path = ROOT) -> dict[Path, str]:
    p = paths(root)
    return {
        p["readme"]: render_readme(catalog, root),
        p["doc_index"]: render_docs_index(catalog, root),
        p["skill_index"]: render_skill_index(catalog, root),
        p["llms"]: render_llms(catalog, root),
    }


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if generated files are stale")
    args = parser.parse_args(argv)
    try:
        catalog = load_catalog(root)
        outputs = expected_outputs(catalog, root)
    except (OSError, ValueError, jsonschema.SchemaError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    stale = [path for path, content in outputs.items() if not path.exists() or path.read_text() != content]
    if args.check:
        if stale:
            print("stale generated files: " + ", ".join(str(path.relative_to(root)) for path in stale), file=sys.stderr)
            return 1
        print(f"catalog valid; {len(outputs)} generated files current")
        return 0

    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        print(f"wrote {path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
