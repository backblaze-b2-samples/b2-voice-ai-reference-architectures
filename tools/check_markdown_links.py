#!/usr/bin/env python3
"""Fail when a local Markdown link points to a missing repository path.

Fenced code blocks are ignored. Dependency folders (.venv, node_modules) and tool
caches are skipped.

With `--external FILE` the tool instead checks that every external http(s) link in
that one file responds. Requests are HEAD (GET when the server rejects HEAD), carry
no credentials or cookies, and time out after `--timeout` seconds (default 15).
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
FENCE = re.compile(r"^\s*(```|~~~)")
AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")
SKIP_DIRS = {".git", ".venv", "node_modules", ".pytest_cache", "__pycache__"}
USER_AGENT = "markdown-link-check/1.0"
DEFAULT_TIMEOUT = 15.0


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


def external_links(path: Path) -> list[str]:
    """Return the unique http(s) targets of the Markdown links in a file, in order."""
    text = without_code_blocks(path.read_text())
    targets: list[str] = []
    for match in LINK.finditer(text):
        raw = match.group(1).strip()
        if raw.startswith("<") and raw.endswith(">"):
            raw = raw[1:-1]
        targets.append(raw.split(maxsplit=1)[0] if raw else raw)
    targets.extend(match.group(1) for match in AUTOLINK.finditer(text))
    seen: dict[str, None] = {}
    for target in targets:
        if urlparse(target).scheme in ("http", "https"):
            seen.setdefault(target, None)
    return list(seen)


def probe(url: str, timeout: float) -> str | None:
    """Return None when the URL answers with a non-error status, otherwise the reason."""
    opener = urllib.request.build_opener()
    for method in ("HEAD", "GET"):
        request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
        try:
            with opener.open(request, timeout=timeout) as response:
                if response.status < 400:
                    return None
                return f"HTTP {response.status}"
        except urllib.error.HTTPError as error:
            if method == "HEAD" and error.code in (400, 403, 405, 501):
                continue
            return f"HTTP {error.code}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            return f"{type(error).__name__}: {getattr(error, 'reason', error)}"
    return "no response"


def check_external(path: Path, timeout: float = DEFAULT_TIMEOUT) -> tuple[int, list[str]]:
    failures: list[str] = []
    links = external_links(path)
    for url in links:
        reason = probe(url, timeout)
        if reason is not None:
            failures.append(f"{path}: {url} ({reason})")
    return len(links), failures


def main_external(path: Path, timeout: float = DEFAULT_TIMEOUT) -> int:
    if not path.is_file():
        print(f"no such file: {path}")
        return 1
    checked, failures = check_external(path, timeout)
    if failures:
        print("\n".join(failures))
        return 1
    print(f"{checked} external links respond in {path}")
    return 0


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Markdown links.")
    parser.add_argument("--external", metavar="FILE", type=Path,
                        help="check that the external http(s) links in FILE respond, instead of the local links")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="seconds to wait for each external link (default %(default)s)")
    args = parser.parse_args(argv)
    if args.external is not None:
        return main_external(args.external, args.timeout)
    return main()


def main(root: Path = ROOT) -> int:
    checked, failures = check(root)
    if failures:
        print("\n".join(failures))
        return 1
    print(f"{checked} local Markdown links resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
