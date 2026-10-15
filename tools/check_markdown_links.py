#!/usr/bin/env python3
"""Fail when a local Markdown link points to a missing repository path.

Fenced code blocks are ignored. Dependency folders (.venv, node_modules) and tool
caches are skipped.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
FENCE = re.compile(r"^\s*(```|~~~)")
SKIP_DIRS = {".git", ".venv", "node_modules", ".pytest_cache", "__pycache__"}


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


def markdown_files(root: Path) -> list[Path]:
    files = [
        path for path in sorted(root.rglob("*.md"))
        if not any(part in SKIP_DIRS for part in path.relative_to(root).parts)
    ]
    llms = root / "llms.txt"
    if llms.exists():
        files.append(llms)
    return files


def check(root: Path = ROOT) -> tuple[int, list[str]]:
    failures: list[str] = []
    checked = 0
    for path in markdown_files(root):
        for match in LINK.finditer(without_code_blocks(path.read_text())):
            raw = match.group(1).strip()
            if raw.startswith("<") and raw.endswith(">"):
                raw = raw[1:-1]
            target = raw.split(maxsplit=1)[0]
            parsed = urlparse(target)
            if parsed.scheme or parsed.netloc or target.startswith("#"):
                continue
            local = (path.parent / unquote(parsed.path)).resolve()
            checked += 1
            try:
                local.relative_to(root.resolve())
            except ValueError:
                failures.append(f"{path.relative_to(root)}: link escapes repository: {target}")
                continue
            if not local.exists():
                failures.append(f"{path.relative_to(root)}: missing {target}")
    return checked, failures


def main(root: Path = ROOT) -> int:
    checked, failures = check(root)
    if failures:
        print("\n".join(failures))
        return 1
    print(f"{checked} local Markdown links resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
