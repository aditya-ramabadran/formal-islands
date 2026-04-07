from pydantic import ValidationError
import pytest
from pathlib import Path

from formal_islands.backends import MockBackend
from formal_islands.extraction.pipeline import (
    build_candidate_selection_request,
    build_extraction_request,
    build_theorem_planning_request,
    extract_proof_graph,
    plan_proof_graph,
    refine_candidate_nodes,
    simplify_proof_graph,
    select_formalization_candidates,
)
from formal_islands.models import FormalArtifact, ProofEdge, ProofGraph, ProofNode, VerificationResult
from formal_islands.progress import use_progress_log


def build_graph() -> ProofGraph:
    return ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="If A then B.",
        root_node_id="n1",
        nodes=[
            ProofNode(
                id="n1",
                title="Main claim",
                informal_statement="B holds.",
                informal_proof_text="Use n2.",
            ),
            ProofNode(
                id="n2",
                title="Technical lemma",
                informal_statement="A implies B.",
                informal_proof_text="By a direct argument.",
            ),
        ],
        edges=[ProofEdge(source_id="n1", target_id="n2")],
    )


def make_failed_artifact() -> FormalArtifact:
    return FormalArtifact(
        lean_theorem_name="dummy",
        lean_statement="dummy",
        lean_code="dummy",
        verification=VerificationResult(status="failed", command="test"),
        attempt_history=[],
    )


def test_build_extraction_request_uses_explicit_schema() -> None:
    request = build_extraction_request(
        theorem_statement="If A then B.",
        raw_proof_text="Assume A and deduce B.",
        theorem_title_hint="Lemma",
    )

    assert request.task_name == "extract_graph"
    assert request.json_schema["type"] == "object"
    assert "theorem_statement" in request.prompt
    assert "local technical subclaims" in request.prompt
    assert "compact, faithful, formalization-sensitive graph" in request.prompt


def test_build_theorem_planning_request_uses_merged_schema() -> None:
    request = build_theorem_planning_request(
        theorem_statement="If A then B.",
        raw_proof_text="Assume A and deduce B.",
        theorem_title_hint="Lemma",
    )

    assert request.task_name == "plan_theorem"
    assert request.json_schema["type"] == "object"
    assert "candidates" in request.prompt
    assert "plan the graph and candidate ranking jointly" in request.prompt.lower()


def test_extract_proof_graph_validates_schema_and_maps_to_internal_graph() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "theorem_title": "Toy theorem",
                "theorem_statement": "If A then B.",
                "root_node_id": "n1",
                "nodes": [
                    {
                        "id": "n1",
                        "title": "Main claim",
                        "informal_statement": "B holds.",
                        "informal_proof_text": "Use n2.",
                    },
                    {
                        "id": "n2",
                        "title": "Lemma",
                        "informal_statement": "A implies B.",
                        "informal_proof_text": "By inspection.",
                        "display_label": "technical estimate",
                    },
                ],
                "edges": [{"source_id": "n1", "target_id": "n2"}],
            }
        ]
    )

    graph = extract_proof_graph(
        backend=backend,
        theorem_statement="If A then B.",
        raw_proof_text="Assume A and deduce B.",
    )

    assert graph.root_node_id == "n1"
    assert graph.nodes[0].status == "informal"
    assert graph.nodes[1].display_label == "technical estimate"


def test_plan_proof_graph_returns_explicit_extracted_and_candidate_graphs() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "theorem_title": "Toy theorem",
                "theorem_statement": "If A then B.",
                "root_node_id": "n1",
                "nodes": [
                    {
                        "id": "n1",
                        "title": "Main claim",
                        "informal_statement": "B holds.",
                        "informal_proof_text": "Use n2.",
                    },
                    {
                        "id": "n2",
                        "title": "Lemma",
                        "informal_statement": "A implies B.",
                        "informal_proof_text": "By inspection.",
                    },
                ],
                "edges": [{"source_id": "n1", "target_id": "n2"}],
                "candidates": [
                    {
                        "node_id": "n2",
                        "priority": 3,
                        "rationale": "Self-contained technical node.",
                    }
                ],
            }
        ]
    )

    artifacts = plan_proof_graph(
        backend=backend,
        theorem_statement="If A then B.",
        raw_proof_text="Assume A and deduce B.",
    )

    assert all(node.status == "informal" for node in artifacts.extracted_graph.nodes)
    selected = next(node for node in artifacts.candidate_graph.nodes if node.id == "n2")
    assert selected.status == "candidate_formal"
    assert selected.formalization_priority == 3


def test_plan_proof_graph_calibrates_small_candidate_set() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "theorem_title": "Toy theorem",
                "theorem_statement": "If A then B.",
                "root_node_id": "n0",
                "nodes": [
                    {
                        "id": "n0",
                        "title": "Main theorem",
                        "informal_statement": "If A then B.",
                        "informal_proof_text": "Assemble n1, n2, n3.",
                    },
                    {
                        "id": "n1",
                        "title": "Setup reduction",
                        "informal_statement": "Rewrite the theorem in a reduced form.",
                        "informal_proof_text": "This is setup.",
                    },
                    {
                        "id": "n2",
                        "title": "Normalization step",
                        "informal_statement": "Normalize the variables into a local range.",
                        "informal_proof_text": "This is useful.",
                    },
                    {
                        "id": "n3",
                        "title": "Local inequality",
                        "informal_statement": r"\[ x^2 + y^2 \le 1 \]",
                        "informal_proof_text": "Concrete local estimate.",
                    },
                ],
                "edges": [
                    {"source_id": "n1", "target_id": "n0"},
                    {"source_id": "n2", "target_id": "n0"},
                    {"source_id": "n3", "target_id": "n0"},
                ],
                "candidates": [
                    {"node_id": "n1", "priority": 3, "rationale": "Setup."},
                    {"node_id": "n2", "priority": 2, "rationale": "Secondary."},
                    {"node_id": "n3", "priority": 1, "rationale": "Best local node."},
                ],
            }
        ]
    )

    artifacts = plan_proof_graph(
        backend=backend,
        theorem_statement="If A then B.",
        raw_proof_text="Proof.",
    )

    candidates = [node.id for node in artifacts.candidate_graph.nodes if node.status == "candidate_formal"]
    assert candidates == ["n2", "n3"]


def test_plan_proof_graph_normalizes_textual_candidate_priority_labels() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "theorem_title": "Toy theorem",
                "theorem_statement": "If A then B.",
                "root_node_id": "n1",
                "nodes": [
                    {
                        "id": "n1",
                        "title": "Main claim",
                        "informal_statement": "B holds.",
                        "informal_proof_text": "Use n2.",
                    },
                    {
                        "id": "n2",
                        "title": "Lemma",
                        "informal_statement": "A implies B.",
                        "informal_proof_text": "By inspection.",
                    },
                ],
                "edges": [{"source_id": "n1", "target_id": "n2"}],
                "candidates": [
                    {
                        "node_id": "n2",
                        "priority": "high",
                        "rationale": "Self-contained technical node.",
                    }
                ],
            }
        ]
    )

    artifacts = plan_proof_graph(
        backend=backend,
        theorem_statement="If A then B.",
        raw_proof_text="Assume A and deduce B.",
    )

    selected = next(node for node in artifacts.candidate_graph.nodes if node.id == "n2")
    assert selected.formalization_priority == 1


def test_plan_proof_graph_logs_backend_prompt_to_progress_file(tmp_path: Path) -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "theorem_title": "Toy theorem",
                "theorem_statement": "If A then B.",
                "root_node_id": "n1",
                "nodes": [
                    {
                        "id": "n1",
                        "title": "Main claim",
                        "informal_statement": "B holds.",
                        "informal_proof_text": "Use n2.",
                    },
                    {
                        "id": "n2",
                        "title": "Lemma",
                        "informal_statement": "A implies B.",
                        "informal_proof_text": "By inspection.",
                    },
                ],
                "edges": [{"source_id": "n1", "target_id": "n2"}],
                "candidates": [
                    {
                        "node_id": "n2",
                        "priority": 3,
                        "rationale": "Self-contained technical node.",
                    }
                ],
            }
        ]
    )
    progress_log = tmp_path / "_progress.log"

    with use_progress_log(progress_log):
        plan_proof_graph(
            backend=backend,
            theorem_statement="If A then B.",
            raw_proof_text="Assume A and deduce B.",
        )

    log_text = progress_log.read_text(encoding="utf-8")
    assert "prompting Mock backend for plan_theorem" in log_text
    assert "Mock backend completed for plan_theorem" in log_text


def test_extract_proof_graph_preserves_input_theorem_statement_exactly() -> None:
    original_statement = r"Let \(f : \mathbb{R}^d \to \mathbb{C}\). Then \[\|f\|_{L^2}^2 \le 1.\]"
    backend = MockBackend(
        queued_payloads=[
            {
                "theorem_title": "Toy theorem",
                "theorem_statement": "Let f : R^d -> C. Then ||f||_2^2 <= 1.",
                "root_node_id": "n1",
                "nodes": [
                    {
                        "id": "n1",
                        "title": "Main claim",
                        "informal_statement": "The claim holds.",
                        "informal_proof_text": "By direct computation.",
                    }
                ],
                "edges": [],
            }
        ]
    )

    graph = extract_proof_graph(
        backend=backend,
        theorem_statement=original_statement,
        raw_proof_text=r"Use \(\nabla\) and conclude.",
    )

    assert graph.theorem_statement == original_statement


def test_extract_proof_graph_rejects_malformed_payload() -> None:
    backend = MockBackend(queued_payloads=[{"theorem_title": "Missing fields"}])

    with pytest.raises(ValidationError):
        extract_proof_graph(
            backend=backend,
            theorem_statement="If A then B.",
            raw_proof_text="Assume A and deduce B.",
        )


def test_plan_proof_graph_rejects_unknown_candidate_node_ids() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "theorem_title": "Toy theorem",
                "theorem_statement": "If A then B.",
                "root_node_id": "n1",
                "nodes": [
                    {
                        "id": "n1",
                        "title": "Main claim",
                        "informal_statement": "B holds.",
                        "informal_proof_text": "Use n2.",
                    }
                ],
                "edges": [],
                "candidates": [
                    {
                        "node_id": "missing",
                        "priority": 2,
                        "rationale": "Bad id.",
                    }
                ],
            }
        ]
    )

    with pytest.raises(ValueError, match="unknown node ids"):
        plan_proof_graph(
            backend=backend,
            theorem_statement="If A then B.",
            raw_proof_text="Assume A and deduce B.",
        )


def test_extract_proof_graph_simplifies_oversegmented_nonnegative_sum() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "theorem_title": "Nonnegative sum",
                "theorem_statement": "If a and b are nonnegative, then a + b is nonnegative.",
                "root_node_id": "n1",
                "nodes": [
                    {
                        "id": "n1",
                        "title": "Nonnegative sum",
                        "informal_statement": "If 0 <= a and 0 <= b, then 0 <= a + b.",
                        "informal_proof_text": "Assume 0 <= a and 0 <= b. It suffices to derive 0 <= a + b from these assumptions.",
                        "display_label": "Main theorem",
                    },
                    {
                        "id": "n2",
                        "title": "Assumptions",
                        "informal_statement": "0 <= a and 0 <= b.",
                        "informal_proof_text": "These are the hypotheses assumed in the proof.",
                        "display_label": "Hypotheses",
                    },
                    {
                        "id": "n3",
                        "title": "Sum of nonnegative reals is nonnegative",
                        "informal_statement": "If 0 <= x and 0 <= y, then 0 <= x + y.",
                        "informal_proof_text": "This is the standard arithmetic lemma invoked in the proof.",
                        "display_label": "Arithmetic lemma",
                    },
                    {
                        "id": "n4",
                        "title": "Derived nonnegativity of the sum",
                        "informal_statement": "0 <= a + b.",
                        "informal_proof_text": "Apply the standard lemma on sums of nonnegative reals to the assumptions 0 <= a and 0 <= b.",
                        "display_label": "Conclusion under assumptions",
                    },
                ],
                "edges": [
                    {"source_id": "n1", "target_id": "n4"},
                    {"source_id": "n4", "target_id": "n2"},
                    {"source_id": "n4", "target_id": "n3"},
                ],
            }
        ]
    )

    graph = extract_proof_graph(
        backend=backend,
        theorem_statement="If a and b are nonnegative, then a + b is nonnegative.",
        raw_proof_text=(
            "Assume 0 <= a and 0 <= b. By the standard arithmetic lemma that sums of "
            "nonnegative reals are nonnegative, we obtain 0 <= a + b."
        ),
        theorem_title_hint="Nonnegative sum",
    )

    assert len(graph.nodes) <= 2
    assert graph.root_node_id == "n1"
    assert all(node.title != "Assumptions" for node in graph.nodes)
    assert all("Conclusion under assumptions" != (node.display_label or "") for node in graph.nodes)
    assert all("derived nonnegativity" not in node.title.lower() for node in graph.nodes)
    assert {node.id for node in graph.nodes} in ({"n1"}, {"n1", "n3"})


def test_build_candidate_selection_request_uses_graph_json() -> None:
    request = build_candidate_selection_request(build_graph())

    assert request.task_name == "select_candidates"
    assert request.json_schema["type"] == "object"
    assert '"theorem_title": "Toy theorem"' in request.prompt
    assert "inferential burden" in request.prompt


def test_select_formalization_candidates_updates_matching_nodes() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "candidates": [
                    {
                        "node_id": "n2",
                        "priority": 3,
                        "rationale": "Self-contained implication lemma.",
                    }
                ]
            }
        ]
    )

    updated = select_formalization_candidates(backend=backend, graph=build_graph())

    assert updated.nodes[0].status == "informal"
    assert updated.nodes[1].status == "candidate_formal"
    assert updated.nodes[1].formalization_priority == 3


def test_simplify_proof_graph_preserves_protected_candidate_node_ids() -> None:
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="If A then B.",
        root_node_id="n1",
        nodes=[
            ProofNode(
                id="n1",
                title="Main claim",
                informal_statement="If A then B.",
                informal_proof_text="Use n2.",
            ),
            ProofNode(
                id="n2",
                title="Derived conclusion",
                informal_statement="B.",
                informal_proof_text="Conclusion under assumptions.",
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="Planner chose this node.",
            ),
        ],
        edges=[ProofEdge(source_id="n1", target_id="n2")],
    )

    simplified = simplify_proof_graph(graph, protected_node_ids={"n2"})

    assert any(node.id == "n2" for node in simplified.nodes)


def test_select_formalization_candidates_rejects_unknown_node_ids() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "candidates": [
                    {"node_id": "missing", "priority": 2, "rationale": "No such node."}
                ]
            }
        ]
    )

    with pytest.raises(ValueError, match="unknown node ids"):
        select_formalization_candidates(backend=backend, graph=build_graph())


def test_select_formalization_candidates_promotes_more_technical_sibling_when_needed() -> None:
    graph = ProofGraph(
        theorem_title="Reduced Glassey blow-up argument",
        theorem_statement="Blow-up theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Main theorem",
                informal_statement="The solution blows up in finite time.",
                informal_proof_text="Use the variance collapse node and the gradient lower bound node.",
            ),
            ProofNode(
                id="n1",
                title="Variance collapse",
                informal_statement=(
                    "There exists a finite time \\(T>0\\) such that \\(V(T)=0\\), and \\(V(t)\\to 0\\) as \\(t\\uparrow T\\)."
                ),
                informal_proof_text="This is the dynamical consequence of the negative second derivative.",
            ),
            ProofNode(
                id="n2",
                title="Gradient lower bound",
                informal_statement=(
                    "\\[\n"
                    "M^2\\le \\frac{2}{d}\\sqrt{V(t)}\\,\\|\\nabla u(t)\\|_{L^2}.\n"
                    "\\]"
                ),
                informal_proof_text=(
                    "Combine the weighted inequality, mass conservation, and "
                    "\\(\\|xu(t)\\|_{L^2}^2=V(t)\\)."
                ),
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
                "candidates": [
                    {
                        "node_id": "n1",
                        "priority": 1,
                        "rationale": "Chosen by the backend as a broad consequence.",
                    }
                ]
            }
        ]
    )

    updated = select_formalization_candidates(backend=backend, graph=graph)

    selected = next(node for node in updated.nodes if node.status == "candidate_formal")
    assert selected.id == "n2"
    assert "high-yield local island" in (selected.formalization_rationale or "")


def test_refine_candidate_nodes_adds_generic_local_consequence() -> None:
    graph = ProofGraph(
        theorem_title="Toy estimate theorem",
        theorem_statement="Main theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Main argument",
                informal_statement="The desired conclusion follows.",
                informal_proof_text=(
                    "Apply the reusable estimate to the current quantities. Substituting the local identities, we obtain\n"
                    "\\[\n"
                    "Q(t)\\le C\\sqrt{R(t)}\\,S(t).\n"
                    "\\]"
                ),
            ),
            ProofNode(
                id="n1",
                title="Background step",
                informal_statement="R(t) tends to zero.",
                informal_proof_text="This is the surrounding informal backbone.",
            ),
            ProofNode(
                id="n2",
                title="Reusable estimate",
                informal_statement=(
                    "For sufficiently regular inputs,\n"
                    "\\[\n"
                    "A\\le C B.\n"
                    "\\]"
                ),
                informal_proof_text=(
                    "This is the broader supporting estimate. Applying it in the present situation gives\n"
                    "\\[\n"
                    "Q(t)\\le C\\sqrt{R(t)}\\,S(t).\n"
                    "\\]"
                ),
                status="formal_failed",
                formalization_priority=1,
                formalization_rationale="Broad estimate node.",
                formal_artifact=make_failed_artifact(),
            ),
        ],
        edges=[
            ProofEdge(source_id="n0", target_id="n1"),
            ProofEdge(source_id="n0", target_id="n2"),
        ],
    )

    refined = refine_candidate_nodes(graph, source_node_id="n2")

    assert len(refined.nodes) == 4
    assert len([node for node in refined.nodes if node.status == "candidate_formal"]) == 1
    refined_node = next(
        node
        for node in refined.nodes
        if node.id not in {"n0", "n1", "n2"} and node.status == "candidate_formal"
    )
    assert "Q(t)\\le C\\sqrt{R(t)}\\,S(t)." in refined_node.informal_statement
    assert refined_node.title == "Local estimate"
    assert refined_node.display_label == "Refined estimate"
    assert any(edge.source_id == "n2" and edge.target_id == refined_node.id for edge in refined.edges)
    assert any(edge.label == "refined_from" and edge.source_id == "n2" and edge.target_id == refined_node.id for edge in refined.edges)
    assert any(edge.source_id == refined_node.id and edge.target_id == "n2" for edge in refined.edges)
    original_candidate = next(node for node in refined.nodes if node.id == "n2")
    assert original_candidate.status == "formal_failed"


def test_refine_candidate_nodes_prefers_backend_proposal_when_available() -> None:
    graph = ProofGraph(
        theorem_title="Toy estimate theorem",
        theorem_statement="Main theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Main argument",
                informal_statement="The desired conclusion follows.",
                informal_proof_text="Use the refined local claim n2.",
            ),
            ProofNode(
                id="n2",
                title="Reusable estimate",
                informal_statement=(
                    "For sufficiently regular inputs,\n"
                    "\\[\n"
                    "A\\le C B.\n"
                    "\\]\n"
                    "Applying it in the present situation gives\n"
                    "\\[\n"
                    "Q(t)\\le C\\sqrt{R(t)}\\,S(t).\n"
                    "\\]"
                ),
                informal_proof_text=(
                    "The broader estimate is available, and the real downstream claim is the concrete bound "
                    "\\(Q(t)\\le C\\sqrt{R(t)}\\,S(t)\\)."
                ),
                status="formal_failed",
                formalization_priority=1,
                formalization_rationale="Broad estimate node.",
                formal_artifact=make_failed_artifact(),
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n2")],
    )
    backend = MockBackend(
        queued_payloads=[
            {
                "proposals": [
                    {
                        "title": "Concrete downstream estimate",
                        "display_label": "Backend refined claim",
                        "informal_statement": (
                            "For the present quantities,\n"
                            "\\[\n"
                            "Q(t)\\le C\\sqrt{R(t)}\\,S(t).\n"
                            "\\]"
                        ),
                        "informal_proof_text": (
                            "Combine the broader estimate with the local identities already established in the parent argument."
                        ),
                        "rationale": (
                            "This is the exact downstream estimate the parent proof uses, and it is narrower than the broad reusable source estimate."
                        ),
                    }
                ]
            }
        ]
    )

    refined = refine_candidate_nodes(graph, backend=backend, source_node_id="n2")

    refined_node = next(node for node in refined.nodes if node.status == "candidate_formal")
    assert refined_node.title == "Concrete downstream estimate"
    assert refined_node.display_label == "Backend refined claim"
    assert refined_node.informal_statement.startswith("For the present quantities")
    assert "Combine the broader estimate" in refined_node.informal_proof_text
    assert refined_node.informal_proof_text.endswith("parent argument.")
    assert len(backend.requests) == 1


def test_refine_candidate_nodes_leaves_already_concrete_candidate_alone() -> None:
    graph = ProofGraph(
        theorem_title="Toy estimate theorem",
        theorem_statement="Main theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Main claim",
                informal_statement="The desired conclusion follows.",
                informal_proof_text="Use n2.",
            ),
            ProofNode(
                id="n2",
                title="Local estimate",
                informal_statement=(
                    "At this step,\n"
                    "\\[\n"
                    "Q(t)\\le C\\sqrt{R(t)}\\,S(t).\n"
                    "\\]"
                ),
                informal_proof_text="This is already the narrow local bound.",
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="Good local island.",
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n2")],
    )

    assert refine_candidate_nodes(graph, source_node_id="n1") == graph


def test_refine_candidate_nodes_can_extract_inline_math_consequence() -> None:
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="Main theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Main argument",
                informal_statement="The desired conclusion follows.",
                informal_proof_text=(
                    "Applying the supporting estimate, we get \\(Q(t)\\le C\\sqrt{R(t)}\\,S(t)\\). "
                    "This gives the final conclusion."
                ),
            ),
            ProofNode(
                id="n2",
                title="Supporting estimate",
                informal_statement=(
                    "For sufficiently regular inputs, \\(A\\le C B\\)."
                ),
                informal_proof_text="Broad supporting estimate with a concrete inline application: \\(Q(t)\\le C\\sqrt{R(t)}\\,S(t)\\).",
                status="formal_failed",
                formalization_priority=1,
                formalization_rationale="Broad estimate node.",
                formal_artifact=make_failed_artifact(),
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n2")],
    )

    refined = refine_candidate_nodes(graph, source_node_id="n2")

    refined_node = next(
        node
        for node in refined.nodes
        if node.id not in {"n0", "n2"} and node.status == "candidate_formal"
    )
    assert "Q(t)\\le C\\sqrt{R(t)}\\,S(t)" in refined_node.informal_statement


def test_refine_candidate_nodes_preserves_reduced_glassey_local_island() -> None:
    graph = ProofGraph(
        theorem_title="Reduced Glassey blow-up argument",
        theorem_statement="Blow-up theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Finite-time blow-up from vanishing variance",
                informal_statement="The solution blows up in finite time.",
                informal_proof_text=(
                    "Apply the weighted inequality with \\(f=u(t)\\). Using mass conservation and the definition "
                    "of the variance, we obtain\n"
                    "\\[\n"
                    "M^2\\le \\frac{2}{d}\\sqrt{V(t)}\\,\\|\\nabla u(t)\\|_{L^2}.\n"
                    "\\]\n"
                    "By the child claim, \\(V(t)\\to 0\\) at finite time, so blow-up follows."
                ),
            ),
            ProofNode(
                id="n1",
                title="Mass-variance-gradient estimate",
                informal_statement=(
                    "For every time of existence,\n"
                    "\\[\n"
                    "M^2\\le \\frac{2}{d}\\sqrt{V(t)}\\,\\|\\nabla u(t)\\|_{L^2}.\n"
                    "\\]"
                ),
                informal_proof_text="This is already the intended local formal island.",
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="Good local island.",
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1")],
    )

    assert refine_candidate_nodes(graph, source_node_id="n1") == graph


def test_refine_candidate_nodes_preserves_full_glassey_local_island_without_special_case() -> None:
    graph = ProofGraph(
        theorem_title="Full Glassey blow-up proof",
        theorem_statement="Blow-up theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Glassey blow-up from negative energy",
                informal_statement="The solution blows up in finite time.",
                informal_proof_text=(
                    "By the virial argument, the variance tends to zero at finite time. Next apply the weighted "
                    "inequality to \\(f=u(t)\\); hence\n"
                    "\\[\n"
                    "M^2\\le \\frac{2}{d}\\sqrt{V(t)}\\,\\|\\nabla u(t)\\|_{L^2}.\n"
                    "\\]\n"
                    "As \\(t\\uparrow T\\), this forces blow-up."
                ),
            ),
            ProofNode(
                id="n1",
                title="Virial identity",
                informal_statement="V''(t)=16E(u_0)<0.",
                informal_proof_text="This is the PDE backbone.",
            ),
            ProofNode(
                id="n2",
                title="Weighted \\(L^2\\) inequality",
                informal_statement=(
                    "For sufficiently regular \\(f\\), "
                    "\\[\\|f\\|_{L^2}^2\\le \\frac{2}{d}\\|xf\\|_{L^2}\\|\\nabla f\\|_{L^2}.\\]\n"
                    "Applied to \\(f=u(t)\\), this gives\n"
                    "\\[\n"
                    "M^2\\le \\frac{2}{d}\\sqrt{V(t)}\\,\\|\\nabla u(t)\\|_{L^2}.\n"
                    "\\]"
                ),
                informal_proof_text="This is the broader local estimate together with the concrete application used downstream.",
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="Broad technical estimate.",
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1"), ProofEdge(source_id="n0", target_id="n2")],
    )

    refined = refine_candidate_nodes(graph, source_node_id="n2")

    candidates = [node for node in refined.nodes if node.status == "candidate_formal"]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert "M^2\\le \\frac{2}{d}\\sqrt{V(t)}\\,\\|\\nabla u(t)\\|_{L^2}" in candidate.informal_statement
    assert "V(t)\\ge 0" not in candidate.informal_statement
    assert len(refined.nodes) <= 4


def test_refine_candidate_nodes_prefers_specialized_application_over_generic_source_theorem() -> None:
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="Main theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Main argument",
                informal_statement="The conclusion follows.",
                informal_proof_text="Use the weighted estimate in the concrete setting.",
            ),
            ProofNode(
                id="n1",
                title="Weighted inequality",
                informal_statement=(
                    "For sufficiently regular \\(f\\),\n"
                    "\\[\n"
                    "\\|f\\|^2\\le C\\|xf\\|\\|\\nabla f\\|.\n"
                    "\\]\n"
                    "Applied to \\(f=u(t)\\), this gives\n"
                    "\\[\n"
                    "M^2\\le C\\sqrt{V(t)}\\,G(t).\n"
                    "\\]"
                ),
                informal_proof_text="A generic source estimate together with the concrete application used downstream.",
                status="formal_failed",
                formalization_priority=1,
                formalization_rationale="Backend picked the broad estimate.",
                formal_artifact=make_failed_artifact(),
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1")],
    )

    refined = refine_candidate_nodes(graph, source_node_id="n1")

    candidate = next(node for node in refined.nodes if node.status == "candidate_formal")
    assert "M^2\\le C\\sqrt{V(t)}\\,G(t)" in candidate.informal_statement
    assert "\\|f\\|^2\\le C\\|xf\\|\\|\\nabla f\\|" not in candidate.informal_statement


def test_refine_candidate_nodes_rejects_trivial_nonnegativity_when_stronger_estimate_exists() -> None:
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="Main theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Main claim",
                informal_statement="The desired conclusion follows.",
                informal_proof_text="Use n1.",
            ),
            ProofNode(
                id="n1",
                title="Broad local argument",
                informal_statement=(
                    "For sufficiently regular data,\n"
                    "\\[\n"
                    "A\\le C B.\n"
                    "\\]\n"
                    "Applied in the current setting, this yields\n"
                    "\\[\n"
                    "M^2\\le C\\sqrt{V(t)}\\,G(t).\n"
                    "\\]"
                ),
                informal_proof_text=(
                    "By definition one also has \\(V(t)\\ge 0\\), but the real downstream estimate is "
                    "\\(M^2\\le C\\sqrt{V(t)}\\,G(t)\\)."
                ),
                status="formal_failed",
                formalization_priority=1,
                formalization_rationale="Broad local argument.",
                formal_artifact=make_failed_artifact(),
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1")],
    )

    refined = refine_candidate_nodes(graph, source_node_id="n1")

    refined_node = next(node for node in refined.nodes if node.status == "candidate_formal")
    assert "M^2\\le C\\sqrt{V(t)}\\,G(t)" in refined_node.informal_statement
    assert "V(t)\\ge 0" not in refined_node.informal_statement


def test_refine_candidate_nodes_does_not_refine_to_bare_nonnegativity_only() -> None:
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="Main theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Main claim",
                informal_statement="The desired conclusion follows.",
                informal_proof_text="Use n1.",
            ),
            ProofNode(
                id="n1",
                title="Broad local argument",
                informal_statement="For sufficiently regular inputs, \\[A\\le C B.\\]",
                informal_proof_text="By definition one has \\(V(t)\\ge 0\\).",
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="Broad local argument.",
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1")],
    )

    assert refine_candidate_nodes(graph) == graph
