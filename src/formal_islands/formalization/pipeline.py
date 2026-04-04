"""Prompt builders for single-node formalization requests."""

from __future__ import annotations

import json
import re

from formal_islands.backends import StructuredBackend, StructuredBackendRequest
from formal_islands.formalization.schemas import FormalizationResult
from formal_islands.models import FormalArtifact, ProofGraph, VerificationResult


FORMALIZATION_SYSTEM_PROMPT = (
    "You are formalizing a single proof node in Lean 4 with Mathlib. "
    "Return only JSON matching the schema. "
    "Keep the formalization local and conservative. "
    "Stay close to the node's actual mathematical content, avoid gratuitous abstraction, "
    "and do not game the task by replacing the node with an easier but low-value nearby fact. "
    "Treat the local Lean workspace as the source of truth for available imports and prefer "
    "small, concrete, stable import lists over broad or speculative boilerplate."
)


class FormalizationFaithfulnessError(ValueError):
    """Raised when a proposed formalization drifts too far from the node text."""

    def __init__(self, message: str, artifact: FormalArtifact):
        super().__init__(message)
        self.artifact = artifact


def build_formalization_request(
    graph: ProofGraph,
    node_id: str,
    compiler_feedback: str | None = None,
    previous_lean_code: str | None = None,
) -> StructuredBackendRequest:
    """Gather bounded local context for a single-node Lean formalization request."""

    node = next((candidate for candidate in graph.nodes if candidate.id == node_id), None)
    if node is None:
        raise ValueError(f"node '{node_id}' was not found in the graph")
    if node.status != "candidate_formal":
        raise ValueError(f"node '{node_id}' must be candidate_formal before formalization")

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

    prompt_parts = [
            f"Theorem title: {graph.theorem_title}",
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
            (
                "Return a JSON object with keys lean_theorem_name, lean_statement, and lean_code."
            ),
            (
                "The Lean code should be self-contained for a scratch file inside a local Mathlib "
                "project and should include any imports it relies on."
            ),
            (
                "Prefer narrow, specific imports that match the identifiers actually used in the theorem. "
                "Do not default to `import Mathlib` for a small local theorem when a few focused imports "
                "would do."
            ),
            (
                "Do not guess deep or speculative module paths just to be safe. Only import modules that are "
                "directly motivated by the code you are writing, and keep the import list short."
            ),
            (
                "Bias strongly toward faithfulness to the target node. Reuse the node's concrete "
                "variables and hypotheses when reasonable. Do not introduce arbitrary index types, "
                "unrelated function families, or a much more generic theorem unless the node text "
                "clearly requires that abstraction."
            ),
            (
                "If the full analytic statement is too heavy, prefer a smaller faithful local theorem "
                "or a concrete algebraic consequence that still matches the node, rather than a highly "
                "abstract schematic statement."
            ),
            (
                "If the node mixes a reusable source estimate with a more concrete downstream application, "
                "prefer the concrete downstream application when it is the part actually used by the parent proof."
            ),
            (
                "Do not collapse the task to an easy side consequence or a theorem that certifies only a small "
                "fragment of the surrounding local argument just because it compiles. If you simplify, the "
                "replacement should still carry meaningful inferential load in the parent proof."
            ),
    ]
    if previous_lean_code:
        prompt_parts.extend(
            [
                (
                    "Previous failed Lean file to revise. Make the smallest changes needed to fix the reported issue. "
                    "Preserve the theorem statement and overall structure unless the compiler error forces a change."
                ),
                f"```lean\n{previous_lean_code}\n```",
            ]
        )
    if compiler_feedback:
        prompt_parts.append(compiler_feedback)
    prompt_parts.append("Produce a local Lean theorem for this node only.")
    prompt = "\n\n".join(prompt_parts)

    return StructuredBackendRequest(
        prompt=prompt,
        system_prompt=FORMALIZATION_SYSTEM_PROMPT,
        json_schema=FormalizationResult.model_json_schema(),
        task_name="formalize_node",
    )


def request_node_formalization(
    backend: StructuredBackend,
    graph: ProofGraph,
    node_id: str,
    compiler_feedback: str | None = None,
    previous_lean_code: str | None = None,
) -> FormalArtifact:
    """Validate a backend-produced formalization for a single candidate node."""

    node = next((candidate for candidate in graph.nodes if candidate.id == node_id), None)
    if node is None:
        raise ValueError(f"node '{node_id}' was not found in the graph")

    response = backend.run_structured(
        build_formalization_request(
            graph=graph,
            node_id=node_id,
            compiler_feedback=compiler_feedback,
            previous_lean_code=previous_lean_code,
        )
    )
    formalization = FormalizationResult.model_validate(response.payload)
    artifact = FormalArtifact(
        lean_theorem_name=formalization.lean_theorem_name,
        lean_statement=formalization.lean_statement,
        lean_code=formalization.lean_code,
        verification=VerificationResult(),
        attempt_history=[],
    )
    _enforce_formalization_faithfulness(node=node, artifact=artifact)
    return artifact


def _enforce_formalization_faithfulness(node, artifact: FormalArtifact) -> None:
    issues = _collect_faithfulness_issues(node, artifact)
    if not issues:
        return
    raise FormalizationFaithfulnessError(
        " ".join(
            [
                "Formalization drifted too far from the target node.",
                *issues,
            ]
        ),
        artifact=artifact,
    )


def _collect_faithfulness_issues(node, artifact: FormalArtifact) -> list[str]:
    issues: list[str] = []
    lean_text = f"{artifact.lean_statement}\n{artifact.lean_code}"
    node_text = " ".join([node.title, node.informal_statement, node.informal_proof_text]).lower()

    if "Type*" in lean_text or re.search(r"\bType u\b|\bType v\b", lean_text):
        issues.append(
            "Avoid introducing arbitrary `Type*` parameters when the node describes a concrete local claim."
        )

    if ("InnerProductSpace" in lean_text or "NormedAddCommGroup" in lean_text) and not any(
        marker in node_text for marker in ("inner product", "hilbert", "normed")
    ):
        issues.append(
            "Avoid translating the node into an arbitrary normed/inner-product space unless the node itself calls for that abstraction."
        )

    suspicious_function_families: list[str] = []
    for match in re.finditer(r"[\(\{]([^:\)\}]+)\s*:\s*([^\)\}]+)[\)\}]", artifact.lean_statement):
        names = [name for name in match.group(1).split() if name]
        annotation = match.group(2)
        if "→" not in annotation and "->" not in annotation:
            continue
        for name in names:
            lowered = name.lower()
            if len(name) <= 1 or lowered in node_text or lowered.startswith("h"):
                continue
            suspicious_function_families.append(name)

    if len(set(suspicious_function_families)) >= 2:
        issues.append(
            "Avoid replacing the node with unrelated families of functions or indexed maps absent from the original claim."
        )

    return issues
