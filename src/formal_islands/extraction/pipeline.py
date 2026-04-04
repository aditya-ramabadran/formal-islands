"""Prompt builders and validation pipelines for graph extraction."""

from __future__ import annotations

import json

from formal_islands.backends import StructuredBackend, StructuredBackendRequest
from formal_islands.extraction.schemas import (
    CandidateSelectionResult,
    ExtractedProofGraph,
)
from formal_islands.models import ProofEdge, ProofGraph, ProofNode


EXTRACTION_SYSTEM_PROMPT = (
    "Convert the user's theorem statement and informal proof into a dependency graph. "
    "Return only JSON that matches the supplied schema. "
    "Do not add candidate-formalization or formal-artifact fields."
)

CANDIDATE_SELECTION_SYSTEM_PROMPT = (
    "Read the proof graph and select conservative local formalization candidates. "
    "Prefer technical, self-contained, low-dependency nodes. "
    "Return only JSON that matches the supplied schema."
)


def build_extraction_request(
    theorem_statement: str,
    raw_proof_text: str,
    theorem_title_hint: str = "Untitled theorem",
) -> StructuredBackendRequest:
    """Build a structured request for graph extraction."""

    prompt = "\n\n".join(
        [
            f"Theorem title hint: {theorem_title_hint}",
            f"Theorem statement:\n{theorem_statement}",
            f"Raw informal proof:\n{raw_proof_text}",
            (
                "Return a JSON object with top-level keys theorem_title, theorem_statement, "
                "root_node_id, nodes, and edges."
            ),
            (
                "Each node must include id, title, informal_statement, informal_proof_text, "
                "and optional display_label. Each edge must include source_id, target_id, "
                "and optional label and explanation."
            ),
            (
                "Represent the proof as dependency edges where a source node depends on its target node. "
                "Keep every node informal at this stage."
            ),
        ]
    )
    return StructuredBackendRequest(
        prompt=prompt,
        system_prompt=EXTRACTION_SYSTEM_PROMPT,
        json_schema=ExtractedProofGraph.model_json_schema(),
        task_name="extract_graph",
    )


def extract_proof_graph(
    backend: StructuredBackend,
    theorem_statement: str,
    raw_proof_text: str,
    theorem_title_hint: str = "Untitled theorem",
) -> ProofGraph:
    """Call a backend for graph extraction and validate the result."""

    response = backend.run_structured(
        build_extraction_request(
            theorem_statement=theorem_statement,
            raw_proof_text=raw_proof_text,
            theorem_title_hint=theorem_title_hint,
        )
    )
    extracted = ExtractedProofGraph.model_validate(response.payload)
    return ProofGraph(
        theorem_title=extracted.theorem_title,
        theorem_statement=extracted.theorem_statement,
        root_node_id=extracted.root_node_id,
        nodes=[
            ProofNode(
                id=node.id,
                title=node.title,
                informal_statement=node.informal_statement,
                informal_proof_text=node.informal_proof_text,
                display_label=node.display_label,
                status="informal",
            )
            for node in extracted.nodes
        ],
        edges=[
            ProofEdge(
                source_id=edge.source_id,
                target_id=edge.target_id,
                label=edge.label,
                explanation=edge.explanation,
            )
            for edge in extracted.edges
        ],
    )


def build_candidate_selection_request(graph: ProofGraph) -> StructuredBackendRequest:
    """Build a structured request for the candidate-selection pass."""

    graph_payload = graph.model_dump(mode="json")
    prompt = "\n\n".join(
        [
            "Proof graph JSON:",
            json.dumps(graph_payload, indent=2),
            (
                "Return a JSON object with a single top-level key candidates. "
                "Each candidate must include node_id, priority, and rationale."
            ),
            (
                "Choose only nodes that look like strong candidates for local Lean formalization "
                "in a first prototype."
            ),
        ]
    )
    return StructuredBackendRequest(
        prompt=prompt,
        system_prompt=CANDIDATE_SELECTION_SYSTEM_PROMPT,
        json_schema=CandidateSelectionResult.model_json_schema(),
        task_name="select_candidates",
    )


def select_formalization_candidates(
    backend: StructuredBackend,
    graph: ProofGraph,
) -> ProofGraph:
    """Apply validated candidate-selection metadata to matching nodes."""

    response = backend.run_structured(build_candidate_selection_request(graph))
    selection = CandidateSelectionResult.model_validate(response.payload)

    candidate_map = {candidate.node_id: candidate for candidate in selection.candidates}
    graph_node_ids = {node.id for node in graph.nodes}
    unknown_ids = sorted(set(candidate_map) - graph_node_ids)
    if unknown_ids:
        raise ValueError(
            f"candidate selection referenced unknown node ids: {', '.join(unknown_ids)}"
        )

    updated_nodes = []
    for node in graph.nodes:
        candidate = candidate_map.get(node.id)
        if candidate is None:
            updated_nodes.append(node)
            continue

        new_status = "candidate_formal" if node.status == "informal" else node.status
        updated_nodes.append(
            node.model_copy(
                update={
                    "status": new_status,
                    "formalization_priority": candidate.priority,
                    "formalization_rationale": candidate.rationale,
                }
            )
        )

    return graph.model_copy(update={"nodes": updated_nodes})
