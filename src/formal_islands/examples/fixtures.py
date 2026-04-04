"""Small example inputs and validated graph fixtures for tests and demos."""

from __future__ import annotations

from formal_islands.models import (
    FormalArtifact,
    ProofEdge,
    ProofGraph,
    ProofNode,
    VerificationResult,
)


TOY_THEOREM_STATEMENT = "If a and b are nonnegative, then a + b is nonnegative."
TOY_RAW_PROOF = (
    "Assume 0 <= a and 0 <= b. By the standard arithmetic lemma that sums of "
    "nonnegative reals are nonnegative, we obtain 0 <= a + b."
)


def build_example_graph() -> ProofGraph:
    """Return a small mixed formal/informal proof graph fixture."""

    return ProofGraph(
        theorem_title="Nonnegative sum",
        theorem_statement=TOY_THEOREM_STATEMENT,
        root_node_id="n1",
        nodes=[
            ProofNode(
                id="n1",
                title="Main conclusion",
                informal_statement="0 <= a + b.",
                informal_proof_text="Use the arithmetic lemma n2.",
            ),
            ProofNode(
                id="n2",
                title="Arithmetic lemma",
                informal_statement="If 0 <= a and 0 <= b, then 0 <= a + b.",
                informal_proof_text="This is a local technical claim.",
                status="formal_verified",
                formalization_priority=3,
                formalization_rationale="Leaf arithmetic fact.",
                formal_artifact=FormalArtifact(
                    lean_theorem_name="sum_nonneg",
                    lean_statement="0 <= a + b",
                    lean_code="import Mathlib\n\ntheorem sum_nonneg : 0 <= a + b := by\n  nlinarith",
                    verification=VerificationResult(
                        status="verified",
                        command="lake env lean FormalIslands/Generated/n2_attempt_1.lean",
                        exit_code=0,
                        stdout="",
                        stderr="",
                        elapsed_seconds=0.2,
                        attempt_count=1,
                        artifact_path="lean_project/FormalIslands/Generated/n2_attempt_1.lean",
                    ),
                    attempt_history=[
                        VerificationResult(
                            status="verified",
                            command="lake env lean FormalIslands/Generated/n2_attempt_1.lean",
                            exit_code=0,
                            stdout="",
                            stderr="",
                            elapsed_seconds=0.2,
                            attempt_count=1,
                            artifact_path="lean_project/FormalIslands/Generated/n2_attempt_1.lean",
                        )
                    ],
                ),
            ),
        ],
        edges=[ProofEdge(source_id="n1", target_id="n2")],
    )
