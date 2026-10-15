#!/usr/bin/env python3
"""Print, or apply, the `b2` commands that create per-zone buckets and scoped keys.

By default the script only prints the commands. Nothing runs unless `--apply` is
passed explicitly, so a plain run is always safe to read and review.

For each architecture the script plans:

* one bucket per zone (incoming, work, releases, protected), named
  `<bucket-prefix>-<architecture short name>-<zone>`;
* one application key per purpose prefix, limited to that bucket and prefix.

There is no per-tenant prefix: the layouts assume a single organization.

Usage:

    python infrastructure/b2-provisioning/provision.py --bucket-prefix my-org
    python infrastructure/b2-provisioning/provision.py --bucket-prefix my-org --apply
    python infrastructure/b2-provisioning/provision.py --bucket-prefix my-org --cleanup

`--apply` runs the `b2` command line tool with the account it is authorized for;
authorize it first with `b2 account authorize`. The script never reads or prints
credentials: the tool prints each new key once, so keep that output.
"""
from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass

DEFAULT_BUCKET_PREFIX = "my-org"

# Zone env var names and purpose prefixes shared with examples/README.md.
ZONES = {
    "incoming": {
        "env": "B2_INCOMING_BUCKET",
        "capabilities": "listFiles,readFiles,writeFiles",
    },
    "work": {
        "env": "B2_WORK_BUCKET",
        "capabilities": "listFiles,readFiles,writeFiles",
    },
    "releases": {
        "env": "B2_RELEASES_BUCKET",
        "capabilities": "listFiles,readFiles,writeFiles",
    },
    "protected": {
        "env": "B2_PROTECTED_BUCKET",
        "capabilities": "listFiles,readFiles,writeFiles,readFileRetentions,readFileLegalHolds",
    },
}

# Prefixes each architecture uses in each zone. The protected zone is created
# with Object Lock enabled, which cannot be turned off later.
ARCHITECTURES = {
    "voice-training-data-lifecycle": {
        "short": "voice-dataset",
        "zones": {
            "incoming": ["contributions/"],
            "work": ["candidates/", "scratch/", "evaluations/"],
            "releases": ["datasets/"],
            "protected": ["consents/"],
        },
    },
    "batch-voice-generation-and-dubbing": {
        "short": "voice-generation",
        "zones": {
            "incoming": ["source/"],
            "work": ["candidates/", "scratch/"],
            "releases": ["deliveries/"],
            "protected": ["consents/"],
        },
    },
    "voice-agent-session-archive": {
        "short": "voice-sessions",
        "zones": {
            "incoming": ["sessions/"],
            "work": ["evaluations/", "scratch/"],
            "releases": ["datasets/"],
            "protected": ["sessions/", "consents/"],
        },
    },
}

BUCKET_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{4,48}[a-z0-9]$")
KEY_NAME = re.compile(r"^[A-Za-z0-9-]{1,100}$")
PLACEHOLDER = re.compile(r"^<[A-Za-z]+>$")


@dataclass(frozen=True)
class Step:
    """One command: a human description and the argument vector to run."""

    kind: str  # "bucket" or "key"
    description: str
    argv: tuple[str, ...]

    def shell(self) -> str:
        # Placeholders such as <applicationKeyId> stay unquoted so they read as placeholders.
        return " ".join(a if PLACEHOLDER.match(a) else shlex.quote(a) for a in self.argv)


@dataclass(frozen=True)
class Bucket:
    architecture: str
    zone: str
    name: str
    env: str


def bucket_name(bucket_prefix: str, architecture: str, zone: str) -> str:
    name = f"{bucket_prefix}-{ARCHITECTURES[architecture]['short']}-{zone}"
    if not BUCKET_NAME.match(name):
        raise ValueError(
            f"Bucket name {name!r} is not valid: use 6 to 50 lowercase letters, digits and hyphens"
        )
    return name


def key_name(architecture: str, zone: str, prefix: str) -> str:
    name = f"{ARCHITECTURES[architecture]['short']}-{zone}-{prefix.strip('/')}"
    if not KEY_NAME.match(name):
        raise ValueError(f"Key name {name!r} is not valid: use letters, digits and hyphens only")
    return name


def buckets(bucket_prefix: str, architectures: list[str]) -> list[Bucket]:
    return [
        Bucket(arch, zone, bucket_name(bucket_prefix, arch, zone), ZONES[zone]["env"])
        for arch in architectures
        for zone in ARCHITECTURES[arch]["zones"]
    ]


def plan(bucket_prefix: str, architectures: list[str] | None = None) -> list[Step]:
    """Return the ordered create commands: all buckets of an architecture, then its keys."""
    selected = architectures or list(ARCHITECTURES)
    steps: list[Step] = []
    for arch in selected:
        for zone, prefixes in ARCHITECTURES[arch]["zones"].items():
            name = bucket_name(bucket_prefix, arch, zone)
            argv = ["b2", "bucket", "create"]
            if zone == "protected":
                argv.append("--file-lock-enabled")
            argv += ["--default-server-side-encryption", "SSE-B2", name, "allPrivate"]
            steps.append(Step("bucket", f"{arch}: {zone} zone bucket", tuple(argv)))
        for zone, prefixes in ARCHITECTURES[arch]["zones"].items():
            name = bucket_name(bucket_prefix, arch, zone)
            for prefix in prefixes:
                argv = [
                    "b2", "key", "create",
                    "--bucket", name,
                    "--name-prefix", prefix,
                    key_name(arch, zone, prefix),
                    ZONES[zone]["capabilities"],
                ]
                steps.append(Step("key", f"{arch}: key for {zone}/{prefix}", tuple(argv)))
    return steps


def cleanup_plan(bucket_prefix: str, architectures: list[str] | None = None) -> list[Step]:
    """Return the commands that remove what `plan` creates. Printed only, never run."""
    selected = architectures or list(ARCHITECTURES)
    steps: list[Step] = []
    for arch in selected:
        for zone, prefixes in ARCHITECTURES[arch]["zones"].items():
            for prefix in prefixes:
                steps.append(Step(
                    "key",
                    f"{arch}: delete key {key_name(arch, zone, prefix)} (find its ID with `b2 key list`)",
                    ("b2", "key", "delete", "<applicationKeyId>"),
                ))
    for arch in selected:
        for zone in ARCHITECTURES[arch]["zones"]:
            name = bucket_name(bucket_prefix, arch, zone)
            uri = f"b2://{name}"
            steps.append(Step("bucket", f"{arch}: cancel unfinished uploads in {name}",
                              ("b2", "file", "large", "unfinished", "cancel", uri)))
            rm = ["b2", "rm", "--recursive", "--versions"]
            if zone == "protected":
                rm.append("--bypass-governance")
            steps.append(Step("bucket", f"{arch}: delete every version in {name}", tuple(rm + [uri])))
            steps.append(Step("bucket", f"{arch}: delete bucket {name}",
                              ("b2", "bucket", "delete", name)))
    return steps


def render(steps: list[Step]) -> str:
    lines: list[str] = []
    for step in steps:
        lines.append(f"# {step.description}")
        lines.append(step.shell())
    return "\n".join(lines)


def environment_block(bucket_prefix: str, architectures: list[str]) -> str:
    lines = ["# Environment variable names for each architecture's buckets"]
    for arch in architectures:
        lines.append(f"# {arch}")
        for zone in ARCHITECTURES[arch]["zones"]:
            lines.append(f"#   {ZONES[zone]['env']}={bucket_name(bucket_prefix, arch, zone)}")
    return "\n".join(lines)


def run(steps: list[Step]) -> None:
    """Run each command with the `b2` tool; its output goes straight to the terminal."""
    if shutil.which("b2") is None:
        sys.exit("The b2 command line tool is not installed or not on PATH.")
    for step in steps:
        print(f"+ {step.shell()}", flush=True)
        subprocess.run(step.argv, check=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bucket-prefix", default=DEFAULT_BUCKET_PREFIX,
                        help="Organization prefix for bucket names (bucket names are unique across B2)")
    parser.add_argument("--architecture", action="append", choices=sorted(ARCHITECTURES),
                        help="Architecture to plan; repeat for several (default: all)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Print the commands and run nothing (this is the default)")
    mode.add_argument("--apply", action="store_true", help="Run the commands with the b2 tool")
    parser.add_argument("--cleanup", action="store_true",
                        help="Print the commands that delete what this script creates (never runs them)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    architectures = args.architecture or list(ARCHITECTURES)
    if args.cleanup:
        if args.apply:
            sys.exit("--cleanup only prints commands; review them and run them yourself.")
        print(render(cleanup_plan(args.bucket_prefix, architectures)))
        return 0
    steps = plan(args.bucket_prefix, architectures)
    if args.apply:
        if args.bucket_prefix == DEFAULT_BUCKET_PREFIX:
            sys.exit("Pass your own --bucket-prefix with --apply: bucket names are unique across B2.")
        run(steps)
        print(environment_block(args.bucket_prefix, architectures))
        return 0
    print("# Dry run: nothing is executed. Pass --apply to run these commands.")
    print(render(steps))
    print(environment_block(args.bucket_prefix, architectures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
