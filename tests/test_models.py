from pydantic import ValidationError

from formal_islands.models import (
    FormalArtifact,
    ProofEdge,
    ProofGraph,
    ProofNode,
    ReviewObligation,
    ReviewObligationKind,
    VerificationResult,
)


def build_formal_artifact() -> FormalArtifact:
    return FormalArtifact(
        lean_theorem_name="sum_nonneg",
        lean_statement="0 <= a + b",
        lean_code="theorem sum_nonneg : 0 <= a + b := by admit",
        verification=VerificationResult(
            status="verified",
            command="lake env lean scratch.lean",
            exit_code=0,
            stdout="",
            stderr="",
            elapsed_seconds=0.4,
            attempt_count=1,
            artifact_path="lean_project/FormalIslands/Generated/scratch.lean",
        ),
    )


def test_proof_graph_accepts_valid_dependency_graph() -> None:
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="A implies B.",
        root_node_id="n1",
        nodes=[
            ProofNode(
                id="n1",
                title="Main claim",
                informal_statement="B holds.",
                informal_proof_text="It follows from n2.",
            ),
            ProofNode(
                id="n2",
                title="Supporting claim",
                informal_statement="A implies B.",
                informal_proof_text="By assumption.",
                status="candidate_formal",
                formalization_priority=3,
                formalization_rationale="Local implication step.",
            ),
            ProofNode(
                id="n3",
                title="Formalized claim",
                informal_statement="0 <= a + b.",
                informal_proof_text="This is a standard positivity fact.",
                status="formal_verified",
                formalization_priority=2,
                formalization_rationale="Small arithmetic lemma.",
                formal_artifact=build_formal_artifact(),
            ),
        ],
        edges=[
            ProofEdge(source_id="n1", target_id="n2"),
            ProofEdge(source_id="n1", target_id="n3", label="uses lemma"),
        ],
    )

    assert graph.root_node_id == "n1"
    assert len(graph.nodes) == 3
    assert graph.nodes[2].formal_artifact is not None


def test_proof_graph_rejects_duplicate_node_ids() -> None:
    try:
        ProofGraph(
            theorem_title="Bad graph",
            theorem_statement="Impossible.",
            root_node_id="dup",
            nodes=[
                ProofNode(
                    id="dup",
                    title="First",
                    informal_statement="S1",
                    informal_proof_text="P1",
                ),
                ProofNode(
                    id="dup",
                    title="Second",
                    informal_statement="S2",
                    informal_proof_text="P2",
                ),
            ],
            edges=[],
        )
    except ValidationError as exc:
        assert "duplicate node ids" in str(exc)
    else:
        raise AssertionError("expected duplicate node ids to fail validation")


def test_proof_graph_rejects_unknown_edge_reference() -> None:
    try:
        ProofGraph(
            theorem_title="Bad edge",
            theorem_statement="Impossible.",
            root_node_id="n1",
            nodes=[
                ProofNode(
                    id="n1",
                    title="Root",
                    informal_statement="S1",
                    informal_proof_text="P1",
                )
            ],
            edges=[ProofEdge(source_id="n1", target_id="missing")],
        )
    except ValidationError as exc:
        assert "target_id 'missing'" in str(exc)
    else:
        raise AssertionError("expected unknown edge targets to fail validation")


def test_formal_nodes_require_formal_artifacts() -> None:
    try:
        ProofNode(
            id="n1",
            title="Formal node",
            informal_statement="S",
            informal_proof_text="P",
            status="formal_failed",
        )
    except ValidationError as exc:
        assert "formal_artifact is required" in str(exc)
    else:
        raise AssertionError("expected formal nodes without artifacts to fail validation")


def test_candidate_metadata_must_be_complete() -> None:
    try:
        ProofNode(
            id="n1",
            title="Candidate node",
            informal_statement="S",
            informal_proof_text="P",
            formalization_priority=2,
        )
    except ValidationError as exc:
        assert "must be set together" in str(exc)
    else:
        raise AssertionError("expected partial candidate metadata to fail validation")


def test_review_obligation_requires_kind_text_and_nodes() -> None:
    obligation = ReviewObligation(
        id="review-1",
        kind=ReviewObligationKind.BOUNDARY_INTERFACE_CHECK,
        text="Check that the formal child proves exactly the bound the parent uses.",
        node_ids=["parent", "child"],
    )

    assert obligation.kind == "boundary_interface_check"
    assert obligation.node_ids == ["parent", "child"]
