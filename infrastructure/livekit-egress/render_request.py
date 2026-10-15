#!/usr/bin/env python3
"""Fill egress-request.template.json from environment variables and print the request.

Variables read from the environment (nothing is stored in the template):

    B2_REGION, B2_INCOMING_BUCKET, B2_APPLICATION_KEY_ID, B2_APPLICATION_KEY

The endpoint is derived from B2_REGION, the same way as in examples/python/b2_client.py.
The `${...}` markers belong to this script; the `{room_name}` and `{time}` markers are
LiveKit filepath templates and are left for LiveKit to fill.

Usage:

    python infrastructure/livekit-egress/render_request.py --room my-room > request.json

The output contains the application key secret: treat it like a credential and pass it
straight to the Egress API call.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from os import environ
from pathlib import Path
from string import Template
from typing import Mapping

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "egress-request.template.json"
REQUIRED = ("B2_REGION", "B2_INCOMING_BUCKET", "B2_APPLICATION_KEY_ID", "B2_APPLICATION_KEY")
REGION_PATTERN = re.compile(r"^[a-z]{2}-[a-z]+-[0-9]{3}$")


def render(room: str, source: Mapping[str, str] | None = None, template: Path = TEMPLATE) -> dict:
    """Return the request as a dictionary, with every placeholder filled."""
    values = dict(source if source is not None else environ)
    missing = [name for name in REQUIRED if not values.get(name)]
    if missing:
        raise RuntimeError("Set " + ", ".join(missing) + " in the environment")
    if not REGION_PATTERN.match(values["B2_REGION"]):
        raise ValueError(f"B2_REGION must look like 'us-west-004', got {values['B2_REGION']!r}")
    if not room:
        raise ValueError("A room name is required")
    # json.dumps escapes the values, so a quote in a value cannot break the document.
    mapping = {name: json.dumps(values[name])[1:-1] for name in REQUIRED}
    mapping["ROOM_NAME"] = json.dumps(room)[1:-1]
    return json.loads(Template(template.read_text()).substitute(mapping))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--room", required=True, help="LiveKit room name to record")
    args = parser.parse_args(argv)
    try:
        request = render(args.room)
    except (RuntimeError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2
    print(json.dumps(request, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
