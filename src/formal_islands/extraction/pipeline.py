"""Prompt builders and validation pipelines for graph extraction."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict
from dataclasses import dataclass

from formal_islands.backends import StructuredBackend, StructuredBackendRequest
from formal_islands.extraction.schemas import (
    CandidateSelectionResult,
    ExtractedProofGraph,
    PlannedProofGraph,
    RefinedLocalClaimProposal,
    RefinedLocalClaimResult,
)
from formal_islands.formalization.pipeline import build_node_coverage_sketch
from formal_islands.models import ProofEdge, ProofGraph, ProofNode
from formal_islands.progress import progress


@dataclass(frozen=True)
class TheoremPlanningArtifacts:
    """Explicit theorem-level planning outputs from a single backend call."""

    extracted_graph: ProofGraph
    candidate_graph: ProofGraph


EXTRACTION_SYSTEM_PROMPT = (
    "Convert the user's theorem statement and informal proof into a dependency graph. "
    "Return only JSON that matches the supplied schema. "
    "Do not add candidate-formalization or formal-artifact fields. "
    "Optimize for a compact, faithful, formalization-sensitive graph. "
    "Preserve the user's mathematical notation, especially LaTeX delimiters and formulas, whenever possible."
)

CANDIDATE_SELECTION_SYSTEM_PROMPT = (
    "Read the proof graph and select conservative local formalization candidates. "
    "Prefer a small, high-yield candidate set with technical, self-contained, inferentially important nodes. "
    "Avoid selecting near-duplicate parent/child claims, generic library facts, or easy but low-value side consequences. "
    "Return only JSON that matches the supplied schema."
)

THEOREM_PLANNING_SYSTEM_PROMPT = (
    "Plan a compact, faithful, formalization-sensitive proof graph for the user's theorem. "
    "Return only JSON that matches the supplied schema. "
    "In one pass, choose the graph shape and rank the best local formalization candidates. "
    "Keep the graph readable and preserve one or two strong formal islands when they carry real inferential load. "
    "Preserve the user's mathematical notation, especially LaTeX delimiters and formulas, whenever possible."
)

LOCAL_CLAIM_CUE_PHRASES = (
    "we obtain",
    "we get",
    "hence",
    "thus",
    "therefore",
    "so",
    "which gives",
    "which yields",
    "yields",
    "gives",
    "implies",
    "deduce",
    "deduces",
    "conclude",
    "concludes",
    "substituting",
    "applying",
    "combining",
    "it follows that",
    "equivalently",
    "forcing",
)

GENERIC_CLAIM_MARKERS = (
    "for every",
    "for all",
    "for any",
    "for arbitrary",
    "for sufficiently",
    "whenever",
)

RELATION_MARKERS = ("\\le", "\\ge", "\\to", "\\Rightarrow", "\\implies", "≤", "≥", "=", "<", ">")


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
                "Use a compact, faithful, formalization-sensitive graph. A node should exist only if it is the root theorem, "
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
                "When a proof contains one or two central local technical subclaims that would make strong future "
                "formal islands, preserve them explicitly even inside an otherwise compressed graph. Good examples "
                "include concrete inequalities, integration-by-parts identities, explicit algebraic simplifications, "
                "monotonicity or concavity consequences, and concrete local estimates."
            ),
            (
                "Prefer local subclaims that are inferentially important and substantially used by the parent proof, "
                "not just easy side facts. Compactness matters, but not at the cost of losing the strongest plausible "
                "formal islands."
            ),
            (
                "Do not preserve microscopic proof-trace fragments. If you keep a local technical node, it should be "
                "substantive, mathematically recognizable, and clearly useful in the final mixed informal/formal report."
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


def build_theorem_planning_request(
    theorem_statement: str,
    raw_proof_text: str,
    theorem_title_hint: str = "Untitled theorem",
) -> StructuredBackendRequest:
    """Build a single theorem-level planning request that emits graph plus candidates."""

    prompt = "\n\n".join(
        [
            f"Theorem title hint: {theorem_title_hint}",
            f"Theorem statement:\n{theorem_statement}",
            f"Raw informal proof:\n{raw_proof_text}",
            (
                "Return one JSON object with top-level keys theorem_title, theorem_statement, "
                "root_node_id, nodes, edges, and candidates."
            ),
            (
                "The nodes and edges must follow the same informal extraction schema as before: "
                "nodes include id, title, informal_statement, informal_proof_text, and optional "
                "display_label; edges include source_id, target_id, and optional label and explanation."
            ),
            (
                "The candidates array must include objects with node_id, priority, and rationale. "
                "Priority must be an integer from 1 to 3, where 1 is highest and 3 is lowest. Do not use words like high, medium, or low. "
                "Candidates should refer only to node ids that exist in the graph you emit."
            ),
            (
                "Plan the graph and candidate ranking jointly. Optimize for a compact, faithful, "
                "formalization-sensitive graph with a small, high-yield candidate set."
            ),
            (
                "A node should exist only if it is the root theorem, a nontrivial intermediate claim, "
                "a meaningful candidate formal island, a reused claim, a separate review obligation, "
                "or a conceptually distinct proof step that improves the final mixed informal/formal report."
            ),
            (
                "Do not create separate nodes for bare assumptions, local variable setup, trivial restatements, "
                "goal-under-assumptions restatements, one-line substitutions, or duplicate near-equivalent claims."
            ),
            (
                "When choosing graph granularity, preserve one or two local technical subclaims if they are concrete, "
                "inferentially important, plausibly formalizable, and substantially used by the surrounding proof. "
                "Good examples include concrete inequalities, integration-by-parts identities, explicit algebraic "
                "simplifications, monotonicity or concavity consequences, and concrete local estimates."
            ),
            (
                "Keep the candidate set small. Prefer local, concrete, technically meaningful nodes that would reduce "
                "real human proof-checking burden. Disfavor easy side consequences, generic library facts, or claims "
                "that are much weaker than the surrounding local argument."
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
        system_prompt=THEOREM_PLANNING_SYSTEM_PROMPT,
        json_schema=PlannedProofGraph.model_json_schema(),
        task_name="plan_theorem",
    )


def extract_proof_graph(
    backend: StructuredBackend,
    theorem_statement: str,
    raw_proof_text: str,
    theorem_title_hint: str = "Untitled theorem",
) -> ProofGraph:
    """Call a backend for graph extraction and validate the result."""

    progress("extracting proof graph")
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
    graph = simplify_proof_graph(graph)
    progress("refining candidate nodes")
    return refine_candidate_nodes(graph, backend=backend)


def plan_proof_graph(
    backend: StructuredBackend,
    theorem_statement: str,
    raw_proof_text: str,
    theorem_title_hint: str = "Untitled theorem",
) -> TheoremPlanningArtifacts:
    """Run a single theorem-level planning pass and emit explicit graph artifacts."""

    progress("planning theorem graph")
    response = backend.run_structured(
        build_theorem_planning_request(
            theorem_statement=theorem_statement,
            raw_proof_text=raw_proof_text,
            theorem_title_hint=theorem_title_hint,
        )
    )
    planned = PlannedProofGraph.model_validate(response.payload)
    extracted_graph = _build_internal_graph(
        extracted=planned,
        theorem_statement=theorem_statement,
    )
    protected_ids = {candidate.node_id for candidate in planned.candidates}
    simplified_graph = simplify_proof_graph(extracted_graph, protected_node_ids=protected_ids)
    candidate_graph = _apply_candidate_selection_result(
        graph=simplified_graph,
        selection=CandidateSelectionResult(candidates=planned.candidates),
        backend=backend,
    )
    progress(
        "planning complete: "
        f"{len(simplified_graph.nodes)} nodes, {len([n for n in candidate_graph.nodes if n.status == 'candidate_formal'])} candidates"
    )
    return TheoremPlanningArtifacts(
        extracted_graph=simplified_graph,
        candidate_graph=candidate_graph,
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
                "Each candidate must include node_id, priority, and rationale, and priority must be an integer from 1 to 3 with 1 as highest priority."
            ),
            (
                "Choose only nodes that look like strong candidates for local Lean formalization "
                "in a first prototype."
            ),
            (
                "Keep the candidate set minimal. For tiny graphs, usually choose exactly one candidate unless "
                "a clearly distinct second candidate is justified."
            ),
            (
                "Prefer concrete local estimates, identities, explicit inequalities, or similarly self-contained "
                "technical steps over broader surrounding arguments when the graph already preserves them."
            ),
            (
                "Prefer nontrivial, high-yield nodes that combine multiple ingredients of the proof or discharge real "
                "inferential burden for the parent argument."
            ),
            (
                "Disfavor side consequences that certify only a small fragment of the surrounding local argument. "
                "Prefer candidates whose formalization would discharge real inferential burden for the parent proof."
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

    progress("selecting formalization candidates")
    response = backend.run_structured(build_candidate_selection_request(graph))
    selection = CandidateSelectionResult.model_validate(response.payload)
    updated = _apply_candidate_selection_result(graph=graph, selection=selection, backend=backend)
    progress(
        f"selected {len([node for node in updated.nodes if node.status == 'candidate_formal'])} candidate nodes"
    )
    return updated


def _build_internal_graph(
    *,
    extracted: ExtractedProofGraph,
    theorem_statement: str,
) -> ProofGraph:
    return ProofGraph(
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


def _apply_candidate_selection_result(
    *,
    graph: ProofGraph,
    selection: CandidateSelectionResult,
    backend: StructuredBackend | None = None,
) -> ProofGraph:
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

    updated_graph = graph.model_copy(update={"nodes": updated_nodes})
    updated_graph = _promote_high_yield_technical_candidate(updated_graph)
    updated_graph = _calibrate_candidate_set(updated_graph)
    return refine_candidate_nodes(updated_graph, backend=backend)


def build_local_refinement_request(
    *,
    graph: ProofGraph,
    parent_id: str,
    candidate_id: str,
    span_hints: list[dict[str, str | int]] | None = None,
) -> StructuredBackendRequest:
    """Ask a backend to propose one or more narrower local claims inside a broad node."""

    parent = next(node for node in graph.nodes if node.id == parent_id)
    candidate = next(node for node in graph.nodes if node.id == candidate_id)
    prompt_parts = [
        f"Theorem title: {graph.theorem_title}",
        "Parent informal node:",
        json.dumps(
            {
                "id": parent.id,
                "title": parent.title,
                "informal_statement": parent.informal_statement,
                "informal_proof_text": parent.informal_proof_text,
            },
            indent=2,
        ),
        "Broad candidate node to refine:",
        json.dumps(
            {
                "id": candidate.id,
                "title": candidate.title,
                "informal_statement": candidate.informal_statement,
                "informal_proof_text": candidate.informal_proof_text,
            },
            indent=2,
        ),
        "Coverage sketch for the broad candidate:",
        json.dumps(asdict(build_node_coverage_sketch(candidate)), indent=2),
    ]
    if span_hints:
        prompt_parts.extend(
            [
                "Deterministic span hints from the current extractor:",
                json.dumps(
                    [
                        {
                            "score": span.get("score"),
                            "segment_kind": span.get("segment_kind"),
                            "body": span.get("body"),
                            "excerpt": _context_window(
                                str(span["source_text"]),
                                int(span["start"]),
                                int(span["end"]),
                            ),
                        }
                        for span in span_hints
                    ],
                    indent=2,
                ),
            ]
        )

    prompt_parts.extend(
        [
            (
                "Propose 1 to 3 narrower concrete local subclaims that sit inside the broad candidate and still "
                "carry real inferential load for the parent node. Prefer a complete mathematical claim over a "
                "clipped fragment. Keep the same concrete setting and make each proposal substantially smaller than "
                "the broad candidate, but not trivial."
            ),
            (
                "Each proposal should include a concise title, an optional display label, a concrete informal statement, "
                "a short informal proof text, and a brief rationale explaining why it is a better local formal island."
            ),
            (
                "Do not over-abstract. Do not replace the candidate with a generic theorem that merely resembles the "
                "proof. Do not output a clipped sentence ending in ellipsis unless that fragment is itself the full claim."
            ),
            (
                "If you can only justify one good refined claim, return one. If multiple claims are plausible, rank them "
                "from strongest to weakest."
            ),
            "Return a JSON object with a top-level key proposals.",
        ]
    )

    return StructuredBackendRequest(
        prompt="\n\n".join(prompt_parts),
        system_prompt=(
            "You propose narrower local claims extracted from a broad informal proof node. Return only JSON matching the schema."
        ),
        json_schema=RefinedLocalClaimResult.model_json_schema(),
        task_name="refine_local_claim",
    )


def _rank_local_consequence_spans(
    *,
    graph: ProofGraph,
    candidate_id: str,
) -> list[dict[str, str | int]]:
    node_by_id = {node.id: node for node in graph.nodes}
    incoming = _incoming_edges(graph)
    candidate = node_by_id[candidate_id]
    parent_edges = incoming.get(candidate_id, [])
    if len(parent_edges) != 1:
        return []
    parent_id = parent_edges[0].source_id
    parent = node_by_id[parent_id]

    existing_statements = {
        _normalize_text(graph.theorem_statement),
        *(
            _normalize_text(node.informal_statement)
            for node in graph.nodes
            if node.id != candidate_id
        ),
    }

    source_segments = [
        ("statement", candidate.informal_statement),
        ("proof", candidate.informal_proof_text),
    ]

    ranked: list[dict[str, str | int]] = []
    for segment_kind, source_text in source_segments:
        for span in _extract_math_spans(source_text):
            score = _score_local_consequence_span(
                span=span,
                parent=parent,
                candidate=candidate,
                source_text=source_text,
                segment_kind=segment_kind,
                existing_statements=existing_statements,
            )
            if score <= 0:
                continue
            ranked.append(
                span
                | {
                    "source_text": source_text,
                    "segment_kind": segment_kind,
                    "score": score,
                    "parent_id": parent_id,
                }
            )

    return sorted(
        ranked,
        key=lambda item: (
            -int(item["score"]),
            int(item["start"]),
            int(item["end"]),
        ),
    )


def _score_refined_local_claim_proposal(
    *,
    proposal: RefinedLocalClaimProposal,
    parent: ProofNode,
    candidate: ProofNode,
) -> int:
    statement = proposal.informal_statement.strip()
    proof_text = proposal.informal_proof_text.strip()
    normalized = _normalize_text(f"{statement}\n{proof_text}")
    if not statement or not proof_text:
        return 0
    if "..." in statement or "..." in proof_text:
        return 0
    if len(statement.split()) < 4 or len(statement.split()) > 48:
        return 0
    if not _has_relation_marker(statement):
        return 0
    if _normalize_text(statement) in {
        _normalize_text(parent.informal_statement),
        _normalize_text(candidate.informal_statement),
    }:
        return 0

    score = _high_yield_statement_score(statement)
    if score <= 0:
        return 0
    if _shared_symbol_count(statement, candidate.informal_statement) >= 2:
        score += 2
    if _shared_symbol_count(statement, candidate.informal_proof_text) >= 2:
        score += 2
    if _shared_symbol_count(statement, parent.informal_proof_text) >= 2:
        score += 1
    if len(proof_text.split()) <= 120:
        score += 1
    if len(proof_text.split()) <= 60:
        score += 1
    if any(marker in normalized for marker in GENERIC_CLAIM_MARKERS):
        score -= 1
    if any(word in normalized for word in ("local", "concrete", "identity", "estimate")):
        score += 1
    if _is_point_evaluation(statement):
        score -= 3
    return score


def _proposal_to_refinement(
    *,
    proposal: RefinedLocalClaimProposal,
    parent_id: str,
    candidate_id: str,
) -> dict[str, str | int]:
    title = proposal.title.strip()
    display_label = proposal.display_label.strip() if proposal.display_label else _refined_label_for_span(
        proposal.informal_statement
    )
    return {
        "parent_id": parent_id,
        "title": title,
        "display_label": display_label,
        "statement": proposal.informal_statement.strip(),
        "proof_text": _normalize_refined_local_claim_text(proposal.informal_proof_text),
        "priority": 1,
        "rationale": proposal.rationale.strip(),
    }


def _normalize_refined_local_claim_text(text: str) -> str:
    text = "".join(ch for ch in text if ch in ("\n", "\t") or ord(ch) >= 32)
    text = text.replace("\\\\(", r"\(").replace("\\\\)", r"\)")
    text = text.replace("\\\\[", r"\[").replace("\\\\]", r"\]")
    return text.strip()


def _request_refined_local_claim(
    *,
    backend: StructuredBackend,
    graph: ProofGraph,
    candidate_id: str,
) -> dict[str, str | int] | None:
    node_by_id = {node.id: node for node in graph.nodes}
    incoming = _incoming_edges(graph)
    candidate = node_by_id[candidate_id]
    parent_edges = incoming.get(candidate_id, [])
    if len(parent_edges) != 1:
        return None
    parent_id = parent_edges[0].source_id

    span_hints = _rank_local_consequence_spans(graph=graph, candidate_id=candidate_id)[:3]
    try:
        response = backend.run_structured(
            build_local_refinement_request(
                graph=graph,
                parent_id=parent_id,
                candidate_id=candidate_id,
                span_hints=span_hints,
            )
        )
        proposals = RefinedLocalClaimResult.model_validate(response.payload).proposals
    except Exception:
        return None

    scored_proposals: list[tuple[int, RefinedLocalClaimProposal]] = []
    for proposal in proposals:
        score = _score_refined_local_claim_proposal(
            proposal=proposal,
            parent=node_by_id[parent_id],
            candidate=candidate,
        )
        if score <= 0:
            continue
        scored_proposals.append((score, proposal))

    if not scored_proposals:
        return None

    _, best_proposal = max(
        scored_proposals,
        key=lambda item: (
            item[0],
            -len(item[1].informal_proof_text),
            -len(item[1].informal_statement),
            item[1].title,
        ),
    )
    return _proposal_to_refinement(
        proposal=best_proposal,
        parent_id=parent_id,
        candidate_id=candidate_id,
    )


def refine_candidate_nodes(
    graph: ProofGraph,
    backend: StructuredBackend | None = None,
) -> ProofGraph:
    """Conservatively carve out at most one smaller downstream formal island."""

    if len(graph.nodes) >= 6:
        return graph

    broad_candidates = [
        node
        for node in graph.nodes
        if node.status == "candidate_formal"
        and (
            (_looks_like_broad_candidate(node) and not _is_high_yield_local_claim(node.informal_statement))
            or _looks_like_mixed_generic_application_candidate(node)
        )
    ]

    for candidate in broad_candidates:
        print(
            f"[formal-islands] considering local refinement for candidate {candidate.id} ({candidate.title})",
            flush=True,
        )
        refinement = None
        if backend is not None:
            progress("asking backend for refined local claim proposal")
            refinement = _request_refined_local_claim(
                backend=backend,
                graph=graph,
                candidate_id=candidate.id,
            )
        if refinement is None:
            progress("using deterministic span-based refinement fallback")
            refinement = _extract_local_consequence_refinement(
                graph=graph,
                candidate_id=candidate.id,
            )
        if refinement is None:
            progress("no refinement found for candidate")
            continue
        progress(f"created refined local claim {refinement['title']}")
        return _apply_candidate_refinement(
            graph=graph,
            parent_id=refinement["parent_id"],
            candidate_id=candidate.id,
            title=refinement["title"],
            display_label=refinement["display_label"],
            statement=refinement["statement"],
            proof_text=refinement["proof_text"],
            priority=refinement["priority"],
            rationale=refinement["rationale"],
        )

    return graph


def _promote_high_yield_technical_candidate(graph: ProofGraph) -> ProofGraph:
    candidates = [node for node in graph.nodes if node.status == "candidate_formal"]
    if len(candidates) != 1:
        return graph

    selected = candidates[0]
    selected_score = _candidate_usefulness_score(selected)

    best_alternative: ProofNode | None = None
    best_score = selected_score
    for node in graph.nodes:
        if node.id in {graph.root_node_id, selected.id} or node.status != "informal":
            continue
        if not _looks_like_local_technical_island(node):
            continue
        node_score = _candidate_usefulness_score(node)
        if node_score <= best_score + 1:
            continue
        best_score = node_score
        best_alternative = node

    if best_alternative is None:
        return graph

    updated_nodes: list[ProofNode] = []
    for node in graph.nodes:
        if node.id == selected.id:
            updated_nodes.append(
                node.model_copy(
                    update={
                        "status": "informal",
                        "formalization_priority": None,
                        "formalization_rationale": None,
                    }
                )
            )
        elif node.id == best_alternative.id:
            updated_nodes.append(
                node.model_copy(
                    update={
                        "status": "candidate_formal",
                        "formalization_priority": selected.formalization_priority or 1,
                        "formalization_rationale": (
                            "Promoted as a more concrete high-yield local island within the same proof neighborhood."
                        ),
                    }
                )
            )
        else:
            updated_nodes.append(node)
    return graph.model_copy(update={"nodes": updated_nodes})


def _calibrate_candidate_set(graph: ProofGraph) -> ProofGraph:
    candidates = [node for node in graph.nodes if node.status == "candidate_formal"]
    if len(candidates) <= 1:
        return graph

    max_candidates = 2 if len(graph.nodes) <= 5 else 3
    ranked_ids = {
        node.id
        for node in sorted(
            candidates,
            key=lambda node: (
                node.formalization_priority or 999,
                -_candidate_usefulness_score(node),
                node.id == graph.root_node_id,
                node.id,
            ),
        )[:max_candidates]
    }

    updated_nodes: list[ProofNode] = []
    for node in graph.nodes:
        if node.status == "candidate_formal" and node.id not in ranked_ids:
            updated_nodes.append(
                node.model_copy(
                    update={
                        "status": "informal",
                        "formalization_priority": None,
                        "formalization_rationale": None,
                    }
                )
            )
        else:
            updated_nodes.append(node)
    return graph.model_copy(update={"nodes": updated_nodes})


def _extract_local_consequence_refinement(
    *,
    graph: ProofGraph,
    candidate_id: str,
) -> dict[str, str | int] | None:
    node_by_id = {node.id: node for node in graph.nodes}
    incoming = _incoming_edges(graph)
    candidate = node_by_id[candidate_id]
    parent_edges = incoming.get(candidate_id, [])
    if len(parent_edges) != 1:
        return None
    parent_id = parent_edges[0].source_id
    parent = node_by_id[parent_id]

    existing_statements = {
        _normalize_text(graph.theorem_statement),
        *(
            _normalize_text(node.informal_statement)
            for node in graph.nodes
            if node.id != candidate_id
        ),
    }

    source_segments = [
        ("statement", candidate.informal_statement),
        ("proof", candidate.informal_proof_text),
    ]

    best_span: dict[str, str | int] | None = None
    best_score = 0
    for segment_kind, source_text in source_segments:
        for span in _extract_math_spans(source_text):
            score = _score_local_consequence_span(
                span=span,
                parent=parent,
                candidate=candidate,
                source_text=source_text,
                segment_kind=segment_kind,
                existing_statements=existing_statements,
            )
            if score <= best_score:
                continue
            best_score = score
            best_span = span | {"source_text": source_text, "segment_kind": segment_kind}

    if best_span is None or best_score < 5:
        return None

    return {
        "parent_id": parent_id,
        "title": _refined_title_for_span(best_span["body"]),
        "display_label": _refined_label_for_span(best_span["body"]),
        "statement": _statement_for_refined_span(best_span["body"]),
        "proof_text": _context_window(str(best_span["source_text"]), best_span["start"], best_span["end"]),
        "priority": 1,
        "rationale": (
            "Smaller concrete local consequence extracted because it is more directly formalizable than the broader surrounding node."
        ),
    }


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


def simplify_proof_graph(
    graph: ProofGraph,
    protected_node_ids: set[str] | None = None,
) -> ProofGraph:
    """Deterministically collapse obviously over-segmented extraction output."""

    protected = protected_node_ids or set()
    current = graph
    while True:
        updated = _remove_assumption_nodes(current, protected_node_ids=protected)
        updated = _collapse_trivial_restatement_nodes(updated, protected_node_ids=protected)
        if updated == current:
            return updated
        current = updated


def _apply_candidate_refinement(
    *,
    graph: ProofGraph,
    parent_id: str,
    candidate_id: str | None,
    title: str,
    display_label: str,
    statement: str,
    proof_text: str,
    priority: int,
    rationale: str,
) -> ProofGraph:
    refined_id = _fresh_node_id(
        graph,
        f"{candidate_id or parent_id}_refined_local_claim",
    )
    refined_node = ProofNode(
        id=refined_id,
        title=title,
        informal_statement=statement,
        informal_proof_text=proof_text,
        status="candidate_formal",
        display_label=display_label,
        formalization_priority=priority,
        formalization_rationale=rationale,
    )

    updated_nodes = []
    for node in graph.nodes:
        if candidate_id is not None and node.id == candidate_id:
            updated_nodes.append(
                node.model_copy(
                    update={
                        "status": "informal",
                        "formalization_priority": None,
                        "formalization_rationale": None,
                    }
                )
            )
        else:
            updated_nodes.append(node)
    updated_nodes.append(refined_node)

    updated_edges: list[ProofEdge] = []
    replaced_parent_edge = False
    for edge in graph.edges:
        if candidate_id is not None and edge.source_id == parent_id and edge.target_id == candidate_id:
            replaced_parent_edge = True
            updated_edges.append(
                ProofEdge(
                    source_id=parent_id,
                    target_id=refined_id,
                    label="refined_from",
                    explanation=(
                        "This refined local claim was carved out from the broader sibling node as provenance, "
                        "not as a proof dependency."
                    ),
                )
            )
            continue
        updated_edges.append(edge)

    if not replaced_parent_edge:
        updated_edges.append(
            ProofEdge(
                source_id=parent_id,
                target_id=refined_id,
                label="refined_from",
                explanation="Refined local consequence extracted from the broader sibling node as provenance.",
            )
        )

    if candidate_id is not None:
        updated_edges.append(
            ProofEdge(
                source_id=refined_id,
                target_id=candidate_id,
                label="uses",
                explanation="This refined local claim depends on the broader supporting node it was carved out from.",
            )
        )

    return graph.model_copy(update={"nodes": updated_nodes, "edges": _dedupe_edges(updated_edges)})


def _remove_assumption_nodes(
    graph: ProofGraph,
    *,
    protected_node_ids: set[str],
) -> ProofGraph:
    incoming = _incoming_edges(graph)
    outgoing = _outgoing_edges(graph)
    removable_ids = {
        node.id
        for node in graph.nodes
        if node.id != graph.root_node_id
        and node.id not in protected_node_ids
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


def _looks_like_broad_candidate(node: ProofNode) -> bool:
    statement = _normalize_text(node.informal_statement)
    proof_text = _normalize_text(node.informal_proof_text)
    if any(marker in statement for marker in GENERIC_CLAIM_MARKERS):
        return True
    if any(
        cue in proof_text
        for cue in (
            "broader estimate",
            "broader claim",
            "downstream claim",
            "local argument",
            "supporting sublemma",
            "concrete bound",
            "local core",
        )
    ):
        return True
    return len(statement.split()) >= 22


def _looks_like_mixed_generic_application_candidate(node: ProofNode) -> bool:
    statement = _normalize_text(node.informal_statement)
    proof_text = _normalize_text(node.informal_proof_text)
    has_generic_part = any(marker in statement for marker in GENERIC_CLAIM_MARKERS)
    has_generic_part = has_generic_part or any(marker in proof_text for marker in GENERIC_CLAIM_MARKERS)
    has_application_cue = any(
        cue in statement or cue in proof_text
        for cue in (
            "applied to",
            "apply this with",
            "this gives",
            "hence",
            "therefore",
            "so",
            "downstream claim",
            "broader estimate",
            "local argument",
        )
    )
    span_count = len(_extract_math_spans(node.informal_statement)) + len(
        _extract_math_spans(node.informal_proof_text)
    )
    return has_generic_part and has_application_cue and span_count >= 2


def _looks_like_local_technical_island(node: ProofNode) -> bool:
    statement = node.informal_statement
    normalized = _normalize_text(statement)
    if not _has_relation_marker(statement):
        return False
    if "there exists" in normalized or "\\to" in statement:
        return False
    if "\\le" not in statement and "\\ge" not in statement and "≤" not in statement and "≥" not in statement and "=" not in statement:
        return False
    return _substantive_feature_score(f"{statement}\n{node.informal_proof_text}") >= 4


def _candidate_usefulness_score(node: ProofNode) -> int:
    statement = node.informal_statement
    score = _high_yield_statement_score(statement)
    if _looks_like_local_technical_island(node):
        score += 2
    if _shared_symbol_count(statement, node.informal_proof_text) >= 2:
        score += 1
    normalized = _normalize_text(statement)
    if "there exists" in normalized or "\\to" in statement:
        score -= 2
    proof_words = len(_normalize_text(node.informal_proof_text).split())
    if proof_words <= 60:
        score += 2
    elif proof_words <= 110:
        score += 1
    elif proof_words >= 170:
        score -= 1
    return score


def _is_concrete_local_claim(node: ProofNode) -> bool:
    statement = _normalize_text(node.informal_statement)
    if not _has_relation_marker(statement):
        return False
    if any(marker in statement for marker in GENERIC_CLAIM_MARKERS):
        return False
    return len(statement.split()) <= 28


def _is_high_yield_local_claim(statement: str) -> bool:
    normalized = _normalize_text(statement)
    if not normalized:
        return False
    return _high_yield_statement_score(statement) >= 8


def _score_local_consequence_span(
    *,
    span: dict[str, str | int],
    parent: ProofNode,
    candidate: ProofNode,
    source_text: str,
    segment_kind: str,
    existing_statements: set[str],
) -> int:
    body = str(span["body"]).strip()
    normalized = _normalize_text(body)
    if not normalized:
        return 0
    for existing in existing_statements:
        if not existing:
            continue
        if normalized == existing or normalized in existing or (
            len(existing.split()) >= 6 and existing in normalized
        ):
            return 0
    if not _has_relation_marker(body):
        return 0
    if ":=" in body or "=:" in body:
        return 0
    if len(normalized.split()) < 4 or len(normalized.split()) > 36:
        return 0

    parent_statement = _normalize_text(parent.informal_statement)
    if normalized == parent_statement or normalized in parent_statement:
        return 0

    score = _high_yield_statement_score(body)
    candidate_score = _high_yield_statement_score(candidate.informal_statement)
    mixed_candidate = _looks_like_mixed_generic_application_candidate(candidate)
    comparison_floor = max(4, candidate_score - 1 if mixed_candidate else candidate_score + 1)
    if (score < comparison_floor) or (not mixed_candidate and score == comparison_floor):
        return 0

    context_before = str(span["context_before"]).lower()
    recent_context = context_before[-90:]
    generic_context = any(marker in recent_context for marker in GENERIC_CLAIM_MARKERS)
    application_context = any(
        cue in recent_context for cue in ("applied to", "apply this with", "this gives", "hence", "therefore", "we obtain")
    )
    if generic_context and not application_context:
        return 0
    if any(cue in context_before for cue in LOCAL_CLAIM_CUE_PHRASES):
        score += 2
    if segment_kind == "statement":
        score += 1
    if _shared_symbol_count(body, candidate.informal_statement) >= 2:
        score += 2
    if _shared_symbol_count(body, parent.informal_proof_text) >= 2:
        score += 1
    if _looks_like_broad_candidate(candidate):
        score += 2
    return score


def _has_relation_marker(text: str) -> bool:
    return any(marker in text for marker in RELATION_MARKERS)


def _is_point_evaluation(statement: str) -> bool:
    """Return True if the statement looks like a pure point-substitution equality.

    Detects claims of the form "f(x) = 0" or "f(a, b) = c" that have no
    inequality and no universal quantifier over a non-trivial domain.  Such
    claims carry very little inferential weight as formal islands.

    A statement is a point evaluation when ALL of:
    - it contains '=' but no inequality marker (\\le, \\ge, ≤, ≥, <, >)
    - it contains '= 0' or ends with a simple equality to a constant
    - it has no quantifier or interval indicator (\\in, \\forall, 'for all',
      'for every', 'for each', 'for p', 'for u', '[0,', '(0,', '\\in (', '\\in [')
    """
    has_equality = "=" in statement
    has_inequality = any(m in statement for m in ("\\le", "\\ge", "≤", "≥", "<", ">"))
    if not has_equality or has_inequality:
        return False

    universal_indicators = (
        "\\forall", "for all", "for every", "for each",
        "\\in (", "\\in [", "\\in\\;", r"\in ",
        "[0,", "(0,", "\\in(0", "\\in[0",
        "for $p", "for $u", "for $x", "for $t",
        "for p \\", "for u \\", "for x \\",
    )
    normalized = statement.lower()
    if any(ind.lower() in normalized for ind in universal_indicators):
        return False

    return True


def _substantive_feature_score(text: str) -> int:
    features = (
        "\\sqrt",
        "\\nabla",
        "\\int",
        "\\|",
        "\\operatorname",
        "^",
        "*",
    )
    score = sum(1 for feature in features if feature in text)
    uppercase_tokens = {
        token
        for token in re.findall(r"\b[A-Z](?:\([^)]+\))?\b", text)
        if token not in {"L", "R", "C"}
    }
    score += min(3, len(uppercase_tokens))
    return score


def _high_yield_statement_score(text: str) -> int:
    normalized = _normalize_text(text)
    if not normalized or not _has_relation_marker(text):
        return 0

    score = 0
    if "\\le" in text or "\\ge" in text or "≤" in text or "≥" in text:
        score += 3
    elif "\\to" in text:
        score += 2
    elif "=" in text:
        score += 1

    score += _substantive_feature_score(text)
    score += min(3, _symbolic_term_count(text))

    if len(normalized.split()) < 4:
        score -= 2
    elif len(normalized.split()) <= 48:
        score += 1
    elif len(normalized.split()) > 72:
        score -= 2

    if body_relation_count := (
        text.count("=") + text.count("\\le") + text.count("\\ge") + text.count("\\to")
    ):
        if body_relation_count >= 2:
            score += 1

    if any(marker in normalized for marker in GENERIC_CLAIM_MARKERS):
        score -= 2

    return score


def _symbolic_term_count(text: str) -> int:
    patterns = [
        r"\\sqrt\{[^}]+\}",
        r"\\int\b",
        r"\\nabla\b",
        r"\|[^|]+\|",
        r"\b[A-Z](?:\([^)]+\))?\b",
        r"\b[a-zA-Z]+\(t\)\b",
    ]
    terms: set[str] = set()
    for pattern in patterns:
        terms.update(re.findall(pattern, text))
    return len(terms)


def _shared_symbol_count(left: str, right: str) -> int:
    return len(_symbolic_terms(left) & _symbolic_terms(right))


def _symbolic_terms(text: str) -> set[str]:
    patterns = [
        r"\\sqrt\{[^}]+\}",
        r"\\int\b",
        r"\\nabla\b",
        r"\|[^|]+\|",
        r"\b[A-Z](?:\([^)]+\))?\b",
        r"\b[a-zA-Z]+\(t\)\b",
    ]
    terms: set[str] = set()
    for pattern in patterns:
        terms.update(re.findall(pattern, text))
    return {term for term in terms if term not in {"L", "R", "C"}}


def _extract_math_spans(text: str) -> list[dict[str, str | int]]:
    spans: list[dict[str, str | int]] = []
    spans.extend(_extract_delimited_math_spans(text, "\\[", "\\]", "display"))
    spans.extend(_extract_delimited_math_spans(text, "\\(", "\\)", "inline"))
    return sorted(spans, key=lambda item: int(item["start"]))


def _extract_delimited_math_spans(
    text: str,
    open_delim: str,
    close_delim: str,
    kind: str,
) -> list[dict[str, str | int]]:
    spans: list[dict[str, str | int]] = []
    start = 0
    while True:
        open_index = text.find(open_delim, start)
        if open_index == -1:
            return spans
        close_index = text.find(close_delim, open_index + len(open_delim))
        if close_index == -1:
            return spans
        spans.append(
            {
                "kind": kind,
                "body": text[open_index + len(open_delim) : close_index].strip(),
                "start": open_index,
                "end": close_index + len(close_delim),
                "context_before": text[max(0, open_index - 180) : open_index],
            }
        )
        start = close_index + len(close_delim)


def _refined_title_for_span(body: str) -> str:
    if "\\le" in body or "\\ge" in body or "≤" in body or "≥" in body:
        return "Local estimate"
    if "\\to" in body:
        return "Local asymptotic consequence"
    if "=" in body:
        return "Local identity"
    return "Local consequence"


def _refined_label_for_span(body: str) -> str:
    if "\\le" in body or "\\ge" in body or "≤" in body or "≥" in body:
        return "Refined estimate"
    if "\\to" in body:
        return "Refined asymptotic claim"
    if "=" in body:
        return "Refined identity"
    return "Refined local claim"


def _statement_for_refined_span(body: str) -> str:
    return "\n".join(["\\[", body.strip(), "\\]"])


def _context_window(text: str, start: int, end: int) -> str:
    window_start = max(0, start - 220)
    window_end = min(len(text), end + 140)
    snippet = text[window_start:window_end].strip()
    if len(snippet) <= 420:
        return snippet
    return snippet[:417].rstrip() + "..."


def _collapse_trivial_restatement_nodes(
    graph: ProofGraph,
    *,
    protected_node_ids: set[str],
) -> ProofGraph:
    incoming = _incoming_edges(graph)
    outgoing = _outgoing_edges(graph)
    node_by_id = {node.id: node for node in graph.nodes}

    for node in graph.nodes:
        if node.id == graph.root_node_id:
            continue
        if node.id in protected_node_ids:
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


def _fresh_node_id(graph: ProofGraph, base_id: str) -> str:
    existing = {node.id for node in graph.nodes}
    if base_id not in existing:
        return base_id

    suffix = 2
    while f"{base_id}_{suffix}" in existing:
        suffix += 1
    return f"{base_id}_{suffix}"


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


def _extract_display_math_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    start = 0
    while True:
        open_index = text.find("\\[", start)
        if open_index == -1:
            return blocks
        close_index = text.find("\\]", open_index + 2)
        if close_index == -1:
            return blocks
        blocks.append(text[open_index + 2 : close_index])
        start = close_index + 2


def _extract_inline_math_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    start = 0
    while True:
        open_index = text.find("\\(", start)
        if open_index == -1:
            return blocks
        close_index = text.find("\\)", open_index + 2)
        if close_index == -1:
            return blocks
        blocks.append(text[open_index + 2 : close_index])
        start = close_index + 2


def _normalize_text(text: str) -> str:
    lowered = text.lower()
    lowered = lowered.replace("≤", "<=").replace("≥", ">=")
    lowered = re.sub(r"[^a-z0-9<>=+\-*/ ]+", " ", lowered)
    return " ".join(lowered.split())
