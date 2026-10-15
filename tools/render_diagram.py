#!/usr/bin/env python3
"""Render the overview diagram of the three architectures to a 1600x900 PNG.

The diagram shows, per architecture, what lives in each B2 zone bucket, what stays
outside B2, and the parts of a voice AI system that B2 is not in the path of.

    python tools/render_diagram.py            # writes docs/images/voice-ai-architectures.png
    python tools/render_diagram.py --output /tmp/diagram.png

The output is deterministic for a given matplotlib version: no timestamp or software
version is written into the PNG metadata.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "images" / "voice-ai-architectures.png"
WIDTH, HEIGHT, DPI = 1600, 900, 100

INK = "#1f2933"
MUTED = "#52606d"
B2_REGION = "#eef4fb"
B2_EDGE = "#2f6db5"
ZONE_FILL = "#ffffff"
ZONE_EDGE = "#2f6db5"
PROTECTED_FILL = "#fff7e6"
PROTECTED_EDGE = "#b7791f"
OUTSIDE_FILL = "#f3f4f6"
OUTSIDE_EDGE = "#7b8794"
ARROW = "#334e68"

# Column x ranges, in pixels from the left edge.
LEFT = (30, 290)
ZONES = {
    "incoming": (320, 560),
    "work": (560, 800),
    "releases": (800, 1040),
    "protected": (1040, 1280),
}
RIGHT = (1310, 1570)
B2_SPAN = (305, 1295)

ROW_TOP = (130, 320, 510)
ROW_HEIGHT = 185
BOX_TOP, BOX_BOTTOM, LANE = 34, 128, 160
ZONE_MARGIN = 12

COLUMN_HEADERS = [
    (LEFT, "Outside B2: sources"),
    (ZONES["incoming"], "Incoming zone"),
    (ZONES["work"], "Work zone"),
    (ZONES["releases"], "Releases zone"),
    (ZONES["protected"], "Protected zone"),
    (RIGHT, "Outside B2: readers"),
]

# Each row: title, boxes per column (title line, detail lines), direct arrows between
# adjacent columns, and routed arrows that pass under the boxes.
ROWS = [
    {
        "title": "1  Voice training data lifecycle",
        "boxes": {
            "left": ("Contributor app", ["consent recorded first,", "then presigned PUT upload"]),
            "incoming": ("contributions/", ["clips and sidecars;", "event starts validation"]),
            "work": ("candidates/", ["validated clips; reviews in", "evaluations/; lifecycle expiry"]),
            "releases": ("datasets/<version>/", ["immutable release,", "release manifest last"]),
            "protected": ("consents/", ["consent records,", "Object Lock retention"]),
            "right": ("Training jobs", ["read-only key, local cache;", "NeMo, Lhotse, WebDataset, MDS"]),
        },
        "direct": [("left", "incoming"), ("incoming", "work"), ("work", "releases")],
        "routed": [("releases", "right", "read through a local cache")],
    },
    {
        "title": "2  Batch voice generation and dubbing",
        "boxes": {
            "left": ("Application and providers", ["ElevenLabs, Deepgram, AssemblyAI", "read sources by presigned GET"]),
            "incoming": ("source/", ["audio, video and scripts;", "event starts a job"]),
            "work": ("candidates/<job_id>/", ["outputs copied by the app;", "unpromoted outputs expire"]),
            "releases": ("deliveries/<version>/", ["approved outputs with", "lineage, never rewritten"]),
            "protected": ("consents/", ["checked before every job,", "Object Lock retention"]),
            "right": ("Recipients", ["short-lived presigned GET", "or a private CDN"]),
        },
        "direct": [("left", "incoming", "both"), ("work", "releases")],
        "routed": [
            ("left", "work", "app copies provider outputs"),
            ("releases", "right", "delivery"),
        ],
    },
    {
        "title": "3  Voice agent session archive",
        "boxes": {
            "left": ("Voice platform", ["live audio stays here, not in B2;", "LiveKit Egress or an adapter"]),
            "incoming": ("sessions/<id>/", ["recordings, manifest last;", "event starts evaluation"]),
            "work": ("evaluations/", ["quality and safety results", "per session"]),
            "releases": ("datasets/", ["passed sessions whose", "consent covers the use"]),
            "protected": ("sessions/, consents/", ["retention per class,", "Object Lock and legal hold"]),
            "right": ("Reviewers and analytics", ["short-lived presigned GET;", "analytics database"]),
        },
        "direct": [("left", "incoming"), ("incoming", "work"), ("protected", "releases"), ("protected", "right")],
        "routed": [("incoming", "protected", "copy by retention class after evaluation")],
    },
]

NOT_IN_PATH = [
    ("Real-time audio transport", "the voice platform"),
    ("Session state during a call", "memory or a low-latency store"),
    ("Message queue", "an external queue"),
    ("Speech and voice models", "providers or your own GPUs"),
    ("Workflow orchestration", "your job runner"),
    ("Metadata catalog and search", "a database"),
    ("Vector search", "a vector database"),
    ("Per-step training reads", "local NVMe or a cluster cache"),
]


def column(name: str) -> tuple[float, float]:
    if name == "left":
        return LEFT
    if name == "right":
        return RIGHT
    x0, x1 = ZONES[name]
    return x0 + ZONE_MARGIN, x1 - ZONE_MARGIN


def box_style(name: str) -> tuple[str, str]:
    if name in ("left", "right"):
        return OUTSIDE_FILL, OUTSIDE_EDGE
    if name == "protected":
        return PROTECTED_FILL, PROTECTED_EDGE
    return ZONE_FILL, ZONE_EDGE


def draw_box(ax, name: str, top: float, title: str, details: list[str]) -> None:
    x0, x1 = column(name)
    y0, y1 = top + BOX_TOP, top + BOX_BOTTOM
    fill, edge = box_style(name)
    ax.add_patch(FancyBboxPatch(
        (x0, y0), x1 - x0, y1 - y0, boxstyle="round,pad=0,rounding_size=8",
        facecolor=fill, edgecolor=edge, linewidth=1.4,
        linestyle="--" if name in ("left", "right") else "-",
    ))
    cx = (x0 + x1) / 2
    ax.text(cx, y0 + 22, title, ha="center", va="center", fontsize=10.5 if "/" in title else 11.5, fontweight="bold",
            color=INK, family="monospace" if "/" in title else "sans-serif")
    for index, line in enumerate(details):
        ax.text(cx, y0 + 48 + index * 20, line, ha="center", va="center", fontsize=9.5, color=MUTED)


def arrow(ax, start: tuple[float, float], end: tuple[float, float], both: bool = False) -> None:
    ax.add_patch(FancyArrowPatch(
        start, end, arrowstyle="<|-|>" if both else "-|>", mutation_scale=14,
        color=ARROW, linewidth=1.6, shrinkA=0, shrinkB=0,
    ))


def direct_arrow(ax, top: float, source: str, target: str, both: bool = False) -> None:
    sx0, sx1 = column(source)
    tx0, tx1 = column(target)
    y = top + (BOX_TOP + BOX_BOTTOM) / 2
    if tx0 >= sx1:
        arrow(ax, (sx1, y), (tx0, y), both)
    else:
        arrow(ax, (sx0, y), (tx1, y), both)


def routed_arrow(ax, top: float, source: str, target: str, label: str) -> None:
    sx = sum(column(source)) / 2
    tx = sum(column(target)) / 2
    y_box, y_lane = top + BOX_BOTTOM, top + LANE
    ax.plot([sx, sx, tx], [y_box, y_lane, y_lane], color=ARROW, linewidth=1.6, solid_joinstyle="miter")
    arrow(ax, (tx, y_lane), (tx, y_box))
    ax.text((sx + tx) / 2, y_lane - 9, label, ha="center", va="bottom", fontsize=9.5, color=ARROW,
            bbox={"facecolor": B2_REGION if B2_SPAN[0] < (sx + tx) / 2 < B2_SPAN[1] else "white",
                  "edgecolor": "none", "pad": 1.5})


def render(output: Path) -> Path:
    fig = plt.figure(figsize=(WIDTH / DPI, HEIGHT / DPI), dpi=DPI)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, WIDTH)
    ax.set_ylim(HEIGHT, 0)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    ax.text(30, 36, "Voice and speech AI on Backblaze B2: three reference architectures",
            fontsize=19, fontweight="bold", color=INK, va="center")
    ax.text(30, 66, "B2 holds finished recordings, datasets, candidates, deliveries and manifests. "
            "Live audio, queues, models and compute stay outside B2.",
            fontsize=11.5, color=MUTED, va="center")

    # B2 region and zone columns.
    ax.add_patch(Rectangle((B2_SPAN[0], 86), B2_SPAN[1] - B2_SPAN[0], 614,
                           facecolor=B2_REGION, edgecolor=B2_EDGE, linewidth=1.6))
    for x0, _ in list(ZONES.values())[1:]:
        ax.plot([x0, x0], [118, 694], color=B2_EDGE, linewidth=0.6, alpha=0.35)
    ax.text(B2_SPAN[1] - 10, 100, "Backblaze B2: one private bucket per zone, purpose prefixes inside",
            ha="right", va="center", fontsize=9.5, color=B2_EDGE, fontweight="bold")
    for (x0, x1), header in COLUMN_HEADERS:
        ax.text((x0 + x1) / 2, 120, header, ha="center", va="center", fontsize=11.5,
                fontweight="bold", color=PROTECTED_EDGE if header.startswith("Protected") else
                (OUTSIDE_EDGE if header.startswith("Outside") else B2_EDGE))

    for top, row in zip(ROW_TOP, ROWS):
        ax.text(LEFT[0], top + 18, row["title"], fontsize=12, fontweight="bold", color=INK, va="center",
                bbox={"facecolor": "white", "edgecolor": "none", "pad": 2})
        for name, (title, details) in row["boxes"].items():
            draw_box(ax, name, top, title, details)
        for item in row["direct"]:
            direct_arrow(ax, top, item[0], item[1], both=len(item) > 2)
        for source, target, label in row["routed"]:
            routed_arrow(ax, top, source, target, label)

    # Where B2 is not in the path.
    band_top = 716
    ax.add_patch(Rectangle((30, band_top), 1540, 166, facecolor=OUTSIDE_FILL,
                           edgecolor=OUTSIDE_EDGE, linewidth=1.2, linestyle="--"))
    ax.text(46, band_top + 20, "Where B2 is not in the path, and what holds it instead",
            fontsize=12, fontweight="bold", color=INK, va="center")
    chip_w, chip_h, gap = 368, 52, 12
    for index, (what, instead) in enumerate(NOT_IN_PATH):
        col, line = index % 4, index // 4
        x = 46 + col * (chip_w + gap)
        y = band_top + 40 + line * (chip_h + 10)
        ax.add_patch(FancyBboxPatch((x, y), chip_w, chip_h, boxstyle="round,pad=0,rounding_size=6",
                                    facecolor="white", edgecolor=OUTSIDE_EDGE, linewidth=1))
        ax.text(x + 14, y + 17, what, fontsize=10.5, fontweight="bold", color=INK, va="center")
        ax.text(x + 14, y + 37, instead, fontsize=10, color=MUTED, va="center")

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=DPI, format="png", metadata={"Software": None})
    plt.close(fig)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=OUTPUT, help="PNG path to write")
    args = parser.parse_args(argv)
    path = render(args.output)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
