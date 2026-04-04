"""Bounded single-node formalization loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from formal_islands.backends import BackendError, StructuredBackend
from formal_islands.formalization.lean import LeanVerifier
from formal_islands.formalization.pipeline import (
    FormalizationFaithfulnessError,
    request_node_formalization,
)
from formal_islands.models import FormalArtifact, ProofGraph, VerificationResult


@dataclass(frozen=True)
class FormalizationOutcome:
    """Result summary for a single-node bounded formalization run."""

    graph: ProofGraph
    node_id: str
    artifact: FormalArtifact


FormalizationUpdateCallback = Callable[[FormalizationOutcome], None]
MAX_TOTAL_FORMALIZATION_ATTEMPTS = 4


def formalize_candidate_node(
    *,
    backend: StructuredBackend,
    verifier: LeanVerifier,
    graph: ProofGraph,
    node_id: str,
    max_attempts: int = MAX_TOTAL_FORMALIZATION_ATTEMPTS,
    on_update: FormalizationUpdateCallback | None = None,
) -> FormalizationOutcome:
    """Attempt to formalize and verify one candidate node with bounded retries."""

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    attempt_limit = min(max_attempts, MAX_TOTAL_FORMALIZATION_ATTEMPTS)
    attempt_history: list[VerificationResult] = []
    latest_artifact: FormalArtifact | None = None
    latest_feedback: str | None = None
    current_graph = graph

    for attempt_number in range(1, attempt_limit + 1):
        try:
            artifact = request_node_formalization(
                backend=backend,
                graph=_graph_for_retry_request(current_graph, node_id),
                node_id=node_id,
                compiler_feedback=latest_feedback,
                previous_lean_code=latest_artifact.lean_code if latest_artifact is not None else None,
            )
        except BackendError as exc:
            verification = VerificationResult(
                status="failed",
                command="backend_request",
                exit_code=None,
                stdout="",
                stderr=str(exc),
                attempt_count=attempt_number,
                artifact_path=None,
            )
            attempt_history.append(verification)
            latest_artifact = _placeholder_failed_artifact(
                node_id=node_id,
                verification=verification,
                attempt_history=attempt_history,
            )
            current_graph = _update_node(current_graph, node_id, "formal_failed", latest_artifact)
            _emit_update(current_graph, node_id, latest_artifact, on_update)
            break
        except FormalizationFaithfulnessError as exc:
            verification = VerificationResult(
                status="failed",
                command="faithfulness_guard",
                exit_code=None,
                stdout="",
                stderr=str(exc),
                attempt_count=attempt_number,
                artifact_path=None,
            )
            attempt_history.append(verification)
            latest_artifact = exc.artifact.model_copy(
                update={
                    "verification": verification,
                    "attempt_history": attempt_history.copy(),
                }
            )
            current_graph = _update_node(current_graph, node_id, "formal_failed", latest_artifact)
            _emit_update(current_graph, node_id, latest_artifact, on_update)
            if attempt_number >= attempt_limit:
                break
            latest_feedback = _build_repair_feedback(
                previous_result=verification,
                extra_guidance=(
                    "The previous theorem was rejected by the faithfulness guard. Stay much closer to the node text."
                ),
            )
            continue

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
        current_graph = _update_node(
            current_graph,
            node_id,
            "formal_verified" if verification.status == "verified" else "formal_failed",
            latest_artifact,
        )
        _emit_update(current_graph, node_id, latest_artifact, on_update)

        if verification.status == "verified":
            return FormalizationOutcome(graph=current_graph, node_id=node_id, artifact=latest_artifact)

        if attempt_number >= attempt_limit or not _is_repairable_failure(verification):
            break

        latest_feedback = _build_repair_feedback(previous_result=verification)

    assert latest_artifact is not None
    return FormalizationOutcome(graph=current_graph, node_id=node_id, artifact=latest_artifact)


def _is_repairable_failure(verification: VerificationResult) -> bool:
    text = f"{verification.stdout}\n{verification.stderr}".lower()
    repairable_markers = (
        "error:",
        "expected token",
        "unknown identifier",
        "unknown constant",
        "type mismatch",
        "application type mismatch",
        "failed to synthesize",
        "invalid field",
        "unsolved goals",
    )
    return any(marker in text for marker in repairable_markers)


def _build_repair_feedback(
    *,
    previous_result: VerificationResult,
    extra_guidance: str | None = None,
) -> str:
    parts = [
        "Compiler feedback from the previous attempt:",
        previous_result.stderr or "(no stderr)",
        "Stdout from the previous attempt:",
        previous_result.stdout or "(no stdout)",
        (
            "Repair guidance: fix the Lean syntax or compiler issue and keep the theorem concrete and faithful to the original node. "
            "Reuse the node's variable names and hypotheses when reasonable. Avoid arbitrary `Type*` parameters, unrelated function "
            "families, or unnecessary higher-order abstraction. Prefer plain Lean syntax that compiles in a scratch file. "
            "Use a short, specific import list that matches the identifiers actually used, and avoid both `import Mathlib` "
            "for tiny local theorems and speculative deep imports that may not exist in the pinned workspace."
        ),
    ]
    if extra_guidance:
        parts.append(extra_guidance)
    return "\n\n".join(parts)


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


def _emit_update(
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
    callback: FormalizationUpdateCallback | None,
) -> None:
    if callback is None:
        return
    callback(FormalizationOutcome(graph=graph, node_id=node_id, artifact=artifact))


def _graph_for_retry_request(graph: ProofGraph, node_id: str) -> ProofGraph:
    return graph.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"status": "candidate_formal"})
                if node.id == node_id
                else node
                for node in graph.nodes
            ]
        }
    )


def _placeholder_failed_artifact(
    *,
    node_id: str,
    verification: VerificationResult,
    attempt_history: list[VerificationResult],
) -> FormalArtifact:
    return FormalArtifact(
        lean_theorem_name=f"{node_id}_backend_failure",
        lean_statement="-- backend did not return a Lean statement",
        lean_code="-- backend did not return Lean code",
        verification=verification,
        attempt_history=attempt_history.copy(),
    )
