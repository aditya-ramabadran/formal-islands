"""One-shot agentic Codex worker for local Lean formalization."""

from __future__ import annotations

import json
import re
from pathlib import Path

from formal_islands.backends import BackendOutputError, CodexCLIBackend, StructuredBackendRequest
from formal_islands.formalization.pipeline import (
    FormalizationFaithfulnessError,
    enforce_formalization_faithfulness,
)
from formal_islands.formalization.schemas import AgenticFormalizationResult
from formal_islands.models import FormalArtifact, ProofGraph, VerificationResult


AGENTIC_FORMALIZATION_SYSTEM_PROMPT = (
    "You are a focused Lean 4 formalization worker operating inside a local Mathlib project. "
    "You may edit files and run local commands within this one Codex run. "
    "Begin with a short explicit planning pass, then formalize. "
    "Work on exactly one scratch file, keep the formalization local and faithful to the target node, "
    "prefer the most concrete faithful theorem you can manage, "
    "and stop when the scratch file is in its best current state. "
    "Return only JSON matching the supplied schema."
)

AGENTIC_WORKER_PLACEHOLDER = "-- agentic formalization worker scratch file\n"


def agentic_worker_plan_path(scratch_file_path: Path) -> Path:
    """Return the sibling markdown plan file used by the agentic worker."""

    return scratch_file_path.with_name(f"{scratch_file_path.stem}_plan.md")


def build_agentic_formalization_request(
    *,
    graph: ProofGraph,
    node_id: str,
    workspace_root: Path,
    scratch_file_path: Path,
    faithfulness_feedback: str | None = None,
    previous_lean_code: str | None = None,
) -> StructuredBackendRequest:
    node = next((candidate for candidate in graph.nodes if candidate.id == node_id), None)
    if node is None:
        raise ValueError(f"node '{node_id}' was not found in the graph")
    if node.status != "candidate_formal":
        raise ValueError(f"node '{node_id}' must be candidate_formal before formalization")

    plan_file_path = agentic_worker_plan_path(scratch_file_path).resolve()

    parents = [edge.source_id for edge in graph.edges if edge.target_id == node_id]
    children = [edge.target_id for edge in graph.edges if edge.source_id == node_id]
    parent_summaries = [
        {
            "id": parent.id,
            "title": parent.title,
            "informal_statement": parent.informal_statement,
        }
        for parent in graph.nodes
        if parent.id in parents
    ][:1]
    child_summaries = [
        {
            "id": child.id,
            "title": child.title,
            "informal_statement": child.informal_statement,
            "formal_artifact": (
                child.formal_artifact.model_dump(mode="json") if child.formal_artifact else None
            ),
        }
        for child in graph.nodes
        if child.id in children and child.formal_artifact is not None
    ][:1]

    prompt = "\n\n".join(
        [
            f"Theorem title: {graph.theorem_title}",
            f"Ambient theorem statement:\n{graph.theorem_statement}",
            "Target node:",
            json.dumps(
                {
                    "id": node.id,
                    "title": node.title,
                    "informal_statement": node.informal_statement,
                    "informal_proof_text": node.informal_proof_text,
                    "formalization_priority": node.formalization_priority,
                    "formalization_rationale": node.formalization_rationale,
                },
                indent=2,
            ),
            (
                "Immediate parent summary:\n" + json.dumps(parent_summaries[0], indent=2)
                if parent_summaries
                else "Immediate parent summary:\n[]"
            ),
            (
                "Verified child context:\n" + json.dumps(child_summaries[0], indent=2)
                if child_summaries
                else "Verified child context:\n[]"
            ),
            f"Lean workspace root: {workspace_root}",
            f"Scratch file to create and edit: {scratch_file_path}",
            f"Plan markdown file to create and maintain: {plan_file_path}",
            (
                "Operate only inside the Lean workspace above, and only edit the specified scratch file. "
                "Do not modify other repository files, except for the required plan markdown file above."
            ),
            (
                "Within this single run, you may inspect local files, write the scratch file, run "
                "`lake env lean <scratch_file_path>` from the Lean workspace root, read compiler feedback, "
                "and revise the same file until it succeeds or you run out of time."
            ),
            (
                "Start with a lightweight planning pass before serious Lean formalization. Create the plan markdown "
                "file above first, then use it to decide the concrete theorem shape you will actually target."
            ),
            (
                "Keep the plan concise. Include short sections for: target node/theorem, ambient setting to preserve, "
                "important symbols or quantities that must remain in the theorem statement, abstractions to avoid, "
                "intended theorem shape (whole node vs concrete sublemma), likely proof route, and likely Mathlib "
                "lemmas or APIs to search for."
            ),
            (
                "Default to the most literal whole-node theorem shape that directly mirrors the target node's stated "
                "mathematical claim. Treat that literal whole-node target as the starting point, not as an optional stretch goal."
            ),
            (
                "Only fall back to a narrower concrete sublemma if your local scouting or compiler experiments show that "
                "the literal whole-node target is infeasible in the available time or Mathlib surface area."
            ),
            (
                "If you do fall back, record that explicitly in the plan file: note the literal whole-node target you tried, "
                "why it looked infeasible, and why the narrower replacement still captures meaningful inferential load."
            ),
            (
                "Use the plan to do brief local scouting before you commit to the final theorem. You may create tiny "
                "scratch experiments, run `#check`, grep or ripgrep for likely lemma names, inspect imports, and read "
                "nearby Mathlib files when that helps you choose the right concrete theorem shape."
            ),
            (
                "Do not spend too long planning. This is a short planning layer meant to sharpen the theorem choice "
                "and proof route before writing serious Lean code."
            ),
            (
                "If you substantially change direction during the run, preserve visible plan history by appending a "
                "new labeled section to the same markdown file instead of silently overwriting the old plan."
            ),
            (
                "Prefer narrow, specific imports that match the identifiers actually used in the theorem. "
                "Do not default to `import Mathlib` for a small local theorem when a few focused imports "
                "would do, and do not guess speculative deep module paths."
            ),
            (
                "Keep the Lean output syntactically conservative. Prefer ASCII identifiers in theorem names, "
                "binder names, and hypotheses unless a non-ASCII symbol is clearly unavoidable. Avoid Unicode "
                "variable names like `λ₁` in declarations; prefer plain names such as `lambda1`. Do not invent "
                "fancy notation when ordinary Lean identifiers and explicit expressions work."
            ),
            (
                "Prefer simple theorem signatures and straightforward binder lists over elaborate notation-heavy "
                "declarations. When in doubt, choose the most boring Lean surface syntax that still states the right theorem."
            ),
            (
                "Bias strongly toward faithfulness to the target node. Reuse the node's concrete variables and "
                "hypotheses when reasonable. Do not introduce arbitrary index types, unrelated function families, "
                "or a much more generic theorem unless the node text clearly requires that abstraction."
            ),
            (
                "Preserve the ambient mathematical setting of the theorem and node. If the node is stated in a concrete "
                "setting, keep that same setting in the Lean theorem unless the node itself explicitly states a more abstract generality."
            ),
            (
                "If you simplify, simplify the local inferential step while keeping the same concrete objects and "
                "ambient setting. Prefer a concrete sublemma about the same named quantities, variables, operators, "
                "or integrals over a theorem about an arbitrary type, arbitrary measure, or unrelated families of functions."
            ),
            (
                "Do not replace a concrete statement with a generic measure-space or arbitrary ambient-type theorem "
                "unless the original node is already phrased in that abstract setting."
            ),
            (
                "Do not game the task by collapsing the node to an easy nearby side fact. If you simplify, the "
                "replacement should still carry meaningful inferential load in the parent proof."
            ),
            (
                "Before settling on a fallback theorem, spend a short attempt on the literal node-level statement or the "
                "closest direct transcription that seems syntactically realistic. Do not jump immediately to a more abstract "
                "or indirect theorem just because it is familiar."
            ),
            (
                "Return a JSON object with keys lean_theorem_name, lean_statement, final_file_path, and plan_file_path. "
                "The final_file_path must be exactly the scratch file path above, and the plan_file_path must be exactly "
                "the plan markdown path above."
            ),
        ]
        + (
            [
                (
                    "Previous faithfulness failure: the prior theorem/file was too abstract. Continue from the current "
                    "scratch file instead of starting over. Revise it in place to stay much closer to the target node's "
                    "concrete setting."
                ),
                faithfulness_feedback or "",
            ]
            if faithfulness_feedback
            else []
        )
        + (
            [
                "Current scratch file to revise:",
                f"```lean\n{previous_lean_code}\n```",
            ]
            if previous_lean_code
            else []
        )
    )

    return StructuredBackendRequest(
        prompt=prompt,
        system_prompt=AGENTIC_FORMALIZATION_SYSTEM_PROMPT,
        json_schema=AgenticFormalizationResult.model_json_schema(),
        task_name="formalize_node_agentic",
        cwd=workspace_root,
    )


def request_agentic_formalization(
    *,
    backend: CodexCLIBackend,
    graph: ProofGraph,
    node_id: str,
    workspace_root: Path,
    scratch_file_path: Path,
    faithfulness_feedback: str | None = None,
    previous_lean_code: str | None = None,
) -> FormalArtifact:
    response = backend.run_agentic_structured(
        build_agentic_formalization_request(
            graph=graph,
            node_id=node_id,
            workspace_root=workspace_root,
            scratch_file_path=scratch_file_path,
            faithfulness_feedback=faithfulness_feedback,
            previous_lean_code=previous_lean_code,
        ),
        timeout_seconds=backend.timeout_seconds,
    )
    formalization = AgenticFormalizationResult.model_validate(response.payload)

    final_path = Path(formalization.final_file_path).resolve()
    expected_path = scratch_file_path.resolve()
    plan_path = Path(formalization.plan_file_path).resolve()
    expected_plan_path = agentic_worker_plan_path(scratch_file_path).resolve()
    if final_path != expected_path:
        raise BackendOutputError(
            "Agentic formalization returned an unexpected final file path: "
            f"{formalization.final_file_path}"
        )
    if plan_path != expected_plan_path:
        raise BackendOutputError(
            "Agentic formalization returned an unexpected plan file path: "
            f"{formalization.plan_file_path}"
        )
    if not final_path.exists():
        raise BackendOutputError(
            f"Agentic formalization did not produce the expected scratch file: {final_path}"
        )
    if not plan_path.exists():
        raise BackendOutputError(
            f"Agentic formalization did not produce the expected plan markdown file: {plan_path}"
        )

    lean_code = final_path.read_text(encoding="utf-8")
    artifact = FormalArtifact(
        lean_theorem_name=formalization.lean_theorem_name,
        lean_statement=formalization.lean_statement,
        lean_code=lean_code,
        verification=VerificationResult(),
        attempt_history=[],
    )
    artifact = enforce_formalization_faithfulness(
        node=next(candidate for candidate in graph.nodes if candidate.id == node_id),
        artifact=artifact,
    )
    return artifact


def recover_agentic_artifact_from_scratch_file(
    *,
    graph: ProofGraph,
    node_id: str,
    scratch_file_path: Path,
) -> FormalArtifact | None:
    resolved_path = scratch_file_path.resolve()
    if not resolved_path.exists():
        return None

    lean_code = resolved_path.read_text(encoding="utf-8")
    if lean_code == AGENTIC_WORKER_PLACEHOLDER:
        return None

    theorem_name, theorem_statement = _extract_primary_lean_theorem(lean_code)
    if theorem_name is None or theorem_statement is None:
        return None

    artifact = FormalArtifact(
        lean_theorem_name=theorem_name,
        lean_statement=theorem_statement,
        lean_code=lean_code,
        verification=VerificationResult(),
        attempt_history=[],
    )
    return enforce_formalization_faithfulness(
        node=next(candidate for candidate in graph.nodes if candidate.id == node_id),
        artifact=artifact,
    )


def _extract_primary_lean_theorem(lean_code: str) -> tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?ms)^\s*(theorem|lemma|example)\s+([A-Za-z0-9_'.]+)(.*?)(:=\s*by|:=|where\b)"
    )
    match = pattern.search(lean_code)
    if match is None:
        return None, None

    keyword = match.group(1)
    theorem_name = match.group(2)
    signature_tail = match.group(3).rstrip()
    statement = f"{keyword} {theorem_name}{signature_tail}".strip()
    statement = re.sub(r"\s+\n", "\n", statement)
    return theorem_name, statement
