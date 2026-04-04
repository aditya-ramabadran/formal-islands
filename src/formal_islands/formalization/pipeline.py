"""Prompt builders for single-node formalization requests."""

from __future__ import annotations

import json

from formal_islands.backends import StructuredBackend, StructuredBackendRequest
from formal_islands.formalization.schemas import FormalizationResult
from formal_islands.models import FormalArtifact, ProofGraph, VerificationResult


FORMALIZATION_SYSTEM_PROMPT = (
    "You are formalizing a single proof node in Lean 4 with Mathlib. "
    "Return only JSON matching the schema. "
    "Keep the formalization local and conservative."
)


def build_formalization_request(
    graph: ProofGraph,
    node_id: str,
    compiler_feedback: str | None = None,
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
    ]
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
        if child.id in children
    ]

    prompt = "\n\n".join(
        [
            f"Theorem title: {graph.theorem_title}",
            f"Theorem statement:\n{graph.theorem_statement}",
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
            "Immediate parents:",
            json.dumps(parent_summaries, indent=2),
            "Immediate children:",
            json.dumps(child_summaries, indent=2),
            (
                "Return a JSON object with keys lean_theorem_name, lean_statement, and lean_code."
            ),
            (
                "The Lean code should be self-contained for a scratch file inside a local Mathlib "
                "project and should include any imports it relies on."
            ),
            compiler_feedback or "",
            "Produce a local Lean theorem for this node only.",
        ]
    )

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
) -> FormalArtifact:
    """Validate a backend-produced formalization for a single candidate node."""

    response = backend.run_structured(
        build_formalization_request(
            graph=graph,
            node_id=node_id,
            compiler_feedback=compiler_feedback,
        )
    )
    formalization = FormalizationResult.model_validate(response.payload)
    return FormalArtifact(
        lean_theorem_name=formalization.lean_theorem_name,
        lean_statement=formalization.lean_statement,
        lean_code=formalization.lean_code,
        verification=VerificationResult(),
        attempt_history=[],
    )
