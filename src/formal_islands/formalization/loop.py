"""Bounded single-node formalization loop."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
import re
from threading import RLock
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
    AbstractionReviewAssessment,
    AbstractionReviewCategory,
    BlockerPromotionAssessment,
    CombinedFormalizationAssessment,
    FaithfulnessClassification,
    FormalizationFaithfulnessError,
    LEAN_PACKAGING_FAILURE_MARKERS,
    ParentPromotionAssessment,
    RepairAssessment,
    RepairCategory,
    build_node_coverage_sketch,
    classify_heuristic_repair_assessment,
    format_faithfulness_notes,
    request_combined_verification_assessment,
    request_blocker_promotion_assessment,
    request_abstraction_review_assessment,
    request_concrete_sublemma_summary,
    request_parent_promotion_assessment,
    request_repair_assessment,
    request_node_formalization,
)
from formal_islands.models import (
    FormalArtifact,
    NodeFailureKind,
    NodeFormalizationOutcome,
    ProofEdge,
    ProofGraph,
    ProofNode,
    VerificationResult,
)
from formal_islands.progress import (
    append_formalization_assessment_to_progress_log,
    append_blocker_promotion_assessment_to_progress_log,
    append_graph_snapshot_to_history_log,
    append_parent_promotion_assessment_to_progress_log,
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


@dataclass
class ParentPromotionCache:
    """Thread-safe cache for planner parent-promotion decisions."""

    decisions: dict[str, ParentPromotionAssessment | BlockerPromotionAssessment | None]
    lock: RLock
    episodes: dict[str, list["ParentPromotionEpisode"]] = field(default_factory=dict)


@dataclass
class ParentPromotionEpisode:
    """A parent-promotion episode anchored to a specific child snapshot."""

    snapshot_key: str
    trigger_child_ids: tuple[str, ...]
    support_child_ids: tuple[str, ...] = ()


FormalizationUpdateCallback = Callable[[FormalizationOutcome], None]
DEFAULT_FORMALIZATION_ATTEMPTS = 2
MAX_TOTAL_FORMALIZATION_ATTEMPTS = 4
CANONICAL_ABSTRACTION_REVIEW_THRESHOLD = 2
FormalizationBackend = StructuredBackend | AristotleBackend


def _progress(message: str) -> None:
    progress(message)


def _count_faithfulness_guard_attempts(attempt_history: list[VerificationResult]) -> int:
    return sum(1 for result in attempt_history if result.command == "faithfulness_guard")


def _should_request_canonical_abstraction_review(
    *,
    artifact: FormalArtifact,
    attempt_history: list[VerificationResult],
    failure_text: str,
) -> bool:
    if artifact.faithfulness_classification != FaithfulnessClassification.OVER_ABSTRACT:
        return False
    if _count_faithfulness_guard_attempts(attempt_history) < CANONICAL_ABSTRACTION_REVIEW_THRESHOLD:
        return False
    text = "\n".join(
        part
        for part in [
            artifact.faithfulness_notes,
            artifact.lean_statement,
            artifact.lean_code,
            failure_text,
        ]
        if part
    )
    return "Type*" in text or re.search(r"\bType u\b|\bType v\b", text) is not None


def _review_repeated_over_abstract_attempt(
    *,
    planning_backend: StructuredBackend | None,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
    attempt_history: list[VerificationResult],
    failure_text: str,
) -> AbstractionReviewAssessment | None:
    if planning_backend is None:
        return None
    if not _should_request_canonical_abstraction_review(
        artifact=artifact,
        attempt_history=attempt_history,
        failure_text=failure_text,
    ):
        return None
    assessment = request_abstraction_review_assessment(
        backend=planning_backend,
        graph=graph,
        node_id=node_id,
        artifact=artifact,
        failure_text=failure_text,
    )
    _progress(
        f"node {node_id}: repeated abstraction review [{assessment.category.value}] {assessment.note}"
    )
    return assessment


def _artifact_with_canonical_abstraction_override(
    artifact: FormalArtifact,
    *,
    assessment: AbstractionReviewAssessment,
) -> FormalArtifact:
    return artifact.model_copy(
        update={
            "faithfulness_classification": FaithfulnessClassification.FULL_NODE,
            "faithfulness_notes": format_faithfulness_notes(
                "canonical_abstraction_override",
                assessment.note,
            ),
        }
    )


def formalize_candidate_node(
    *,
    backend: FormalizationBackend,
    planning_backend: StructuredBackend | None = None,
    verifier: LeanVerifier,
    graph: ProofGraph,
    node_id: str,
    max_attempts: int = DEFAULT_FORMALIZATION_ATTEMPTS,
    on_update: FormalizationUpdateCallback | None = None,
    parent_promotion_cache: ParentPromotionCache | None = None,
    enable_parent_promotion: bool = True,
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
            parent_promotion_cache=parent_promotion_cache,
            enable_parent_promotion=enable_parent_promotion,
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
            parent_promotion_cache=parent_promotion_cache,
            enable_parent_promotion=enable_parent_promotion,
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
            parent_promotion_cache=parent_promotion_cache,
            enable_parent_promotion=enable_parent_promotion,
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
    parent_promotion_cache: ParentPromotionCache | None = None,
    initial_attempted_ids: set[str] | None = None,
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
            parent_promotion_cache=parent_promotion_cache or ParentPromotionCache(decisions={}, lock=RLock()),
            enable_parent_promotion=node_ids is None,
            initial_attempted_ids=initial_attempted_ids,
        )

    current_graph = graph
    outcomes: list[FormalizationOutcome] = []
    attempted_ids: set[str] = set(initial_attempted_ids or set())
    parent_promotion_cache = parent_promotion_cache or ParentPromotionCache(decisions={}, lock=RLock())
    enable_parent_promotion = node_ids is None

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
                parent_promotion_cache=parent_promotion_cache,
                enable_parent_promotion=enable_parent_promotion,
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
                if enable_parent_promotion:
                    promoted_graph = _run_auto_promotion_sweeps(
                        graph=current_graph,
                        planning_backend=planning_backend,
                        parent_promotion_cache=parent_promotion_cache,
                        blocked_node_ids=attempted_ids,
                    )
                    if promoted_graph.model_dump(mode="json") != current_graph.model_dump(mode="json"):
                        current_graph = promoted_graph
                        continue
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
                parent_promotion_cache=parent_promotion_cache,
                enable_parent_promotion=enable_parent_promotion,
                mode=mode,
            )
            current_graph = outcome.graph
            outcomes.append(outcome)

    return MultiFormalizationOutcome(graph=current_graph, outcomes=outcomes)


def _run_auto_promotion_sweeps(
    *,
    graph: ProofGraph,
    planning_backend: StructuredBackend | None,
    parent_promotion_cache: ParentPromotionCache | None,
    blocked_node_ids: set[str] | None,
) -> ProofGraph:
    """Run late auto-discovery promotion passes in priority order.

    When no candidate nodes are currently queued, we first check whether any
    now-eligible informal parents should be promoted, and only then fall back
    to last-blocker promotion.
    """

    current_graph = graph
    parent_promoted = _promote_informal_parents_with_verified_children(
        graph=current_graph,
        planning_backend=planning_backend,
        parent_promotion_cache=parent_promotion_cache,
        blocked_parent_ids=blocked_node_ids,
    )
    if parent_promoted.model_dump(mode="json") != current_graph.model_dump(mode="json"):
        return parent_promoted

    blocker_promoted = _promote_last_blocker_nodes(
        graph=current_graph,
        planning_backend=planning_backend,
        parent_promotion_cache=parent_promotion_cache,
        blocked_node_ids=blocked_node_ids,
    )
    return blocker_promoted


def _formalize_candidate_node_structured(
    *,
    backend: StructuredBackend,
    planning_backend: StructuredBackend | None,
    verifier: LeanVerifier,
    graph: ProofGraph,
    node_id: str,
    max_attempts: int,
    on_update: FormalizationUpdateCallback | None,
    parent_promotion_cache: ParentPromotionCache | None,
    enable_parent_promotion: bool,
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
            current_graph = _record_node_formalization_episode(
                graph=current_graph,
                node_id=node_id,
                attempt_count=verification.attempt_count,
                outcome=NodeFormalizationOutcome.FAILED,
                failure_kind=_failure_kind_from_verification_result(verification),
                note="Most recent formalization attempt failed before the backend returned a usable Lean artifact.",
            )
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
            abstraction_review = _review_repeated_over_abstract_attempt(
                planning_backend=planning_backend,
                graph=current_graph,
                node_id=node_id,
                artifact=latest_artifact,
                attempt_history=attempt_history,
                failure_text=str(verification.stderr or verification.stdout),
            )
            if (
                abstraction_review is not None
                and abstraction_review.category == AbstractionReviewCategory.CANONICAL_ENCODING
            ):
                latest_artifact = _artifact_with_canonical_abstraction_override(
                    latest_artifact,
                    assessment=abstraction_review,
                )
                artifact = latest_artifact
                _progress(
                    f"node {node_id}: planner accepted the current abstraction as a canonical Lean encoding; "
                    "proceeding to local Lean verification"
                )
            else:
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
            parent_promotion_cache=parent_promotion_cache,
            enable_parent_promotion=enable_parent_promotion,
            blocked_parent_ids={node_id},
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
    parent_promotion_cache: ParentPromotionCache | None,
    enable_parent_promotion: bool,
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
            current_graph = _record_node_formalization_episode(
                graph=current_graph,
                node_id=node_id,
                attempt_count=verification.attempt_count,
                outcome=NodeFormalizationOutcome.FAILED,
                failure_kind=_failure_kind_from_verification_result(verification),
                note="Most recent formalization attempt failed before the Aristotle backend returned a usable Lean artifact.",
            )
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
            abstraction_review = _review_repeated_over_abstract_attempt(
                planning_backend=planning_backend,
                graph=current_graph,
                node_id=node_id,
                artifact=latest_artifact,
                attempt_history=attempt_history,
                failure_text=str(verification.stderr or verification.stdout),
            )
            if (
                abstraction_review is not None
                and abstraction_review.category == AbstractionReviewCategory.CANONICAL_ENCODING
            ):
                latest_artifact = _artifact_with_canonical_abstraction_override(
                    latest_artifact,
                    assessment=abstraction_review,
                )
                artifact = latest_artifact
                _progress(
                    f"node {node_id}: planner accepted the current abstraction as a canonical Lean encoding; "
                    "proceeding to local Lean verification"
                )
            else:
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
            parent_promotion_cache=parent_promotion_cache,
            enable_parent_promotion=enable_parent_promotion,
            blocked_parent_ids={node_id},
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
    parent_promotion_cache: ParentPromotionCache | None,
    enable_parent_promotion: bool,
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
                    parent_promotion_cache=parent_promotion_cache,
                    enable_parent_promotion=enable_parent_promotion,
                    blocked_parent_ids={node_id},
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
            updated_graph = _record_node_formalization_episode(
                graph=updated_graph,
                node_id=node_id,
                attempt_count=verification.attempt_count,
                outcome=NodeFormalizationOutcome.FAILED,
                failure_kind=_failure_kind_from_verification_result(verification),
                note="Most recent formalization attempt failed before the backend returned a usable Lean artifact.",
            )
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
            abstraction_review = _review_repeated_over_abstract_attempt(
                planning_backend=planning_backend,
                graph=current_graph,
                node_id=node_id,
                artifact=artifact,
                attempt_history=attempt_history,
                failure_text=str(verification.stderr or verification.stdout),
            )
            if (
                abstraction_review is not None
                and abstraction_review.category == AbstractionReviewCategory.CANONICAL_ENCODING
            ):
                artifact = _artifact_with_canonical_abstraction_override(
                    artifact,
                    assessment=abstraction_review,
                )
                _progress(
                    f"node {node_id}: planner accepted the current abstraction as a canonical Lean encoding; "
                    "proceeding to local Lean verification"
                )
            else:
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
            parent_promotion_cache=parent_promotion_cache,
            enable_parent_promotion=enable_parent_promotion,
            blocked_parent_ids={node_id},
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
                assessment=assessment,
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
    parts.extend(_packaging_specific_repair_lines(previous_result))
    return "\n\n".join(parts)


def _packaging_specific_repair_lines(previous_result: VerificationResult) -> list[str]:
    text = "\n".join(
        part for part in (previous_result.stderr, previous_result.stdout) if part.strip()
    ).lower()
    if "object file" not in text and ".olean" not in text:
        return []
    return [
        (
            "This failure is about a missing imported module object file (.olean), not about the theorem "
            "mathematics. Rewrite the file to be self-contained instead of depending on a generated support-module "
            "import."
        ),
        (
            "If the current file imports `FormalIslands.Generated.Support.*` or another generated helper module, "
            "remove that import and inline or copy/adapt the minimal helper theorem(s) needed into the scratch file."
        ),
        (
            "Do not keep retrying the same cross-file packaging pattern. Preserve the main theorem statement and "
            "rebuild the helper support locally inside the file."
        ),
    ]


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


def _record_node_formalization_episode(
    *,
    graph: ProofGraph,
    node_id: str,
    attempt_count: int | None,
    outcome: NodeFormalizationOutcome,
    failure_kind: NodeFailureKind | None = None,
    note: str,
) -> ProofGraph:
    attempt_text = f" after {attempt_count} Lean verification attempt(s)" if attempt_count else ""
    _progress(
        f"node {node_id}: recorded formalization episode outcome={outcome}{attempt_text} "
        f"note={_truncate_progress_summary(note, max_length=160)}"
    )
    updated_nodes = [
        node.model_copy(
            update={
                "last_formalization_attempt_count": attempt_count if attempt_count and attempt_count > 0 else None,
                "last_formalization_outcome": outcome,
                "last_formalization_failure_kind": failure_kind,
                "last_formalization_note": note.strip() or None,
            }
        )
        if node.id == node_id
        else node
        for node in graph.nodes
    ]
    return graph.model_copy(update={"nodes": updated_nodes})


def _failure_kind_from_verification_result(verification: VerificationResult) -> NodeFailureKind:
    """Classify the most recent failed episode into a stable artifact-facing category."""

    command = (verification.command or "").lower()
    stderr = verification.stderr or ""
    stdout = verification.stdout or ""
    text = "\n".join(part.lower() for part in [stderr, stdout] if part)
    if command == "backend_request":
        return NodeFailureKind.BACKEND_FAILURE
    if command == "faithfulness_guard":
        return NodeFailureKind.FAITHFULNESS_REJECTION
    if any(marker in text for marker in LEAN_PACKAGING_FAILURE_MARKERS) or ".olean" in text or "object file" in text:
        return NodeFailureKind.PACKAGING_FAILURE
    return NodeFailureKind.LEAN_FAILURE


def _integrate_successful_formalization(
    *,
    graph: ProofGraph,
    backend: FormalizationBackend | None,
    planning_backend: StructuredBackend | None,
    node_id: str,
    artifact: FormalArtifact,
    verification_status: str,
    parent_promotion_cache: ParentPromotionCache | None = None,
    enable_parent_promotion: bool = True,
    blocked_parent_ids: set[str] | None = None,
) -> ProofGraph:
    if verification_status != "verified":
        updated = _update_node(graph, node_id, "formal_failed", artifact)
        return _record_node_formalization_episode(
            graph=updated,
            node_id=node_id,
            attempt_count=artifact.verification.attempt_count,
            outcome=NodeFormalizationOutcome.FAILED,
            failure_kind=_failure_kind_from_verification_result(artifact.verification),
            note="Most recent formalization attempt failed local Lean verification.",
        )

    if artifact.faithfulness_classification == FaithfulnessClassification.FULL_NODE:
        updated = _update_node(graph, node_id, "formal_verified", artifact)
        updated = _record_node_formalization_episode(
            graph=updated,
            node_id=node_id,
            attempt_count=artifact.verification.attempt_count,
            outcome=NodeFormalizationOutcome.VERIFIED_FULL_NODE,
            failure_kind=None,
            note="Most recent formalization attempt verified the full parent node.",
        )
        updated = _swallow_supporting_formal_cores(graph=updated, parent_node_id=node_id)
        if enable_parent_promotion:
            updated = _promote_followup_candidates_after_verified_node(
                graph=updated,
                planning_backend=planning_backend,
                verified_node_id=node_id,
                parent_promotion_cache=parent_promotion_cache,
                blocked_parent_ids=blocked_parent_ids,
            )
        return updated

    if artifact.faithfulness_classification == FaithfulnessClassification.CONCRETE_SUBLEMMA:
        promotion_snapshot_key = _parent_promotion_cache_key(graph, node_id)
        updated, support_child_id = _promote_concrete_sublemma(
            graph=graph,
            backend=planning_backend if planning_backend is not None else backend,
            parent_node_id=node_id,
            artifact=artifact,
        )
        updated = _record_node_formalization_episode(
            graph=updated,
            node_id=node_id,
            attempt_count=artifact.verification.attempt_count,
            outcome=NodeFormalizationOutcome.PRODUCED_SUPPORTING_CORE,
            failure_kind=None,
            note=(
                f"Most recent formalization attempt produced the verified supporting core "
                f"'{support_child_id}' rather than a full-node theorem."
            ),
        )
        consumed = _consume_parent_promotion_episode(
            parent_promotion_cache=parent_promotion_cache,
            parent_node_id=node_id,
            snapshot_key=promotion_snapshot_key,
            support_child_id=support_child_id,
        )
        if consumed:
            _progress(
                f"node {node_id}: parent promotion episode consumed via support child {support_child_id}"
            )
        if enable_parent_promotion:
            return _promote_followup_candidates_after_verified_node(
                graph=updated,
                planning_backend=planning_backend,
                verified_node_id=None,
                parent_promotion_cache=parent_promotion_cache,
                blocked_parent_ids=blocked_parent_ids,
            )
        return updated

    updated = _update_node(graph, node_id, "formal_failed", artifact)
    return _record_node_formalization_episode(
        graph=updated,
        node_id=node_id,
        attempt_count=artifact.verification.attempt_count,
        outcome=NodeFormalizationOutcome.FAILED,
        failure_kind=_failure_kind_from_verification_result(artifact.verification),
        note="Most recent formalization attempt failed the faithfulness guard.",
    )


def _swallow_supporting_formal_cores(*, graph: ProofGraph, parent_node_id: str) -> ProofGraph:
    """Remove direct formal-core support children once their parent is fully verified."""

    outgoing_support_edges = [
        edge
        for edge in graph.edges
        if edge.source_id == parent_node_id and edge.label == "formal_sublemma_for"
    ]
    if not outgoing_support_edges:
        return graph

    node_by_id = {node.id: node for node in graph.nodes}
    absorbable_child_ids: set[str] = set()
    for edge in outgoing_support_edges:
        child = node_by_id.get(edge.target_id)
        if child is None or child.status != "formal_verified" or child.formal_artifact is None:
            continue
        child_incoming = [candidate for candidate in graph.edges if candidate.target_id == child.id]
        child_outgoing = [candidate for candidate in graph.edges if candidate.source_id == child.id]
        if child_outgoing:
            continue
        if any(
            candidate.label != "formal_sublemma_for" or candidate.source_id != parent_node_id
            for candidate in child_incoming
        ):
            continue
        absorbable_child_ids.add(child.id)

    if not absorbable_child_ids:
        return graph

    _progress(
        f"node {parent_node_id}: swallowing supporting formal core(s) "
        f"{', '.join(sorted(absorbable_child_ids))}"
    )
    updated_nodes = [node for node in graph.nodes if node.id not in absorbable_child_ids]
    updated_edges = [
        edge
        for edge in graph.edges
        if edge.source_id not in absorbable_child_ids and edge.target_id not in absorbable_child_ids
    ]
    return graph.model_copy(update={"nodes": updated_nodes, "edges": updated_edges})


def _promote_followup_candidates_after_verified_node(
    *,
    graph: ProofGraph,
    planning_backend: StructuredBackend | None,
    verified_node_id: str | None,
    parent_promotion_cache: ParentPromotionCache | None,
    blocked_parent_ids: set[str] | None = None,
) -> ProofGraph:
    updated = graph
    updated = _promote_informal_parents_with_verified_children(
        graph=updated,
        planning_backend=planning_backend,
        parent_promotion_cache=parent_promotion_cache,
        blocked_parent_ids=blocked_parent_ids,
    )
    return updated


def _parent_promotion_cache_key(graph: ProofGraph, parent_node_id: str) -> str:
    parent = next(node for node in graph.nodes if node.id == parent_node_id)
    verified_children = _current_verified_direct_child_nodes(graph, parent_node_id)
    payload = {
        "parent": {
            "id": parent.id,
            "title": parent.title,
            "informal_statement": parent.informal_statement,
            "informal_proof_text": parent.informal_proof_text,
        },
        "children": [
            {
                "id": child.id,
                "theorem": child.formal_artifact.lean_theorem_name if child.formal_artifact else "",
                "statement": child.formal_artifact.lean_statement if child.formal_artifact else "",
                "classification": child.formal_artifact.faithfulness_classification if child.formal_artifact else "",
            }
            for child in verified_children
        ],
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _current_direct_child_ids(graph: ProofGraph, parent_node_id: str) -> list[str]:
    return sorted(edge.target_id for edge in graph.edges if edge.source_id == parent_node_id)


def _current_verified_direct_child_nodes(graph: ProofGraph, parent_node_id: str) -> list[ProofNode]:
    child_ids = set(_current_direct_child_ids(graph, parent_node_id))
    return [
        node
        for node in graph.nodes
        if node.id in child_ids and node.status == "formal_verified" and node.formal_artifact is not None
    ]


def _current_verified_direct_child_ids(graph: ProofGraph, parent_node_id: str) -> tuple[str, ...]:
    return tuple(node.id for node in _current_verified_direct_child_nodes(graph, parent_node_id))


def _parent_promotion_episode_basis(episode: ParentPromotionEpisode) -> frozenset[str]:
    return frozenset(episode.trigger_child_ids) | frozenset(episode.support_child_ids)


def _record_parent_promotion_episode(
    *,
    parent_promotion_cache: ParentPromotionCache | None,
    parent_node_id: str,
    snapshot_key: str,
    trigger_child_ids: tuple[str, ...],
) -> None:
    if parent_promotion_cache is None:
        return
    with parent_promotion_cache.lock:
        episodes = parent_promotion_cache.episodes.setdefault(parent_node_id, [])
        if any(
            episode.snapshot_key == snapshot_key and episode.trigger_child_ids == trigger_child_ids
            for episode in episodes
        ):
            return
        episodes.append(
            ParentPromotionEpisode(
                snapshot_key=snapshot_key,
                trigger_child_ids=trigger_child_ids,
            )
        )


def _consume_parent_promotion_episode(
    *,
    parent_promotion_cache: ParentPromotionCache | None,
    parent_node_id: str,
    snapshot_key: str,
    support_child_id: str,
) -> bool:
    if parent_promotion_cache is None:
        return False
    with parent_promotion_cache.lock:
        episodes = parent_promotion_cache.episodes.get(parent_node_id, [])
        for episode in reversed(episodes):
            if episode.snapshot_key == snapshot_key and not episode.support_child_ids:
                episode.support_child_ids = (support_child_id,)
                return True
        for episode in reversed(episodes):
            if not episode.support_child_ids:
                episode.support_child_ids = (support_child_id,)
                return True
    return False


def _has_consumed_parent_promotion_episode(
    *,
    parent_promotion_cache: ParentPromotionCache | None,
    parent_node_id: str,
    current_child_ids: tuple[str, ...],
) -> bool:
    if parent_promotion_cache is None:
        return False
    current_basis = frozenset(current_child_ids)
    with parent_promotion_cache.lock:
        for episode in parent_promotion_cache.episodes.get(parent_node_id, []):
            if not episode.support_child_ids:
                continue
            if current_basis == _parent_promotion_episode_basis(episode):
                return True
    return False


def _eligible_informal_parents_with_verified_children(graph: ProofGraph) -> list[str]:
    node_by_id = {node.id: node for node in graph.nodes}
    eligible: list[str] = []
    children_by_parent: dict[str, list[str]] = {}
    for edge in graph.edges:
        children_by_parent.setdefault(edge.source_id, []).append(edge.target_id)

    for parent_id, child_ids in children_by_parent.items():
        parent = node_by_id.get(parent_id)
        if parent is None or parent.status != "informal":
            continue
        if not child_ids:
            continue
        if all(
            node_by_id.get(child_id) is not None
            and node_by_id[child_id].status == "formal_verified"
            and node_by_id[child_id].formal_artifact is not None
            for child_id in child_ids
        ):
            eligible.append(parent_id)
    return sorted(eligible)


def _promote_informal_parents_with_verified_children(
    *,
    graph: ProofGraph,
    planning_backend: StructuredBackend | None,
    parent_promotion_cache: ParentPromotionCache | None,
    blocked_parent_ids: set[str] | None = None,
) -> ProofGraph:
    if planning_backend is None:
        return graph

    eligible_parent_ids = _eligible_informal_parents_with_verified_children(graph)
    if not eligible_parent_ids:
        _progress("parent promotion sweep: no informal parents with all-verified children")
        return graph

    _progress(
        "parent promotion sweep: evaluating "
        f"{len(eligible_parent_ids)} eligible informal parent(s)"
    )
    current_graph = graph
    updated_nodes = list(current_graph.nodes)
    node_index = {node.id: index for index, node in enumerate(updated_nodes)}

    for parent_id in eligible_parent_ids:
        if blocked_parent_ids is not None and parent_id in blocked_parent_ids:
            _progress(
                f"node {parent_id}: skipping parent promotion because this node was already formalized in the current pass"
            )
            continue
        parent = next(node for node in updated_nodes if node.id == parent_id)
        current_verified_child_ids = _current_verified_direct_child_ids(current_graph, parent_id)
        if _has_consumed_parent_promotion_episode(
            parent_promotion_cache=parent_promotion_cache,
            parent_node_id=parent_id,
            current_child_ids=current_verified_child_ids,
        ):
            _progress(
                f"node {parent_id}: skipping parent promotion because this verified child set already "
                "came from a prior parent-assembly episode"
            )
            continue
        cache_key = _parent_promotion_cache_key(current_graph, parent_id)
        decision: ParentPromotionAssessment | None = None
        cached_hit = False
        if parent_promotion_cache is not None:
            with parent_promotion_cache.lock:
                if cache_key in parent_promotion_cache.decisions:
                    decision = parent_promotion_cache.decisions[cache_key]
                    cached_hit = True
        if not cached_hit:
            _progress(f"node {parent_id}: requesting parent assembly promotion assessment")
            try:
                decision = request_parent_promotion_assessment(
                    backend=planning_backend,
                    graph=current_graph,
                    parent_node_id=parent_id,
                )
            except BackendError as exc:
                _progress(
                    f"node {parent_id}: parent assembly promotion assessment failed: "
                    f"{_truncate_progress_summary(str(exc), max_length=180)}"
                )
                decision = None
            if parent_promotion_cache is not None:
                with parent_promotion_cache.lock:
                    parent_promotion_cache.decisions[cache_key] = decision
        else:
            _progress(
                f"node {parent_id}: parent promotion decision cache hit -> "
                f"promote={(decision.promote_parent if decision is not None else False)}"
            )

        if decision is None:
            _progress(f"node {parent_id}: leaving informal because no planner decision was available")
            continue

        append_parent_promotion_assessment_to_progress_log(
            parent_node_id=parent_id,
            promote_parent=decision.promote_parent,
            recommended_priority=decision.recommended_priority,
            verified_child_count=len([edge for edge in current_graph.edges if edge.source_id == parent_id]),
            reason=_truncate_progress_summary(decision.reason, max_length=180),
        )

        _progress(
            f"node {parent_id}: parent promotion assessment -> promote={decision.promote_parent} "
            f"priority={decision.recommended_priority if decision.recommended_priority is not None else 'null'} "
            f"reason={_truncate_progress_summary(decision.reason, max_length=180)}"
        )
        if not decision.promote_parent:
            continue

        priority = decision.recommended_priority if decision.recommended_priority is not None else 3
        updated_nodes[node_index[parent_id]] = parent.model_copy(
            update={
                "status": "candidate_formal",
                "formalization_priority": priority,
                "formalization_rationale": _truncate_progress_summary(
                    (
                        f"Promoted after all direct children were verified; {decision.reason}"
                    ),
                    max_length=240,
                ),
            }
        )
        _progress(
            f"node {parent_id}: promoted to candidate_formal at priority {priority}"
        )
        promoted_graph = current_graph.model_copy(update={"nodes": list(updated_nodes)})
        append_graph_snapshot_to_history_log(
            promoted_graph,
            label=f"parent promotion ({parent_id})",
            previous_graph=current_graph,
            event="parent_promotion",
            node_id=parent_id,
            metadata={
                "reason": decision.reason,
                "recommended_priority": priority,
                "verified_child_ids": list(current_verified_child_ids),
            },
        )
        current_graph = promoted_graph
        _record_parent_promotion_episode(
            parent_promotion_cache=parent_promotion_cache,
            parent_node_id=parent_id,
            snapshot_key=cache_key,
            trigger_child_ids=current_verified_child_ids,
        )

    return current_graph


def _eligible_last_blocker_nodes(graph: ProofGraph) -> list[str]:
    node_by_id = {node.id: node for node in graph.nodes}
    parent_candidates: set[str] = set()
    for edge in graph.edges:
        parent = node_by_id.get(edge.source_id)
        child = node_by_id.get(edge.target_id)
        if parent is None or child is None:
            continue
        if parent.status != "informal":
            continue
        if child.status == "formal_verified":
            continue
        child_ids = [candidate_edge.target_id for candidate_edge in graph.edges if candidate_edge.source_id == parent.id]
        if not child_ids:
            continue
        remaining_unverified = [
            child_id
            for child_id in child_ids
            if child_id in node_by_id and node_by_id[child_id].status != "formal_verified"
        ]
        if remaining_unverified != [child.id]:
            continue
        if not any(
            child_id in node_by_id and node_by_id[child_id].status == "formal_verified"
            for child_id in child_ids
            if child_id != child.id
        ):
            continue
        if child.formal_artifact is not None or child.last_formalization_outcome is not None:
            continue
        grandchild_ids = [candidate_edge.target_id for candidate_edge in graph.edges if candidate_edge.source_id == child.id]
        if any(
            grandchild_id in node_by_id and node_by_id[grandchild_id].status != "formal_verified"
            for grandchild_id in grandchild_ids
        ):
            continue
        parent_candidates.add(child.id)
    return sorted(parent_candidates)


def _blocker_promotion_cache_key(graph: ProofGraph, node_id: str) -> str:
    snapshot = tuple(_current_direct_child_ids(graph, node_id))
    return f"blocker::{node_id}::{snapshot}"


def _promote_last_blocker_nodes(
    *,
    graph: ProofGraph,
    planning_backend: StructuredBackend | None,
    parent_promotion_cache: ParentPromotionCache | None,
    blocked_node_ids: set[str] | None = None,
) -> ProofGraph:
    if planning_backend is None:
        return graph

    eligible_node_ids = _eligible_last_blocker_nodes(graph)
    if not eligible_node_ids:
        _progress("blocker promotion sweep: no last-blocker informal nodes were eligible")
        return graph

    _progress(
        "blocker promotion sweep: evaluating "
        f"{len(eligible_node_ids)} eligible blocker node(s)"
    )
    current_graph = graph
    updated_nodes = list(current_graph.nodes)
    node_index = {node.id: index for index, node in enumerate(updated_nodes)}
    node_by_id = {node.id: node for node in updated_nodes}

    for node_id in eligible_node_ids:
        if blocked_node_ids is not None and node_id in blocked_node_ids:
            _progress(
                f"node {node_id}: skipping blocker promotion because this node was already formalized in the current pass"
            )
            continue
        blocker = node_by_id[node_id]
        cache_key = _blocker_promotion_cache_key(current_graph, node_id)
        decision: BlockerPromotionAssessment | None = None
        cached_hit = False
        if parent_promotion_cache is not None:
            with parent_promotion_cache.lock:
                if cache_key in parent_promotion_cache.decisions:
                    decision = parent_promotion_cache.decisions[cache_key]  # type: ignore[assignment]
                    cached_hit = True
        if not cached_hit:
            _progress(f"node {node_id}: requesting blocker promotion assessment")
            try:
                decision = request_blocker_promotion_assessment(
                    backend=planning_backend,
                    graph=current_graph,
                    blocker_node_id=node_id,
                )
            except BackendError as exc:
                _progress(
                    f"node {node_id}: blocker promotion assessment failed: "
                    f"{_truncate_progress_summary(str(exc), max_length=180)}"
                )
                decision = None
            if parent_promotion_cache is not None:
                with parent_promotion_cache.lock:
                    parent_promotion_cache.decisions[cache_key] = decision
        else:
            _progress(
                f"node {node_id}: blocker promotion decision cache hit -> "
                f"promote={(decision.promote_node if decision is not None else False)}"
            )

        if decision is None:
            _progress(f"node {node_id}: leaving informal because no blocker-promotion decision was available")
            continue

        parent_ids = sorted({edge.source_id for edge in current_graph.edges if edge.target_id == node_id})
        append_blocker_promotion_assessment_to_progress_log(
            node_id=node_id,
            promote_node=decision.promote_node,
            reason=_truncate_progress_summary(decision.reason, max_length=180),
            recommended_priority=decision.recommended_priority,
            parent_ids=parent_ids,
        )
        _progress(
            f"node {node_id}: blocker promotion assessment -> promote={decision.promote_node} "
            f"priority={decision.recommended_priority if decision.recommended_priority is not None else 'null'} "
            f"reason={_truncate_progress_summary(decision.reason, max_length=180)}"
        )
        if not decision.promote_node:
            continue

        priority = decision.recommended_priority if decision.recommended_priority is not None else 2
        updated_nodes[node_index[node_id]] = blocker.model_copy(
            update={
                "status": "candidate_formal",
                "formalization_priority": priority,
                "formalization_rationale": _truncate_progress_summary(
                    (
                        f"Promoted as the last remaining blocker to parent/root closure; {decision.reason}"
                    ),
                    max_length=240,
                ),
            }
        )
        promoted_graph = current_graph.model_copy(update={"nodes": list(updated_nodes)})
        _progress(f"node {node_id}: promoted to candidate_formal as a last-blocker node at priority {priority}")
        append_graph_snapshot_to_history_log(
            promoted_graph,
            label=f"blocker promotion ({node_id})",
            previous_graph=current_graph,
            event="blocker_promotion",
            node_id=node_id,
            metadata={
                "reason": decision.reason,
                "recommended_priority": priority,
                "parent_ids": parent_ids,
            },
        )
        current_graph = promoted_graph
        node_by_id[node_id] = updated_nodes[node_index[node_id]]

    return current_graph


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
    forward_edges: dict[str, set[str]] = {}
    for edge in graph.edges:
        forward_edges.setdefault(edge.source_id, set()).add(edge.target_id)

    queue = [graph.root_node_id]
    seen = {graph.root_node_id}
    while queue:
        current = queue.pop(0)
        if current == node_id:
            return True
        for dependency in forward_edges.get(current, set()):
            if dependency in seen:
                continue
            seen.add(dependency)
            queue.append(dependency)
    return False


def _should_run_bonus_retry(
    *,
    graph: ProofGraph,
    node_id: str,
    assessment: CombinedFormalizationAssessment | None,
) -> bool:
    if assessment is None:
        return False
    if assessment.result_kind == "full_match":
        return False
    if not assessment.worth_retrying_later:
        return False
    if not assessment.expansion_warranted:
        return False
    if not _node_is_on_main_proof_path(graph, node_id):
        return False
    node = next((candidate for candidate in graph.nodes if candidate.id == node_id), None)
    if node is None or node.formalization_priority not in {1, 2}:
        return False
    if assessment.result_kind == "faithful_core":
        return True
    if assessment.certifies_main_burden:
        return False
    if assessment.coverage_score > 6:
        return False
    return True


def _build_bonus_retry_feedback(*, assessment: CombinedFormalizationAssessment) -> str:
    return "\n\n".join(
        [
            "Planning backend upgrade-from-core guidance:",
            f"[{RepairCategory.TRY_LARGER_CORE.value}] {assessment.reason}",
            (
                "Starting from the already verified theorem, try one broader concrete theorem in the same "
                "mathematical setting. Keep the same proof path and expand toward the missing parent burden "
                "rather than changing to an easier analogue."
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
    _progress(f"node {node_id}: starting explicit upgrade-from-verified-core retry")
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
        _progress(f"node {node_id}: upgrade-from-core retry failed before verification")
        scratch_path.write_text(original_code, encoding="utf-8")
        return None

    _progress(f"node {node_id}: running local Lean verification for upgrade-from-core retry")
    if verification.status != "verified":
        _progress(
            f"node {node_id}: upgrade-from-core retry verification failed with status {verification.status}"
        )
        scratch_path.write_text(original_code, encoding="utf-8")
        return None

    bonus = bonus.model_copy(
        update={
            "verification": verification,
            "attempt_history": attempt_history.copy() + [verification],
        }
    )
    if bonus.faithfulness_classification == FaithfulnessClassification.FULL_NODE:
        _progress(f"node {node_id}: upgrade-from-core retry produced a full-node theorem")
        return bonus

    scratch_path.write_text(original_code, encoding="utf-8")
    _progress(
        f"node {node_id}: upgrade-from-core retry remained narrower than full-node; restoring original code"
    )
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


def _promote_concrete_sublemma(
    *,
    graph: ProofGraph,
    backend: StructuredBackend | None,
    parent_node_id: str,
    artifact: FormalArtifact,
) -> tuple[ProofGraph, str]:
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
            source_id=parent_node_id,
            target_id=child_id,
            label="formal_sublemma_for",
            explanation=(
                "This verified Lean theorem certifies a narrower concrete local core used inside the parent informal step."
            ),
        )
    )
    return graph.model_copy(update={"nodes": updated_nodes, "edges": updated_edges}), child_id


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
                assessment=assessment,
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
                assessment=assessment,
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
    parent_promotion_cache: ParentPromotionCache | None,
    enable_parent_promotion: bool,
    initial_attempted_ids: set[str] | None = None,
) -> MultiFormalizationOutcome:
    current_graph = graph
    outcomes: list[FormalizationOutcome] = []
    attempted_ids: set[str] = set(initial_attempted_ids or set())
    wave_index = 0

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
            remaining_nodes = [
                node
                for node in sorted(
                    current_graph.nodes,
                    key=lambda n: (n.formalization_priority or 999, n.id),
                )
                if node.status == "candidate_formal" and node.id not in attempted_ids
            ]
            batch_nodes = _select_dependency_aware_aristotle_wave(
                graph=current_graph,
                remaining_nodes=remaining_nodes,
                attempted_ids=attempted_ids,
            )
        if not batch_nodes:
            if node_ids is None and enable_parent_promotion:
                promoted_graph = _run_auto_promotion_sweeps(
                    graph=current_graph,
                    planning_backend=planning_backend,
                    parent_promotion_cache=parent_promotion_cache,
                    blocked_node_ids=attempted_ids,
                )
                if promoted_graph.model_dump(mode="json") != current_graph.model_dump(mode="json"):
                    current_graph = promoted_graph
                    continue
            break

        wave_index += 1
        _progress(
            "Aristotle dependency-aware wave "
            f"{wave_index}: submitting {len(batch_nodes)} candidate node(s): "
            + ", ".join(node.id for node in batch_nodes)
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
                    parent_promotion_cache=parent_promotion_cache,
                    enable_parent_promotion=False,
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
            if enable_parent_promotion:
                current_graph = _promote_informal_parents_with_verified_children(
                    graph=current_graph,
                    planning_backend=planning_backend,
                    parent_promotion_cache=parent_promotion_cache,
                    blocked_parent_ids=attempted_ids,
                )
            _emit_update(current_graph, outcome.node_id, outcome.artifact, on_update)
            _progress(
                f"Aristotle node {outcome.node_id}: completed with status "
                f"{outcome.artifact.verification.status} as {outcome.artifact.faithfulness_classification}"
            )

        if node_ids is not None:
            break

    return MultiFormalizationOutcome(graph=current_graph, outcomes=outcomes)


def _select_dependency_aware_aristotle_wave(
    *,
    graph: ProofGraph,
    remaining_nodes: list[ProofNode],
    attempted_ids: set[str],
) -> list[ProofNode]:
    """Return the next parallel Aristotle wave, favoring dependency leaves first."""

    if not remaining_nodes:
        return []

    pending_ids = {node.id for node in remaining_nodes if node.id not in attempted_ids}
    ready_nodes = [
        node
        for node in remaining_nodes
        if not any(
            edge.source_id == node.id
            and edge.target_id in pending_ids
            for edge in graph.edges
        )
    ]
    if ready_nodes:
        return ready_nodes

    _progress(
        "Aristotle dependency-aware scheduler: no leaf-ready candidates found; "
        "falling back to the remaining candidate frontier"
    )
    return remaining_nodes


def _build_coverage_expansion_feedback(
    *,
    node: ProofNode,
    artifact: FormalArtifact,
    assessment: CombinedFormalizationAssessment | None = None,
) -> str:
    sketch = build_node_coverage_sketch(node)
    component_lines = "\n".join(
        f"- [{component.kind}] {component.text}" for component in sketch.components
    ) or "- Broaden coverage toward the full node."
    prompt_parts = [
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
    ]
    if assessment is not None and assessment.reason.strip():
        prompt_parts.extend(
            [
                "Why the current theorem was judged narrower than the parent node:",
                assessment.reason.strip(),
            ]
        )
        if assessment.certifies_main_burden:
            prompt_parts.append(
                "The current theorem already appears to certify the main technical burden. Focus the expansion on the "
                "remaining interface, lift, assembly, or parent-shaped packaging gap that turns this verified core into "
                "a theorem that actually matches the parent node. Do not spend the attempt merely reproving or lightly "
                "repackaging the same verified core."
            )
        else:
            prompt_parts.append(
                "The current theorem does not yet cover enough of the parent node. Use the reason above to identify "
                "the missing local burden and expand specifically toward that gap rather than wandering to a different theorem family."
            )
    prompt_parts.extend(
        [
            "Potential missing substeps from the parent node:",
            component_lines,
        ]
    )
    return "\n\n".join(prompt_parts)


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
