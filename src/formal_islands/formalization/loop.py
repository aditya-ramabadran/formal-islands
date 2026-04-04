"""Bounded single-node formalization loop."""

from __future__ import annotations

from dataclasses import dataclass

from formal_islands.backends import StructuredBackend
from formal_islands.formalization.lean import LeanVerifier
from formal_islands.formalization.pipeline import request_node_formalization
from formal_islands.models import FormalArtifact, ProofGraph, VerificationResult


@dataclass(frozen=True)
class FormalizationOutcome:
    """Result summary for a single-node bounded formalization run."""

    graph: ProofGraph
    node_id: str
    artifact: FormalArtifact


def formalize_candidate_node(
    *,
    backend: StructuredBackend,
    verifier: LeanVerifier,
    graph: ProofGraph,
    node_id: str,
    max_attempts: int = 2,
) -> FormalizationOutcome:
    """Attempt to formalize and verify one candidate node with bounded retries."""

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    attempt_history: list[VerificationResult] = []
    latest_artifact: FormalArtifact | None = None
    latest_feedback: str | None = None

    for attempt_number in range(1, max_attempts + 1):
        artifact = request_node_formalization(
            backend=backend,
            graph=graph,
            node_id=node_id,
            compiler_feedback=latest_feedback,
        )
        verification = verifier.verify_code(
            lean_code=artifact.lean_code,
            node_id=node_id,
            attempt_number=attempt_number,
        )
        attempt_history.append(verification)
        latest_artifact = artifact.model_copy(
            update={
                "verification": verification,
                "attempt_history": attempt_history.copy(),
            }
        )

        if verification.status == "verified":
            updated_graph = _update_node(graph, node_id, "formal_verified", latest_artifact)
            return FormalizationOutcome(graph=updated_graph, node_id=node_id, artifact=latest_artifact)

        latest_feedback = "\n\n".join(
            [
                "Compiler feedback from the previous attempt:",
                verification.stderr or "(no stderr)",
                "Stdout from the previous attempt:",
                verification.stdout or "(no stdout)",
            ]
        )

    assert latest_artifact is not None
    updated_graph = _update_node(graph, node_id, "formal_failed", latest_artifact)
    return FormalizationOutcome(graph=updated_graph, node_id=node_id, artifact=latest_artifact)


def _update_node(
    graph: ProofGraph,
    node_id: str,
    status: str,
    artifact: FormalArtifact,
) -> ProofGraph:
    updated_nodes = [
        node.model_copy(update={"status": status, "formal_artifact": artifact})
        if node.id == node_id
        else node
        for node in graph.nodes
    ]
    return graph.model_copy(update={"nodes": updated_nodes})
