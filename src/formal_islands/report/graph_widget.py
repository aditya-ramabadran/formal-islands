"""SVG proof-graph rendering helpers for static reports."""

from __future__ import annotations

from collections import defaultdict, deque
from html import escape

from formal_islands.models import ProofEdge, ProofGraph, ProofNode, ReviewObligation
from formal_islands.report.rendering import slugify

NODE_WIDTH = 132
NODE_HEIGHT = 66
ROW_GAP = 52
COL_GAP = 36
MARGIN_X = 24
MARGIN_Y = 20
REFINEMENT_EDGE_LABELS = {"refined_from"}


def render_graph_widget(graph: ProofGraph, *, widget_key: str = "graph") -> str:
    layout = compute_graph_layout(graph)
    width = layout["width"]
    height = layout["height"]
    marker_id = f"graph-arrow-{slugify(widget_key)}"
    edges_svg = "\n".join(render_edge(edge, layout, marker_id=marker_id) for edge in graph.edges)
    nodes_svg = "\n".join(render_node(node, layout) for node in graph.nodes)
    return f"""
    <div class="graph-frame">
      <svg class="graph-widget" viewBox="0 0 {width} {height}" width="{width}" height="{height}" preserveAspectRatio="xMidYMin meet" role="img" aria-label="Proof graph">
        <defs>
          <marker id="{marker_id}" markerWidth="8" markerHeight="8" refX="6.4" refY="2.5" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L0,5 L7,2.5 z" fill="#b3aa9f"></path>
          </marker>
        </defs>
        <g>{edges_svg}</g>
        <g>{nodes_svg}</g>
      </svg>
    </div>
    """


def render_edge(edge: ProofEdge, layout: dict, *, marker_id: str) -> str:
    x1, y1 = layout["centers"][edge.source_id]
    x2, y2 = layout["centers"][edge.target_id]
    start_y = y1 + NODE_HEIGHT / 2 - 3
    end_y = y2 - NODE_HEIGHT / 2 + 3
    dx = x2 - x1
    span = end_y - start_y
    ctrl1_x, ctrl1_y = x1, start_y + span * 0.4
    ctrl2_x = x2 - dx * 0.3
    ctrl2_y = end_y - span * 0.3
    edge_class = edge_class_name(edge.source_id, edge.target_id)
    refinement_class = " edge-refinement" if edge.label in REFINEMENT_EDGE_LABELS else ""
    return (
        f'<path class="graph-edge {edge_class}{refinement_class}" '
        f'd="M {x1:.1f} {start_y:.1f} C {ctrl1_x:.1f} {ctrl1_y:.1f}, '
        f'{ctrl2_x:.1f} {ctrl2_y:.1f}, {x2:.1f} {end_y:.1f}" '
        f'marker-end="url(#{marker_id})"></path>'
    )


def render_node(node: ProofNode, layout: dict) -> str:
    x, y = layout["positions"][node.id]
    cx = x + NODE_WIDTH / 2
    node_key = node_class(node.id)
    visual_status = graph_visual_status(node)
    status = status_class(visual_status)
    rx = node_corner_radius(visual_status)
    raw_label = node.display_label or node.title
    fo_x = x + 4
    fo_y = y + 4
    fo_w = NODE_WIDTH - 8
    fo_h = NODE_HEIGHT - 22
    return f"""
    <a class="graph-node-link {node_key} {status}" href="#node-{escape(node.id)}" data-graph-node-id="{escape(node.id)}" data-graph-node-status="{escape(visual_status)}" data-graph-node-title="{escape(raw_label)}">
      <rect class="graph-node-box {status}" x="{x}" y="{y}" rx="{rx}" ry="{rx}" width="{NODE_WIDTH}" height="{NODE_HEIGHT}"></rect>
      <circle class="graph-node-badge {status}" cx="{x + NODE_WIDTH - 14}" cy="{y + 12}" r="4.6"></circle>
      <foreignObject x="{fo_x}" y="{fo_y}" width="{fo_w}" height="{fo_h}" overflow="visible">
        <div xmlns="http://www.w3.org/1999/xhtml" class="graph-node-fo-title">{escape(raw_label)}</div>
      </foreignObject>
      <text class="graph-node-id" x="{cx}" y="{y + NODE_HEIGHT - 18}">{escape(node.id)}</text>
    </a>
    """


def compute_graph_layout(graph: ProofGraph) -> dict:
    children_of: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        children_of[edge.source_id].append(edge.target_id)

    depths = {graph.root_node_id: 0}
    queue = deque([graph.root_node_id])
    while queue:
        node_id = queue.popleft()
        depth = depths[node_id]
        for child_id in children_of.get(node_id, []):
            proposed = depth + 1
            previous = depths.get(child_id)
            if previous is None or proposed < previous:
                depths[child_id] = proposed
                queue.append(child_id)

    extra_start = max(depths.values(), default=0) + 1
    for node in graph.nodes:
        if node.id not in depths:
            depths[node.id] = extra_start

    rows: dict[int, list[str]] = defaultdict(list)
    for node in graph.nodes:
        rows[depths[node.id]].append(node.id)

    max_row_size = max(len(row) for row in rows.values())
    width = max(420, MARGIN_X * 2 + max_row_size * NODE_WIDTH + max(0, max_row_size - 1) * COL_GAP)
    max_depth = max(rows, default=0)
    height = MARGIN_Y * 2 + (max_depth + 1) * NODE_HEIGHT + max_depth * ROW_GAP

    positions: dict[str, tuple[float, float]] = {}
    centers: dict[str, tuple[float, float]] = {}
    for depth in sorted(rows):
        row_ids = sorted(rows[depth])
        row_width = len(row_ids) * NODE_WIDTH + max(0, len(row_ids) - 1) * COL_GAP
        start_x = (width - row_width) / 2
        y = MARGIN_Y + depth * (NODE_HEIGHT + ROW_GAP)
        for index, node_id in enumerate(row_ids):
            x = start_x + index * (NODE_WIDTH + COL_GAP)
            positions[node_id] = (x, y)
            centers[node_id] = (x + NODE_WIDTH / 2, y + NODE_HEIGHT / 2)

    return {"positions": positions, "centers": centers, "width": width, "height": height}


def render_interaction_styles(graph: ProofGraph, obligations: list[ReviewObligation]) -> str:
    node_edge_map = incident_edge_classes(graph)
    blocks: list[str] = []

    for node in graph.nodes:
        node_key = node_class(node.id)
        blocks.append(
            f"""
            .report-root:has(a.{node_key}:hover) a.{node_key} .graph-node-box {{
              border-color: var(--highlight);
              stroke: var(--highlight);
              background: var(--highlight-soft);
              fill: var(--highlight-soft);
            }}
            .report-root:has(#{escape('node-' + node.id)}:target).report-root .{node_key}.node-card {{
              border-color: var(--highlight);
              box-shadow: 0 0 0 3px rgba(217, 124, 43, 0.18), 0 12px 30px rgba(60, 40, 20, 0.06);
              background: var(--target-panel);
            }}
            """
        )

    for obligation in obligations:
        obligation_slug = slugify(obligation.id)
        control_id = obligation_control_id(obligation.id)
        node_classes = [node_class(node_id) for node_id in obligation.node_ids]
        edge_classes = sorted(
            {
                edge_class
                for node_id in obligation.node_ids
                for edge_class in node_edge_map.get(node_id, set())
            }
        )

        hover_node_selector = ",\n".join(
            [f".report-root:has(.obligation-{obligation_slug}:hover) a.{node_class_name} .graph-node-box" for node_class_name in node_classes]
            + [f".report-root:has(.obligation-{obligation_slug}:hover) .{node_class_name}.node-card" for node_class_name in node_classes]
        )
        if hover_node_selector:
            blocks.append(
                f"""
                {hover_node_selector} {{
                  border-color: var(--highlight);
                  stroke: var(--highlight);
                  stroke-width: 2.8;
                  background: var(--preview-soft);
                  fill: var(--preview-soft);
                  filter: drop-shadow(0 8px 16px rgba(140, 79, 43, 0.14));
                }}
                """
            )

        checked_node_selector = ",\n".join(
            [f".report-root:has(#{control_id}:checked) a.{node_class_name} .graph-node-box" for node_class_name in node_classes]
            + [f".report-root:has(#{control_id}:checked) .{node_class_name}.node-card" for node_class_name in node_classes]
        )
        if checked_node_selector:
            blocks.append(
                f"""
                {checked_node_selector} {{
                  border-color: var(--checked);
                  stroke: var(--checked);
                  stroke-width: 2.9;
                  background: var(--checked-soft);
                  fill: var(--checked-soft);
                }}
                """
            )

        checked_edge_selector = ",\n".join(
            [f".report-root:has(#{control_id}:checked) .{edge_class_name}" for edge_class_name in edge_classes]
        )
        if checked_edge_selector:
            blocks.append(
                f"""
                {checked_edge_selector} {{
                  stroke: var(--checked);
                  stroke-width: 4.5;
                }}
                """
            )

        blocks.append(
            f"""
            .report-root:has(.obligation-{obligation_slug}:hover) .obligation-{obligation_slug} {{
              border-color: var(--highlight);
              background: var(--preview-soft);
            }}
            .report-root:has(#{control_id}:checked) .obligation-{obligation_slug} {{
              border-color: var(--checked);
              background: var(--checked-soft);
            }}
            """
        )

    return "\n".join(blocks)


def incident_edge_classes(graph: ProofGraph) -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        edge_class_name = edge_class(edge.source_id, edge.target_id)
        mapping[edge.source_id].add(edge_class_name)
        mapping[edge.target_id].add(edge_class_name)
    return mapping


def node_class(node_id: str) -> str:
    return f"node-{slugify(node_id)}"


def edge_class(source_id: str, target_id: str) -> str:
    return f"edge-{slugify(source_id)}-{slugify(target_id)}"


def edge_class_name(source_id: str, target_id: str) -> str:
    return edge_class(source_id, target_id)


def obligation_control_id(obligation_id: str) -> str:
    return f"obligation-check-{slugify(obligation_id)}"


def status_class(status: str) -> str:
    return f"status-{slugify(status).replace('_', '-')}"


def node_corner_radius(status: str) -> int:
    if status == "formal_verified":
        return 28
    if status == "formal_failed":
        return 10
    if status == "candidate_formal":
        return 22
    return 14


def graph_visual_status(node: ProofNode) -> str:
    if node.status == "candidate_formal":
        return "candidate_formal"
    if node.status in {"formal_verified", "formal_failed"} and node.formal_artifact is not None:
        return str(node.status)
    return "informal"
