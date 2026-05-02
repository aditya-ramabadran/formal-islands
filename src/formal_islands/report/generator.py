"""Static HTML and JSON report generation."""

from __future__ import annotations

from collections import Counter
from html import escape

from formal_islands.formalization.pipeline import parse_faithfulness_notes
from formal_islands.models import ProofGraph, ProofNode, ReviewObligation
from formal_islands.progress import GraphHistoryEntry
from formal_islands.report.graph_visibility import (
    display_graph_without_hidden_subsumed_nodes,
    subsumed_support_core_node_ids,
    subsumed_informal_node_ids,
)
from formal_islands.report.graph_widget import (
    NODE_HEIGHT,
    node_class,
    obligation_control_id,
    compute_graph_layout,
    render_graph_widget,
    render_interaction_styles,
)
from formal_islands.report.history import (
    build_graph_history_frames,
    graph_history_bundle,
    load_graph_history_entries,
    render_graph_history_script,
    render_graph_history_widget,
    render_graph_history_widget_with_cleanup,
)
from formal_islands.report.rendering import (
    display_verification_command,
    render_faithfulness_label,
    render_inline_code_html,
    render_lean_code_block,
    render_math_text,
    sanitize_report_payload,
    slugify,
)

# Backward-compatible re-exports for internal tests and helper callers.
_build_graph_history_frames = build_graph_history_frames
_compute_graph_layout = compute_graph_layout
_render_math_text = render_math_text


def _subsumed_cleanup_caption(hidden_nodes: list[ProofNode]) -> str:
    if not hidden_nodes:
        return ""
    if len(hidden_nodes) == 1:
        return (
            f"Final display cleanup hid subsumed informal node `{hidden_nodes[0].id}` because a verified parent "
            "theorem already discharged that dependency."
        )
    formatted = ", ".join(f"`{node.id}`" for node in hidden_nodes)
    return (
        f"Final display cleanup hid subsumed informal nodes {formatted} because verified parent theorems already "
        "discharged those dependencies."
    )


def _support_core_cleanup_caption(hidden_nodes: list[ProofNode]) -> str:
    if not hidden_nodes:
        return ""
    if len(hidden_nodes) == 1:
        return (
            f"Final display cleanup hid stale supporting core `{hidden_nodes[0].id}` because its parent node "
            "is now fully verified."
        )
    formatted = ", ".join(f"`{node.id}`" for node in hidden_nodes)
    return (
        f"Final display cleanup hid stale supporting cores {formatted} because their parent nodes are now "
        "fully verified."
    )


def _cleanup_caption(*, hidden_informal_nodes: list[ProofNode], hidden_support_nodes: list[ProofNode]) -> str:
    captions = [
        caption
        for caption in (
            _subsumed_cleanup_caption(hidden_informal_nodes),
            _support_core_cleanup_caption(hidden_support_nodes),
        )
        if caption
    ]
    return " ".join(captions)


def _render_hidden_subsumed_nodes_section(hidden_nodes: list[ProofNode], graph: ProofGraph) -> str:
    if not hidden_nodes:
        return ""
    hidden_sections = "\n".join(_render_node_section(node, graph) for node in hidden_nodes)
    summary = (
        f"Hidden subsumed nodes ({len(hidden_nodes)})"
    )
    return f"""
      <details class="subsumed-nodes">
        <summary>{escape(summary)}</summary>
        <p class="graph-caption">
          These informal nodes are hidden from the final graph display because a formal-verified parent theorem
          appears to have discharged them internally. They remain in the saved artifacts and graph history.
        </p>
        <div class="nodes-grid nodes-grid-subsumed">
          {hidden_sections}
        </div>
      </details>
    """


def _render_hidden_support_nodes_section(hidden_nodes: list[ProofNode], graph: ProofGraph) -> str:
    if not hidden_nodes:
        return ""
    hidden_sections = "\n".join(_render_node_section(node, graph) for node in hidden_nodes)
    summary = f"Hidden stale support cores ({len(hidden_nodes)})"
    return f"""
      <details class="subsumed-nodes">
        <summary>{escape(summary)}</summary>
        <p class="graph-caption">
          These verified support-core nodes are hidden from the final graph display because their parent node
          is now fully verified. They remain in the saved artifacts and graph history.
        </p>
        <div class="nodes-grid nodes-grid-subsumed">
          {hidden_sections}
        </div>
      </details>
    """


def _display_review_obligations(
    obligations: list[ReviewObligation],
    *,
    hidden_node_ids: set[str],
) -> list[ReviewObligation]:
    if not hidden_node_ids:
        return obligations
    return [
        obligation
        for obligation in obligations
        if not any(node_id in hidden_node_ids for node_id in obligation.node_ids)
    ]


def export_report_bundle(
    graph: ProofGraph,
    obligations: list[ReviewObligation],
    *,
    graph_history: list[GraphHistoryEntry] | None = None,
) -> dict:
    """Export a JSON-serializable report bundle."""

    hidden_subsumed_ids = subsumed_informal_node_ids(graph)
    hidden_support_ids = subsumed_support_core_node_ids(graph)
    hidden_node_ids = hidden_subsumed_ids | hidden_support_ids
    display_graph = display_graph_without_hidden_subsumed_nodes(graph, hidden_node_ids)
    display_obligations = _display_review_obligations(obligations, hidden_node_ids=hidden_node_ids)
    bundle = {
        "graph": sanitize_report_payload(display_graph.model_dump(mode="json")),
        "review_obligations": [obligation.model_dump(mode="json") for obligation in display_obligations],
    }
    if graph_history:
        bundle["graph_history"] = sanitize_report_payload(graph_history_bundle(graph_history))
    return bundle


def render_html_report(
    graph: ProofGraph,
    obligations: list[ReviewObligation],
    *,
    graph_history: list[GraphHistoryEntry] | None = None,
) -> str:
    """Render a static HTML report with a pure SVG/CSS graph widget."""

    hidden_subsumed_ids = subsumed_informal_node_ids(graph)
    hidden_support_ids = subsumed_support_core_node_ids(graph)
    hidden_node_ids = hidden_subsumed_ids | hidden_support_ids
    display_graph = display_graph_without_hidden_subsumed_nodes(graph, hidden_node_ids)
    display_obligations = _display_review_obligations(obligations, hidden_node_ids=hidden_node_ids)
    status_counts = Counter(node.status for node in display_graph.nodes)
    checklist_items = "\n".join(_render_checklist_item(obligation, graph) for obligation in display_obligations)
    visible_nodes = [node for node in graph.nodes if node.id not in hidden_node_ids]
    hidden_nodes = [node for node in graph.nodes if node.id in hidden_subsumed_ids]
    hidden_support_nodes = [node for node in graph.nodes if node.id in hidden_support_ids]
    node_sections = "\n".join(_render_node_section(node, graph) for node in visible_nodes)
    hidden_node_section = _render_hidden_subsumed_nodes_section(hidden_nodes, graph)
    hidden_support_section = _render_hidden_support_nodes_section(hidden_support_nodes, graph)
    history_frames = build_graph_history_frames(graph_history or [])
    has_history_timeline = len(history_frames) >= 2
    graph_widget = (
        render_graph_history_widget_with_cleanup(
            graph_history or [],
            cleaned_graph=display_graph,
            cleaned_caption=_cleanup_caption(
                hidden_informal_nodes=hidden_nodes,
                hidden_support_nodes=hidden_support_nodes,
            ),
        )
        if has_history_timeline and hidden_node_ids
        else render_graph_history_widget(graph_history or [])
        if has_history_timeline
        else render_graph_widget(display_graph, widget_key="current")
    )
    interaction_styles = render_interaction_styles(display_graph, display_obligations)
    graph_history_script = render_graph_history_script() if has_history_timeline else ""
    graph_caption_primary = (
        "Use the timeline controls to step through how the proof graph changed over the run. The latest graph is shown by default."
        if has_history_timeline
        else "Click a node to jump to its detail section."
    )
    graph_caption_cleanup = _cleanup_caption(
        hidden_informal_nodes=hidden_nodes,
        hidden_support_nodes=hidden_support_nodes,
    )
    fixed_root_spec_html = ""
    if graph.fixed_root_lean_spec is not None:
        fixed_root_spec_html = f"""
      <div class="note-card">
        <p><strong>Fixed root Lean specification:</strong> this run is anchored to an exact root theorem header.</p>
        <p class="meta">Source: {escape(graph.fixed_root_lean_spec.source or 'manual')} &bull; SHA256: <code>{escape(graph.fixed_root_lean_spec.statement_hash)}</code></p>
        <pre class="lean-code"><code>{escape(graph.fixed_root_lean_spec.lean_statement)}</code></pre>
      </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(graph.theorem_title)} - Formal Islands Report</title>
  <script>
    window.MathJax = {{
      tex: {{
        inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
        displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
        processEscapes: true
      }},
      options: {{
        skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']
      }}
    }};
  </script>
  <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f7f4ec;
      --panel: #fffdf8;
      --ink: #1f1a17;
      --muted: #6a625d;
      --border: #d8cfc2;
      --accent: #8c4f2b;
      --accent-soft: #f5e8d6;
      --highlight: #d97c2b;
      --highlight-soft: #fff1df;
      --preview-soft: #fff5e8;
      --checked: #2f8f5b;
      --checked-soft: #e6f5eb;
      --edge: #b3aa9f;
      --status-informal-stroke: #8c4f2b;
      --status-informal-fill: #fffaf1;
      --status-candidate-stroke: #d97c2b;
      --status-candidate-fill: #fff0da;
      --status-verified-stroke: #2f8f5b;
      --status-verified-fill: #e6f5eb;
      --status-failed-stroke: #b45746;
      --status-failed-fill: #f9e6e1;
      --graph-shell-top: rgba(255, 251, 242, 0.96);
      --graph-shell-bottom: rgba(245, 238, 227, 0.96);
      --checklist-panel: rgba(255, 250, 241, 0.95);
      --target-panel: #fff8ee;
      --code-surface: #f4efe6;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #161311;
        --panel: #211b17;
        --ink: #f3ede5;
        --muted: #c0b2a3;
        --border: #4a3d32;
        --accent: #f0b47b;
        --accent-soft: #3a2b20;
        --highlight: #ffb25c;
        --highlight-soft: #3f2d19;
        --preview-soft: #312419;
        --checked: #65c08a;
        --checked-soft: #213126;
        --edge: #8f8172;
        --status-informal-stroke: #d69760;
        --status-informal-fill: #2e221a;
        --status-candidate-stroke: #f0a64e;
        --status-candidate-fill: #3f2d18;
        --status-verified-stroke: #65c08a;
        --status-verified-fill: #213126;
        --status-failed-stroke: #dd8a79;
        --status-failed-fill: #3b2320;
        --graph-shell-top: rgba(44, 34, 27, 0.98);
        --graph-shell-bottom: rgba(32, 25, 21, 0.98);
        --checklist-panel: rgba(36, 29, 24, 0.98);
        --target-panel: #31261f;
        --code-surface: #2a231e;
      }}
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
    @media (prefers-color-scheme: dark) {{
      body {{
        background: radial-gradient(circle at top, #2a211b, var(--bg));
      }}
    }}
    .fi-attribution {{
      text-align: center;
      font-size: 0.78rem;
      color: var(--muted);
      padding: 0.55rem 1rem;
      border-bottom: 1px solid var(--border);
      margin-bottom: 0;
    }}
    .fi-attribution a {{
      color: var(--muted);
      text-decoration: none;
      font-weight: 500;
    }}
    .fi-attribution a:hover {{
      text-decoration: underline;
    }}
    main.report-root {{
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
    .note-card {{
      border: 1px solid var(--border);
      border-radius: 12px;
      background: var(--target-panel);
      padding: 0.8rem 0.9rem;
      margin-top: 0.9rem;
    }}
    .math-text {{
      white-space: pre-wrap;
      line-height: 1.45;
      max-width: 100%;
      min-width: 0;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    .math-text mjx-container {{
      max-width: 100%;
    }}
    .math-text mjx-container[display="true"] {{
      margin: 0.45rem 0 !important;
      overflow-x: auto;
      overflow-y: hidden;
    }}
    .math-text mjx-container[jax="CHTML"][display="true"] {{
      padding: 0 !important;
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
      margin-top: 0.85rem;
      border: 1px solid var(--border);
      border-radius: 16px;
      background: linear-gradient(180deg, var(--graph-shell-top), var(--graph-shell-bottom));
      padding: 0.5rem 0.75rem;
      overflow-x: auto;
      overflow-y: visible;
    }}
    .graph-frame {{
      width: min(100%, 720px);
      margin: 0 auto;
    }}
    .graph-widget {{
      display: block;
      width: 100%;
      height: auto;
      min-width: 240px;
      overflow: visible;
    }}
    .graph-edge {{
      stroke: var(--edge);
      stroke-width: 2.8;
      fill: none;
      transition: stroke 140ms ease, stroke-width 140ms ease;
    }}
    .graph-edge.edge-refinement {{
      stroke-dasharray: 7 5;
      opacity: 0.66;
    }}
    .graph-node-link {{
      cursor: pointer;
      text-decoration: none;
    }}
    .graph-node-link.graph-node-dimmed {{
      opacity: 0.22;
    }}
    .graph-node-box {{
      fill: var(--status-informal-fill);
      stroke: var(--status-informal-stroke);
      stroke-width: 2.25;
      transition: fill 140ms ease, stroke 140ms ease, stroke-width 140ms ease, filter 140ms ease;
      filter: drop-shadow(0 6px 14px rgba(73, 48, 24, 0.08));
    }}
    .graph-node-box.status-informal {{
      fill: var(--status-informal-fill);
      stroke: var(--status-informal-stroke);
      stroke-dasharray: 5 4;
    }}
    .graph-node-box.status-candidate-formal {{
      fill: var(--status-candidate-fill);
      stroke: var(--status-candidate-stroke);
      stroke-dasharray: 8 4;
    }}
    .graph-node-box.status-formal-verified {{
      fill: var(--status-verified-fill);
      stroke: var(--status-verified-stroke);
      stroke-dasharray: none;
    }}
    .graph-node-box.status-formal-failed {{
      fill: var(--status-failed-fill);
      stroke: var(--status-failed-stroke);
      stroke-dasharray: none;
    }}
    .graph-node-fo-title {{
      font-size: 9px;
      font-weight: 700;
      text-align: center;
      color: var(--ink);
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
      pointer-events: none;
      overflow: visible;
      word-break: break-word;
      overflow-wrap: anywhere;
      line-height: 1.3;
    }}
    .graph-node-id {{
      fill: var(--muted);
      font-size: 9.5px;
      text-anchor: middle;
      dominant-baseline: middle;
      pointer-events: none;
    }}
    .graph-node-badge {{
      stroke: rgba(31, 26, 23, 0.18);
      stroke-width: 1.2;
      pointer-events: none;
    }}
    .graph-node-badge.status-informal {{
      fill: var(--status-informal-stroke);
    }}
    .graph-node-badge.status-candidate-formal {{
      fill: var(--status-candidate-stroke);
    }}
    .graph-node-badge.status-formal-verified {{
      fill: var(--status-verified-stroke);
    }}
    .graph-node-badge.status-formal-failed {{
      fill: var(--status-failed-stroke);
    }}
    .graph-caption {{
      margin-top: 0.7rem;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .graph-history-controls {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      flex-wrap: wrap;
      margin-bottom: 0.65rem;
    }}
    .graph-history-buttons {{
      display: flex;
      align-items: center;
      gap: 0.45rem;
      flex-wrap: wrap;
    }}
    .graph-history-button {{
      border: 1px solid var(--border);
      background: var(--panel);
      color: var(--ink);
      border-radius: 10px;
      min-width: 2.4rem;
      padding: 0.36rem 0.7rem;
      font: inherit;
      cursor: pointer;
      transition: border-color 140ms ease, background 140ms ease, color 140ms ease;
    }}
    .graph-history-button:hover:not(:disabled) {{
      border-color: var(--highlight);
      background: var(--highlight-soft);
    }}
    .graph-history-button:disabled {{
      opacity: 0.42;
      cursor: default;
    }}
    .graph-history-status {{
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .graph-history-toggle {{
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .graph-history-caption {{
      margin-top: 0;
      margin-bottom: 0.65rem;
      color: var(--ink);
      font-size: 0.96rem;
      line-height: 1.45;
    }}
    .graph-history-meta {{
      color: var(--muted);
      font-size: 0.9rem;
      margin-top: 0.25rem;
    }}
    .graph-history-frame[hidden] {{
      display: none;
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
      background: var(--checklist-panel);
      transition: border-color 140ms ease, background 140ms ease;
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
    .checklist-item,
    .checklist-label > span,
    .nodes-grid,
    .node-card {{
      min-width: 0;
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
    .node-navigation {{
      display: grid;
      gap: 0.75rem;
      margin-bottom: 1rem;
    }}
    .node-search {{
      display: grid;
      gap: 0.35rem;
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .node-search input {{
      width: min(100%, 28rem);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 0.6rem 0.75rem;
      font: inherit;
      color: var(--ink);
      background: var(--panel);
    }}
    .node-filter-pills {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.55rem;
      align-items: center;
    }}
    .node-filter-pill {{
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 0.32rem 0.65rem;
      font-size: 0.9rem;
      background: var(--panel);
      color: var(--ink);
    }}
    .node-filter-reset {{
      min-width: auto;
    }}
    .node-card {{
      scroll-margin-top: 1rem;
      transition: border-color 140ms ease, box-shadow 140ms ease, background 140ms ease;
    }}
    .node-card:target {{
      border-color: var(--highlight);
      box-shadow: 0 0 0 3px rgba(217, 124, 43, 0.18), 0 12px 30px rgba(60, 40, 20, 0.06);
      background: var(--target-panel);
    }}
    pre {{
      overflow-x: auto;
      max-width: 100%;
      padding: 0.75rem;
      background: var(--code-surface);
      border-radius: 8px;
      border: 1px solid var(--border);
      margin: 0.6rem 0;
    }}
    code {{
      font-family: "SFMono-Regular", Menlo, monospace;
    }}
    code.inline-code {{
      background: var(--code-surface);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 0.1rem 0.35rem;
      font-size: 0.95em;
      white-space: break-spaces;
    }}
    .statement-code {{
      margin-top: 0.35rem;
    }}
    .statement-code pre,
    .checklist-code pre {{
      margin-top: 0.35rem;
      background: var(--code-surface);
    }}
    .lean-code {{
      color: #2a241f;
    }}
    .lean-code .tok-keyword {{
      color: #8b2f18;
      font-weight: 700;
    }}
    .lean-code .tok-tactic {{
      color: #9d5d17;
      font-weight: 600;
    }}
    .lean-code .tok-type {{
      color: #245f7a;
    }}
    .lean-code .tok-number {{
      color: #7442a8;
    }}
    .lean-code .tok-comment {{
      color: #6e7a60;
      font-style: italic;
    }}
    .lean-code .tok-string {{
      color: #2d7f4d;
    }}
    @media (prefers-color-scheme: dark) {{
      .lean-code {{
        color: #f2e8dc;
      }}
      .lean-code .tok-keyword {{
        color: #f08f63;
      }}
      .lean-code .tok-tactic {{
        color: #f4b35d;
      }}
      .lean-code .tok-type {{
        color: #73c7ff;
      }}
      .lean-code .tok-number {{
        color: #be8cff;
      }}
      .lean-code .tok-comment {{
        color: #97b18b;
      }}
      .lean-code .tok-string {{
        color: #67d59a;
      }}
    }}
    {interaction_styles}
    @media (max-width: 720px) {{
      main.report-root {{
        padding-inline: 0.8rem;
      }}
    }}
  </style>
</head>
<body>
  <div class="fi-attribution">Created by <a href="https://aditya-ramabadran.github.io/formal-islands/" target="_blank">Formal Islands</a> &bull; <a href="https://github.com/aditya-ramabadran/formal-islands" target="_blank">GitHub</a></div>
  <main class="report-root">
    <section>
      <h1>{escape(graph.theorem_title)}</h1>
      {render_math_text(graph.theorem_statement)}
      <p class="meta">Root node: {escape(graph.root_node_id)}</p>
      {fixed_root_spec_html}
    </section>
    <section>
      <h2>Graph Summary</h2>
      <p>
        <span class="pill">Nodes: {len(display_graph.nodes)}</span>
        <span class="pill">Edges: {len(display_graph.edges)}</span>
        <span class="pill">Informal: {status_counts.get("informal", 0)}</span>
        <span class="pill">Candidates: {status_counts.get("candidate_formal", 0)}</span>
        <span class="pill">Verified: {status_counts.get("formal_verified", 0)}</span>
        <span class="pill">Failed: {status_counts.get("formal_failed", 0)}</span>
      </p>
      <div class="graph-shell">
        {graph_widget}
      </div>
      <p class="graph-caption">
        {graph_caption_primary}
      </p>
      <p class="graph-caption">
        Informal nodes use dashed brown outlines. Candidate formal nodes use dashed gold outlines, verified formal nodes use green, and failed formal nodes use red.
      </p>
      <p class="graph-caption">
        All arrows point from a claim to one of its dependencies.
      </p>
      <p class="graph-caption">
        Dashed gray arrows mark refinement edges: they are still dependency edges, but they indicate that a narrower claim was carved out from a broader proof step.
      </p>
      {f'<p class="graph-caption">{render_inline_code_html(graph_caption_cleanup)}</p>' if graph_caption_cleanup else ''}
    </section>
    <section>
      <h2>Review Checklist</h2>
      <p class="graph-caption">The following are sufficient items for a human to check to verify correctness of the proof. Hovering or checking review items highlights related nodes and edges.</p>
      <ul class="checklist">
        {checklist_items}
      </ul>
    </section>
    <section>
      <h2>Nodes</h2>
      <div class="node-navigation">
        <label class="node-search">
          <span>Filter nodes</span>
          <input type="search" placeholder="Search by node id or title" data-node-search />
        </label>
        <div class="node-filter-pills">
          <label class="node-filter-pill"><input type="checkbox" data-node-filter-status="informal" checked /> Informal</label>
          <label class="node-filter-pill"><input type="checkbox" data-node-filter-status="candidate_formal" checked /> Candidate</label>
          <label class="node-filter-pill"><input type="checkbox" data-node-filter-status="formal_verified" checked /> Verified</label>
          <label class="node-filter-pill"><input type="checkbox" data-node-filter-status="formal_failed" checked /> Failed</label>
          <button type="button" class="graph-history-button node-filter-reset" data-node-filter-reset>Reset</button>
        </div>
      </div>
      <div class="nodes-grid" data-node-grid>
        {node_sections}
      </div>
      {hidden_node_section}
      {hidden_support_section}
    </section>
  </main>
  {graph_history_script}
  {_render_node_navigation_script()}
</body>
</html>
"""


def _render_checklist_item(obligation: ReviewObligation, graph: ProofGraph) -> str:
    control_id = obligation_control_id(obligation.id)
    node_links = ", ".join(
        (
            f'<a class="node-jump" href="#node-{escape(node_id)}">{escape(node_id)}</a>'
        )
        for node_id in obligation.node_ids
    )
    code_block = _render_obligation_code_block(obligation, graph)
    return f"""
    <li class="checklist-item obligation-{slugify(obligation.id)}" data-obligation-id="{escape(obligation.id)}">
      <label class="checklist-label" for="{control_id}">
        <input id="{control_id}" type="checkbox" />
        <span>
          <span class="checklist-kind">{escape(obligation.kind)}</span><br />
          {render_inline_code_html(obligation.text)}
          {code_block}
          <div class="checklist-nodes">Related nodes: {node_links}</div>
        </span>
      </label>
    </li>
    """


def _render_node_section(node: ProofNode, graph: ProofGraph) -> str:
    display_label = (
        f"<p class=\"meta\">Display label: {escape(node.display_label)}</p>"
        if node.display_label
        else ""
    )
    candidate_block = ""
    if node.formalization_priority is not None and node.formalization_rationale is not None:
        followup_sentence = ""
        if node.status == "candidate_formal" and node.last_formalization_outcome is not None:
            followup_sentence = " This node remains eligible for a future broader formalization attempt."
        candidate_block = (
            "<p class=\"meta\">"
            f"Formalization priority: {node.formalization_priority}. "
            f"Rationale: {render_inline_code_html(node.formalization_rationale)}"
            f"{escape(followup_sentence)}"
            "</p>"
        )
    attempt_block = _render_formalization_attempt_block(node)

    # Build dependency-neighbor links.
    # Edges go source → target where source depends on target.
    # "Used by (parent nodes)" are incoming edges (claims that depend on this node).
    # "Depends on (child nodes)" are outgoing edges (claims this node relies on).
    dependent_ids = [e.source_id for e in graph.edges if e.target_id == node.id]
    dependency_ids = [e.target_id for e in graph.edges if e.source_id == node.id]
    node_id_set = {n.id for n in graph.nodes}

    def _nlink(nid: str) -> str:
        if nid in node_id_set:
            return f'<a class="node-jump" href="#node-{escape(nid)}">{escape(nid)}</a>'
        return escape(nid)

    neighbor_parts: list[str] = []
    if dependent_ids:
        neighbor_parts.append(
            "Used by (parent nodes): " + ", ".join(_nlink(nid) for nid in dependent_ids)
        )
    if dependency_ids:
        neighbor_parts.append(
            "Depends on (child nodes): " + ", ".join(_nlink(nid) for nid in dependency_ids)
        )
    neighbor_block = (
        '<p class="meta">' + " &nbsp;|&nbsp; ".join(neighbor_parts) + "</p>"
        if neighbor_parts
        else ""
    )

    formal_block = ""
    if node.formal_artifact is not None:
        verification = node.formal_artifact.verification
        result_kind, reason = parse_faithfulness_notes(node.formal_artifact.faithfulness_notes)
        coverage_label = render_faithfulness_label(
            result_kind=result_kind,
            classification=str(node.formal_artifact.faithfulness_classification),
        )
        coverage_note = f"<p><strong>Coverage:</strong> {escape(coverage_label)}</p>"
        if reason:
            coverage_note += f"<p class=\"meta\">{render_inline_code_html(reason)}</p>"
        elif node.formal_artifact.faithfulness_notes:
            coverage_note += f"<p class=\"meta\">{render_inline_code_html(node.formal_artifact.faithfulness_notes)}</p>"
        formal_block = f"""
        <h4>Formal Artifact</h4>
        <p><strong>Lean theorem name:</strong> {escape(node.formal_artifact.lean_theorem_name)}</p>
        {coverage_note}
        <div class="statement-code">
          <strong>Lean statement:</strong>
          {render_lean_code_block(node.formal_artifact.lean_statement)}
        </div>
        <p><strong>Verification status:</strong> {escape(verification.status)}</p>
        <p><strong>Verification command:</strong> <code>{escape(display_verification_command(verification))}</code></p>
        <details>
          <summary>Lean code</summary>
          {render_lean_code_block(node.formal_artifact.lean_code)}
        </details>
        <details>
          <summary>Verification logs</summary>
          <pre><code>stdout:
{escape(verification.stdout)}

stderr:
{escape(verification.stderr)}</code></pre>
          </details>
        """

    remaining_burden_block = _render_remaining_proof_burden_section(node, graph)

    node_key = node_class(node.id)
    return f"""
    <article class="node-card {node_key}" id="node-{escape(node.id)}" data-node-id="{escape(node.id)}" data-node-title="{escape(node.title)}" data-node-status="{escape(str(node.status))}">
      <h3>{escape(node.title)}</h3>
      <p class="meta">Node id: {escape(node.id)} | Status: {escape(node.status)}</p>
      {neighbor_block}
      {display_label}
      {candidate_block}
      {attempt_block}
      <p><strong>Informal statement:</strong></p>
      {render_math_text(node.informal_statement)}
      <p><strong>Informal proof:</strong></p>
      {render_math_text(node.informal_proof_text)}
      {remaining_burden_block}
      {formal_block}
    </article>
    """


def _render_formalization_attempt_block(node: ProofNode) -> str:
    if node.last_formalization_outcome is None:
        return ""

    labels = {
        "verified_full_node": "verified the full parent node",
        "produced_supporting_core": "produced a verified supporting core",
        "failed": "failed to produce a verified formalization",
    }
    outcome_label = labels.get(str(node.last_formalization_outcome), str(node.last_formalization_outcome))
    count_text = ""
    if node.last_formalization_attempt_count is not None:
        plural = "s" if node.last_formalization_attempt_count != 1 else ""
        count_text = (
            f" after {node.last_formalization_attempt_count} Lean verification attempt{plural}"
        )
    failure_kind_text = ""
    if node.last_formalization_failure_kind is not None:
        failure_kind_text = (
            f" Failure kind: {render_inline_code_html(str(node.last_formalization_failure_kind))}."
        )
    note_text = (
        f" {render_inline_code_html(node.last_formalization_note)}"
        if node.last_formalization_note
        else ""
    )
    return (
        "<p class=\"meta\">"
        f"Most recent formalization episode: {escape(outcome_label)}{count_text}.{failure_kind_text}{note_text}"
        "</p>"
    )


def _render_remaining_proof_burden_section(node: ProofNode, graph: ProofGraph) -> str:
    if not node.remaining_proof_burden:
        return ""

    verified_child_ids = [
        edge.target_id
        for edge in graph.edges
        if edge.source_id == node.id
        and any(
            child.id == edge.target_id
            and child.status == "formal_verified"
            and child.formal_artifact is not None
            for child in graph.nodes
        )
    ]
    if not verified_child_ids:
        return ""

    node_id_set = {candidate.id for candidate in graph.nodes}

    def _link(node_id: str) -> str:
        if node_id in node_id_set:
            return f'<a class="node-jump" href="#node-{escape(node_id)}">{escape(node_id)}</a>'
        return escape(node_id)

    child_links = ", ".join(_link(node_id) for node_id in verified_child_ids)
    return f"""
      <p><strong>Remaining proof burden (assuming results of {child_links}):</strong></p>
      {render_math_text(node.remaining_proof_burden)}
    """


def _render_obligation_code_block(obligation: ReviewObligation, graph: ProofGraph) -> str:
    if obligation.kind != "formal_semantic_match_check" or len(obligation.node_ids) != 1:
        return ""
    node = next((candidate for candidate in graph.nodes if candidate.id == obligation.node_ids[0]), None)
    if node is None or node.formal_artifact is None:
        return ""
    return f'<div class="checklist-code">{render_lean_code_block(node.formal_artifact.lean_statement)}</div>'


def _render_node_navigation_script() -> str:
    return """
  <script>
    document.querySelectorAll('.report-root').forEach((root) => {
      const searchInput = root.querySelector('[data-node-search]');
      const resetButton = root.querySelector('[data-node-filter-reset]');
      const statusInputs = Array.from(root.querySelectorAll('[data-node-filter-status]'));
      const cards = Array.from(root.querySelectorAll('[data-node-id]'));
      const graphNodes = Array.from(root.querySelectorAll('[data-graph-node-id]'));
      if (!searchInput || cards.length === 0) return;

      const update = () => {
        const query = searchInput.value.trim().toLowerCase();
        const allowedStatuses = new Set(
          statusInputs.filter((input) => input.checked).map((input) => input.dataset.nodeFilterStatus)
        );
        cards.forEach((card) => {
          const nodeId = (card.dataset.nodeId || '').toLowerCase();
          const title = (card.dataset.nodeTitle || '').toLowerCase();
          const status = card.dataset.nodeStatus || '';
          const matchesQuery = query === '' || nodeId.includes(query) || title.includes(query);
          const matchesStatus = allowedStatuses.has(status);
          const visible = matchesQuery && matchesStatus;
          card.hidden = !visible;
        });
        graphNodes.forEach((node) => {
          const nodeId = node.dataset.graphNodeId || '';
          const linkedCard = root.querySelector(`[data-node-id="${CSS.escape(nodeId)}"]`);
          node.classList.toggle('graph-node-dimmed', !!linkedCard && linkedCard.hidden);
        });
      };

      statusInputs.forEach((input) => input.addEventListener('change', update));
      searchInput.addEventListener('input', update);
      resetButton?.addEventListener('click', () => {
        searchInput.value = '';
        statusInputs.forEach((input) => { input.checked = true; });
        update();
      });
      update();
    });
  </script>
    """
