"""Static HTML and JSON report generation."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict, deque
from html import escape
from pathlib import Path

from formal_islands.formalization.pipeline import parse_faithfulness_notes
from formal_islands.models import ProofEdge, ProofGraph, ProofNode, ReviewObligation


NODE_WIDTH = 132
NODE_HEIGHT = 66
ROW_GAP = 52
COL_GAP = 36
MARGIN_X = 24
MARGIN_Y = 20
REFINEMENT_EDGE_LABELS = {"refined_from"}


def export_report_bundle(graph: ProofGraph, obligations: list[ReviewObligation]) -> dict:
    """Export a JSON-serializable report bundle."""

    return {
        "graph": _sanitize_report_payload(graph.model_dump(mode="json")),
        "review_obligations": [obligation.model_dump(mode="json") for obligation in obligations],
    }


def render_html_report(graph: ProofGraph, obligations: list[ReviewObligation]) -> str:
    """Render a static HTML report with a pure SVG/CSS graph widget."""

    status_counts = Counter(node.status for node in graph.nodes)
    checklist_items = "\n".join(_render_checklist_item(obligation, graph) for obligation in obligations)
    node_sections = "\n".join(_render_node_section(node, graph) for node in graph.nodes)
    graph_widget = _render_graph_widget(graph)
    interaction_styles = _render_interaction_styles(graph, obligations)

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
      fill: var(--status-informal-fill);
      stroke: var(--status-informal-stroke);
      stroke-dasharray: 5 4;
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
      fill: var(--status-informal-stroke);
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
  <div class="fi-attribution">Created by <a href="https://github.com/aditya-ramabadran/formal-islands" target="_blank">Formal Islands</a></div>
  <main class="report-root">
    <section>
      <h1>{escape(graph.theorem_title)}</h1>
      {_render_math_text(graph.theorem_statement)}
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
        Click a node to jump to its detail section.
      </p>
      <p class="graph-caption">
        Nodes without attached Lean artifacts use dashed amber outlines. Verified formal nodes use green, and failed formal nodes use red.
      </p>
      <p class="graph-caption">
        All arrows point from a claim to one of its dependencies.
      </p>
      <p class="graph-caption">
        Dashed gray arrows mark refinement edges: they are still dependency edges, but they indicate that a narrower claim was carved out from a broader proof step.
      </p>
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
      <div class="nodes-grid">
        {node_sections}
      </div>
    </section>
  </main>
</body>
</html>
"""


def _render_checklist_item(obligation: ReviewObligation, graph: ProofGraph) -> str:
    control_id = _obligation_control_id(obligation.id)
    node_links = ", ".join(
        (
            f'<a class="node-jump" href="#node-{escape(node_id)}">{escape(node_id)}</a>'
        )
        for node_id in obligation.node_ids
    )
    code_block = _render_obligation_code_block(obligation, graph)
    return f"""
    <li class="checklist-item obligation-{_slugify(obligation.id)}" data-obligation-id="{escape(obligation.id)}">
      <label class="checklist-label" for="{control_id}">
        <input id="{control_id}" type="checkbox" />
        <span>
          <span class="checklist-kind">{escape(obligation.kind)}</span><br />
          {_render_inline_code_html(obligation.text)}
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
            f"Rationale: {_render_inline_code_html(node.formalization_rationale)}"
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
        coverage_label = _render_faithfulness_label(
            result_kind=result_kind,
            classification=str(node.formal_artifact.faithfulness_classification),
        )
        coverage_note = f"<p><strong>Coverage:</strong> {escape(coverage_label)}</p>"
        if reason:
            coverage_note += f"<p class=\"meta\">{_render_inline_code_html(reason)}</p>"
        elif node.formal_artifact.faithfulness_notes:
            coverage_note += f"<p class=\"meta\">{_render_inline_code_html(node.formal_artifact.faithfulness_notes)}</p>"
        formal_block = f"""
        <h4>Formal Artifact</h4>
        <p><strong>Lean theorem name:</strong> {escape(node.formal_artifact.lean_theorem_name)}</p>
        {coverage_note}
        <div class="statement-code">
          <strong>Lean statement:</strong>
          {_render_lean_code_block(node.formal_artifact.lean_statement)}
        </div>
        <p><strong>Verification status:</strong> {escape(verification.status)}</p>
        <p><strong>Verification command:</strong> <code>{escape(_display_verification_command(verification))}</code></p>
        <details>
          <summary>Lean code</summary>
          {_render_lean_code_block(node.formal_artifact.lean_code)}
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

    node_key = _node_class(node.id)
    return f"""
    <article class="node-card {node_key}" id="node-{escape(node.id)}" data-node-id="{escape(node.id)}">
      <h3>{escape(node.title)}</h3>
      <p class="meta">Node id: {escape(node.id)} | Status: {escape(node.status)}</p>
      {neighbor_block}
      {display_label}
      {candidate_block}
      {attempt_block}
      <p><strong>Informal statement:</strong></p>
      {_render_math_text(node.informal_statement)}
      <p><strong>Informal proof:</strong></p>
      {_render_math_text(node.informal_proof_text)}
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
    note_text = (
        f" {_render_inline_code_html(node.last_formalization_note)}"
        if node.last_formalization_note
        else ""
    )
    return (
        "<p class=\"meta\">"
        f"Most recent formalization episode: {escape(outcome_label)}{count_text}.{note_text}"
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
      {_render_math_text(node.remaining_proof_burden)}
    """


def _display_verification_command(verification: object) -> str:
    """Render verification commands using repo-relative paths when possible."""

    command = getattr(verification, "command", "")
    artifact_path = getattr(verification, "artifact_path", None)
    if not isinstance(command, str) or not command:
        return ""
    if not isinstance(artifact_path, str) or not artifact_path:
        return command
    repo_relative = _repo_relative_artifact_path(artifact_path)
    if repo_relative and artifact_path in command:
        return command.replace(artifact_path, repo_relative)
    return command


def _repo_relative_artifact_path(artifact_path: str) -> str | None:
    """Map an absolute artifact path into a repo-relative path when possible."""

    path = Path(artifact_path)
    parts = path.parts
    if "lean_project" not in parts:
        return None
    anchor_index = parts.index("lean_project")
    return Path(*parts[anchor_index:]).as_posix()


def _sanitize_report_payload(value: object) -> object:
    """Recursively scrub public report payloads before serialization."""

    if isinstance(value, dict):
        sanitized = {key: _sanitize_report_payload(inner_value) for key, inner_value in value.items()}
        command = sanitized.get("command")
        artifact_path = sanitized.get("artifact_path")
        if isinstance(command, str) and isinstance(artifact_path, str):
            repo_relative = _repo_relative_artifact_path(artifact_path)
            if repo_relative:
                sanitized["artifact_path"] = repo_relative
            if repo_relative and artifact_path in command:
                sanitized["command"] = command.replace(artifact_path, repo_relative)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_report_payload(item) for item in value]
    return value


def _render_math_text(text: str) -> str:
    compacted = _compact_report_text(text)
    return f'<div class="math-text">{_render_inline_code_html(compacted)}</div>'


def _render_faithfulness_label(*, result_kind: str | None, classification: str) -> str:
    if result_kind is None:
        return classification
    labels = {
        "full_match": "full match",
        "faithful_core": "faithful core",
        "downstream_consequence": "downstream consequence",
        "dimensional_analogue": "dimensional analogue",
        "helper_shard": "helper shard",
    }
    return labels.get(result_kind, result_kind.replace("_", " "))


def _render_inline_code_html(text: str) -> str:
    # Pass 1: split on backtick spans first (they may contain * characters).
    parts = re.split(r"(`[^`]+`)", text)
    rendered: list[str] = []
    for part in parts:
        if len(part) >= 2 and part.startswith("`") and part.endswith("`"):
            rendered.append(f'<code class="inline-code">{escape(part[1:-1])}</code>')
        else:
            # Pass 2: render **bold** and *italic* as <em> within plain-text segments.
            # Bold (double asterisk) must be matched before single asterisk.
            segments = re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", part)
            for seg in segments:
                if seg.startswith("**") and seg.endswith("**") and len(seg) >= 5:
                    rendered.append(f"<em>{escape(seg[2:-2])}</em>")
                elif seg.startswith("*") and seg.endswith("*") and len(seg) >= 3:
                    rendered.append(f"<em>{escape(seg[1:-1])}</em>")
                else:
                    rendered.append(escape(seg))
    return "".join(rendered)


def _render_obligation_code_block(obligation: ReviewObligation, graph: ProofGraph) -> str:
    if obligation.kind != "formal_semantic_match_check" or len(obligation.node_ids) != 1:
        return ""
    node = next((candidate for candidate in graph.nodes if candidate.id == obligation.node_ids[0]), None)
    if node is None or node.formal_artifact is None:
        return ""
    return f'<div class="checklist-code">{_render_lean_code_block(node.formal_artifact.lean_statement)}</div>'


def _compact_report_text(text: str) -> str:
    stripped = text.strip()
    stripped = re.sub(r"\n{3,}", "\n\n", stripped)
    stripped = re.sub(r"[ \t]+\n", "\n", stripped)
    stripped = re.sub(r"\n{2,}(\\\[)", r"\n\1", stripped)
    stripped = re.sub(r"(\\\])\n{2,}", r"\1\n", stripped)
    return stripped


def _render_lean_code_block(code: str) -> str:
    return f'<pre><code class="language-lean lean-code">{_highlight_lean_html(code)}</code></pre>'


def _highlight_lean_html(code: str) -> str:
    lines = []
    for raw_line in code.splitlines():
        comment_index = raw_line.find("--")
        if comment_index != -1:
            code_part = raw_line[:comment_index]
            comment_part = raw_line[comment_index:]
        else:
            code_part = raw_line
            comment_part = ""
        highlighted = _highlight_lean_code_part(code_part)
        if comment_part:
            highlighted += f'<span class="tok-comment">{escape(comment_part)}</span>'
        lines.append(highlighted)
    return "\n".join(lines)


def _highlight_lean_code_part(text: str) -> str:
    keywords = {
        "import",
        "open",
        "namespace",
        "section",
        "end",
        "variable",
        "variables",
        "theorem",
        "lemma",
        "example",
        "def",
        "axiom",
        "where",
        "structure",
        "class",
        "instance",
        "inductive",
        "deriving",
        "match",
        "with",
        "let",
        "in",
        "if",
        "then",
        "else",
        "fun",
        "forall",
    }
    tactics = {
        "by",
        "intro",
        "intros",
        "rintro",
        "apply",
        "exact",
        "show",
        "have",
        "simpa",
        "simp",
        "rw",
        "calc",
        "constructor",
        "cases",
        "refine",
        "obtain",
        "aesop",
        "omega",
        "ring",
        "linarith",
        "norm_num",
    }
    builtin_types = {"ℝ", "ℕ", "ℤ", "Prop", "Type", "Type*", "Bool"}
    token_pattern = re.compile(
        r"(\"(?:[^\"\\\\]|\\\\.)*\")|(\b[A-Za-z_][A-Za-z0-9_']*\b)|(ℝ|ℕ|ℤ|Prop|Type\*?|Bool)|(\d+(?:\.\d+)?)"
    )

    parts: list[str] = []
    last_index = 0
    for match in token_pattern.finditer(text):
        start, end = match.span()
        if start > last_index:
            parts.append(escape(text[last_index:start]))
        string_token, word_token, type_token, number_token = match.groups()
        if string_token is not None:
            parts.append(f'<span class="tok-string">{escape(string_token)}</span>')
        elif type_token is not None or (word_token is not None and word_token in builtin_types):
            token = type_token or word_token
            parts.append(f'<span class="tok-type">{escape(token)}</span>')
        elif word_token is not None:
            if word_token in keywords:
                parts.append(f'<span class="tok-keyword">{escape(word_token)}</span>')
            elif word_token in tactics:
                parts.append(f'<span class="tok-tactic">{escape(word_token)}</span>')
            else:
                parts.append(escape(word_token))
        elif number_token is not None:
            parts.append(f'<span class="tok-number">{escape(number_token)}</span>')
        last_index = end
    if last_index < len(text):
        parts.append(escape(text[last_index:]))
    return "".join(parts)


def _render_graph_widget(graph: ProofGraph) -> str:
    layout = _compute_graph_layout(graph)
    width = layout["width"]
    height = layout["height"]
    edges_svg = "\n".join(_render_edge(edge, layout) for edge in graph.edges)
    nodes_svg = "\n".join(_render_node(node, layout) for node in graph.nodes)
    return f"""
    <div class="graph-frame">
      <svg class="graph-widget" viewBox="0 0 {width} {height}" width="{width}" height="{height}" preserveAspectRatio="xMidYMin meet" role="img" aria-label="Proof graph">
        <defs>
          <marker id="graph-arrow" markerWidth="8" markerHeight="8" refX="6.4" refY="2.5" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L0,5 L7,2.5 z" fill="#b3aa9f"></path>
          </marker>
        </defs>
        <g>{edges_svg}</g>
        <g>{nodes_svg}</g>
      </svg>
    </div>
    """


def _render_edge(edge: ProofEdge, layout: dict) -> str:
    x1, y1 = layout["centers"][edge.source_id]
    x2, y2 = layout["centers"][edge.target_id]
    # Source is the theorem/claim and target is one of its dependencies.
    # Connect from the bottom of the source node to the top of the target node.
    start_y = y1 + NODE_HEIGHT / 2 - 3
    end_y = y2 - NODE_HEIGHT / 2 + 3
    dx = x2 - x1
    span = end_y - start_y
    # Vertical exit tangent from source (ctrl1 shares x with source).
    ctrl1_x, ctrl1_y = x1, start_y + span * 0.4
    # Natural angled entry tangent at target: ctrl2 is placed along the line
    # from source to target, so the arrowhead follows the edge direction instead
    # of always pointing straight down.
    ctrl2_x = x2 - dx * 0.3
    ctrl2_y = end_y - span * 0.3
    edge_class = _edge_class(edge.source_id, edge.target_id)
    refinement_class = " edge-refinement" if edge.label in REFINEMENT_EDGE_LABELS else ""
    return (
        f'<path class="graph-edge {edge_class}{refinement_class}" '
        f'd="M {x1:.1f} {start_y:.1f} C {ctrl1_x:.1f} {ctrl1_y:.1f}, '
        f'{ctrl2_x:.1f} {ctrl2_y:.1f}, {x2:.1f} {end_y:.1f}" '
        f'marker-end="url(#graph-arrow)"></path>'
    )


def _render_node(node: ProofNode, layout: dict) -> str:
    x, y = layout["positions"][node.id]
    cx = x + NODE_WIDTH / 2
    node_key = _node_class(node.id)
    visual_status = _graph_visual_status(node)
    status_class = _status_class(visual_status)
    rx = _node_corner_radius(visual_status)
    raw_label = node.display_label or node.title
    fo_x = x + 4
    fo_y = y + 4
    fo_w = NODE_WIDTH - 8
    fo_h = NODE_HEIGHT - 22
    return f"""
    <a class="graph-node-link {node_key} {status_class}" href="#node-{escape(node.id)}">
      <rect class="graph-node-box {status_class}" x="{x}" y="{y}" rx="{rx}" ry="{rx}" width="{NODE_WIDTH}" height="{NODE_HEIGHT}"></rect>
      <circle class="graph-node-badge {status_class}" cx="{x + NODE_WIDTH - 14}" cy="{y + 12}" r="4.6"></circle>
      <foreignObject x="{fo_x}" y="{fo_y}" width="{fo_w}" height="{fo_h}" overflow="visible">
        <div xmlns="http://www.w3.org/1999/xhtml" class="graph-node-fo-title">{escape(raw_label)}</div>
      </foreignObject>
      <text class="graph-node-id" x="{cx}" y="{y + NODE_HEIGHT - 18}">{escape(node.id)}</text>
    </a>
    """


def _compute_graph_layout(graph: ProofGraph) -> dict:
    # Edges go source → target where source depends on target.
    # To assign depths, we traverse forward from the root: each outgoing edge
    # reaches a dependency that should appear lower in the layout.
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


def _wrap_title_lines(text: str, max_chars: int = 16, max_lines: int = 2) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) == max_lines - 1:
            break
    remaining_words = words[len(" ".join(lines + [current]).split()):]
    if lines and len(lines) == max_lines - 1:
        rest = " ".join([current] + remaining_words)
        current = _ellipsize(rest, max_chars)
        return lines + [current]

    lines.append(current)
    if len(lines) < max_lines and remaining_words:
        lines.append(_ellipsize(" ".join(remaining_words), max_chars))
    elif len(lines) > max_lines:
        lines = lines[:max_lines]
    return lines[:max_lines]


def _ellipsize(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _title_y_positions(y: float, line_count: int) -> list[float]:
    if line_count <= 1:
        return [y + 27]
    return [y + 22, y + 34]


def _render_interaction_styles(graph: ProofGraph, obligations: list[ReviewObligation]) -> str:
    node_edge_map = _incident_edge_classes(graph)
    blocks: list[str] = []

    for node in graph.nodes:
        node_key = _node_class(node.id)
        blocks.append(
            f"""
            .report-root:has(a.{node_key}:hover) .{node_key}.node-card,
            .report-root:has(#{escape('node-' + node.id)}:target) a.{node_key} .graph-node-box {{
              border-color: var(--highlight);
              stroke: var(--highlight);
              background: var(--highlight-soft);
              fill: var(--highlight-soft);
            }}
            .report-root:has(a.{node_key}:hover) .{node_key}.node-card,
            .report-root:has(#{escape('node-' + node.id)}:target).report-root .{node_key}.node-card {{
              border-color: var(--highlight);
              box-shadow: 0 0 0 3px rgba(217, 124, 43, 0.18), 0 12px 30px rgba(60, 40, 20, 0.06);
              background: var(--target-panel);
            }}
            """
        )

    for obligation in obligations:
        obligation_slug = _slugify(obligation.id)
        control_id = _obligation_control_id(obligation.id)
        node_classes = [_node_class(node_id) for node_id in obligation.node_ids]
        edge_classes = sorted(
            {
                edge_class
                for node_id in obligation.node_ids
                for edge_class in node_edge_map.get(node_id, set())
            }
        )

        hover_node_selector = ",\n".join(
            [
                f".report-root:has(.obligation-{obligation_slug}:hover) a.{node_class} .graph-node-box"
                for node_class in node_classes
            ]
            + [
                f".report-root:has(.obligation-{obligation_slug}:hover) .{node_class}.node-card"
                for node_class in node_classes
            ]
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
            [
                f".report-root:has(#{control_id}:checked) a.{node_class} .graph-node-box"
                for node_class in node_classes
            ]
            + [
                f".report-root:has(#{control_id}:checked) .{node_class}.node-card"
                for node_class in node_classes
            ]
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
            [
                f".report-root:has(#{control_id}:checked) .{edge_class}"
                for edge_class in edge_classes
            ]
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


def _incident_edge_classes(graph: ProofGraph) -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        edge_class = _edge_class(edge.source_id, edge.target_id)
        mapping[edge.source_id].add(edge_class)
        mapping[edge.target_id].add(edge_class)
    return mapping


def _node_class(node_id: str) -> str:
    return f"node-{_slugify(node_id)}"


def _edge_class(source_id: str, target_id: str) -> str:
    return f"edge-{_slugify(source_id)}-{_slugify(target_id)}"


def _obligation_control_id(obligation_id: str) -> str:
    return f"obligation-check-{_slugify(obligation_id)}"


def _status_class(status: str) -> str:
    return f"status-{_slugify(status).replace('_', '-')}"


def _node_corner_radius(status: str) -> int:
    if status == "formal_verified":
        return 28
    if status == "formal_failed":
        return 10
    if status == "candidate_formal":
        return 22
    return 14


def _graph_visual_status(node: ProofNode) -> str:
    if node.status in {"formal_verified", "formal_failed"} and node.formal_artifact is not None:
        return node.status
    return "informal"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return slug or "item"
