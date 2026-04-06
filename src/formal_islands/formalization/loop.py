"""Bounded single-node formalization loop."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable

from formal_islands.backends import AgenticStructuredBackend, BackendError, StructuredBackend
from formal_islands.backends.aristotle import AristotleBackend
from formal_islands.formalization.agentic import (
    recover_agentic_artifact_from_scratch_file,
    request_agentic_formalization,
)
from formal_islands.formalization.aristotle import request_aristotle_formalization
from formal_islands.formalization.lean import LeanVerifier
from formal_islands.formalization.pipeline import (
    FaithfulnessClassification,
    FormalizationFaithfulnessError,
    build_node_coverage_sketch,
    request_coverage_expansion_assessment,
    request_concrete_sublemma_summary,
    request_node_formalization,
)
from formal_islands.models import FormalArtifact, ProofEdge, ProofGraph, ProofNode, VerificationResult
from formal_islands.progress import progress


@dataclass(frozen=True)
class FormalizationOutcome:
    """Result summary for a single-node bounded formalization run."""

    graph: ProofGraph
    node_id: str
    artifact: FormalArtifact


@dataclass(frozen=True)
class MultiFormalizationOutcome:
    """Result summary for a multi-node formalization pass."""

    graph: ProofGraph
    outcomes: list[FormalizationOutcome]


FormalizationUpdateCallback = Callable[[FormalizationOutcome], None]
MAX_TOTAL_FORMALIZATION_ATTEMPTS = 4
FormalizationBackend = StructuredBackend | AristotleBackend


def _progress(message: str) -> None:
    progress(message)


def formalize_candidate_node(
    *,
    backend: FormalizationBackend,
    planning_backend: StructuredBackend | None = None,
    verifier: LeanVerifier,
    graph: ProofGraph,
    node_id: str,
    max_attempts: int = MAX_TOTAL_FORMALIZATION_ATTEMPTS,
    on_update: FormalizationUpdateCallback | None = None,
    mode: str = "auto",
) -> FormalizationOutcome:
    """Attempt to formalize and verify one candidate node with bounded retries."""

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if mode not in {"auto", "agentic", "structured"}:
        raise ValueError("mode must be one of: auto, agentic, structured")

    _progress(f"starting formalization for node {node_id}")
    if isinstance(backend, AristotleBackend):
        return _formalize_candidate_node_aristotle(
            backend=backend,
            planning_backend=planning_backend,
            verifier=verifier,
            graph=graph,
            node_id=node_id,
            max_attempts=max_attempts,
            on_update=on_update,
        )

    if _should_use_agentic_formalization(backend=backend, mode=mode):
        return _formalize_candidate_node_agentic(
            backend=backend,
            planning_backend=planning_backend,
            verifier=verifier,
            graph=graph,
            node_id=node_id,
            on_update=on_update,
        )

    return _formalize_candidate_node_structured(
        backend=backend,
        planning_backend=planning_backend,
        verifier=verifier,
        graph=graph,
        node_id=node_id,
        max_attempts=max_attempts,
        on_update=on_update,
    )


def formalize_candidate_nodes(
    *,
    backend: FormalizationBackend,
    planning_backend: StructuredBackend | None = None,
    verifier: LeanVerifier,
    graph: ProofGraph,
    node_ids: list[str] | None = None,
    max_attempts: int = MAX_TOTAL_FORMALIZATION_ATTEMPTS,
    on_update: FormalizationUpdateCallback | None = None,
    mode: str = "auto",
) -> MultiFormalizationOutcome:
    """Formalize multiple candidate nodes sequentially, reusing the updated graph each time.

    When node_ids is None (auto mode), the loop is dynamic: nodes promoted to
    candidate_formal during the run (e.g. informal parents of a verified refined
    local claim) are discovered and attempted after the original candidates.

    When node_ids is explicitly provided, only those nodes are attempted in order.
    """

    if isinstance(backend, AristotleBackend):
        return _formalize_candidate_nodes_aristotle_parallel(
            backend=backend,
            planning_backend=planning_backend,
            verifier=verifier,
            graph=graph,
            node_ids=node_ids,
            max_attempts=max_attempts,
            on_update=on_update,
            mode=mode,
        )

    current_graph = graph
    outcomes: list[FormalizationOutcome] = []
    attempted_ids: set[str] = set()

    if node_ids is not None:
        # Explicit list: static order, no dynamic discovery.
        for node_id in node_ids:
            current_node = next((n for n in current_graph.nodes if n.id == node_id), None)
            if current_node is None or current_node.status != "candidate_formal":
                continue
            attempted_ids.add(node_id)
            _progress(f"node {node_id}: scheduled from explicit candidate list")
            outcome = formalize_candidate_node(
                backend=backend,
                planning_backend=planning_backend,
                verifier=verifier,
                graph=current_graph,
                node_id=node_id,
                max_attempts=max_attempts,
                on_update=on_update,
                mode=mode,
            )
            current_graph = outcome.graph
            outcomes.append(outcome)
    else:
        # Auto mode: dynamic discovery picks up any newly promoted candidates.
        while True:
            next_node = next(
                (
                    node
                    for node in sorted(
                        current_graph.nodes,
                        key=lambda n: (n.formalization_priority or 999, n.id),
                    )
                    if node.status == "candidate_formal" and node.id not in attempted_ids
                ),
                None,
            )
            if next_node is None:
                break
            attempted_ids.add(next_node.id)
            _progress(f"node {next_node.id}: scheduled from auto candidate discovery")
            outcome = formalize_candidate_node(
                backend=backend,
                planning_backend=planning_backend,
                verifier=verifier,
                graph=current_graph,
                node_id=next_node.id,
                max_attempts=max_attempts,
                on_update=on_update,
                mode=mode,
            )
            current_graph = outcome.graph
            outcomes.append(outcome)

    return MultiFormalizationOutcome(graph=current_graph, outcomes=outcomes)


def _formalize_candidate_node_structured(
    *,
    backend: StructuredBackend,
    planning_backend: StructuredBackend | None,
    verifier: LeanVerifier,
    graph: ProofGraph,
    node_id: str,
    max_attempts: int,
    on_update: FormalizationUpdateCallback | None,
) -> FormalizationOutcome:
    """Existing structured formalization + retry loop."""

    attempt_limit = min(max_attempts, MAX_TOTAL_FORMALIZATION_ATTEMPTS)
    attempt_history: list[VerificationResult] = []
    latest_artifact: FormalArtifact | None = None
    latest_feedback: str | None = None
    current_graph = graph

    for attempt_number in range(1, attempt_limit + 1):
        _progress(f"node {node_id}: structured attempt {attempt_number}/{attempt_limit}")
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
        if latest_artifact.faithfulness_classification == FaithfulnessClassification.CONCRETE_SUBLEMMA:
            _progress(f"node {node_id}: trying bounded coverage expansion for concrete sublemma")
            expanded_artifact = _attempt_structured_coverage_expansion(
                backend=backend,
                planning_backend=planning_backend,
                verifier=verifier,
                graph=current_graph,
                node_id=node_id,
                artifact=latest_artifact,
                attempt_history=attempt_history,
            )
            if expanded_artifact is not None:
                latest_artifact = expanded_artifact
        current_graph = _integrate_successful_formalization(
            graph=current_graph,
            backend=backend,
            planning_backend=planning_backend,
            node_id=node_id,
            artifact=latest_artifact,
            verification_status=verification.status,
        )
        _emit_update(current_graph, node_id, latest_artifact, on_update)

        if verification.status == "verified":
            _progress(
                f"node {node_id}: verified successfully as {latest_artifact.faithfulness_classification}"
            )
            return FormalizationOutcome(graph=current_graph, node_id=node_id, artifact=latest_artifact)

        if attempt_number >= attempt_limit or not _is_repairable_failure(verification):
            break

        _progress(f"node {node_id}: retrying after compiler feedback")
        latest_feedback = _build_repair_feedback(previous_result=verification)

    assert latest_artifact is not None
    return FormalizationOutcome(graph=current_graph, node_id=node_id, artifact=latest_artifact)


def _formalize_candidate_node_aristotle(
    *,
    backend: AristotleBackend,
    planning_backend: StructuredBackend | None,
    verifier: LeanVerifier,
    graph: ProofGraph,
    node_id: str,
    max_attempts: int,
    on_update: FormalizationUpdateCallback | None,
) -> FormalizationOutcome:
    """Project-based formalization loop for Aristotle."""

    attempt_limit = min(max_attempts, 2)
    attempt_history: list[VerificationResult] = []
    latest_artifact: FormalArtifact | None = None
    latest_feedback: str | None = None
    current_graph = graph
    previous_lean_code: str | None = None
    faithfulness_feedback: str | None = None
    scratch_path = verifier.workspace.prepare_worker_file(node_id).resolve()

    for attempt_number in range(1, attempt_limit + 1):
        _progress(f"node {node_id}: structured attempt {attempt_number}/{attempt_limit}")
        try:
            artifact = request_aristotle_formalization(
                backend=backend,
                graph=_graph_for_retry_request(current_graph, node_id),
                node_id=node_id,
                workspace_root=verifier.workspace.root.resolve(),
                scratch_file_path=scratch_path,
                faithfulness_feedback=faithfulness_feedback,
                previous_lean_code=previous_lean_code,
                compiler_feedback=latest_feedback,
            )
        except BackendError as exc:
            verification = VerificationResult(
                status="failed",
                command="backend_request",
                exit_code=None,
                stdout="",
                stderr=str(exc),
                attempt_count=attempt_number,
                artifact_path=str(scratch_path) if scratch_path.exists() else None,
            )
            attempt_history.append(verification)
            latest_artifact = _placeholder_failed_artifact(
                node_id=node_id,
                verification=verification,
                attempt_history=attempt_history,
            )
            current_graph = _update_node(current_graph, node_id, "formal_failed", latest_artifact)
            _emit_update(current_graph, node_id, latest_artifact, on_update)
            if attempt_number >= attempt_limit:
                break
            latest_feedback = _build_repair_feedback(
                previous_result=verification,
                extra_guidance=(
                    "The previous Aristotle submission failed before producing a usable Lean file. "
                    "Revise the theorem and keep it close to the node text."
                ),
            )
            continue
        except FormalizationFaithfulnessError as exc:
            verification = VerificationResult(
                status="failed",
                command="faithfulness_guard",
                exit_code=None,
                stdout="",
                stderr=str(exc),
                attempt_count=attempt_number,
                artifact_path=str(scratch_path) if scratch_path.exists() else None,
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
            _progress(f"node {node_id}: retrying after compiler feedback")
            previous_lean_code = scratch_path.read_text(encoding="utf-8") if scratch_path.exists() else None
            faithfulness_feedback = _build_aristotle_faithfulness_feedback(previous_result=verification)
            continue

        verification = verifier.verify_existing_file(file_path=scratch_path, attempt_number=attempt_number)
        attempt_history.append(verification)
        latest_artifact = artifact.model_copy(
            update={
                "verification": verification,
                "attempt_history": attempt_history.copy(),
            }
        )
        if latest_artifact.faithfulness_classification == FaithfulnessClassification.CONCRETE_SUBLEMMA:
            expanded_artifact = _attempt_aristotle_coverage_expansion(
                backend=backend,
                planning_backend=planning_backend,
                verifier=verifier,
                graph=current_graph,
                node_id=node_id,
                artifact=latest_artifact,
                scratch_path=scratch_path,
                attempt_history=attempt_history,
            )
            if expanded_artifact is not None:
                latest_artifact = expanded_artifact
        current_graph = _integrate_successful_formalization(
            graph=current_graph,
            backend=backend,
            planning_backend=planning_backend,
            node_id=node_id,
            artifact=latest_artifact,
            verification_status=verification.status,
        )
        _emit_update(current_graph, node_id, latest_artifact, on_update)

        if verification.status == "verified":
            return FormalizationOutcome(graph=current_graph, node_id=node_id, artifact=latest_artifact)

        if attempt_number >= attempt_limit or not _is_repairable_failure(verification):
            break

        latest_feedback = _build_repair_feedback(previous_result=verification)
        previous_lean_code = scratch_path.read_text(encoding="utf-8") if scratch_path.exists() else None

    assert latest_artifact is not None
    return FormalizationOutcome(graph=current_graph, node_id=node_id, artifact=latest_artifact)


def _formalize_candidate_node_agentic(
    *,
    backend: AgenticStructuredBackend,
    planning_backend: StructuredBackend | None,
    verifier: LeanVerifier,
    graph: ProofGraph,
    node_id: str,
    on_update: FormalizationUpdateCallback | None,
) -> FormalizationOutcome:
    workspace_root = verifier.workspace.root.resolve()
    scratch_path = verifier.workspace.prepare_worker_file(node_id).resolve()
    attempt_history: list[VerificationResult] = []
    current_graph = graph
    previous_lean_code: str | None = None
    faithfulness_feedback: str | None = None

    for attempt_number in (1, 2):
        _progress(f"node {node_id}: agentic attempt {attempt_number}/2")
        try:
            artifact = request_agentic_formalization(
                backend=backend,
                graph=_graph_for_retry_request(current_graph, node_id),
                node_id=node_id,
                workspace_root=workspace_root,
                scratch_file_path=scratch_path,
                faithfulness_feedback=faithfulness_feedback,
                previous_lean_code=previous_lean_code,
            )
        except BackendError as exc:
            salvaged_artifact = _recover_agentic_backend_failure(
                graph=current_graph,
                node_id=node_id,
                verifier=verifier,
                scratch_path=scratch_path,
                attempt_number=attempt_number,
                error=exc,
                attempt_history=attempt_history,
                backend=backend,
            )
            if salvaged_artifact is not None:
                _progress(f"node {node_id}: recovered usable artifact from backend failure")
                expanded_artifact = (
                    _attempt_agentic_coverage_expansion(
                        backend=backend,
                        planning_backend=planning_backend,
                        verifier=verifier,
                        graph=current_graph,
                        node_id=node_id,
                        artifact=salvaged_artifact,
                        scratch_path=scratch_path,
                        attempt_history=attempt_history,
                    )
                    if salvaged_artifact.faithfulness_classification
                    == FaithfulnessClassification.CONCRETE_SUBLEMMA
                    else None
                )
                if expanded_artifact is not None:
                    salvaged_artifact = expanded_artifact
                updated_graph = _integrate_successful_formalization(
                    graph=current_graph,
                    backend=backend,
                    planning_backend=planning_backend,
                    node_id=node_id,
                    artifact=salvaged_artifact,
                    verification_status=salvaged_artifact.verification.status,
                )
                _emit_update(updated_graph, node_id, salvaged_artifact, on_update)
                _progress(
                    f"node {node_id}: completed with {salvaged_artifact.faithfulness_classification}"
                )
                return FormalizationOutcome(
                    graph=updated_graph,
                    node_id=node_id,
                    artifact=salvaged_artifact,
                )

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
            artifact = _placeholder_failed_artifact(
                node_id=node_id,
                verification=verification,
                attempt_history=attempt_history,
            )
            updated_graph = _update_node(current_graph, node_id, "formal_failed", artifact)
            _emit_update(updated_graph, node_id, artifact, on_update)
            _progress(f"node {node_id}: formalization failed after backend error")
            return FormalizationOutcome(graph=updated_graph, node_id=node_id, artifact=artifact)
        except FormalizationFaithfulnessError as exc:
            verification = VerificationResult(
                status="failed",
                command="faithfulness_guard",
                exit_code=None,
                stdout="",
                stderr=str(exc),
                attempt_count=attempt_number,
                artifact_path=str(scratch_path) if scratch_path.exists() else None,
            )
            attempt_history.append(verification)
            artifact = exc.artifact.model_copy(
                update={
                    "verification": verification,
                    "attempt_history": attempt_history.copy(),
                }
            )
            current_graph = _update_node(current_graph, node_id, "formal_failed", artifact)
            _emit_update(current_graph, node_id, artifact, on_update)
            if attempt_number >= 2:
                _progress(f"node {node_id}: formalization failed after faithfulness guard")
                return FormalizationOutcome(graph=current_graph, node_id=node_id, artifact=artifact)
            _progress(f"node {node_id}: retrying after faithfulness guard feedback")
            previous_lean_code = scratch_path.read_text(encoding="utf-8") if scratch_path.exists() else None
            faithfulness_feedback = _build_agentic_faithfulness_feedback(previous_result=verification)
            continue

        verification = verifier.verify_existing_file(file_path=scratch_path, attempt_number=attempt_number)
        attempt_history.append(verification)
        artifact = artifact.model_copy(
            update={
                "verification": verification,
                "attempt_history": attempt_history.copy(),
            }
        )
        if artifact.faithfulness_classification == FaithfulnessClassification.CONCRETE_SUBLEMMA:
            _progress(f"node {node_id}: trying bounded coverage expansion for concrete sublemma")
            expanded_artifact = _attempt_agentic_coverage_expansion(
                backend=backend,
                planning_backend=planning_backend,
                verifier=verifier,
                graph=current_graph,
                node_id=node_id,
                artifact=artifact,
                scratch_path=scratch_path,
                attempt_history=attempt_history,
            )
            if expanded_artifact is not None:
                artifact = expanded_artifact
        updated_graph = _integrate_successful_formalization(
            graph=current_graph,
            backend=backend,
            planning_backend=planning_backend,
            node_id=node_id,
            artifact=artifact,
            verification_status=verification.status,
        )
        _emit_update(updated_graph, node_id, artifact, on_update)
        _progress(f"node {node_id}: completed with {artifact.faithfulness_classification}")
        return FormalizationOutcome(graph=updated_graph, node_id=node_id, artifact=artifact)

    raise AssertionError("agentic formalization loop should always return within two attempts")


def _attempt_aristotle_coverage_expansion(
    *,
    backend: AristotleBackend,
    planning_backend: StructuredBackend | None,
    verifier: LeanVerifier,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
    scratch_path: Path,
    attempt_history: list[VerificationResult],
) -> FormalArtifact | None:
    if (
        artifact.verification.status != "verified"
        or artifact.faithfulness_classification != FaithfulnessClassification.CONCRETE_SUBLEMMA
    ):
        return None

    upgraded = _maybe_upgrade_concrete_sublemma_to_full_node(
        planning_backend=planning_backend,
        graph=graph,
        node_id=node_id,
        artifact=artifact,
    )
    if upgraded is not None:
        return upgraded

    original_code = artifact.lean_code
    try:
        expanded = request_aristotle_formalization(
            backend=backend,
            graph=_graph_for_retry_request(graph, node_id),
            node_id=node_id,
            workspace_root=verifier.workspace.root.resolve(),
            scratch_file_path=scratch_path,
            faithfulness_feedback=_build_coverage_expansion_feedback(
                node=next(node for node in graph.nodes if node.id == node_id),
                artifact=artifact,
            ),
            previous_lean_code=artifact.lean_code,
            compiler_feedback="Try to expand the verified local core upward toward the parent node.",
        )
    except (BackendError, FormalizationFaithfulnessError):
        scratch_path.write_text(original_code, encoding="utf-8")
        return None

    verification = verifier.verify_existing_file(
        file_path=scratch_path,
        attempt_number=(artifact.verification.attempt_count or 1) + 1,
    )
    if verification.status != "verified":
        scratch_path.write_text(original_code, encoding="utf-8")
        return None
    expanded = expanded.model_copy(
        update={
            "verification": verification,
            "attempt_history": attempt_history.copy() + [verification],
        }
    )
    if expanded.faithfulness_classification == FaithfulnessClassification.FULL_NODE:
        return expanded

    scratch_path.write_text(original_code, encoding="utf-8")
    return None


def _should_use_agentic_formalization(*, backend: StructuredBackend, mode: str) -> bool:
    if mode == "structured":
        return False
    return hasattr(backend, "run_agentic_structured")


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
            "families, unnecessary higher-order abstraction, or a shift to an arbitrary measure-space theorem when the node is concrete. "
            "Preserve the ambient setting when possible. Prefer plain Lean syntax that compiles in a scratch file. "
            "Use a short, specific import list that matches the identifiers actually used, and avoid both `import Mathlib` "
            "for tiny local theorems and speculative deep imports that may not exist in the pinned workspace."
        ),
    ]
    if extra_guidance:
        parts.append(extra_guidance)
    return "\n\n".join(parts)


def _build_agentic_faithfulness_feedback(*, previous_result: VerificationResult) -> str:
    return "\n\n".join(
        [
            "Faithfulness feedback from the previous agentic attempt:",
            previous_result.stderr or "(no faithfulness message)",
            (
                "Revise the current scratch file in place. Stay closer to the target node's concrete mathematical "
                "setting, variables, hypotheses, and local inferential role."
            ),
            (
                "Do not introduce arbitrary `Type*`, arbitrary measures, arbitrary Hilbert or inner-product spaces, "
                "or unrelated families of functions unless the node itself explicitly requires that abstraction."
            ),
            (
                "Keep the revised Lean file syntactically conservative: prefer ASCII identifiers in declarations, "
                "use names like `lambda1` instead of Unicode binder names like `λ₁`, and avoid fancy notation when plain Lean syntax works."
            ),
            (
                "If the full node is too hard, replace it with a smaller but still concrete local sublemma in the "
                "same ambient setting rather than a more abstract theorem."
            ),
            (
                "For this revision, explicitly reconsider the most literal whole-node theorem shape first. If you still "
                "cannot make that work, keep the fallback concrete, document the reason for the fallback in the plan file, "
                "and do not jump to a more abstract ambient theorem."
            ),
        ]
    )


def _build_aristotle_faithfulness_feedback(*, previous_result: VerificationResult) -> str:
    return "\n\n".join(
        [
            "Faithfulness feedback from the previous Aristotle attempt:",
            previous_result.stderr or "(no faithfulness message)",
            (
                "Revise the current scratch file in place. Stay closer to the target node's concrete mathematical "
                "setting, variables, hypotheses, and local inferential role."
            ),
            (
                "Do not introduce arbitrary `Type*`, arbitrary measures, arbitrary Hilbert or inner-product spaces, "
                "or unrelated families of functions unless the node itself explicitly requires that abstraction."
            ),
            (
                "If the theorem is too ambitious, replace it with a smaller but still concrete local sublemma in the "
                "same ambient setting rather than a more abstract theorem."
            ),
            (
                "For this revision, explicitly reconsider the most literal whole-node theorem shape first. If you still "
                "cannot make that work, keep the fallback concrete and do not jump to a more abstract ambient theorem."
            ),
        ]
    )


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


def _integrate_successful_formalization(
    *,
    graph: ProofGraph,
    backend: FormalizationBackend | None,
    planning_backend: StructuredBackend | None,
    node_id: str,
    artifact: FormalArtifact,
    verification_status: str,
) -> ProofGraph:
    if verification_status != "verified":
        return _update_node(graph, node_id, "formal_failed", artifact)

    if artifact.faithfulness_classification == FaithfulnessClassification.FULL_NODE:
        updated = _update_node(graph, node_id, "formal_verified", artifact)
        return _promote_informal_parents_via_uses_edges(updated, node_id)

    if artifact.faithfulness_classification == FaithfulnessClassification.CONCRETE_SUBLEMMA:
        return _promote_concrete_sublemma(
            graph=graph,
            backend=planning_backend if planning_backend is not None else backend,
            parent_node_id=node_id,
            artifact=artifact,
        )

    return _update_node(graph, node_id, "formal_failed", artifact)


def _maybe_upgrade_concrete_sublemma_to_full_node(
    *,
    planning_backend: StructuredBackend | None,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
) -> FormalArtifact | None:
    """Use the planning backend to check whether a concrete sublemma already matches the target."""

    if planning_backend is None:
        return None

    try:
        assessment = request_coverage_expansion_assessment(
            backend=planning_backend,
            graph=_graph_for_retry_request(graph, node_id),
            node_id=node_id,
            artifact=artifact,
        )
    except BackendError:
        return None

    if not assessment.already_matches_target:
        return None

    _progress(
        f"node {node_id}: planning backend judged the verified theorem already matches the target; "
        "skipping coverage expansion"
    )
    return artifact.model_copy(
        update={
            "faithfulness_classification": FaithfulnessClassification.FULL_NODE,
            "faithfulness_notes": assessment.reason,
        }
    )


def _promote_informal_parents_via_uses_edges(graph: ProofGraph, node_id: str) -> ProofGraph:
    """After a full-node success, promote any informal parent reachable via a 'uses' edge.

    A refined local claim node has an outgoing edge with label 'uses' pointing to
    the broader informal node it was carved out from.  If that parent is still
    informal, elevate it to candidate_formal at priority 3 so the dynamic
    formalize_candidate_nodes loop can attempt it next.

    Guards:
    - Only promotes nodes whose current status is 'informal' (skips candidate_formal,
      formal_verified, formal_failed).
    - Uses 'attempted_ids' in the caller loop to prevent double-attempting.
    """
    parent_ids = [
        edge.target_id
        for edge in graph.edges
        if edge.source_id == node_id and edge.label == "uses"
    ]
    if not parent_ids:
        return graph

    updated_nodes = []
    promoted_any = False
    for node in graph.nodes:
        if node.id in parent_ids and node.status == "informal":
            updated_nodes.append(
                node.model_copy(
                    update={
                        "status": "candidate_formal",
                        "formalization_priority": 3,
                        "formalization_rationale": (
                            f"Promoted after verified child '{node_id}' certified a local core; "
                            "attempting broader parent coverage."
                        ),
                    }
                )
            )
            promoted_any = True
        else:
            updated_nodes.append(node)

    if not promoted_any:
        return graph
    return graph.model_copy(update={"nodes": updated_nodes})


def _promote_concrete_sublemma(
    *,
    graph: ProofGraph,
    backend: StructuredBackend | None,
    parent_node_id: str,
    artifact: FormalArtifact,
) -> ProofGraph:
    parent = next(node for node in graph.nodes if node.id == parent_node_id)
    child_id = _fresh_support_node_id(graph, parent_node_id)
    child_title = f"Certified local core for {parent.title}"
    child_statement, child_proof = _build_concrete_sublemma_text(
        graph=graph,
        backend=backend,
        parent_node_id=parent_node_id,
        artifact=artifact,
    )

    support_node = ProofNode(
        id=child_id,
        title=child_title,
        informal_statement=child_statement,
        informal_proof_text=child_proof,
        status="formal_verified",
        display_label="Certified core",
        formal_artifact=artifact,
    )

    updated_nodes: list[ProofNode] = []
    for node in graph.nodes:
        if node.id == parent_node_id:
            updated_nodes.append(
                node.model_copy(
                    update={
                        "status": "informal",
                        "formalization_priority": None,
                        "formalization_rationale": None,
                        "formal_artifact": None,
                    }
                )
            )
        else:
            updated_nodes.append(node)
    updated_nodes.append(support_node)

    updated_edges = list(graph.edges)
    updated_edges.append(
        ProofEdge(
            source_id=child_id,
            target_id=parent_node_id,
            label="formal_sublemma_for",
            explanation=(
                "This verified Lean theorem certifies a narrower concrete local core used inside the parent informal step."
            ),
        )
    )
    return graph.model_copy(update={"nodes": updated_nodes, "edges": updated_edges})


def _merge_formalization_outcome(
    *,
    current_graph: ProofGraph,
    batch_base_graph: ProofGraph,
    updated_graph: ProofGraph,
) -> ProofGraph:
    """Merge one batch result as a diff against the shared batch snapshot."""

    base_nodes = {node.id: node for node in batch_base_graph.nodes}
    current_nodes = {node.id: node for node in current_graph.nodes}
    for node in updated_graph.nodes:
        base_node = base_nodes.get(node.id)
        if base_node is None or node != base_node:
            current_nodes[node.id] = node

    base_edges = {
        (edge.source_id, edge.target_id, edge.label, edge.explanation)
        for edge in batch_base_graph.edges
    }
    current_edges = list(current_graph.edges)
    seen_edges = {
        (edge.source_id, edge.target_id, edge.label, edge.explanation)
        for edge in current_edges
    }
    for edge in updated_graph.edges:
        edge_key = (edge.source_id, edge.target_id, edge.label, edge.explanation)
        if edge_key in base_edges or edge_key in seen_edges:
            continue
        current_edges.append(edge)
        seen_edges.add(edge_key)

    return current_graph.model_copy(update={"nodes": list(current_nodes.values()), "edges": current_edges})


def _attempt_structured_coverage_expansion(
    *,
    backend: StructuredBackend,
    planning_backend: StructuredBackend | None,
    verifier: LeanVerifier,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
    attempt_history: list[VerificationResult],
) -> FormalArtifact | None:
    if (
        artifact.verification.status != "verified"
        or artifact.faithfulness_classification != FaithfulnessClassification.CONCRETE_SUBLEMMA
    ):
        return None

    upgraded = _maybe_upgrade_concrete_sublemma_to_full_node(
        planning_backend=planning_backend,
        graph=graph,
        node_id=node_id,
        artifact=artifact,
    )
    if upgraded is not None:
        return upgraded

    _progress(f"node {node_id}: running structured coverage expansion")
    try:
        expanded = request_node_formalization(
            backend=backend,
            graph=_graph_for_retry_request(graph, node_id),
            node_id=node_id,
            compiler_feedback=_build_coverage_expansion_feedback(
                node=next(node for node in graph.nodes if node.id == node_id),
                artifact=artifact,
            ),
            previous_lean_code=artifact.lean_code,
        )
    except (BackendError, FormalizationFaithfulnessError):
        return None

    verification = verifier.verify_code(
        lean_code=expanded.lean_code,
        node_id=node_id,
        attempt_number=(artifact.verification.attempt_count or 1) + 1,
    )
    if verification.status != "verified":
        return None
    expanded = expanded.model_copy(
        update={
            "verification": verification,
            "attempt_history": attempt_history.copy() + [verification],
        }
    )
    if expanded.faithfulness_classification == FaithfulnessClassification.FULL_NODE:
        return expanded
    return None


def _attempt_agentic_coverage_expansion(
    *,
    backend: AgenticStructuredBackend,
    planning_backend: StructuredBackend | None,
    verifier: LeanVerifier,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
    scratch_path: Path,
    attempt_history: list[VerificationResult],
) -> FormalArtifact | None:
    if (
        artifact.verification.status != "verified"
        or artifact.faithfulness_classification != FaithfulnessClassification.CONCRETE_SUBLEMMA
    ):
        return None

    upgraded = _maybe_upgrade_concrete_sublemma_to_full_node(
        planning_backend=planning_backend,
        graph=graph,
        node_id=node_id,
        artifact=artifact,
    )
    if upgraded is not None:
        return upgraded

    _progress(f"node {node_id}: running agentic coverage expansion")
    original_code = artifact.lean_code
    try:
        expanded = request_agentic_formalization(
            backend=backend,
            graph=_graph_for_retry_request(graph, node_id),
            node_id=node_id,
            workspace_root=verifier.workspace.root.resolve(),
            scratch_file_path=scratch_path,
            faithfulness_feedback=_build_coverage_expansion_feedback(
                node=next(node for node in graph.nodes if node.id == node_id),
                artifact=artifact,
            ),
            previous_lean_code=artifact.lean_code,
        )
    except (BackendError, FormalizationFaithfulnessError):
        scratch_path.write_text(original_code, encoding="utf-8")
        return None

    verification = verifier.verify_existing_file(
        file_path=scratch_path,
        attempt_number=(artifact.verification.attempt_count or 1) + 1,
    )
    if verification.status != "verified":
        scratch_path.write_text(original_code, encoding="utf-8")
        return None
    expanded = expanded.model_copy(
        update={
            "verification": verification,
            "attempt_history": attempt_history.copy() + [verification],
        }
    )
    if expanded.faithfulness_classification == FaithfulnessClassification.FULL_NODE:
        return expanded

    scratch_path.write_text(original_code, encoding="utf-8")
    return None
def _build_concrete_sublemma_text(
    *,
    graph: ProofGraph,
    backend: StructuredBackend | None,
    parent_node_id: str,
    artifact: FormalArtifact,
) -> tuple[str, str]:
    default_statement = (
        "Lean verifies a narrower concrete local core supporting this parent step. "
        "See the attached Lean statement and code for the exact certified sublemma."
    )
    prefix = (
        f"This node records a verified supporting sublemma extracted from the formalization of parent node "
        f"'{parent_node_id}'. "
    )
    default_proof = (
        prefix
        + "It certifies part of the parent's local proof burden without claiming to cover the full informal node."
    )
    if backend is None or not hasattr(backend, "run_structured"):
        return default_statement, default_proof
    try:
        summary = request_concrete_sublemma_summary(
            backend=backend,
            graph=graph,
            parent_node_id=parent_node_id,
            artifact=artifact,
        )
    except BackendError:
        return default_statement, default_proof
    return (
        summary.informal_statement,
        prefix + summary.informal_proof_text,
    )


def _formalize_candidate_nodes_aristotle_parallel(
    *,
    backend: AristotleBackend,
    planning_backend: StructuredBackend | None,
    verifier: LeanVerifier,
    graph: ProofGraph,
    node_ids: list[str] | None,
    max_attempts: int,
    on_update: FormalizationUpdateCallback | None,
    mode: str,
) -> MultiFormalizationOutcome:
    current_graph = graph
    outcomes: list[FormalizationOutcome] = []
    attempted_ids: set[str] = set()

    while True:
        batch_base_graph = current_graph
        if node_ids is not None:
            batch_nodes = [
                node
                for node_id in node_ids
                if node_id not in attempted_ids
                for node in current_graph.nodes
                if node.id == node_id and node.status == "candidate_formal"
            ]
        else:
            batch_nodes = [
                node
                for node in sorted(
                    current_graph.nodes,
                    key=lambda n: (n.formalization_priority or 999, n.id),
                )
                if node.status == "candidate_formal" and node.id not in attempted_ids
            ]
        if not batch_nodes:
            break

        _progress(
            "Aristotle batch submitting "
            f"{len(batch_nodes)} candidate node(s): " + ", ".join(node.id for node in batch_nodes)
        )
        batch_outcomes: list[FormalizationOutcome] = []
        with ThreadPoolExecutor(max_workers=len(batch_nodes)) as executor:
            future_to_node = {
                executor.submit(
                    formalize_candidate_node,
                    backend=backend,
                    planning_backend=planning_backend,
                    verifier=verifier,
                    graph=batch_base_graph,
                    node_id=node.id,
                    max_attempts=max_attempts,
                    on_update=None,
                    mode=mode,
                ): node.id
                for node in batch_nodes
            }
            for future in as_completed(future_to_node):
                batch_outcomes.append(future.result())

        batch_order = {node.id: index for index, node in enumerate(batch_nodes)}
        for outcome in sorted(batch_outcomes, key=lambda item: batch_order.get(item.node_id, 999)):
            current_graph = _merge_formalization_outcome(
                current_graph=current_graph,
                batch_base_graph=batch_base_graph,
                updated_graph=outcome.graph,
            )
            attempted_ids.add(outcome.node_id)
            outcomes.append(outcome)
            _emit_update(current_graph, outcome.node_id, outcome.artifact, on_update)
            _progress(
                f"node {outcome.node_id}: completed with {outcome.artifact.faithfulness_classification}"
            )

        if node_ids is not None:
            break

    return MultiFormalizationOutcome(graph=current_graph, outcomes=outcomes)


def _build_coverage_expansion_feedback(*, node: ProofNode, artifact: FormalArtifact) -> str:
    sketch = build_node_coverage_sketch(node)
    component_lines = "\n".join(
        f"- [{component.kind}] {component.text}" for component in sketch.components
    ) or "- Broaden coverage toward the full node."
    return "\n\n".join(
        [
            "Coverage expansion follow-up:",
            (
                "A verified Lean theorem was accepted as a narrower concrete local core, not as full-node coverage. "
                "Continue from the already verified code and try to enlarge coverage upward while staying in the same concrete setting."
            ),
            "Coverage sketch:",
            json.dumps(
                {
                    "summary": sketch.summary,
                    "components": [
                        {"kind": component.kind, "text": component.text}
                        for component in sketch.components
                    ],
                },
                indent=2,
            ),
            "Currently verified Lean theorem:",
            artifact.lean_statement,
            "Broader target node:",
            f"{node.title}: {node.informal_statement}",
            (
                "Aim first at the closest broader theorem that still directly mirrors the parent node, preserving the "
                "same ambient setting, symbols, quantities, and inferential role."
            ),
            (
                "Do not switch to a more abstract ambient theorem. Build upward from the verified core toward adjacent "
                "missing steps in the same local argument."
            ),
            "Potential missing substeps from the parent node:",
            component_lines,
        ]
    )


def _node_coverage_targets(node: ProofNode) -> list[str]:
    text = f"{node.informal_statement}\n{node.informal_proof_text}"
    clauses = re.split(r"(?<=[.;])\s+|\n+", text)
    targets: list[str] = []
    for clause in clauses:
        cleaned = clause.strip()
        lowered = cleaned.lower()
        if not cleaned:
            continue
        if any(
            word in lowered
            for word in (
                "define",
                "set",
                "write",
                "rewrite",
                "expand",
                "compute",
                "differentiate",
                "derive",
                "show",
                "prove",
                "deduce",
                "conclude",
                "substitute",
                "apply",
                "use",
                "evaluate",
                "normalize",
                "reduce",
                "combine",
                "test",
                "split",
                "identify",
            )
        ) or any(marker in cleaned for marker in ("=", "\\le", "\\ge", "≤", "≥", "∫", "\\int")):
            targets.append(cleaned.rstrip("."))
        if len(targets) >= 4:
            break
    return targets


def _fresh_support_node_id(graph: ProofGraph, parent_node_id: str) -> str:
    existing = {node.id for node in graph.nodes}
    base = f"{parent_node_id}__formal_core"
    candidate = base
    index = 2
    while candidate in existing:
        candidate = f"{base}_{index}"
        index += 1
    return candidate


def _recover_agentic_backend_failure(
    *,
    graph: ProofGraph,
    node_id: str,
    verifier: LeanVerifier,
    scratch_path: Path,
    attempt_number: int,
    error: BackendError,
    attempt_history: list[VerificationResult],
    backend: StructuredBackend | None,
) -> FormalArtifact | None:
    try:
        artifact = recover_agentic_artifact_from_scratch_file(
            graph=graph,
            node_id=node_id,
            scratch_file_path=scratch_path,
        )
    except FormalizationFaithfulnessError:
        return None

    if artifact is None:
        return None

    backend_failure = VerificationResult(
        status="failed",
        command="backend_request",
        exit_code=None,
        stdout="",
        stderr=(
            "Recovered from an agentic backend failure using the scratch file left on disk.\n"
            f"{error}"
        ),
        attempt_count=attempt_number,
        artifact_path=str(scratch_path) if scratch_path.exists() else None,
    )
    attempt_history.append(backend_failure)
    verification = verifier.verify_existing_file(file_path=scratch_path, attempt_number=attempt_number)
    attempt_history.append(verification)
    return artifact.model_copy(
        update={
            "verification": verification,
            "attempt_history": attempt_history.copy(),
        }
    )


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
