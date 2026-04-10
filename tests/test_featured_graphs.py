from pathlib import Path
import re

from formal_islands.models import FormalArtifact, ProofEdge, ProofGraph, ProofNode
from formal_islands.site.featured_graphs import (
    FeaturedGraphSpec,
    build_featured_graph_bundle,
    render_featured_graph_html,
)


def test_render_featured_graph_html_preserves_mathish_titles_and_statuses() -> None:
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="Main theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title=r"Root with \(\lambda\)",
                informal_statement="Root.",
                informal_proof_text="Use n1.",
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="Try it.",
            ),
            ProofNode(
                id="n1",
                title="Verified child",
                informal_statement="Child.",
                informal_proof_text="Done.",
                status="formal_verified",
                formal_artifact=FormalArtifact(
                    lean_theorem_name="child_core",
                    lean_statement="theorem child_core : True",
                    lean_code="theorem child_core : True := by trivial",
                    faithfulness_classification="full_node",
                ),
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1")],
    )

    html = render_featured_graph_html(graph, graph_id="toy")

    assert 'class="featured-graph"' in html
    assert 'featured-graph-node-box status-candidate-formal' in html
    assert 'featured-graph-node-box status-formal-verified' in html
    assert r"Root with \(\lambda\)" in html
    assert "featured-graph-id" not in html


def test_render_featured_graph_html_uses_variable_node_sizes() -> None:
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="Main theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="A very long homepage label that should make the teaser node substantially wider",
                informal_statement="Root.",
                informal_proof_text="Use n1.",
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="Try it.",
            ),
            ProofNode(
                id="n1",
                title="Short",
                informal_statement="Child.",
                informal_proof_text="Done.",
                status="formal_verified",
                formal_artifact=FormalArtifact(
                    lean_theorem_name="child_core",
                    lean_statement="theorem child_core : True",
                    lean_code="theorem child_core : True := by trivial",
                    faithfulness_classification="full_node",
                ),
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1")],
    )

    html = render_featured_graph_html(graph, graph_id="toy")

    widths = [float(match.group(1)) for match in re.finditer(r'width="([0-9.]+)"', html)]
    assert any(width >= 300.0 for width in widths)


def test_build_featured_graph_bundle_from_graph_json(tmp_path: Path) -> None:
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="Main theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Root",
                informal_statement="Root.",
                informal_proof_text="Done.",
            )
        ],
        edges=[],
    )
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(graph.model_dump_json(indent=2), encoding="utf-8")

    bundle = build_featured_graph_bundle(
        [FeaturedGraphSpec(id="toy", report_url="reports/toy.html", graph_json=str(graph_path.relative_to(tmp_path)))],
        repo_root=tmp_path,
    )

    assert "toy" in bundle
    assert "featured-graph" in bundle["toy"]


def test_render_featured_graph_html_hides_subsumed_informal_child() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    spec = FeaturedGraphSpec(
        id="discrete_loomis_whitney",
        report_url="reports/discrete_loomis_whitney.html",
        graph_json="artifacts/manual-testing/run20-discrete-loomis-whitney-gemini-aristotle/03_formalized_graph.json",
    )

    graph = build_featured_graph_bundle([spec], repo_root=repo_root)["discrete_loomis_whitney"]

    assert "Discrete Loomis-Whitney inequality" in graph
    assert "Multilinear Hölder" in graph
    assert "estimate on slices" in graph
    assert "Base case (d=2)" not in graph
