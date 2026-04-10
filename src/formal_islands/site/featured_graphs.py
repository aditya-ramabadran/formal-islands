"""Generate lightweight homepage graph teasers from saved run artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
import math
from pathlib import Path
import re

from formal_islands.models import ProofGraph
from formal_islands.report.graph_visibility import display_graph_without_hidden_subsumed_nodes
from formal_islands.report.graph_widget import graph_visual_status
from formal_islands.report.history import build_graph_history_frames, load_graph_history_entries
from formal_islands.report.rendering import slugify


@dataclass(frozen=True)
class FeaturedGraphSpec:
    """Configuration for one homepage graph teaser."""

    id: str
    report_url: str
    graph_json: str | None = None
    graph_history_jsonl: str | None = None
    history_index: int | str | None = None
    visually_distinct_history: bool = True


def load_featured_graph_specs(config_path: Path) -> list[FeaturedGraphSpec]:
    """Load homepage graph teaser specs from JSON."""

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    raw_examples = payload.get("examples")
    if not isinstance(raw_examples, list):
        raise ValueError("featured graph config must contain an examples list")
    specs: list[FeaturedGraphSpec] = []
    for raw in raw_examples:
        if not isinstance(raw, dict):
            raise ValueError("featured graph entries must be objects")
        specs.append(
            FeaturedGraphSpec(
                id=str(raw["id"]),
                report_url=str(raw["report_url"]),
                graph_json=str(raw["graph_json"]) if raw.get("graph_json") else None,
                graph_history_jsonl=(
                    str(raw["graph_history_jsonl"]) if raw.get("graph_history_jsonl") else None
                ),
                history_index=raw.get("history_index"),
                visually_distinct_history=bool(raw.get("visually_distinct_history", True)),
            )
        )
    return specs


def resolve_featured_graph(spec: FeaturedGraphSpec, *, repo_root: Path) -> ProofGraph:
    """Resolve a teaser graph from either a saved graph JSON or a graph-history snapshot."""

    if spec.graph_history_jsonl:
        history_path = (repo_root / spec.graph_history_jsonl).resolve()
        entries = load_graph_history_entries(history_path)
        frames = build_graph_history_frames(entries, visually_distinct_only=spec.visually_distinct_history)
        if not frames:
            raise ValueError(f"no usable graph-history frames found for {spec.id}")
        history_index = spec.history_index
        if history_index in (None, "latest"):
            frame = frames[-1]
        elif history_index == "first":
            frame = frames[0]
        elif isinstance(history_index, int):
            frame = frames[history_index]
        else:
            raise ValueError(f"unsupported history index for {spec.id}: {history_index!r}")
        return frame["graph"]

    if not spec.graph_json:
        raise ValueError(f"featured graph {spec.id} is missing graph_json or graph_history_jsonl")
    graph_path = (repo_root / spec.graph_json).resolve()
    return ProofGraph.model_validate_json(graph_path.read_text(encoding="utf-8"))


def render_featured_graph_html(graph: ProofGraph, *, graph_id: str) -> str:
    """Render a static, minimal homepage graph preview."""

    graph = display_graph_without_hidden_subsumed_nodes(graph)
    layout = _compute_featured_graph_layout(graph)
    width = float(layout["width"])
    height = float(layout["height"])
    font_scale = max(1.0, min(1.15, 440.0 / width))
    marker_id = f"featured-arrow-{slugify(graph_id)}"
    edges_svg = "\n".join(_render_featured_edge(edge, layout, marker_id=marker_id) for edge in graph.edges)
    nodes_svg = "\n".join(_render_featured_node_box(node, layout) for node in graph.nodes)
    text_svg = "\n".join(
        text
        for node in graph.nodes
        for text in [_render_featured_svg_label(node, layout, font_scale=font_scale)]
        if text
    )
    label_html = "\n".join(
        label
        for node in graph.nodes
        for label in [_render_featured_html_label(node, layout, width=width, height=height)]
        if label
    )
    aspect_ratio = f"{int(width)} / {int(height)}"
    return f"""
<div class="featured-graph" data-featured-graph-root style="aspect-ratio: {aspect_ratio}; --featured-graph-font-scale: {font_scale:.3f};">
  <svg class="featured-graph-svg" viewBox="0 0 {int(width)} {int(height)}" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
    <defs>
      <marker id="{marker_id}" markerWidth="8" markerHeight="8" refX="6.4" refY="2.5" orient="auto" markerUnits="strokeWidth">
        <path d="M0,0 L0,5 L7,2.5 z" fill="#b3aa9f"></path>
      </marker>
    </defs>
    <g>{edges_svg}</g>
    <g>{nodes_svg}</g>
    <g>{text_svg}</g>
  </svg>
  <div class="featured-graph-overlay">
    {label_html}
  </div>
</div>
""".strip()


def build_featured_graph_bundle(specs: list[FeaturedGraphSpec], *, repo_root: Path) -> dict[str, str]:
    """Render all configured homepage graph teasers."""

    bundle: dict[str, str] = {}
    for spec in specs:
        graph = resolve_featured_graph(spec, repo_root=repo_root)
        bundle[spec.id] = render_featured_graph_html(graph, graph_id=spec.id)
    return bundle


def write_featured_graph_bundle(
    *,
    config_path: Path,
    output_path: Path,
    repo_root: Path,
) -> None:
    """Generate the homepage graph JS payload from config."""

    specs = load_featured_graph_specs(config_path)
    bundle = build_featured_graph_bundle(specs, repo_root=repo_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(bundle, ensure_ascii=False)
    output_path.write_text(
        "// Generated by scripts/generate_featured_graphs.py\n"
        f"window.FORMAL_ISLANDS_FEATURED_GRAPHS = {payload};\n",
        encoding="utf-8",
    )


def _render_featured_edge(edge, layout: dict, *, marker_id: str) -> str:
    x1, y1 = layout["centers"][edge.source_id]
    x2, y2 = layout["centers"][edge.target_id]
    start_node_height = float(layout["node_sizes"][edge.source_id][1])
    end_node_height = float(layout["node_sizes"][edge.target_id][1])
    start_y = y1 + start_node_height / 2 - 3
    end_y = y2 - end_node_height / 2 + 3
    dx = x2 - x1
    span = end_y - start_y
    ctrl1_x, ctrl1_y = x1, start_y + span * 0.4
    ctrl2_x = x2 - dx * 0.3
    ctrl2_y = end_y - span * 0.3
    refinement_class = " featured-edge-refinement" if edge.label == "refined_from" else ""
    return (
        f'<path class="featured-graph-edge{refinement_class}" '
        f'd="M {x1:.1f} {start_y:.1f} C {ctrl1_x:.1f} {ctrl1_y:.1f}, '
        f'{ctrl2_x:.1f} {ctrl2_y:.1f}, {x2:.1f} {end_y:.1f}" marker-end="url(#{marker_id})"></path>'
    )


def _render_featured_node_box(node, layout: dict) -> str:
    x, y = layout["positions"][node.id]
    node_width, node_height = layout["node_sizes"][node.id]
    status = graph_visual_status(node)
    status_class = f"status-{slugify(status).replace('_', '-')}"
    rx = 28 if status == "formal_verified" else 10 if status == "formal_failed" else 22 if status == "candidate_formal" else 14
    return (
        f'<rect class="featured-graph-node-box {status_class}" x="{x}" y="{y}" rx="{rx}" ry="{rx}" width="{node_width}" height="{node_height}"></rect>'
        f'<circle class="featured-graph-node-badge {status_class}" cx="{x + node_width - 14}" cy="{y + 12}" r="4.6"></circle>'
    )


def _label_uses_mathjax(label: str) -> bool:
    return "\\(" in label or "\\[" in label or "$" in label


def _wrap_featured_label(text: str, *, node_width: float, font_scale: float) -> list[str]:
    words = text.split()
    if not words:
        return [text]
    if len(words) == 1:
        return [text]

    estimated_char_width = max(4.6, 7.1 * font_scale)
    hard_max_chars = max(10, int((node_width - 26) / estimated_char_width))
    comfortable_chars = max(9, int(hard_max_chars * 0.72))
    preferred_line_count = min(3, max(1, math.ceil(len(text) / comfortable_chars)))

    partitions: list[list[str]] = []

    def backtrack(start: int, remaining_lines: int, acc: list[str]) -> None:
        if remaining_lines == 1:
            partitions.append(acc + [" ".join(words[start:])])
            return
        max_split = len(words) - remaining_lines + 1
        for split in range(start + 1, max_split + 1):
            backtrack(split, remaining_lines - 1, acc + [" ".join(words[start:split])])

    for line_count in range(1, min(3, len(words)) + 1):
        backtrack(0, line_count, [])

    def score(lines: list[str]) -> tuple[float, float, float]:
        lengths = [len(line) for line in lines]
        max_len = max(lengths)
        overflow_penalty = max(0, max_len - hard_max_chars) * 100.0
        ragged_penalty = sum((length - (sum(lengths) / len(lengths))) ** 2 for length in lengths)
        line_count_penalty = abs(len(lines) - preferred_line_count) * 12.0
        return (overflow_penalty + ragged_penalty + line_count_penalty, max_len, ragged_penalty)

    best = min(partitions, key=score)
    return best


def _render_featured_svg_label(node, layout: dict, *, font_scale: float) -> str:
    x, y = layout["positions"][node.id]
    node_width, node_height = layout["node_sizes"][node.id]
    raw_label = node.display_label or node.title
    if _label_uses_mathjax(raw_label):
        return ""
    status = graph_visual_status(node)
    status_class = f"status-{slugify(status).replace('_', '-')}"
    center_x = x + node_width / 2
    center_y = y + node_height / 2
    lines = _wrap_featured_label(raw_label, node_width=node_width, font_scale=font_scale)
    line_height = 18.0 * font_scale
    start_y = center_y - (line_height * (len(lines) - 1) / 2)
    tspans = "".join(
        f'<tspan x="{center_x:.2f}" y="{start_y + idx * line_height:.2f}">{escape(line)}</tspan>'
        for idx, line in enumerate(lines)
    )
    return (
        f'<text class="featured-graph-node-text {status_class}" '
        f'x="{center_x:.2f}" y="{center_y:.2f}" text-anchor="middle" dominant-baseline="middle">'
        f"{tspans}</text>"
    )


def _render_featured_html_label(node, layout: dict, *, width: float, height: float) -> str:
    x, y = layout["positions"][node.id]
    node_width, node_height = layout["node_sizes"][node.id]
    raw_label = node.display_label or node.title
    if not _label_uses_mathjax(raw_label):
        return ""
    left = x / width * 100
    top = y / height * 100
    box_width = node_width / width * 100
    box_height = node_height / height * 100
    status = graph_visual_status(node)
    status_class = f"status-{slugify(status).replace('_', '-')}"
    return f"""
    <div class="featured-graph-label {status_class}" style="left:{left:.5f}%;top:{top:.5f}%;width:{box_width:.5f}%;height:{box_height:.5f}%;">
      <div class="featured-graph-title">{escape(raw_label)}</div>
    </div>
    """.strip()


def _compute_featured_graph_layout(graph: ProofGraph) -> dict:
    row_gap = 42
    col_gap = 34
    margin_x = 20
    margin_y = 16

    children_of: dict[str, list[str]] = {}
    for edge in graph.edges:
        children_of.setdefault(edge.source_id, []).append(edge.target_id)

    depths = {graph.root_node_id: 0}
    pending = [graph.root_node_id]
    while pending:
        node_id = pending.pop(0)
        depth = depths[node_id]
        for child_id in children_of.get(node_id, []):
            proposed = depth + 1
            previous = depths.get(child_id)
            if previous is None or proposed < previous:
                depths[child_id] = proposed
                pending.append(child_id)

    extra_start = max(depths.values(), default=0) + 1
    for node in graph.nodes:
        if node.id not in depths:
            depths[node.id] = extra_start

    rows: dict[int, list[str]] = {}
    for node in graph.nodes:
        rows.setdefault(depths[node.id], []).append(node.id)

    node_sizes = {node.id: _featured_node_dimensions(node.display_label or node.title) for node in graph.nodes}
    max_row_size = max(len(row) for row in rows.values())
    if max_row_size == 1:
        node_sizes = {
            node_id: (min(340.0, width * 1.08), height + 4.0)
            for node_id, (width, height) in node_sizes.items()
        }

    width = max(
        360.0,
        max(
            margin_x * 2
            + sum(node_sizes[node_id][0] for node_id in row_ids)
            + max(0, len(row_ids) - 1) * col_gap
            for row_ids in rows.values()
        ),
    )
    max_depth = max(rows, default=0)
    row_heights = {depth: max(node_sizes[node_id][1] for node_id in row_ids) for depth, row_ids in rows.items()}
    height = margin_y * 2 + sum(row_heights[depth] for depth in sorted(rows)) + max_depth * row_gap

    positions: dict[str, tuple[float, float]] = {}
    centers: dict[str, tuple[float, float]] = {}
    row_y_offsets: dict[int, float] = {}
    cursor_y = margin_y
    for depth in sorted(rows):
        row_y_offsets[depth] = cursor_y
        cursor_y += row_heights[depth] + row_gap
    for depth in sorted(rows):
        row_ids = sorted(rows[depth])
        row_width = sum(node_sizes[node_id][0] for node_id in row_ids) + max(0, len(row_ids) - 1) * col_gap
        start_x = (width - row_width) / 2
        row_height = row_heights[depth]
        cursor_x = start_x
        for node_id in row_ids:
            node_width, node_height = node_sizes[node_id]
            y = row_y_offsets[depth] + (row_height - node_height) / 2
            x = cursor_x
            positions[node_id] = (x, y)
            centers[node_id] = (x + node_width / 2, y + node_height / 2)
            cursor_x += node_width + col_gap

    return {
        "positions": positions,
        "centers": centers,
        "width": width,
        "height": height,
        "node_sizes": node_sizes,
    }


def _featured_node_dimensions(label: str) -> tuple[float, float]:
    plain_length = _plain_text_length(label)
    node_width = min(320.0, max(190.0, 165.0 + max(0, plain_length - 10) * 5.5))
    chars_per_line = max(12, int((node_width - 44.0) / 8.5))
    estimated_lines = max(1, min(3, (plain_length + chars_per_line - 1) // chars_per_line))
    node_height = 68.0 + estimated_lines * 16.0
    return node_width, node_height


def _plain_text_length(text: str) -> int:
    simplified = re.sub(r"\\\((.*?)\\\)", r"\1", text)
    simplified = re.sub(r"\$([^$]+)\$", r"\1", simplified)
    simplified = re.sub(r"\\[A-Za-z]+", "", simplified)
    simplified = re.sub(r"[\{\}_^]", "", simplified)
    return len(" ".join(simplified.split()))
