"""Static HTML and JSON report generation."""

from __future__ import annotations

import json
from collections import Counter, defaultdict, deque
from html import escape

from formal_islands.models import ProofEdge, ProofGraph, ProofNode, ReviewObligation


NODE_WIDTH = 152
NODE_HEIGHT = 76
X_GAP = 188
Y_GAP = 124
MARGIN_X = 44
MARGIN_Y = 32


def export_report_bundle(graph: ProofGraph, obligations: list[ReviewObligation]) -> dict:
    """Export a JSON-serializable report bundle."""

    return {
        "graph": graph.model_dump(mode="json"),
        "review_obligations": [obligation.model_dump(mode="json") for obligation in obligations],
    }


def render_html_report(graph: ProofGraph, obligations: list[ReviewObligation]) -> str:
    """Render a compact static HTML report with interactive checklist and graph widget."""

    status_counts = Counter(node.status for node in graph.nodes)
    checklist_items = "\n".join(_render_checklist_item(obligation) for obligation in obligations)
    node_sections = "\n".join(_render_node_section(node) for node in graph.nodes)
    graph_widget = _render_graph_widget(graph)
    graph_payload = json.dumps(graph.model_dump(mode="json"))
    obligation_payload = json.dumps([obligation.model_dump(mode="json") for obligation in obligations])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(graph.theorem_title)} - Formal Islands Report</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f4ec;
      --panel: #fffdf8;
      --ink: #1f1a17;
      --muted: #6a625d;
      --border: #d8cfc2;
      --accent: #8c4f2b;
      --accent-soft: #f5e8d6;
      --highlight: #d97c2b;
      --checked: #2f8f5b;
      --checked-soft: #e6f5eb;
      --edge: #b3aa9f;
    }}
    * {{
      box-sizing: border-box;
    }}
    html {{
      scroll-behavior: smooth;
    }}
    body {{
      margin: 0;
      font-family: Georgia, "Iowan Old Style", serif;
      background: radial-gradient(circle at top, #fff8e7, var(--bg));
      color: var(--ink);
    }}
    main {{
      max-width: 1040px;
      margin: 0 auto;
      padding: 2rem 1rem 4rem;
    }}
    section, article {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1rem 1.15rem;
      margin-top: 1rem;
      box-shadow: 0 12px 30px rgba(60, 40, 20, 0.06);
    }}
    h1, h2, h3, h4 {{
      margin-top: 0;
    }}
    .meta {{
      color: var(--muted);
    }}
    .pill {{
      display: inline-block;
      margin-right: 0.45rem;
      margin-bottom: 0.45rem;
      padding: 0.2rem 0.58rem;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 0.88rem;
    }}
    .graph-shell {{
      overflow-x: auto;
      padding-bottom: 0.3rem;
    }}
    .graph-stage {{
      position: relative;
      min-width: 100%;
      margin-top: 0.8rem;
      border: 1px solid var(--border);
      border-radius: 16px;
      background: linear-gradient(180deg, rgba(255, 251, 242, 0.92), rgba(245, 238, 227, 0.92));
    }}
    .graph-svg {{
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      overflow: visible;
      pointer-events: none;
    }}
    .graph-edge {{
      stroke: var(--edge);
      stroke-width: 3;
      opacity: 0.95;
      transition: stroke 140ms ease, stroke-width 140ms ease;
    }}
    .graph-edge.is-active {{
      stroke: var(--highlight);
      stroke-width: 4;
    }}
    .graph-edge.is-checked {{
      stroke: var(--checked);
      stroke-width: 4;
    }}
    .graph-node-btn {{
      position: absolute;
      width: {NODE_WIDTH}px;
      min-height: {NODE_HEIGHT}px;
      border-radius: 22px;
      border: 2px solid var(--accent);
      background: rgba(255, 250, 241, 0.98);
      color: var(--ink);
      padding: 0.6rem 0.7rem 0.55rem;
      cursor: pointer;
      box-shadow: 0 8px 18px rgba(73, 48, 24, 0.08);
      text-align: center;
      transition: border-color 140ms ease, background 140ms ease, box-shadow 140ms ease;
    }}
    .graph-node-btn:hover {{
      border-color: var(--highlight);
      box-shadow: 0 10px 18px rgba(217, 124, 43, 0.12);
    }}
    .graph-node-btn.is-active {{
      border-color: var(--highlight);
      background: #fff0df;
    }}
    .graph-node-btn.is-checked {{
      border-color: var(--checked);
      background: var(--checked-soft);
    }}
    .graph-node-title {{
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      line-height: 1.15;
      font-size: 0.98rem;
      font-weight: 700;
      margin-bottom: 0.35rem;
      text-wrap: balance;
    }}
    .graph-node-id {{
      display: block;
      font-size: 0.84rem;
      color: var(--muted);
      letter-spacing: 0.02em;
    }}
    .graph-caption {{
      margin-top: 0.65rem;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .checklist {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 0.8rem;
    }}
    .checklist-item {{
      border: 1px solid var(--border);
      border-radius: 12px;
      background: rgba(255, 250, 241, 0.95);
      transition: border-color 140ms ease, background 140ms ease;
    }}
    .checklist-item.is-checked {{
      border-color: var(--checked);
      background: var(--checked-soft);
    }}
    .checklist-label {{
      display: grid;
      grid-template-columns: 1.35rem 1fr;
      gap: 0.8rem;
      align-items: start;
      padding: 0.85rem 0.95rem;
      cursor: pointer;
    }}
    .checklist-label input {{
      width: 1.1rem;
      height: 1.1rem;
      margin-top: 0.1rem;
      accent-color: var(--checked);
    }}
    .checklist-kind {{
      display: inline-block;
      margin-bottom: 0.24rem;
      color: var(--accent);
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .checklist-nodes {{
      margin-top: 0.45rem;
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .node-jump {{
      color: inherit;
      text-decoration: none;
      border-bottom: 1px dotted currentColor;
    }}
    .nodes-grid {{
      display: grid;
      gap: 1rem;
    }}
    .node-card {{
      scroll-margin-top: 1rem;
      transition: border-color 160ms ease, box-shadow 160ms ease, background 160ms ease;
    }}
    .node-card.is-highlighted {{
      border-color: var(--highlight);
      box-shadow: 0 0 0 3px rgba(217, 124, 43, 0.18), 0 12px 30px rgba(60, 40, 20, 0.06);
      background: #fff8ee;
    }}
    .node-card.is-checked {{
      border-color: var(--checked);
      background: #f8fdf9;
    }}
    pre {{
      overflow-x: auto;
      padding: 0.75rem;
      background: #f4efe6;
      border-radius: 8px;
      border: 1px solid var(--border);
    }}
    code {{
      font-family: "SFMono-Regular", Menlo, monospace;
    }}
    @media (max-width: 720px) {{
      main {{
        padding-inline: 0.8rem;
      }}
      .graph-node-btn {{
        width: 138px;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <section>
      <h1>{escape(graph.theorem_title)}</h1>
      <p>{escape(graph.theorem_statement)}</p>
      <p class="meta">Root node: {escape(graph.root_node_id)}</p>
    </section>
    <section>
      <h2>Graph Summary</h2>
      <p>
        <span class="pill">Nodes: {len(graph.nodes)}</span>
        <span class="pill">Edges: {len(graph.edges)}</span>
        <span class="pill">Informal: {status_counts.get("informal", 0)}</span>
        <span class="pill">Candidates: {status_counts.get("candidate_formal", 0)}</span>
        <span class="pill">Verified: {status_counts.get("formal_verified", 0)}</span>
        <span class="pill">Failed: {status_counts.get("formal_failed", 0)}</span>
      </p>
      <div class="graph-shell">
        {graph_widget}
      </div>
      <p class="graph-caption">
        Click a node to jump to its detail section. Hovering or checking review items highlights related nodes and incident edges.
      </p>
    </section>
    <section>
      <h2>Review Checklist</h2>
      <ul class="checklist">
        {checklist_items}
      </ul>
    </section>
    <section>
      <h2>Nodes</h2>
      <div class="nodes-grid">
        {node_sections}
      </div>
    </section>
  </main>
  <script id="report-graph-data" type="application/json">{escape(graph_payload)}</script>
  <script id="report-obligation-data" type="application/json">{escape(obligation_payload)}</script>
  <script>
    (() => {{
      const graph = JSON.parse(document.getElementById("report-graph-data").textContent);
      const obligations = JSON.parse(document.getElementById("report-obligation-data").textContent);

      const nodeCards = new Map(
        [...document.querySelectorAll("[data-node-id]")].map((el) => [el.dataset.nodeId, el])
      );
      const graphButtons = new Map(
        [...document.querySelectorAll("[data-graph-node-id]")].map((el) => [el.dataset.graphNodeId, el])
      );
      const graphEdges = [...document.querySelectorAll("[data-edge-key]")];
      const checklistItems = [...document.querySelectorAll("[data-obligation-id]")];

      function edgeKey(sourceId, targetId) {{
        return `${{sourceId}}->${{targetId}}`;
      }}

      function clearHighlights() {{
        nodeCards.forEach((card) => card.classList.remove("is-highlighted"));
        graphButtons.forEach((button) => button.classList.remove("is-active"));
        graphEdges.forEach((edge) => edge.classList.remove("is-active"));
      }}

      function highlightNodeSet(nodeIds) {{
        const nodeSet = new Set(nodeIds);
        clearHighlights();

        nodeSet.forEach((nodeId) => {{
          const card = nodeCards.get(nodeId);
          const button = graphButtons.get(nodeId);
          if (card) {{
            card.classList.add("is-highlighted");
          }}
          if (button) {{
            button.classList.add("is-active");
          }}
        }});

        graph.edges.forEach((edge) => {{
          if (nodeSet.has(edge.source_id) || nodeSet.has(edge.target_id)) {{
            const edgeElement = document.querySelector(`[data-edge-key="${{edgeKey(edge.source_id, edge.target_id)}}"]`);
            if (edgeElement) {{
              edgeElement.classList.add("is-active");
            }}
          }}
        }});
      }}

      function syncCheckedState() {{
        const checkedNodeIds = new Set();

        checklistItems.forEach((item) => {{
          const checkbox = item.querySelector('input[type="checkbox"]');
          const nodeIds = JSON.parse(item.dataset.nodeIds || "[]");
          item.classList.toggle("is-checked", checkbox.checked);
          if (checkbox.checked) {{
            nodeIds.forEach((nodeId) => checkedNodeIds.add(nodeId));
          }}
        }});

        nodeCards.forEach((card, nodeId) => {{
          card.classList.toggle("is-checked", checkedNodeIds.has(nodeId));
        }});
        graphButtons.forEach((button, nodeId) => {{
          button.classList.toggle("is-checked", checkedNodeIds.has(nodeId));
        }});
        graphEdges.forEach((edgeElement) => {{
          const [sourceId, targetId] = edgeElement.dataset.edgeKey.split("->");
          edgeElement.classList.toggle(
            "is-checked",
            checkedNodeIds.has(sourceId) || checkedNodeIds.has(targetId)
          );
        }});
      }}

      checklistItems.forEach((item) => {{
        const nodeIds = JSON.parse(item.dataset.nodeIds || "[]");
        const checkbox = item.querySelector('input[type="checkbox"]');
        item.addEventListener("mouseenter", () => highlightNodeSet(nodeIds));
        item.addEventListener("mouseleave", clearHighlights);
        checkbox.addEventListener("change", syncCheckedState);
      }});

      function scrollToNode(nodeId) {{
        const card = nodeCards.get(nodeId);
        if (!card) {{
          return;
        }}
        highlightNodeSet([nodeId]);
        card.scrollIntoView({{ behavior: "smooth", block: "start" }});
        window.setTimeout(clearHighlights, 1400);
      }}

      graphButtons.forEach((button, nodeId) => {{
        button.addEventListener("click", () => scrollToNode(nodeId));
      }});

      document.querySelectorAll("[data-node-jump]").forEach((link) => {{
        link.addEventListener("click", (event) => {{
          const nodeId = event.currentTarget.dataset.nodeJump;
          event.preventDefault();
          scrollToNode(nodeId);
        }});
      }});

      syncCheckedState();
    }})();
  </script>
</body>
</html>
"""


def _render_checklist_item(obligation: ReviewObligation) -> str:
    node_links = ", ".join(
        (
            f'<a class="node-jump" href="#node-{escape(node_id)}" '
            f'data-node-jump="{escape(node_id)}">{escape(node_id)}</a>'
        )
        for node_id in obligation.node_ids
    )
    return f"""
    <li class="checklist-item" data-obligation-id="{escape(obligation.id)}" data-node-ids='{escape(json.dumps(obligation.node_ids))}'>
      <label class="checklist-label">
        <input type="checkbox" />
        <span>
          <span class="checklist-kind">{escape(obligation.kind)}</span><br />
          {escape(obligation.text)}
          <div class="checklist-nodes">Related nodes: {node_links}</div>
        </span>
      </label>
    </li>
    """


def _render_node_section(node: ProofNode) -> str:
    display_label = (
        f"<p class=\"meta\">Display label: {escape(node.display_label)}</p>"
        if node.display_label
        else ""
    )
    candidate_block = ""
    if node.formalization_priority is not None and node.formalization_rationale is not None:
        candidate_block = (
            "<p class=\"meta\">"
            f"Formalization priority: {node.formalization_priority}. "
            f"Rationale: {escape(node.formalization_rationale)}"
            "</p>"
        )

    formal_block = ""
    if node.formal_artifact is not None:
        verification = node.formal_artifact.verification
        formal_block = f"""
        <h4>Formal Artifact</h4>
        <p><strong>Lean theorem name:</strong> {escape(node.formal_artifact.lean_theorem_name)}</p>
        <p><strong>Lean statement:</strong> {escape(node.formal_artifact.lean_statement)}</p>
        <p><strong>Verification status:</strong> {escape(verification.status)}</p>
        <p><strong>Verification command:</strong> <code>{escape(verification.command)}</code></p>
        <details>
          <summary>Lean code</summary>
          <pre><code>{escape(node.formal_artifact.lean_code)}</code></pre>
        </details>
        <details>
          <summary>Verification logs</summary>
          <pre><code>stdout:
{escape(verification.stdout)}

stderr:
{escape(verification.stderr)}</code></pre>
        </details>
        """

    return f"""
    <article class="node-card" id="node-{escape(node.id)}" data-node-id="{escape(node.id)}">
      <h3>{escape(node.title)}</h3>
      <p class="meta">Node id: {escape(node.id)} | Status: {escape(node.status)}</p>
      {display_label}
      {candidate_block}
      <p><strong>Informal statement:</strong> {escape(node.informal_statement)}</p>
      <p><strong>Informal proof:</strong> {escape(node.informal_proof_text)}</p>
      {formal_block}
    </article>
    """


def _render_graph_widget(graph: ProofGraph) -> str:
    layout = _compute_graph_layout(graph)
    width = layout["width"]
    height = layout["height"]
    edges_svg = "\n".join(
        _render_graph_edge(edge, layout["centers"][edge.source_id], layout["centers"][edge.target_id])
        for edge in graph.edges
    )
    nodes_html = "\n".join(
        _render_graph_node_button(node, *layout["positions"][node.id]) for node in graph.nodes
    )
    return f"""
    <div class="graph-stage" style="height: {height}px;">
      <svg class="graph-svg" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMin meet" aria-hidden="true">
        {edges_svg}
      </svg>
      <div style="position: relative; width: {width}px; height: {height}px;">
        {nodes_html}
      </div>
    </div>
    """


def _compute_graph_layout(graph: ProofGraph) -> dict:
    node_ids = [node.id for node in graph.nodes]
    children_by_source: dict[str, list[str]] = defaultdict(list)
    depth_by_node: dict[str, int] = {graph.root_node_id: 0}
    queue = deque([graph.root_node_id])
    seen = {graph.root_node_id}

    for edge in graph.edges:
        children_by_source[edge.source_id].append(edge.target_id)

    while queue:
        node_id = queue.popleft()
        depth = depth_by_node[node_id]
        for child_id in children_by_source.get(node_id, []):
            proposed = depth + 1
            previous = depth_by_node.get(child_id)
            if previous is None or proposed < previous:
                depth_by_node[child_id] = proposed
            if child_id not in seen:
                seen.add(child_id)
                queue.append(child_id)

    remaining = [node_id for node_id in node_ids if node_id not in depth_by_node]
    extra_depth_start = max(depth_by_node.values(), default=0) + 1
    for offset, node_id in enumerate(remaining):
        depth_by_node[node_id] = extra_depth_start + offset

    nodes_by_depth: dict[int, list[str]] = defaultdict(list)
    for node in graph.nodes:
        nodes_by_depth[depth_by_node[node.id]].append(node.id)

    max_nodes_per_row = max(len(row) for row in nodes_by_depth.values())
    width = max(420, MARGIN_X * 2 + max_nodes_per_row * NODE_WIDTH + (max_nodes_per_row - 1) * (X_GAP - NODE_WIDTH))
    height = MARGIN_Y * 2 + (max(depth_by_node.values(), default=0) + 1) * NODE_HEIGHT + max(depth_by_node.values(), default=0) * (Y_GAP - NODE_HEIGHT)

    positions: dict[str, tuple[float, float]] = {}
    centers: dict[str, tuple[float, float]] = {}

    for depth in sorted(nodes_by_depth):
        row_ids = sorted(nodes_by_depth[depth])
        row_width = len(row_ids) * NODE_WIDTH + (len(row_ids) - 1) * (X_GAP - NODE_WIDTH)
        start_x = (width - row_width) / 2
        y = MARGIN_Y + depth * Y_GAP
        for index, node_id in enumerate(row_ids):
            x = start_x + index * X_GAP
            positions[node_id] = (x, y)
            centers[node_id] = (x + NODE_WIDTH / 2, y + NODE_HEIGHT / 2)

    return {
        "positions": positions,
        "centers": centers,
        "width": int(width),
        "height": int(height),
    }


def _render_graph_edge(
    edge: ProofEdge,
    source_center: tuple[float, float],
    target_center: tuple[float, float],
) -> str:
    x1, y1 = source_center
    x2, y2 = target_center
    return (
        f'<line class="graph-edge" data-edge-key="{escape(edge.source_id)}-&gt;{escape(edge.target_id)}" '
        f'x1="{x1}" y1="{y1 + NODE_HEIGHT / 2 - 12}" x2="{x2}" y2="{y2 - NODE_HEIGHT / 2 + 12}" />'
    )


def _render_graph_node_button(node: ProofNode, x: float, y: float) -> str:
    label = escape(node.display_label or node.title)
    return f"""
    <button
      type="button"
      class="graph-node-btn"
      data-graph-node-id="{escape(node.id)}"
      style="left: {x}px; top: {y}px;"
      title="{escape(node.title)}"
    >
      <span class="graph-node-title">{label}</span>
      <span class="graph-node-id">{escape(node.id)}</span>
    </button>
    """
