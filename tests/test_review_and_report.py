import json
from pathlib import Path

from formal_islands.models import FormalArtifact, ProofEdge, ProofGraph, ProofNode
from formal_islands.examples.fixtures import build_example_graph
from formal_islands.backends import MockBackend
from formal_islands.report.annotation import (
    build_remaining_proof_burden_assessment_request,
    synthesize_remaining_proof_burdens,
)
from formal_islands.report.generator import (
    NODE_HEIGHT,
    _build_graph_history_frames,
    _compute_graph_layout,
    _render_math_text,
    export_report_bundle,
    load_graph_history_entries,
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


def test_export_report_bundle_includes_graph_history_summary_when_present(tmp_path) -> None:
    graph = build_example_graph()
    obligations = derive_review_obligations(graph)
    history_path = tmp_path / "graph_history.jsonl"
    history_path.write_text(
        json.dumps(
            {
                "version": 1,
                "timestamp": "2026-04-08T15:01:14-07:00",
                "event": "plan_stage_extracted_graph",
                "label": "01_extracted_graph.json",
                "node_id": None,
                "graph": graph.model_dump(mode="json"),
                "diff": None,
                "metadata": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    bundle = export_report_bundle(
        graph,
        obligations,
        graph_history=load_graph_history_entries(history_path),
    )

    assert "graph_history" in bundle
    assert bundle["graph_history"][0]["caption"] == "Initial extracted proof graph."


def test_render_html_report_sanitizes_verification_command_paths() -> None:
    artifact_path = (
        "/Users/example/GitHub/formal-islands/lean_project/"
        "FormalIslands/Generated/test_attempt_1.lean"
    )
    artifact = FormalArtifact(
        lean_theorem_name="child_core",
        lean_statement="theorem child_core : True",
        lean_code="theorem child_core : True := by trivial",
        faithfulness_classification="full_node",
        verification={
            "status": "verified",
            "command": f"lake env lean {artifact_path}",
            "artifact_path": artifact_path,
        },
    )
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="Main theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Parent theorem",
                informal_statement="Show the parent theorem.",
                informal_proof_text="Use n1.",
            ),
            ProofNode(
                id="n1",
                title="Verified child",
                informal_statement="Child.",
                informal_proof_text="Core.",
                status="formal_verified",
                formal_artifact=artifact,
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1")],
    )
    obligations = derive_review_obligations(graph)

    bundle = export_report_bundle(graph, obligations)
    html = render_html_report(graph, obligations)
    serialized = json.dumps(bundle)

    assert artifact_path not in serialized
    assert artifact_path not in html
    assert "lean_project/FormalIslands/Generated/test_attempt_1.lean" in serialized
    assert "lean_project/FormalIslands/Generated/test_attempt_1.lean" in html


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
    assert "width: min(100%, 720px);" in html
    assert "Candidate formal nodes use dashed gold outlines" in html
    assert "All arrows point from a claim to one of its dependencies." in html
    assert "Dashed gray arrows mark refinement edges" in html
    assert "Used by (parent nodes):" in html
    assert "Depends on (child nodes):" in html
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
    assert "data-graph-history" not in html


def test_render_html_report_with_graph_history_renders_timeline_controls() -> None:
    before = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="A implies B.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Root",
                informal_statement="A implies B.",
                informal_proof_text="Use n1.",
            ),
            ProofNode(
                id="n1",
                title="Leaf",
                informal_statement="A.",
                informal_proof_text="Given.",
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="Leaf node.",
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1")],
    )
    after = before.model_copy(
        update={
            "nodes": [
                node.model_copy(
                    update={
                        "status": "formal_verified",
                        "formal_artifact": FormalArtifact(
                            lean_theorem_name="n1_core",
                            lean_statement="theorem n1_core : True",
                            lean_code="theorem n1_core : True := by trivial",
                            faithfulness_classification="full_node",
                        ),
                    }
                )
                if node.id == "n1"
                else node
                for node in before.nodes
            ]
        }
    )
    obligations = derive_review_obligations(after)
    graph_history = [
        {
            "version": 1,
            "timestamp": "2026-04-08T15:01:14-07:00",
            "event": "plan_stage_candidate_graph",
            "label": "02_candidate_graph.json",
            "node_id": None,
            "diff": {
                "added_nodes": [],
                "removed_nodes": [],
                "changed_nodes": [
                    {
                        "id": "n1",
                        "before_status": "informal",
                        "after_status": "candidate_formal",
                        "before_priority": None,
                        "after_priority": 1,
                    }
                ],
                "added_edges": [],
                "removed_edges": [],
            },
            "metadata": {},
            "graph": before.model_dump(mode="json"),
        },
        {
            "version": 1,
            "timestamp": "2026-04-08T15:02:14-07:00",
            "event": "formalization_update",
            "label": "03_formalized_graph.json (n1)",
            "node_id": "n1",
            "diff": {
                "added_nodes": [],
                "removed_nodes": [],
                "changed_nodes": [
                    {
                        "id": "n1",
                        "before_status": "candidate_formal",
                        "after_status": "formal_verified",
                        "before_priority": 1,
                        "after_priority": 1,
                    }
                ],
                "added_edges": [],
                "removed_edges": [],
            },
            "metadata": {},
            "graph": after.model_dump(mode="json"),
        },
    ]

    html = render_html_report(after, obligations, graph_history=graph_history)

    assert 'data-graph-history' in html
    assert 'data-history-action="start"' in html
    assert 'data-history-action="end"' in html
    assert 'Snapshot <span data-history-index>2</span> of <span data-history-count>2</span>' in html
    assert (
        'data-caption-html="Candidate selection marked &lt;code class=&quot;inline-code&quot;&gt;n1&lt;/code&gt; for formalization."'
        in html
    )
    assert (
        'Node <code class="inline-code">n1</code> was successfully formalized, status upgraded from '
        '<code class="inline-code">candidate_formal</code> to <code class="inline-code">formal_verified</code>.'
        in html
    )


def test_render_html_report_hides_subsumed_informal_child_from_final_display() -> None:
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="Main theorem.",
        root_node_id="root",
        nodes=[
            ProofNode(
                id="root",
                title="Root theorem",
                informal_statement="Main theorem.",
                informal_proof_text="Use base case.",
                status="formal_verified",
                formal_artifact=FormalArtifact(
                    lean_theorem_name="root_core",
                    lean_statement="theorem root_core : True",
                    lean_code="theorem root_core : True := by trivial",
                    faithfulness_classification="full_node",
                ),
            ),
            ProofNode(
                id="base_case",
                title="Base case",
                informal_statement="Base case.",
                informal_proof_text="Trivial.",
            ),
        ],
        edges=[ProofEdge(source_id="root", target_id="base_case")],
    )
    obligations = derive_review_obligations(graph)

    html = render_html_report(graph, obligations)
    bundle = export_report_bundle(graph, obligations)

    assert "Hidden subsumed nodes (1)" in html
    assert "Final display cleanup hid subsumed informal node <code class=\"inline-code\">base_case</code>" in html
    assert "<span class=\"pill\">Nodes: 1</span>" in html
    assert "Check that node 'base_case'" not in html
    assert bundle["graph"]["nodes"][0]["id"] == "root"
    assert bundle["review_obligations"] == [
        {
            "id": "semantic-match-root",
            "kind": "formal_semantic_match_check",
            "text": "Check that node 'root' (Root theorem) matches the verified Lean theorem for this node.",
            "node_ids": ["root"],
        }
    ]


def test_graph_history_frames_skip_annotation_only_or_duplicate_visual_snapshots() -> None:
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="A implies B.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Root",
                informal_statement="A implies B.",
                informal_proof_text="Use n1.",
            ),
            ProofNode(
                id="n1",
                title="Leaf",
                informal_statement="A.",
                informal_proof_text="Given.",
                status="formal_verified",
                formal_artifact=FormalArtifact(
                    lean_theorem_name="n1_core",
                    lean_statement="theorem n1_core : True",
                    lean_code="theorem n1_core : True := by trivial",
                    faithfulness_classification="full_node",
                ),
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1")],
    )
    graph_with_burden = graph.model_copy(
        update={
            "nodes": [
                node.model_copy(
                    update={
                        "remaining_proof_burden": "Only the final rewrite remains.",
                    }
                )
                if node.id == "n0"
                else node
                for node in graph.nodes
            ]
        }
    )
    entries = [
        {
            "version": 1,
            "timestamp": "2026-04-09T09:00:00-07:00",
            "event": "formalization_update",
            "label": "03_formalized_graph.json (n1)",
            "node_id": "n1",
            "diff": None,
            "metadata": {},
            "graph": graph.model_dump(mode="json"),
        },
        {
            "version": 1,
            "timestamp": "2026-04-09T09:01:00-07:00",
            "event": "report_stage_graph",
            "label": "04_report_graph",
            "node_id": None,
            "diff": {
                "added_nodes": [],
                "removed_nodes": [],
                "changed_nodes": [
                    {
                        "id": "n0",
                        "before_status": "informal",
                        "after_status": "informal",
                        "before_priority": None,
                        "after_priority": None,
                        "remaining_proof_burden_changed": True,
                        "formal_artifact_attached_changed": False,
                    }
                ],
                "added_edges": [],
                "removed_edges": [],
            },
            "metadata": {},
            "graph": graph_with_burden.model_dump(mode="json"),
        },
        {
            "version": 1,
            "timestamp": "2026-04-09T09:02:00-07:00",
            "event": "report_stage_graph",
            "label": "04_report_graph",
            "node_id": None,
            "diff": {
                "added_nodes": [],
                "removed_nodes": [],
                "changed_nodes": [
                    {
                        "id": "n0",
                        "before_status": "informal",
                        "after_status": "informal",
                        "before_priority": None,
                        "after_priority": None,
                        "remaining_proof_burden_changed": True,
                        "formal_artifact_attached_changed": False,
                    }
                ],
                "added_edges": [],
                "removed_edges": [],
            },
            "metadata": {},
            "graph": graph_with_burden.model_dump(mode="json"),
        },
    ]

    frames = _build_graph_history_frames(entries)

    assert len(frames) == 1
    assert frames[0]["event"] == "formalization_update"


def test_render_html_report_with_parent_promotion_history_renders_promotion_caption() -> None:
    before = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="A implies B.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Root",
                informal_statement="A implies B.",
                informal_proof_text="Use n1.",
            ),
            ProofNode(
                id="n1",
                title="Leaf",
                informal_statement="A.",
                informal_proof_text="Given.",
                status="formal_verified",
                formal_artifact=FormalArtifact(
                    lean_theorem_name="n1_core",
                    lean_statement="theorem n1_core : True",
                    lean_code="theorem n1_core : True := by trivial",
                    faithfulness_classification="full_node",
                ),
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1")],
    )
    after = before.model_copy(
        update={
            "nodes": [
                    node.model_copy(
                        update={
                            "status": "candidate_formal",
                            "formalization_priority": 2,
                            "formalization_rationale": "Promoted after verified child support.",
                        }
                    )
                if node.id == "n0"
                else node
                for node in before.nodes
            ]
        }
    )
    obligations = derive_review_obligations(after)
    graph_history = [
        {
            "version": 1,
            "timestamp": "2026-04-09T09:59:00-07:00",
            "event": "plan_stage_extracted_graph",
            "label": "01_extracted_graph.json",
            "node_id": None,
            "diff": None,
            "metadata": {},
            "graph": before.model_dump(mode="json"),
        },
        {
            "version": 1,
            "timestamp": "2026-04-09T10:00:00-07:00",
            "event": "parent_promotion",
            "label": "parent promotion (n0)",
            "node_id": "n0",
            "diff": {
                "added_nodes": [],
                "removed_nodes": [],
                "changed_nodes": [
                    {
                        "id": "n0",
                        "before_status": "informal",
                        "after_status": "candidate_formal",
                        "before_priority": None,
                        "after_priority": 2,
                    }
                ],
                "added_edges": [],
                "removed_edges": [],
            },
            "metadata": {
                "recommended_priority": 2,
            },
            "graph": after.model_dump(mode="json"),
        },
        {
            "version": 1,
            "timestamp": "2026-04-09T10:01:00-07:00",
            "event": "formalization_update",
            "label": "03_formalized_graph.json (n0)",
            "node_id": "n0",
            "diff": {
                "added_nodes": [],
                "removed_nodes": [],
                "changed_nodes": [
                    {
                        "id": "n0",
                        "before_status": "candidate_formal",
                        "after_status": "candidate_formal",
                        "before_priority": 2,
                        "after_priority": 2,
                        "remaining_proof_burden_changed": True,
                    }
                ],
                "added_edges": [],
                "removed_edges": [],
            },
            "metadata": {},
            "graph": after.model_dump(mode="json"),
        },
    ]

    html = render_html_report(after, obligations, graph_history=graph_history)

    assert (
        'data-caption-html="Node &lt;code class=&quot;inline-code&quot;&gt;n0&lt;/code&gt; was promoted from '
        '&lt;code class=&quot;inline-code&quot;&gt;informal&lt;/code&gt; to '
        '&lt;code class=&quot;inline-code&quot;&gt;candidate_formal&lt;/code&gt; at priority '
        '&lt;code class=&quot;inline-code&quot;&gt;2&lt;/code&gt; after its direct children were verified."'
    ) in html


def test_render_html_report_shows_remaining_proof_burden_for_verified_children() -> None:
    artifact = FormalArtifact(
        lean_theorem_name="child_core",
        lean_statement="theorem child_core : True",
        lean_code="theorem child_core : True := by trivial",
        faithfulness_classification="full_node",
    )
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="Main theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Parent theorem",
                informal_statement="Show the parent theorem.",
                informal_proof_text="Use n1 and n2.",
                remaining_proof_burden="Assuming the verified child lemmas, it remains to assemble their conclusions and discharge the final parent-level rewrite.",
            ),
            ProofNode(
                id="n1",
                title="Verified child one",
                informal_statement="Child one.",
                informal_proof_text="Core one.",
                status="formal_verified",
                formal_artifact=artifact,
            ),
            ProofNode(
                id="n2",
                title="Verified child two",
                informal_statement="Child two.",
                informal_proof_text="Core two.",
                status="formal_verified",
                formal_artifact=artifact,
            ),
        ],
        edges=[
            ProofEdge(source_id="n0", target_id="n1"),
            ProofEdge(source_id="n0", target_id="n2"),
        ],
    )
    obligations = derive_review_obligations(graph)

    html = render_html_report(graph, obligations)

    assert "Remaining proof burden (assuming results of" in html
    assert '<a class="node-jump" href="#node-n1">n1</a>' in html
    assert '<a class="node-jump" href="#node-n2">n2</a>' in html
    assert "final parent-level rewrite" in html


def test_render_html_report_shows_most_recent_formalization_episode_for_informal_parent() -> None:
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="Main theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Parent theorem",
                informal_statement="Show the parent theorem.",
                informal_proof_text="Use n0__formal_core.",
                last_formalization_attempt_count=1,
                last_formalization_outcome="produced_supporting_core",
                last_formalization_note=(
                    "Most recent formalization attempt produced the verified supporting core "
                    "`n0__formal_core` rather than a full-node theorem."
                ),
            ),
            ProofNode(
                id="n0__formal_core",
                title="Certified local core for Parent theorem",
                informal_statement="Core statement.",
                informal_proof_text="Core proof.",
                status="formal_verified",
                formal_artifact=FormalArtifact(
                    lean_theorem_name="n0_core",
                    lean_statement="theorem n0_core : True",
                    lean_code="theorem n0_core : True := by trivial",
                    faithfulness_classification="concrete_sublemma",
                ),
            ),
        ],
        edges=[
            ProofEdge(source_id="n0", target_id="n0__formal_core", label="formal_sublemma_for"),
        ],
    )
    obligations = derive_review_obligations(graph)

    html = render_html_report(graph, obligations)

    assert "Most recent formalization episode: produced a verified supporting core after 1 Lean verification attempt." in html
    assert "n0__formal_core" in html


def test_synthesize_remaining_proof_burdens_uses_planner_and_updates_graph() -> None:
    artifact = FormalArtifact(
        lean_theorem_name="child_core",
        lean_statement="theorem child_core : True",
        lean_code="theorem child_core : True := by trivial",
        faithfulness_classification="full_node",
    )
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="Main theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Parent theorem",
                informal_statement="Show the parent theorem.",
                informal_proof_text="Use n1 and n2.",
            ),
            ProofNode(
                id="n1",
                title="Verified child one",
                informal_statement="Child one.",
                informal_proof_text="Core one.",
                status="formal_verified",
                formal_artifact=artifact,
            ),
            ProofNode(
                id="n2",
                title="Verified child two",
                informal_statement="Child two.",
                informal_proof_text="Core two.",
                status="formal_verified",
                formal_artifact=artifact,
            ),
        ],
        edges=[
            ProofEdge(source_id="n0", target_id="n1"),
            ProofEdge(source_id="n0", target_id="n2"),
        ],
    )
    backend = MockBackend(
        queued_payloads=[
            {
                "remaining_proof_burden": "Assuming n1 and n2, the remaining work is to assemble the two certified identities and discharge the final parent-level rewrite."
            }
        ]
    )

    updated = synthesize_remaining_proof_burdens(graph=graph, planning_backend=backend)
    parent = next(node for node in updated.nodes if node.id == "n0")

    assert parent.remaining_proof_burden is not None
    assert "assemble the two certified identities" in parent.remaining_proof_burden
    assert len(backend.requests) == 1
    assert backend.requests[0].task_name == "assess_remaining_proof_burden"


def test_remaining_proof_burden_prompt_is_concrete_about_the_residual_delta() -> None:
    artifact = FormalArtifact(
        lean_theorem_name="child_core",
        lean_statement="theorem child_core : True",
        lean_code="theorem child_core : True := by trivial",
        faithfulness_classification="full_node",
    )
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="Main theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Parent theorem",
                informal_statement="Show the parent theorem.",
                informal_proof_text="Use n1 and n2.",
            ),
            ProofNode(
                id="n1",
                title="Verified child one",
                informal_statement="Child one.",
                informal_proof_text="Core one.",
                status="formal_verified",
                formal_artifact=artifact,
            ),
            ProofNode(
                id="n2",
                title="Verified child two",
                informal_statement="Child two.",
                informal_proof_text="Core two.",
                status="formal_verified",
                formal_artifact=artifact,
            ),
        ],
        edges=[
            ProofEdge(source_id="n0", target_id="n1"),
            ProofEdge(source_id="n0", target_id="n2"),
        ],
    )

    request = build_remaining_proof_burden_assessment_request(graph=graph, parent_node_id="n0")

    lowered = request.prompt.lower()
    assert "dependency direction note" in lowered
    assert "human would still need to prove" in lowered
    assert "specific missing steps" in lowered
    assert "two to four sentences" in lowered


def test_remaining_proof_burden_prompt_mentions_downstream_verified_support() -> None:
    artifact = FormalArtifact(
        lean_theorem_name="child_core",
        lean_statement="theorem child_core : True",
        lean_code="theorem child_core : True := by trivial",
        faithfulness_classification="full_node",
    )
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="Main theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Parent theorem",
                informal_statement="Show the parent theorem.",
                informal_proof_text="Use n1.",
            ),
            ProofNode(
                id="n1",
                title="Intermediate informal child",
                informal_statement="Bridge to n2.",
                informal_proof_text="Use n2.",
            ),
            ProofNode(
                id="n2",
                title="Verified downstream support",
                informal_statement="Child two.",
                informal_proof_text="Core two.",
                status="formal_verified",
                formal_artifact=artifact,
            ),
        ],
        edges=[
            ProofEdge(source_id="n0", target_id="n1"),
            ProofEdge(source_id="n1", target_id="n2"),
        ],
    )

    request = build_remaining_proof_burden_assessment_request(graph=graph, parent_node_id="n0")

    lowered = request.prompt.lower()
    assert "deeper verified support already available downstream" in lowered
    assert "not direct child lemmas" in lowered


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


def test_render_html_report_styles_candidate_nodes_distinctly() -> None:
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="A implies B.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Root",
                informal_statement="A implies B.",
                informal_proof_text="Use n1.",
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="Worth formalizing.",
            ),
            ProofNode(
                id="n1",
                title="Leaf",
                informal_statement="A.",
                informal_proof_text="Given.",
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1")],
    )
    obligations = derive_review_obligations(graph)

    html = render_html_report(graph, obligations)

    assert 'class="graph-node-link node-n0 status-candidate-formal"' in html
    assert 'class="graph-node-box status-candidate-formal"' in html
    assert 'class="graph-node-badge status-candidate-formal"' in html
    assert ".graph-node-box.status-candidate-formal" in html
    assert "Candidate formal nodes use dashed gold outlines" in html


def test_render_html_report_includes_node_navigation_controls_snapshot() -> None:
    graph = build_example_graph()
    obligations = derive_review_obligations(graph)

    html = render_html_report(graph, obligations)
    fixture = Path("tests/fixtures/report_node_navigation.snapshot.html").read_text(encoding="utf-8").strip()

    assert fixture in html


def test_render_html_report_history_toggle_snapshot_for_duplicate_visual_states() -> None:
    before = build_example_graph()
    candidate = before.model_copy(
        update={
            "nodes": [
                node.model_copy(
                    update={
                        "status": "candidate_formal",
                        "formalization_priority": 2,
                        "formalization_rationale": "Worth a later attempt.",
                    }
                )
                if node.id == before.root_node_id
                else node
                for node in before.nodes
            ]
        }
    )
    after = candidate.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"remaining_proof_burden": "Only the final rewrite remains."})
                if node.id == before.root_node_id
                else node
                for node in candidate.nodes
            ]
        }
    )
    obligations = derive_review_obligations(after)
    graph_history = [
        {
            "version": 1,
            "timestamp": "2026-04-09T09:00:00-07:00",
            "event": "plan_stage_extracted_graph",
            "label": "01_extracted_graph.json",
            "node_id": None,
            "diff": None,
            "metadata": {},
            "graph": before.model_dump(mode="json"),
        },
        {
            "version": 1,
            "timestamp": "2026-04-09T09:00:30-07:00",
            "event": "candidate_selection_output",
            "label": "02_candidate_graph.json",
            "node_id": before.root_node_id,
            "diff": {
                "added_nodes": [],
                "removed_nodes": [],
                "changed_nodes": [
                    {
                        "id": before.root_node_id,
                        "before_status": "informal",
                        "after_status": "candidate_formal",
                        "before_priority": None,
                        "after_priority": 2,
                    }
                ],
                "added_edges": [],
                "removed_edges": [],
            },
            "metadata": {},
            "graph": candidate.model_dump(mode="json"),
        },
        {
            "version": 1,
            "timestamp": "2026-04-09T09:01:00-07:00",
            "event": "report_stage_graph",
            "label": "04_report_graph",
            "node_id": None,
            "diff": {
                "added_nodes": [],
                "removed_nodes": [],
                "changed_nodes": [
                    {
                        "id": before.root_node_id,
                        "before_status": "informal",
                        "after_status": "informal",
                        "before_priority": None,
                        "after_priority": None,
                        "remaining_proof_burden_changed": True,
                        "formal_artifact_attached_changed": False,
                    }
                ],
                "added_edges": [],
                "removed_edges": [],
            },
            "metadata": {},
            "graph": after.model_dump(mode="json"),
        },
    ]

    html = render_html_report(after, obligations, graph_history=graph_history)
    fixture = Path("tests/fixtures/report_history_toggle.snapshot.html").read_text(encoding="utf-8").strip()

    assert fixture in html


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


def test_render_html_report_marks_refinement_edges_as_refinement() -> None:
    graph = build_example_graph().model_copy(
        update={
            "edges": [
                ProofEdge(source_id="n1", target_id="n2", label="refined_from"),
            ]
        }
    )
    obligations = derive_review_obligations(graph)

    html = render_html_report(graph, obligations)

    assert "edge-refinement" in html
    assert "refinement edges" in html


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


def test_render_math_text_preserves_asterisks_inside_math() -> None:
    html = _render_math_text("Suppose $f*g$ and use `code` outside math.")

    assert "$f*g$" in html
    assert "<em>" not in html
    assert '<code class="inline-code">code</code>' in html


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
        edges=[ProofEdge(source_id="n1", target_id="n1__formal_core", label="formal_sublemma_for")],
    )

    obligations = derive_review_obligations(graph)
    semantic = next(item for item in obligations if item.kind == "formal_semantic_match_check")
    boundary = next(item for item in obligations if item.kind == "boundary_interface_check")

    assert "narrower verified lean supporting sublemma" in semantic.text.lower()
    assert "statement '" not in semantic.text.lower()
    assert "without overclaiming full coverage" in boundary.text.lower()
