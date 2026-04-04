"""Static HTML and JSON report generation."""

from __future__ import annotations

import json
from collections import Counter
from html import escape

from formal_islands.models import ProofGraph, ReviewObligation


def export_report_bundle(graph: ProofGraph, obligations: list[ReviewObligation]) -> dict:
    """Export a JSON-serializable report bundle."""

    return {
        "graph": graph.model_dump(mode="json"),
        "review_obligations": [obligation.model_dump(mode="json") for obligation in obligations],
    }


def render_html_report(graph: ProofGraph, obligations: list[ReviewObligation]) -> str:
    """Render a simple static HTML report for the prototype."""

    status_counts = Counter(node.status for node in graph.nodes)
    obligation_items = "\n".join(
        f"<li><strong>{escape(obligation.kind)}</strong>: {escape(obligation.text)}</li>"
        for obligation in obligations
    )
    node_sections = "\n".join(_render_node_section(node) for node in graph.nodes)

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
    }}
    body {{
      margin: 0;
      font-family: Georgia, "Iowan Old Style", serif;
      background: radial-gradient(circle at top, #fff8e7, var(--bg));
      color: var(--ink);
    }}
    main {{
      max-width: 960px;
      margin: 0 auto;
      padding: 2rem 1.25rem 4rem;
    }}
    section, article {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1rem 1.25rem;
      margin-top: 1rem;
      box-shadow: 0 12px 30px rgba(60, 40, 20, 0.06);
    }}
    h1, h2, h3 {{
      margin-top: 0;
    }}
    .meta {{
      color: var(--muted);
    }}
    .pill {{
      display: inline-block;
      margin-right: 0.5rem;
      margin-bottom: 0.5rem;
      padding: 0.2rem 0.6rem;
      border-radius: 999px;
      background: #f2e7d8;
      color: var(--accent);
      font-size: 0.9rem;
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
    </section>
    <section>
      <h2>Review Checklist</h2>
      <ol>
        {obligation_items}
      </ol>
    </section>
    <section>
      <h2>Nodes</h2>
      {node_sections}
    </section>
  </main>
</body>
</html>
"""


def _render_node_section(node: object) -> str:
    informal_statement = escape(node.informal_statement)
    informal_proof_text = escape(node.informal_proof_text)
    node_title = escape(node.title)
    node_id = escape(node.id)
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
    <article data-node-id="{node_id}">
      <h3>{node_title}</h3>
      <p class="meta">Node id: {node_id} | Status: {escape(node.status)}</p>
      {display_label}
      {candidate_block}
      <p><strong>Informal statement:</strong> {informal_statement}</p>
      <p><strong>Informal proof:</strong> {informal_proof_text}</p>
      {formal_block}
    </article>
    """
