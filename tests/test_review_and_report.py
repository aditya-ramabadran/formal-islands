import json

from formal_islands.models import FormalArtifact, ProofEdge, ProofGraph, ProofNode
from formal_islands.examples.fixtures import build_example_graph
from formal_islands.report.generator import (
    NODE_HEIGHT,
    _compute_graph_layout,
    _render_math_text,
    export_report_bundle,
    render_html_report,
)
from formal_islands.review.extractor import derive_review_obligations


def test_derive_review_obligations_covers_all_required_kinds() -> None:
    graph = build_example_graph()

    obligations = derive_review_obligations(graph)
    kinds = {obligation.kind for obligation in obligations}

    assert "informal_proof_check" in kinds
    assert "formal_semantic_match_check" in kinds
    assert "boundary_interface_check" in kinds


def test_export_report_bundle_is_json_serializable() -> None:
    graph = build_example_graph()
    obligations = derive_review_obligations(graph)

    bundle = export_report_bundle(graph, obligations)
    serialized = json.dumps(bundle)

    assert "review_obligations" in serialized
    assert "formal_verified" in serialized


def test_render_html_report_includes_core_sections() -> None:
    graph = build_example_graph()
    obligations = derive_review_obligations(graph)

    html = render_html_report(graph, obligations)

    assert "<h1>Nonnegative sum</h1>" in html
    assert "Review Checklist" in html
    assert "Formal Artifact" in html
    assert "Lean code" in html
    assert "Verification logs" in html
    assert 'type="checkbox"' in html
    assert 'class="graph-widget"' in html
    assert 'class="graph-frame"' in html
    assert 'class="graph-node-link node-n1 status-informal"' in html
    assert 'data-obligation-id="informal-proof-n1"' in html
    assert 'id="MathJax-script"' in html
    assert "width: min(100%, 460px);" in html
    assert "Nodes without attached Lean artifacts use dashed amber outlines." in html
    assert "Dashed gray edges show provenance or refinement links" in html
    assert "language-lean" in html
    assert 'class="tok-keyword"' in html or 'class="tok-type"' in html
    assert 'preserveAspectRatio="xMidYMin meet"' in html
    assert "overflow-y: visible;" in html
    assert "overflow-wrap: anywhere;" in html
    assert "color-scheme: light dark;" in html
    assert "@media (prefers-color-scheme: dark)" in html
    assert "--graph-shell-top:" in html
    assert "--checklist-panel:" in html
    assert ".lean-code {" in html
    assert "#f2e8dc" in html


def test_render_html_report_preserves_latex_text_blocks() -> None:
    graph = build_example_graph().model_copy(
        update={
            "theorem_statement": r"Let \(f : \mathbb{R}^d \to \mathbb{C}\). Then \[\|f\|_{L^2}^2 \le 1.\]",
            "nodes": [
                node.model_copy(
                    update={
                        "informal_statement": r"Show \(\|f\|_{L^2}^2 \le 1\).",
                        "informal_proof_text": r"Apply \(\nabla(|f|^2)\) and conclude from \[\int_{\mathbb{R}^d} |f|^2\,dx \le 1.\]",
                    }
                )
                for node in build_example_graph().nodes
            ],
        }
    )
    obligations = derive_review_obligations(graph)

    html = render_html_report(graph, obligations)

    assert r"\(f : \mathbb{R}^d \to \mathbb{C}\)" in html
    assert r"\[\|f\|_{L^2}^2 \le 1.\]" in html
    assert 'class="math-text"' in html
    assert "margin: 0.45rem 0 !important;" in html


def test_render_html_report_uses_preview_highlight_for_hover_and_green_for_checked() -> None:
    graph = build_example_graph()
    obligations = derive_review_obligations(graph)

    html = render_html_report(graph, obligations)

    assert ".report-root:has(.obligation-informal-proof-n1:hover)" in html
    assert "background: var(--preview-soft);" in html
    assert ".report-root:has(#obligation-check-informal-proof-n1:checked)" in html
    assert "background: var(--checked-soft);" in html


def test_render_html_report_styles_graph_nodes_by_status() -> None:
    graph = build_example_graph()
    obligations = derive_review_obligations(graph)

    html = render_html_report(graph, obligations)

    assert 'class="graph-node-link node-n1 status-informal"' in html
    assert 'class="graph-node-link node-n2 status-formal-verified"' in html
    assert 'class="graph-node-box status-formal-verified"' in html
    assert 'class="graph-node-badge status-formal-verified"' in html
    assert ".graph-node-box.status-informal" in html
    assert ".graph-node-box.status-formal-verified" in html


def test_render_html_report_formats_lean_statements_as_code() -> None:
    graph = build_example_graph()
    obligations = derive_review_obligations(graph)

    html = render_html_report(graph, obligations)

    assert "<strong>Lean statement:</strong>" in html
    assert '<pre><code class="language-lean lean-code">' in html
    assert 'class="checklist-code"' in html


def test_render_html_report_highlights_lean_tokens_locally() -> None:
    graph = build_example_graph()
    obligations = derive_review_obligations(graph)

    html = render_html_report(graph, obligations)

    assert "tok-keyword" in html
    assert "tok-tactic" in html


def test_render_html_report_marks_refinement_edges_as_provenance() -> None:
    graph = build_example_graph().model_copy(
        update={
            "edges": [
                ProofEdge(source_id="n1", target_id="n2", label="refined_from"),
            ]
        }
    )
    obligations = derive_review_obligations(graph)

    html = render_html_report(graph, obligations)

    assert "edge-provenance" in html
    assert "provenance or refinement links" in html


def test_compute_graph_layout_height_covers_all_nodes() -> None:
    graph = build_example_graph()

    layout = _compute_graph_layout(graph)

    for node in graph.nodes:
        _, y = layout["positions"][node.id]
        assert y + NODE_HEIGHT <= layout["height"]


def test_render_math_text_formats_backticks_as_inline_code() -> None:
    html = _render_math_text("Use `grad_u` and `horth`.")

    assert '<code class="inline-code">grad_u</code>' in html
    assert '<code class="inline-code">horth</code>' in html


def test_derive_review_obligations_words_supporting_sublemma_honestly() -> None:
    artifact = FormalArtifact(
        lean_theorem_name="core",
        lean_statement="theorem core : True",
        lean_code="theorem core : True := by trivial",
        faithfulness_classification="concrete_sublemma",
    )
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="Main theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Main theorem",
                informal_statement="Main theorem.",
                informal_proof_text="Use n1.",
            ),
            ProofNode(
                id="n1",
                title="Broad informal step",
                informal_statement="Broad informal step.",
                informal_proof_text="Use supporting core.",
            ),
            ProofNode(
                id="n1__formal_core",
                title="Certified local core",
                informal_statement="Supporting core.",
                informal_proof_text="Verified core.",
                status="formal_verified",
                formal_artifact=artifact,
            ),
        ],
        edges=[ProofEdge(source_id="n1__formal_core", target_id="n1", label="formal_sublemma_for")],
    )

    obligations = derive_review_obligations(graph)
    semantic = next(item for item in obligations if item.kind == "formal_semantic_match_check")
    boundary = next(item for item in obligations if item.kind == "boundary_interface_check")

    assert "narrower verified lean supporting sublemma" in semantic.text.lower()
    assert "without overclaiming full coverage" in boundary.text.lower()
