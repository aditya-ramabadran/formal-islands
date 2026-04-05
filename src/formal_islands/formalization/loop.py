"""Bounded single-node formalization loop."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable

from formal_islands.backends import AgenticStructuredBackend, BackendError, StructuredBackend
from formal_islands.formalization.agentic import (
    recover_agentic_artifact_from_scratch_file,
    request_agentic_formalization,
)
from formal_islands.formalization.lean import LeanVerifier
from formal_islands.formalization.pipeline import (
    FaithfulnessClassification,
    FormalizationFaithfulnessError,
    request_concrete_sublemma_summary,
    request_node_formalization,
)
from formal_islands.models import FormalArtifact, ProofEdge, ProofGraph, ProofNode, VerificationResult


@dataclass(frozen=True)
class FormalizationOutcome:
    """Result summary for a single-node bounded formalization run."""

    graph: ProofGraph
    node_id: str
    artifact: FormalArtifact


@dataclass(frozen=True)
class MultiFormalizationOutcome:
    """Result summary for a sequential multi-node formalization pass."""

    graph: ProofGraph
    outcomes: list[FormalizationOutcome]


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
    mode: str = "auto",
) -> FormalizationOutcome:
    """Attempt to formalize and verify one candidate node with bounded retries."""

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if mode not in {"auto", "agentic", "structured"}:
        raise ValueError("mode must be one of: auto, agentic, structured")

    if _should_use_agentic_formalization(backend=backend, mode=mode):
        return _formalize_candidate_node_agentic(
            backend=backend,
            verifier=verifier,
            graph=graph,
            node_id=node_id,
            on_update=on_update,
        )

    return _formalize_candidate_node_structured(
        backend=backend,
        verifier=verifier,
        graph=graph,
        node_id=node_id,
        max_attempts=max_attempts,
        on_update=on_update,
    )


def formalize_candidate_nodes(
    *,
    backend: StructuredBackend,
    verifier: LeanVerifier,
    graph: ProofGraph,
    node_ids: list[str] | None = None,
    max_attempts: int = MAX_TOTAL_FORMALIZATION_ATTEMPTS,
    on_update: FormalizationUpdateCallback | None = None,
    mode: str = "auto",
) -> MultiFormalizationOutcome:
    """Formalize multiple candidate nodes sequentially, reusing the updated graph each time."""

    current_graph = graph
    outcomes: list[FormalizationOutcome] = []
    target_ids = node_ids or [
        node.id
        for node in sorted(
            [node for node in current_graph.nodes if node.status == "candidate_formal"],
            key=lambda node: ((node.formalization_priority or 999), node.id),
        )
    ]

    for node_id in target_ids:
        current_node = next((node for node in current_graph.nodes if node.id == node_id), None)
        if current_node is None or current_node.status != "candidate_formal":
            continue
        outcome = formalize_candidate_node(
            backend=backend,
            verifier=verifier,
            graph=current_graph,
            node_id=node_id,
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
        expanded_artifact = _attempt_structured_coverage_expansion(
            backend=backend,
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

    assert latest_artifact is not None
    return FormalizationOutcome(graph=current_graph, node_id=node_id, artifact=latest_artifact)


def _formalize_candidate_node_agentic(
    *,
    backend: AgenticStructuredBackend,
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
                updated_graph = _integrate_successful_formalization(
                    graph=current_graph,
                    backend=backend,
                    node_id=node_id,
                    artifact=salvaged_artifact,
                    verification_status=salvaged_artifact.verification.status,
                )
                _emit_update(updated_graph, node_id, salvaged_artifact, on_update)
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
                return FormalizationOutcome(graph=current_graph, node_id=node_id, artifact=artifact)
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
        expanded_artifact = _attempt_agentic_coverage_expansion(
            backend=backend,
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
            node_id=node_id,
            artifact=artifact,
            verification_status=verification.status,
        )
        _emit_update(updated_graph, node_id, artifact, on_update)
        return FormalizationOutcome(graph=updated_graph, node_id=node_id, artifact=artifact)

    raise AssertionError("agentic formalization loop should always return within two attempts")


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
    backend: StructuredBackend | None,
    node_id: str,
    artifact: FormalArtifact,
    verification_status: str,
) -> ProofGraph:
    if verification_status != "verified":
        return _update_node(graph, node_id, "formal_failed", artifact)

    if artifact.faithfulness_classification == FaithfulnessClassification.FULL_NODE:
        return _update_node(graph, node_id, "formal_verified", artifact)

    if artifact.faithfulness_classification == FaithfulnessClassification.CONCRETE_SUBLEMMA:
        return _promote_concrete_sublemma(
            graph=graph,
            backend=backend,
            parent_node_id=node_id,
            artifact=artifact,
        )

    return _update_node(graph, node_id, "formal_failed", artifact)


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


def _attempt_structured_coverage_expansion(
    *,
    backend: StructuredBackend,
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


def _build_coverage_expansion_feedback(*, node: ProofNode, artifact: FormalArtifact) -> str:
    targets = _node_coverage_targets(node)
    target_lines = "\n".join(f"- {item}" for item in targets) or "- Broaden coverage toward the full node."
    return "\n\n".join(
        [
            "Coverage expansion follow-up:",
            (
                "A verified Lean theorem was accepted as a narrower concrete local core, not as full-node coverage. "
                "Continue from the already verified code and try to enlarge coverage upward while staying in the same concrete setting."
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
            target_lines,
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
