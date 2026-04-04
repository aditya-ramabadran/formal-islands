"""Prompt builders and validation pipelines for graph extraction."""

from __future__ import annotations

import json
import re
from collections import defaultdict

from formal_islands.backends import StructuredBackend, StructuredBackendRequest
from formal_islands.extraction.schemas import (
    CandidateSelectionResult,
    ExtractedProofGraph,
)
from formal_islands.models import ProofEdge, ProofGraph, ProofNode


EXTRACTION_SYSTEM_PROMPT = (
    "Convert the user's theorem statement and informal proof into a dependency graph. "
    "Return only JSON that matches the supplied schema. "
    "Do not add candidate-formalization or formal-artifact fields. "
    "Optimize for the smallest faithful graph. "
    "Preserve the user's mathematical notation, especially LaTeX delimiters and formulas, whenever possible."
)

CANDIDATE_SELECTION_SYSTEM_PROMPT = (
    "Read the proof graph and select conservative local formalization candidates. "
    "Prefer the smallest useful candidate set, with technical, self-contained, low-dependency nodes. "
    "Avoid selecting near-duplicate parent/child claims or generic library facts unless they are the clearest local island. "
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
            (
                "Use the smallest faithful graph. A node should exist only if it is the root theorem, "
                "a nontrivial intermediate claim, a meaningful candidate formal island, a reused claim, "
                "a separate review obligation, or a conceptually distinct proof step that improves the report."
            ),
            (
                "Do not create separate nodes for bare assumptions, local variable setup, trivial restatements "
                "of the parent claim, immediate corollaries of a single child with no extra content, one-line "
                "substitutions, goal-under-assumptions restatements, or duplicate near-equivalent claims."
            ),
            (
                "For a tiny proof, prefer a single root node unless a second node for a reusable or conceptually "
                "distinct lemma clearly improves the graph."
            ),
            (
                "Preserve the original mathematical notation in your output whenever feasible. "
                "Do not normalize LaTeX into plain ASCII unless absolutely necessary."
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
    graph = ProofGraph(
        theorem_title=extracted.theorem_title,
        theorem_statement=theorem_statement,
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
    return simplify_proof_graph(graph)


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
            (
                "Keep the candidate set minimal. For tiny graphs, usually choose exactly one candidate unless "
                "a clearly distinct second candidate is justified."
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


ASSUMPTION_KEYWORDS = (
    "assumption",
    "assumptions",
    "hypothesis",
    "hypotheses",
    "given that",
)

DERIVED_RESTATEMENT_KEYWORDS = (
    "derived",
    "conclusion under assumptions",
    "goal",
    "under assumptions",
    "it suffices",
    "therefore",
)


def simplify_proof_graph(graph: ProofGraph) -> ProofGraph:
    """Deterministically collapse obviously over-segmented extraction output."""

    current = graph
    while True:
        updated = _remove_assumption_nodes(current)
        updated = _collapse_trivial_restatement_nodes(updated)
        if updated == current:
            return updated
        current = updated


def _remove_assumption_nodes(graph: ProofGraph) -> ProofGraph:
    incoming = _incoming_edges(graph)
    outgoing = _outgoing_edges(graph)
    removable_ids = {
        node.id
        for node in graph.nodes
        if node.id != graph.root_node_id
        and not outgoing.get(node.id)
        and _looks_like_assumption_node(node)
    }
    if not removable_ids:
        return graph

    filtered_nodes = [node for node in graph.nodes if node.id not in removable_ids]
    filtered_edges = [
        edge
        for edge in graph.edges
        if edge.source_id not in removable_ids and edge.target_id not in removable_ids
    ]
    return graph.model_copy(update={"nodes": filtered_nodes, "edges": filtered_edges})


def _collapse_trivial_restatement_nodes(graph: ProofGraph) -> ProofGraph:
    incoming = _incoming_edges(graph)
    outgoing = _outgoing_edges(graph)
    node_by_id = {node.id: node for node in graph.nodes}

    for node in graph.nodes:
        if node.id == graph.root_node_id:
            continue
        parents = incoming.get(node.id, [])
        if len(parents) != 1:
            continue
        parent_edge = parents[0]
        parent = node_by_id[parent_edge.source_id]
        if not _is_trivial_restatement(node=node, parent=parent):
            continue

        child_edges = outgoing.get(node.id, [])
        updated_parent = parent
        combined_proof = _combine_proof_text(parent.informal_proof_text, node.informal_proof_text)
        if combined_proof != parent.informal_proof_text:
            updated_parent = parent.model_copy(update={"informal_proof_text": combined_proof})

        updated_nodes = []
        for existing_node in graph.nodes:
            if existing_node.id == node.id:
                continue
            if existing_node.id == parent.id:
                updated_nodes.append(updated_parent)
            else:
                updated_nodes.append(existing_node)

        new_edges = []
        for edge in graph.edges:
            if edge.source_id == node.id or edge.target_id == node.id:
                continue
            new_edges.append(edge)
        for child_edge in child_edges:
            if child_edge.target_id == parent.id:
                continue
            new_edges.append(
                ProofEdge(
                    source_id=parent.id,
                    target_id=child_edge.target_id,
                    label=child_edge.label,
                    explanation=child_edge.explanation,
                )
            )

        deduped_edges = _dedupe_edges(new_edges)
        return graph.model_copy(update={"nodes": updated_nodes, "edges": deduped_edges})

    return graph


def _looks_like_assumption_node(node: ProofNode) -> bool:
    combined = " ".join(
        part
        for part in [node.title, node.display_label or "", node.informal_statement, node.informal_proof_text]
        if part
    )
    normalized = _normalize_text(combined)
    if any(keyword in normalized for keyword in ASSUMPTION_KEYWORDS):
        return True
    return "these are the hypotheses" in normalized or "assumed in the proof" in normalized


def _is_trivial_restatement(node: ProofNode, parent: ProofNode) -> bool:
    parent_statement = _normalize_statement(parent.informal_statement)
    node_statement = _normalize_statement(node.informal_statement)
    parent_consequent = _extract_consequent(parent.informal_statement)
    node_text = _normalize_text(
        " ".join([node.title, node.display_label or "", node.informal_proof_text])
    )

    if node_statement and parent_consequent and node_statement == parent_consequent:
        return True
    if node_statement and parent_statement and node_statement == parent_statement:
        return True
    if any(keyword in node_text for keyword in DERIVED_RESTATEMENT_KEYWORDS):
        if node_statement and parent_consequent and node_statement in {parent_consequent, parent_statement}:
            return True
    return False


def _combine_proof_text(parent_text: str, child_text: str) -> str:
    normalized_parent = _normalize_text(parent_text)
    normalized_child = _normalize_text(child_text)
    if not normalized_child or normalized_child in normalized_parent:
        return parent_text
    return f"{parent_text} {child_text}".strip()


def _incoming_edges(graph: ProofGraph) -> dict[str, list[ProofEdge]]:
    edges: dict[str, list[ProofEdge]] = defaultdict(list)
    for edge in graph.edges:
        edges[edge.target_id].append(edge)
    return edges


def _outgoing_edges(graph: ProofGraph) -> dict[str, list[ProofEdge]]:
    edges: dict[str, list[ProofEdge]] = defaultdict(list)
    for edge in graph.edges:
        edges[edge.source_id].append(edge)
    return edges


def _dedupe_edges(edges: list[ProofEdge]) -> list[ProofEdge]:
    deduped: list[ProofEdge] = []
    seen: set[tuple[str, str, str | None, str | None]] = set()
    for edge in edges:
        key = (edge.source_id, edge.target_id, edge.label, edge.explanation)
        if key in seen or edge.source_id == edge.target_id:
            continue
        seen.add(key)
        deduped.append(edge)
    return deduped


def _normalize_statement(text: str) -> str:
    return _normalize_text(text)


def _extract_consequent(text: str) -> str:
    match = re.search(r"\bif\b.*\bthen\b(?P<consequent>.+)$", text, flags=re.IGNORECASE)
    if not match:
        return ""
    return _normalize_text(match.group("consequent"))


def _normalize_text(text: str) -> str:
    lowered = text.lower()
    lowered = lowered.replace("≤", "<=").replace("≥", ">=")
    lowered = re.sub(r"[^a-z0-9<>=+\-*/ ]+", " ", lowered)
    return " ".join(lowered.split())
