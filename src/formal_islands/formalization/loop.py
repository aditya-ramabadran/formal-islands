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
    CombinedFormalizationAssessment,
    FaithfulnessClassification,
    FormalizationFaithfulnessError,
    RepairAssessment,
    RepairCategory,
    build_node_coverage_sketch,
    classify_heuristic_repair_assessment,
    format_faithfulness_notes,
    request_combined_verification_assessment,
    request_concrete_sublemma_summary,
    request_repair_assessment,
    request_node_formalization,
)
from formal_islands.models import FormalArtifact, ProofEdge, ProofGraph, ProofNode, VerificationResult
from formal_islands.progress import (
    append_formalization_assessment_to_progress_log,
    progress,
)


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
DEFAULT_FORMALIZATION_ATTEMPTS = 2
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
    max_attempts: int = DEFAULT_FORMALIZATION_ATTEMPTS,
    on_update: FormalizationUpdateCallback | None = None,
    mode: str = "agentic",
) -> FormalizationOutcome:
    """Attempt to formalize and verify one candidate node with bounded retries."""

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if mode != "agentic":
        raise ValueError("formalization mode is agentic-only in this prototype")

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

    if hasattr(backend, "run_agentic_structured"):
        return _formalize_candidate_node_agentic(
            backend=backend,
            planning_backend=planning_backend,
            verifier=verifier,
            graph=graph,
            node_id=node_id,
            max_attempts=max_attempts,
            on_update=on_update,
        )

    if hasattr(backend, "run_structured"):
        return _formalize_candidate_node_structured(
            backend=backend,
            planning_backend=planning_backend,
            verifier=verifier,
            graph=graph,
            node_id=node_id,
            max_attempts=max_attempts,
            on_update=on_update,
        )

    raise ValueError("formalization backend must support either agentic or structured output")


def formalize_candidate_nodes(
    *,
    backend: FormalizationBackend,
    planning_backend: StructuredBackend | None = None,
    verifier: LeanVerifier,
    graph: ProofGraph,
    node_ids: list[str] | None = None,
    max_attempts: int = DEFAULT_FORMALIZATION_ATTEMPTS,
    on_update: FormalizationUpdateCallback | None = None,
    mode: str = "agentic",
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
        _progress(f"node {node_id}: formalization attempt {attempt_number}/{attempt_limit} (structured backend)")
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
            repair_assessment = _classify_retry_failure(
                planning_backend=planning_backend,
                graph=current_graph,
                node_id=node_id,
                artifact=latest_artifact,
                failure_text=str(verification.stderr or verification.stdout),
                previous_result=verification,
            )
            _log_retry_diagnosis(node_id=node_id, repair_assessment=repair_assessment)
            _progress(f"node {node_id}: retrying after faithfulness guard feedback")
            latest_feedback = _build_repair_feedback(
                previous_result=verification,
                repair_assessment=repair_assessment,
                extra_guidance=(
                    "The previous theorem was rejected by the faithfulness guard. Stay much closer to the node text "
                    "and keep the theorem shape locked to the target claim."
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

        feedback_summary = _summarize_compiler_feedback(verification)
        _progress(f"node {node_id}: compiler feedback summary: {feedback_summary}")
        repair_assessment = _classify_retry_failure(
            planning_backend=planning_backend,
            graph=current_graph,
            node_id=node_id,
            artifact=latest_artifact,
            failure_text=f"{verification.stderr}\n{verification.stdout}",
            previous_result=verification,
        )
        _log_retry_diagnosis(node_id=node_id, repair_assessment=repair_assessment)
        _progress(f"node {node_id}: retrying after compiler feedback")
        latest_feedback = _build_repair_feedback(
            previous_result=verification,
            repair_assessment=repair_assessment,
        )

    assert latest_artifact is not None
    if latest_artifact.verification.status != "verified":
        current_graph = _maybe_refine_failed_node(
            planning_backend=planning_backend,
            graph=current_graph,
            node_id=node_id,
            artifact=latest_artifact,
            verification=latest_artifact.verification,
            on_update=on_update,
        )
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

    attempt_limit = max_attempts
    attempt_history: list[VerificationResult] = []
    latest_artifact: FormalArtifact | None = None
    latest_feedback: str | None = None
    current_graph = graph
    previous_lean_code: str | None = None
    faithfulness_feedback: str | None = None
    scratch_path = verifier.workspace.prepare_worker_file(node_id).resolve()

    for attempt_number in range(1, attempt_limit + 1):
        _progress(f"node {node_id}: formalization attempt {attempt_number}/{attempt_limit} (Aristotle backend)")
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
            previous_lean_code = scratch_path.read_text(encoding="utf-8") if scratch_path.exists() else None
            repair_assessment = _classify_retry_failure(
                planning_backend=planning_backend,
                graph=current_graph,
                node_id=node_id,
                artifact=latest_artifact,
                failure_text=str(verification.stderr or verification.stdout),
                previous_result=verification,
            )
            _log_retry_diagnosis(node_id=node_id, repair_assessment=repair_assessment)
            _progress(f"node {node_id}: retrying after faithfulness guard feedback")
            faithfulness_feedback = "\n\n".join(
                [
                    _build_aristotle_faithfulness_feedback(
                        previous_result=verification,
                        repair_assessment=repair_assessment,
                    ),
                    _build_repair_feedback(
                        previous_result=verification,
                        repair_assessment=repair_assessment,
                        extra_guidance=(
                            "The previous Aristotle submission was rejected by the faithfulness guard. "
                            "Keep the theorem much closer to the node text and preserve the theorem shape."
                        ),
                    ),
                ]
            )
            continue

        _progress(f"node {node_id}: running local Lean verification for Aristotle output")
        verification = verifier.verify_existing_file(file_path=scratch_path, attempt_number=attempt_number)
        attempt_history.append(verification)
        _progress(
            f"node {node_id}: local Lean verification completed with status {verification.status}"
        )
        latest_artifact = artifact.model_copy(
            update={
                "verification": verification,
                "attempt_history": attempt_history.copy(),
            }
        )
        _progress(f"node {node_id}: requesting combined semantic assessment")
        latest_artifact, assessment = _apply_combined_verification_assessment(
            planning_backend=planning_backend,
            graph=current_graph,
            node_id=node_id,
            artifact=latest_artifact,
        )
        if assessment is not None:
            _progress(
                f"node {node_id}: combined semantic assessment -> {assessment.result_kind} "
                f"(coverage={assessment.coverage_score}, main_burden={assessment.certifies_main_burden}, "
                f"expansion={assessment.expansion_warranted}, retry={assessment.worth_retrying_later})"
            )
        if latest_artifact.faithfulness_classification == FaithfulnessClassification.CONCRETE_SUBLEMMA:
            _progress(f"node {node_id}: trying bounded coverage expansion for concrete sublemma")
            expanded_artifact = _attempt_aristotle_coverage_expansion(
                backend=backend,
                planning_backend=planning_backend,
                verifier=verifier,
                graph=current_graph,
                node_id=node_id,
                artifact=latest_artifact,
                scratch_path=scratch_path,
                attempt_history=attempt_history,
                assessment=assessment,
            )
            if expanded_artifact is not None:
                latest_artifact = expanded_artifact
        if (
            attempt_number < attempt_limit
            and latest_artifact.faithfulness_classification == FaithfulnessClassification.CONCRETE_SUBLEMMA
            and _should_run_bonus_retry(graph=current_graph, node_id=node_id, assessment=assessment)
        ):
            _progress(f"node {node_id}: trying bonus larger-core retry")
            bonus_artifact = _attempt_bonus_retry(
                backend=backend,
                verifier=verifier,
                graph=current_graph,
                node_id=node_id,
                artifact=latest_artifact,
                scratch_path=scratch_path,
                attempt_history=attempt_history,
                assessment=assessment,
            )
            if bonus_artifact is not None:
                latest_artifact = bonus_artifact
        current_graph = _integrate_successful_formalization(
            graph=current_graph,
            backend=backend,
            planning_backend=planning_backend,
            node_id=node_id,
            artifact=latest_artifact,
            verification_status=verification.status,
        )
        _progress(f"node {node_id}: integrated Aristotle result into graph")
        _emit_update(current_graph, node_id, latest_artifact, on_update)

        if verification.status == "verified":
            return FormalizationOutcome(graph=current_graph, node_id=node_id, artifact=latest_artifact)

        if attempt_number >= attempt_limit or not _is_repairable_failure(verification):
            break

        feedback_summary = _summarize_compiler_feedback(verification)
        _progress(f"node {node_id}: compiler feedback summary: {feedback_summary}")
        repair_assessment = _classify_retry_failure(
            planning_backend=planning_backend,
            graph=current_graph,
            node_id=node_id,
            artifact=latest_artifact,
            failure_text=f"{verification.stderr}\n{verification.stdout}",
            previous_result=verification,
        )
        _log_retry_diagnosis(node_id=node_id, repair_assessment=repair_assessment)
        _progress(f"node {node_id}: retrying after compiler feedback")
        latest_feedback = _build_repair_feedback(
            previous_result=verification,
            repair_assessment=repair_assessment,
        )
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
    max_attempts: int,
    on_update: FormalizationUpdateCallback | None,
) -> FormalizationOutcome:
    workspace_root = verifier.workspace.root.resolve()
    scratch_path = verifier.workspace.prepare_worker_file(node_id).resolve()
    attempt_history: list[VerificationResult] = []
    current_graph = graph
    previous_lean_code: str | None = None
    faithfulness_feedback: str | None = None

    attempt_limit = max_attempts
    for attempt_number in range(1, attempt_limit + 1):
        _progress(f"node {node_id}: agentic attempt {attempt_number}/{attempt_limit}")
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
                    f"node {node_id}: completed with status {salvaged_artifact.verification.status} "
                    f"as {salvaged_artifact.faithfulness_classification}"
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
            if attempt_number >= attempt_limit:
                updated_graph = _maybe_refine_failed_node(
                    planning_backend=planning_backend,
                    graph=updated_graph,
                    node_id=node_id,
                    artifact=artifact,
                    verification=verification,
                    on_update=on_update,
                )
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
            if attempt_number >= attempt_limit:
                _progress(f"node {node_id}: formalization failed after faithfulness guard")
                current_graph = _maybe_refine_failed_node(
                    planning_backend=planning_backend,
                    graph=current_graph,
                    node_id=node_id,
                    artifact=artifact,
                    verification=verification,
                    on_update=on_update,
                )
                return FormalizationOutcome(graph=current_graph, node_id=node_id, artifact=artifact)
            _progress(f"node {node_id}: retrying after faithfulness guard feedback")
            repair_assessment = _classify_retry_failure(
                planning_backend=planning_backend,
                graph=current_graph,
                node_id=node_id,
                artifact=artifact,
                failure_text=str(verification.stderr or verification.stdout),
                previous_result=verification,
            )
            _log_retry_diagnosis(node_id=node_id, repair_assessment=repair_assessment)
            previous_lean_code = scratch_path.read_text(encoding="utf-8") if scratch_path.exists() else None
            faithfulness_feedback = _build_agentic_faithfulness_feedback(
                previous_result=verification,
                repair_assessment=repair_assessment,
            )
            continue

        verification = verifier.verify_existing_file(file_path=scratch_path, attempt_number=attempt_number)
        attempt_history.append(verification)
        artifact = artifact.model_copy(
            update={
                "verification": verification,
                "attempt_history": attempt_history.copy(),
            }
        )
        artifact, assessment = _apply_combined_verification_assessment(
            planning_backend=planning_backend,
            graph=current_graph,
            node_id=node_id,
            artifact=artifact,
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
                assessment=assessment,
            )
            if expanded_artifact is not None:
                artifact = expanded_artifact
        if (
            attempt_number < attempt_limit
            and artifact.faithfulness_classification == FaithfulnessClassification.CONCRETE_SUBLEMMA
            and _should_run_bonus_retry(graph=current_graph, node_id=node_id, assessment=assessment)
        ):
            _progress(f"node {node_id}: trying bonus larger-core retry")
            bonus_artifact = _attempt_bonus_retry(
                backend=backend,
                verifier=verifier,
                graph=current_graph,
                node_id=node_id,
                artifact=artifact,
                scratch_path=scratch_path,
                attempt_history=attempt_history,
                assessment=assessment,
            )
            if bonus_artifact is not None:
                artifact = bonus_artifact
        updated_graph = _integrate_successful_formalization(
            graph=current_graph,
            backend=backend,
            planning_backend=planning_backend,
            node_id=node_id,
            artifact=artifact,
            verification_status=verification.status,
        )
        _emit_update(updated_graph, node_id, artifact, on_update)
        _progress(
            f"node {node_id}: completed with status {artifact.verification.status} "
            f"as {artifact.faithfulness_classification}"
        )
        return FormalizationOutcome(graph=updated_graph, node_id=node_id, artifact=artifact)

    raise AssertionError(
        f"agentic formalization loop should always return within {attempt_limit} attempts"
    )


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
    assessment: CombinedFormalizationAssessment | None = None,
) -> FormalArtifact | None:
    if (
        artifact.verification.status != "verified"
        or artifact.faithfulness_classification != FaithfulnessClassification.CONCRETE_SUBLEMMA
    ):
        return None

    if assessment is None:
        upgraded = _maybe_upgrade_concrete_sublemma_to_full_node(
            planning_backend=planning_backend,
            graph=graph,
            node_id=node_id,
            artifact=artifact,
        )
        if upgraded is not None:
            return upgraded
    elif assessment.result_kind == "full_match":
        return artifact.model_copy(
            update={
                "faithfulness_classification": FaithfulnessClassification.FULL_NODE,
                "faithfulness_notes": format_faithfulness_notes(
                    assessment.result_kind,
                    assessment.reason,
                ),
            }
        )
    elif assessment.result_kind == "faithful_core":
        _progress(
            f"node {node_id}: planning backend judged the theorem already faithful enough; "
            "skipping coverage expansion"
        )
        return None
    elif not assessment.expansion_warranted:
        return None

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


def _summarize_compiler_feedback(verification: VerificationResult, *, max_length: int = 180) -> str:
    """Extract a short human-readable summary from Lean compiler output."""

    text = "\n".join(part for part in (verification.stderr, verification.stdout) if part.strip())
    if not text.strip():
        return "no compiler output"

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "no compiler output"

    priority_markers = (
        "error:",
        "type mismatch",
        "application type mismatch",
        "unknown identifier",
        "unknown constant",
        "unknown namespace",
        "failed to synthesize",
        "unsolved goals",
        "expected token",
        "cannot",
        "mismatch",
    )
    for line in lines:
        lowered = line.lower()
        if any(marker in lowered for marker in priority_markers):
            return _truncate_progress_summary(line, max_length=max_length)

    return _truncate_progress_summary(lines[0], max_length=max_length)


def _truncate_progress_summary(text: str, *, max_length: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[: max_length - 1].rstrip() + "…"


def _log_retry_diagnosis(*, node_id: str, repair_assessment: RepairAssessment | None) -> None:
    if repair_assessment is None:
        return
    _progress(
        f"node {node_id}: retry diagnosis [{repair_assessment.category.value}] "
        f"{_truncate_progress_summary(repair_assessment.note, max_length=180)}"
    )


def _repair_policy_lines(repair_assessment: RepairAssessment | None) -> list[str]:
    if repair_assessment is None:
        return []

    note = f"[{repair_assessment.category.value}] {repair_assessment.note}"
    lines = [f"Retry diagnosis: {note}"]
    if repair_assessment.category == RepairCategory.SETTING_FIX:
        lines.append(
            "Lock the theorem to the same mathematical universe: keep the ambient setting, dimension profile, "
            "and object types fixed instead of swapping to a proxy model."
        )
    elif repair_assessment.category == RepairCategory.THEOREM_SHAPE_FIX:
        lines.append(
            "Lock the theorem shape to the target node: do not replace the claim with a consequence, "
            "analogue, or assumed intermediate step."
        )
    elif repair_assessment.category == RepairCategory.LEAN_PACKAGING_FIX:
        lines.append(
            "Keep the theorem statement fixed and repair Lean packaging only: imports, namespaces, "
            "identifiers, typeclass plumbing, and Lean-safe binder names."
        )
    elif repair_assessment.category == RepairCategory.PROOF_STRATEGY_FIX:
        lines.append(
            "Keep the theorem statement fixed and change only the proof strategy, lemma order, or tactic script."
        )
    elif repair_assessment.category == RepairCategory.TRY_SMALLER_SUBLEMMA:
        lines.append(
            "If you must shrink, stay in the same setting and extract the nearest honest local core; do not "
            "switch universes or hide the hard step as a hypothesis."
        )
    elif repair_assessment.category == RepairCategory.TRY_LARGER_CORE:
        lines.append(
            "Stay in the same setting and expand toward the missing core without changing the mathematical universe."
        )
    return lines


def _faithfulness_repair_lines(repair_assessment: RepairAssessment | None) -> list[str]:
    if repair_assessment is None:
        return []

    if repair_assessment.category == RepairCategory.SETTING_FIX:
        return [
            "The theorem family is locked: keep the same ambient universe, dimension profile, and object types. "
            "Do not retreat to a lower-dimensional, proxy, or analogue theorem family as a workaround.",
            "If the current theorem cannot be made to work, the next attempt should still stay in the same setting; "
            "do not change the theorem family just to obtain a proof that compiles.",
        ]

    if repair_assessment.category == RepairCategory.THEOREM_SHAPE_FIX:
        return [
            "The theorem family is locked: keep the same concrete claim and proof role. Do not replace it with a "
            "consequence, analogue, assumed intermediate step, or easier side fact.",
            "Do not use a smaller sublemma escape hatch unless a later diagnostic explicitly says try_smaller_sublemma.",
        ]

    if repair_assessment.category == RepairCategory.LEAN_PACKAGING_FIX:
        return [
            "Keep the theorem statement fixed and repair Lean packaging only: imports, namespaces, identifiers, "
            "typeclass plumbing, and Lean-safe binder names.",
            "Do not change the theorem family or ambient setting while fixing packaging issues.",
            "If a binder name or theorem-header identifier uses Unicode like `λ₁`, rename it to a plain ASCII "
            "identifier such as `lambda1` and keep everything else fixed.",
        ]

    if repair_assessment.category == RepairCategory.PROOF_STRATEGY_FIX:
        return [
            "Keep the theorem statement fixed and change only the proof strategy, lemma order, or tactic script.",
            "The theorem family is already acceptable; do not switch to a different setting or a smaller analogue "
            "unless a later diagnostic explicitly asks for it.",
        ]

    if repair_assessment.category == RepairCategory.TRY_SMALLER_SUBLEMMA:
        return [
            "If you must shrink, stay in the same setting and extract the nearest honest local core; do not "
            "switch universes or hide the hard step as a hypothesis.",
            "The fallback must still be a genuinely nontrivial local theorem, not a bookkeeping identity or a "
            "mere substitution step.",
        ]

    if repair_assessment.category == RepairCategory.TRY_LARGER_CORE:
        return [
            "Stay in the same setting and expand toward the missing core without changing the mathematical universe.",
        ]

    return []


def _build_repair_feedback(
    *,
    previous_result: VerificationResult,
    repair_assessment: RepairAssessment | None = None,
    extra_guidance: str | None = None,
) -> str:
    heuristic = classify_heuristic_repair_assessment(
        previous_result=previous_result,
        extra_guidance=extra_guidance,
    )
    selected_assessment = repair_assessment or heuristic
    parts: list[str] = []
    if repair_assessment is not None:
        parts.extend(
            [
                "Planning backend repair diagnosis:",
                f"[{repair_assessment.category.value}] {repair_assessment.note}",
            ]
        )
    parts.extend(_repair_policy_lines(selected_assessment))
    parts.extend(
        [
            "Heuristic repair diagnosis:",
            f"[{heuristic.category.value}] {heuristic.note}",
            "Compiler feedback from the previous attempt:",
            previous_result.stderr or "(no stderr)",
            "Stdout from the previous attempt:",
            previous_result.stdout or "(no stdout)",
            (
            "Repair guidance: fix the Lean syntax or compiler issue and keep the theorem concrete and faithful to the original node. "
            "Reuse the node's variable names and hypotheses when reasonable. Avoid arbitrary `Type*` parameters, unrelated function "
            "families, unnecessary higher-order abstraction, or a shift to an arbitrary measure-space theorem when the node is concrete. "
            "Preserve the ambient setting when possible. If the diagnosis is setting_fix or theorem_shape_fix, do not "
            "use a smaller theorem family as an escape hatch; keep the same setting and theorem shape locked. "
            "If the diagnosis is proof_strategy_fix or lean_packaging_fix, keep the theorem statement fixed and only "
            "repair the proof or packaging. Prefer plain Lean syntax that compiles in a scratch file. "
            "Use a short, specific import list that matches the identifiers actually used, and avoid both `import Mathlib` "
            "for tiny local theorems and speculative deep imports that may not exist in the pinned workspace."
        ),
        ]
    )
    if extra_guidance:
        parts.append(extra_guidance)
    return "\n\n".join(parts)


def _build_agentic_faithfulness_feedback(
    *,
    previous_result: VerificationResult,
    repair_assessment: RepairAssessment | None = None,
) -> str:
    parts = [
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
    ]
    parts.extend(_repair_policy_lines(repair_assessment))
    parts.extend(_faithfulness_repair_lines(repair_assessment))
    return "\n\n".join(parts)


def _build_aristotle_faithfulness_feedback(
    *,
    previous_result: VerificationResult,
    repair_assessment: RepairAssessment | None = None,
) -> str:
    parts = [
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
    ]
    parts.extend(_repair_policy_lines(repair_assessment))
    parts.extend(_faithfulness_repair_lines(repair_assessment))
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


def _apply_combined_verification_assessment(
    *,
    planning_backend: StructuredBackend | None,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
) -> tuple[FormalArtifact, CombinedFormalizationAssessment | None]:
    if planning_backend is None:
        return artifact, None

    try:
        _progress(f"node {node_id}: starting combined semantic assessment request")
        assessment = request_combined_verification_assessment(
            backend=planning_backend,
            graph=_graph_for_retry_request(graph, node_id),
            node_id=node_id,
            artifact=artifact,
        )
    except BackendError:
        _progress(f"node {node_id}: combined semantic assessment request failed")
        return artifact, None

    _progress(f"node {node_id}: combined semantic assessment request completed")
    updated = artifact.model_copy(
        update={
            "faithfulness_notes": format_faithfulness_notes(
                assessment.result_kind,
                assessment.reason,
            )
        }
    )
    append_formalization_assessment_to_progress_log(
        node_id=node_id,
        result_kind=assessment.result_kind,
        reason=assessment.reason,
        coverage_score=assessment.coverage_score,
        certifies_main_burden=assessment.certifies_main_burden,
        expansion_warranted=assessment.expansion_warranted,
        worth_retrying_later=assessment.worth_retrying_later,
    )
    if assessment.result_kind == "full_match" and updated.faithfulness_classification != FaithfulnessClassification.FULL_NODE:
        updated = updated.model_copy(
            update={"faithfulness_classification": FaithfulnessClassification.FULL_NODE}
        )
    return updated, assessment


def _request_planning_repair_assessment(
    *,
    planning_backend: StructuredBackend | None,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
    failure_text: str,
) -> RepairAssessment | None:
    if planning_backend is None:
        return None

    try:
        return request_repair_assessment(
            backend=planning_backend,
            graph=_graph_for_retry_request(graph, node_id),
            node_id=node_id,
            artifact=artifact,
            failure_text=failure_text,
        )
    except BackendError:
        return None


def _classify_retry_failure(
    *,
    planning_backend: StructuredBackend | None,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
    failure_text: str,
    previous_result: VerificationResult,
) -> RepairAssessment:
    repair_assessment = _request_planning_repair_assessment(
        planning_backend=planning_backend,
        graph=graph,
        node_id=node_id,
        artifact=artifact,
        failure_text=failure_text,
    )
    if repair_assessment is None:
        repair_assessment = classify_heuristic_repair_assessment(
            previous_result=previous_result,
            extra_guidance=artifact.faithfulness_notes,
        )
    return repair_assessment


def _should_attempt_refinement_after_failure(repair_assessment: RepairAssessment | None) -> bool:
    if repair_assessment is None:
        return False
    if repair_assessment.category in {
        RepairCategory.SETTING_FIX,
        RepairCategory.LEAN_PACKAGING_FIX,
        RepairCategory.THEOREM_SHAPE_FIX,
    }:
        return False
    if repair_assessment.category == RepairCategory.TRY_SMALLER_SUBLEMMA:
        return True
    if repair_assessment.category in {
        RepairCategory.THEOREM_SHAPE_FIX,
        RepairCategory.PROOF_STRATEGY_FIX,
    }:
        note = repair_assessment.note.lower()
        return any(
            marker in note
            for marker in (
                "smaller",
                "local",
                "sublemma",
                "core",
                "narrow",
                "coverage",
                "fallback",
            )
        )
    return False


def _maybe_refine_failed_node(
    *,
    planning_backend: StructuredBackend | None,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
    verification: VerificationResult,
    on_update: FormalizationUpdateCallback | None,
) -> ProofGraph:
    repair_assessment = _classify_retry_failure(
        planning_backend=planning_backend,
        graph=graph,
        node_id=node_id,
        artifact=artifact,
        failure_text=f"{verification.stderr}\n{verification.stdout}",
        previous_result=verification,
    )
    _log_retry_diagnosis(node_id=node_id, repair_assessment=repair_assessment)

    if not _should_attempt_refinement_after_failure(repair_assessment):
        return graph

    from formal_islands.extraction.pipeline import refine_candidate_nodes

    refined_graph = refine_candidate_nodes(
        graph,
        backend=planning_backend,
        source_node_id=node_id,
    )
    if refined_graph == graph:
        _progress(
            f"node {node_id}: repair guidance suggested a smaller subclaim, but no acceptable refinement was found"
        )
        return graph

    _progress(
        f"node {node_id}: created fallback refined local claim after {repair_assessment.category.value}"
    )
    _emit_update(refined_graph, node_id, artifact, on_update)
    return refined_graph


def _node_is_on_main_proof_path(graph: ProofGraph, node_id: str) -> bool:
    reverse_edges: dict[str, set[str]] = {}
    provenance_labels = {"refined_from", "uses", "formal_sublemma_for"}
    for edge in graph.edges:
        if edge.label in provenance_labels:
            continue
        reverse_edges.setdefault(edge.target_id, set()).add(edge.source_id)

    queue = [node_id]
    seen = {node_id}
    while queue:
        current = queue.pop(0)
        if current == graph.root_node_id:
            return True
        for parent in reverse_edges.get(current, set()):
            if parent in seen:
                continue
            seen.add(parent)
            queue.append(parent)
    return False


def _should_run_bonus_retry(
    *,
    graph: ProofGraph,
    node_id: str,
    assessment: CombinedFormalizationAssessment | None,
) -> bool:
    if assessment is None:
        return False
    if assessment.result_kind in {"full_match", "faithful_core"}:
        return False
    if assessment.certifies_main_burden:
        return False
    if assessment.coverage_score > 6:
        return False
    if not assessment.worth_retrying_later:
        return False
    if not _node_is_on_main_proof_path(graph, node_id):
        return False
    node = next((candidate for candidate in graph.nodes if candidate.id == node_id), None)
    if node is None or node.formalization_priority not in {1, 2}:
        return False
    return True


def _build_bonus_retry_feedback(*, assessment: CombinedFormalizationAssessment) -> str:
    return "\n\n".join(
        [
            "Planning backend bonus-retry guidance:",
            f"[{RepairCategory.TRY_LARGER_CORE.value}] {assessment.reason}",
            (
                "Try a broader concrete core in the same mathematical setting. Keep the same proof path and "
                "expand toward the missing parent burden rather than changing to an easier analogue."
            ),
        ]
    )


def _attempt_bonus_retry(
    *,
    backend: FormalizationBackend,
    verifier: LeanVerifier,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
    scratch_path: Path,
    attempt_history: list[VerificationResult],
    assessment: CombinedFormalizationAssessment,
) -> FormalArtifact | None:
    bonus_feedback = _build_bonus_retry_feedback(assessment=assessment)
    original_code = artifact.lean_code
    _progress(f"node {node_id}: starting bonus larger-core retry")
    try:
        if isinstance(backend, AristotleBackend):
            bonus = request_aristotle_formalization(
                backend=backend,
                graph=_graph_for_retry_request(graph, node_id),
                node_id=node_id,
                workspace_root=verifier.workspace.root.resolve(),
                scratch_file_path=scratch_path,
                faithfulness_feedback=bonus_feedback,
                previous_lean_code=artifact.lean_code,
                compiler_feedback="Try to expand the verified local core upward toward the parent node.",
            )
            verification = verifier.verify_existing_file(
                file_path=scratch_path,
                attempt_number=(artifact.verification.attempt_count or 1) + 1,
            )
        else:
            bonus = request_agentic_formalization(
                backend=backend,
                graph=_graph_for_retry_request(graph, node_id),
                node_id=node_id,
                workspace_root=verifier.workspace.root.resolve(),
                scratch_file_path=scratch_path,
                faithfulness_feedback=bonus_feedback,
                previous_lean_code=artifact.lean_code,
            )
            verification = verifier.verify_existing_file(
                file_path=scratch_path,
                attempt_number=(artifact.verification.attempt_count or 1) + 1,
            )
    except (BackendError, FormalizationFaithfulnessError):
        _progress(f"node {node_id}: bonus larger-core retry failed before verification")
        scratch_path.write_text(original_code, encoding="utf-8")
        return None

    _progress(f"node {node_id}: running local Lean verification for bonus retry")
    if verification.status != "verified":
        _progress(f"node {node_id}: bonus retry verification failed with status {verification.status}")
        scratch_path.write_text(original_code, encoding="utf-8")
        return None

    bonus = bonus.model_copy(
        update={
            "verification": verification,
            "attempt_history": attempt_history.copy() + [verification],
        }
    )
    if bonus.faithfulness_classification == FaithfulnessClassification.FULL_NODE:
        _progress(f"node {node_id}: bonus retry produced a full-node theorem")
        return bonus

    scratch_path.write_text(original_code, encoding="utf-8")
    _progress(f"node {node_id}: bonus retry remained a concrete sublemma; restoring original code")
    return None


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
        _progress(f"node {node_id}: starting planning-backend full-match check")
        assessment = request_combined_verification_assessment(
            backend=planning_backend,
            graph=_graph_for_retry_request(graph, node_id),
            node_id=node_id,
            artifact=artifact,
        )
    except BackendError:
        _progress(f"node {node_id}: planning-backend full-match check failed")
        return None

    _progress(
        f"node {node_id}: planning-backend full-match check completed with result_kind="
        f"{assessment.result_kind}"
    )

    if assessment.result_kind not in {"full_match", "faithful_core"}:
        return None

    _progress(
        f"node {node_id}: planning backend judged the verified theorem already matches the target; "
        "skipping coverage expansion"
    )
    append_formalization_assessment_to_progress_log(
        node_id=node_id,
        result_kind=assessment.result_kind,
        reason=assessment.reason,
        coverage_score=assessment.coverage_score,
        certifies_main_burden=assessment.certifies_main_burden,
        expansion_warranted=assessment.expansion_warranted,
        worth_retrying_later=assessment.worth_retrying_later,
    )
    if assessment.result_kind == "full_match":
        return artifact.model_copy(
            update={
                "faithfulness_classification": FaithfulnessClassification.FULL_NODE,
                "faithfulness_notes": format_faithfulness_notes(
                    assessment.result_kind,
                    assessment.reason,
                ),
            }
        )
    return artifact.model_copy(
        update={
            "faithfulness_notes": format_faithfulness_notes(
                assessment.result_kind,
                assessment.reason,
            )
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


def _apply_combined_verification_assessment(
    *,
    planning_backend: StructuredBackend | None,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
) -> tuple[FormalArtifact, CombinedFormalizationAssessment | None]:
    if planning_backend is None:
        return artifact, None

    try:
        assessment = request_combined_verification_assessment(
            backend=planning_backend,
            graph=_graph_for_retry_request(graph, node_id),
            node_id=node_id,
            artifact=artifact,
        )
    except BackendError:
        return artifact, None

    updated = artifact.model_copy(
        update={
            "faithfulness_notes": format_faithfulness_notes(
                assessment.result_kind,
                assessment.reason,
            )
        }
    )
    if assessment.result_kind == "full_match" and updated.faithfulness_classification != FaithfulnessClassification.FULL_NODE:
        updated = updated.model_copy(
            update={"faithfulness_classification": FaithfulnessClassification.FULL_NODE}
        )
    return updated, assessment


def _attempt_structured_coverage_expansion(
    *,
    backend: StructuredBackend,
    planning_backend: StructuredBackend | None,
    verifier: LeanVerifier,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
    attempt_history: list[VerificationResult],
    assessment: CombinedFormalizationAssessment | None = None,
) -> FormalArtifact | None:
    if (
        artifact.verification.status != "verified"
        or artifact.faithfulness_classification != FaithfulnessClassification.CONCRETE_SUBLEMMA
    ):
        return None

    if assessment is None:
        upgraded = _maybe_upgrade_concrete_sublemma_to_full_node(
            planning_backend=planning_backend,
            graph=graph,
            node_id=node_id,
            artifact=artifact,
        )
        if upgraded is not None:
            return upgraded
    elif assessment.result_kind == "full_match":
        return artifact.model_copy(
            update={
                "faithfulness_classification": FaithfulnessClassification.FULL_NODE,
                "faithfulness_notes": format_faithfulness_notes(
                    assessment.result_kind,
                    assessment.reason,
                ),
            }
        )
    elif assessment.result_kind == "faithful_core":
        _progress(
            f"node {node_id}: planning backend judged the theorem already faithful enough; "
            "skipping coverage expansion"
        )
        return None
    elif not assessment.expansion_warranted:
        return None

    _progress(f"node {node_id}: starting structured coverage expansion")
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
        _progress(f"node {node_id}: structured coverage expansion failed before verification")
        return None

    _progress(f"node {node_id}: running local Lean verification for structured coverage expansion")
    verification = verifier.verify_code(
        lean_code=expanded.lean_code,
        node_id=node_id,
        attempt_number=(artifact.verification.attempt_count or 1) + 1,
    )
    if verification.status != "verified":
        _progress(
            f"node {node_id}: structured coverage expansion verification failed with status {verification.status}"
        )
        return None
    _progress(f"node {node_id}: structured coverage expansion verified successfully")
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
    assessment: CombinedFormalizationAssessment | None = None,
) -> FormalArtifact | None:
    if (
        artifact.verification.status != "verified"
        or artifact.faithfulness_classification != FaithfulnessClassification.CONCRETE_SUBLEMMA
    ):
        return None

    if assessment is None:
        upgraded = _maybe_upgrade_concrete_sublemma_to_full_node(
            planning_backend=planning_backend,
            graph=graph,
            node_id=node_id,
            artifact=artifact,
        )
        if upgraded is not None:
            return upgraded
    elif assessment.result_kind == "full_match":
        return artifact.model_copy(
            update={
                "faithfulness_classification": FaithfulnessClassification.FULL_NODE,
                "faithfulness_notes": format_faithfulness_notes(
                    assessment.result_kind,
                    assessment.reason,
                ),
            }
        )
    elif assessment.result_kind == "faithful_core":
        _progress(
            f"node {node_id}: planning backend judged the theorem already faithful enough; "
            "skipping coverage expansion"
        )
        return None
    elif not assessment.expansion_warranted:
        return None

    _progress(f"node {node_id}: starting agentic coverage expansion")
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
        _progress(f"node {node_id}: agentic coverage expansion failed before verification")
        scratch_path.write_text(original_code, encoding="utf-8")
        return None

    _progress(f"node {node_id}: running local Lean verification for agentic coverage expansion")
    verification = verifier.verify_existing_file(
        file_path=scratch_path,
        attempt_number=(artifact.verification.attempt_count or 1) + 1,
    )
    if verification.status != "verified":
        _progress(
            f"node {node_id}: agentic coverage expansion verification failed with status {verification.status}"
        )
        scratch_path.write_text(original_code, encoding="utf-8")
        return None
    _progress(f"node {node_id}: agentic coverage expansion verified successfully")
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
                node_id = future_to_node[future]
                _progress(f"Aristotle batch: waiting for node {node_id} future to finish")
                batch_outcomes.append(future.result())
                _progress(f"Aristotle batch: node {node_id} future finished")

        batch_order = {node.id: index for index, node in enumerate(batch_nodes)}
        for outcome in sorted(batch_outcomes, key=lambda item: batch_order.get(item.node_id, 999)):
            current_graph = _merge_formalization_outcome(
                current_graph=current_graph,
                batch_base_graph=batch_base_graph,
                updated_graph=outcome.graph,
            )
            attempted_ids.add(outcome.node_id)
            outcomes.append(outcome)
            _progress(
                f"Aristotle batch: merging node {outcome.node_id} result into shared graph"
            )
            _emit_update(current_graph, outcome.node_id, outcome.artifact, on_update)
            _progress(
                f"Aristotle node {outcome.node_id}: completed with status "
                f"{outcome.artifact.verification.status} as {outcome.artifact.faithfulness_classification}"
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
