"""The overview diagram renders at 1600x900 and the committed PNG has that size."""
from __future__ import annotations

import struct
from pathlib import Path

import yaml

import render_diagram

REPO = Path(__file__).resolve().parents[1]
COMMITTED = REPO / "docs" / "images" / "voice-ai-architectures.png"


def png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", header[16:24])


def test_committed_diagram_is_1600_by_900():
    assert png_size(COMMITTED) == (1600, 900)


def test_render_writes_1600_by_900(tmp_path: Path):
    output = tmp_path / "diagram.png"
    assert render_diagram.main(["--output", str(output)]) == 0
    assert png_size(output) == (1600, 900)


def test_render_is_deterministic(tmp_path: Path):
    first, second = tmp_path / "a.png", tmp_path / "b.png"
    render_diagram.render(first)
    render_diagram.render(second)
    assert first.read_bytes() == second.read_bytes()


def test_diagram_shows_every_catalog_architecture_and_zone():
    titles = " ".join(row["title"] for row in render_diagram.ROWS)
    catalog = yaml.safe_load((REPO / "catalog" / "architectures.yaml").read_text())
    for architecture in catalog["architectures"]:
        assert architecture["name"] in titles
    assert set(render_diagram.ZONES) == {"incoming", "work", "releases", "protected"}
    for row in render_diagram.ROWS:
        assert set(row["boxes"]) == {"left", "incoming", "work", "releases", "protected", "right"}
    assert len(render_diagram.NOT_IN_PATH) == 8
