"""End-of-run report synthesis helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from threading import RLock

from formal_islands.backends import BackendError, StructuredBackend, StructuredBackendRequest
from formal_islands.formalization.pipeline import (
    build_local_proof_context,
    build_verified_direct_child_context,
    format_local_proof_context,
    format_verified_direct_child_context,
)
from formal_islands.models import ProofGraph
from formal_islands.progress import (
    append_remaining_proof_burden_to_progress_log,
    progress,
    run_structured_with_progress,
)


@dataclass(frozen=True)
class RemainingProofBurdenAssessment:
    """Planner output for a remaining-proof-burden summary."""

    remaining_proof_burden: str


@dataclass
class RemainingProofBurdenCache:
    """Thread-safe cache for report-time burden synthesis decisions."""

    decisions: dict[str, RemainingProofBurdenAssessment | None]
    lock: RLock


def build_remaining_proof_burden_assessment_request(
    *,
    graph: ProofGraph,
    parent_node_id: str,
) -> StructuredBackendRequest:
    parent = next(node for node in graph.nodes if node.id == parent_node_id)
    verified_children = build_verified_direct_child_context(graph, parent_node_id)
    local_context = build_local_proof_context(graph, parent_node_id)
    verified_child_ids = [node.id for node in verified_children.child_nodes]
    title_child_list = f"[{', '.join(verified_child_ids)}]" if verified_child_ids else "[]"
    child_ids = [edge.target_id for edge in graph.edges if edge.source_id == parent_node_id]
    child_by_id = {node.id: node for node in graph.nodes}
    child_inventory = [
        {
            "id": child.id,
            "title": child.title,
            "status": child.status,
            "formal_artifact": (
                child.formal_artifact.model_dump(mode="json") if child.formal_artifact else None
            ),
        }
        for child in sorted(
            (child_by_id[child_id] for child_id in child_ids if child_id in child_by_id),
            key=lambda node: node.id,
        )
    ]
    prompt = "\n\n".join(
        [
            f"Theorem title: {graph.theorem_title}",
            (
                "Target informal parent node:\n"
                f"- id: {parent.id}\n"
                f"- title: {parent.title}\n"
                f"- informal statement: {parent.informal_statement}\n"
                f"- informal proof text: {parent.informal_proof_text}\n"
                f"- formalization priority: {parent.formalization_priority if parent.formalization_priority is not None else 'unset'}\n"
                f"- formalization rationale: {parent.formalization_rationale or '(no rationale recorded)'}"
            ),
            "Direct child inventory:",
            json.dumps(child_inventory, indent=2),
            "Verified direct child lemmas already available:",
            format_verified_direct_child_context(verified_children),
            "Local proof neighborhood:",
            format_local_proof_context(local_context),
            (
                "Write a short report-ready paragraph describing the remaining proof burden for this parent, "
                "assuming the verified child results listed above. The paragraph should explain the delta between "
                "the informal proof and the already certified children: what is left to assemble, rewrite, or "
                "check manually once those children are granted."
            ),
            (
                "Be concrete about how the verified children fit into the parent proof. If the remaining work is "
                "mostly parent-level assembly, say so. If some branches are still unverified, mention that the "
                "verified children cover only part of the burden and that the rest remains informal."
            ),
            (
                "Do not restate the verified children themselves. Focus on the residual burden that a human reviewer "
                "should still check in the informal proof, using the verified children as assumptions."
            ),
            (
                "Keep it concise, one to three sentences, and suitable for a section titled "
                f"Remaining proof burden (assuming results of {title_child_list})."
            ),
            "Return JSON with key remaining_proof_burden.",
        ]
    )
    return StructuredBackendRequest(
        prompt=prompt,
        system_prompt=(
            "You are a report-stage proof-graph annotator. Return only JSON matching the schema."
        ),
        json_schema={
            "type": "object",
            "properties": {
                "remaining_proof_burden": {"type": "string", "minLength": 1},
            },
            "required": ["remaining_proof_burden"],
            "additionalProperties": False,
        },
        task_name="assess_remaining_proof_burden",
    )


def request_remaining_proof_burden_assessment(
    *,
    backend: StructuredBackend,
    graph: ProofGraph,
    parent_node_id: str,
) -> RemainingProofBurdenAssessment:
    response = run_structured_with_progress(
        backend,
        build_remaining_proof_burden_assessment_request(graph=graph, parent_node_id=parent_node_id),
    )
    payload = response.payload
    return RemainingProofBurdenAssessment(
        remaining_proof_burden=str(payload["remaining_proof_burden"]).strip()
    )


def _remaining_proof_burden_cache_key(graph: ProofGraph, parent_node_id: str) -> str:
    parent = next(node for node in graph.nodes if node.id == parent_node_id)
    child_ids = [edge.target_id for edge in graph.edges if edge.source_id == parent_node_id]
    verified_children = [
        node
        for node in graph.nodes
        if node.id in child_ids and node.status == "formal_verified" and node.formal_artifact is not None
    ]
    payload = {
        "parent": {
            "id": parent.id,
            "title": parent.title,
            "statement": parent.informal_statement,
            "proof": parent.informal_proof_text,
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
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _eligible_parents_with_verified_children(graph: ProofGraph) -> list[str]:
    node_by_id = {node.id: node for node in graph.nodes}
    eligible: list[str] = []
    for node in graph.nodes:
        if node.status == "formal_verified":
            continue
        child_ids = [edge.target_id for edge in graph.edges if edge.source_id == node.id]
        if not child_ids:
            continue
        if any(
            child_id in node_by_id
            and node_by_id[child_id].status == "formal_verified"
            and node_by_id[child_id].formal_artifact is not None
            for child_id in child_ids
        ):
            eligible.append(node.id)
    return sorted(eligible)


def synthesize_remaining_proof_burdens(
    *,
    graph: ProofGraph,
    planning_backend: StructuredBackend | None,
    cache: RemainingProofBurdenCache | None = None,
) -> ProofGraph:
    """Annotate informal parents with report-ready remaining-proof-burden text."""

    if planning_backend is None:
        progress("remaining proof burden synthesis skipped (no planning backend)")
        return graph

    local_cache = cache or RemainingProofBurdenCache(decisions={}, lock=RLock())

    eligible_parent_ids = _eligible_parents_with_verified_children(graph)
    if not eligible_parent_ids:
        progress("remaining proof burden synthesis: no parents with verified children")
        return graph

    progress(
        "remaining proof burden synthesis: evaluating "
        f"{len(eligible_parent_ids)} parent(s) with verified children"
    )

    updated_nodes = list(graph.nodes)
    node_index = {node.id: index for index, node in enumerate(updated_nodes)}

    for parent_id in eligible_parent_ids:
        parent = next(node for node in updated_nodes if node.id == parent_id)
        cache_key = _remaining_proof_burden_cache_key(graph, parent_id)
        decision: RemainingProofBurdenAssessment | None = None
        cached_hit = False
        with local_cache.lock:
            if cache_key in local_cache.decisions:
                decision = local_cache.decisions[cache_key]
                cached_hit = True

        if not cached_hit:
            progress(f"node {parent_id}: requesting remaining proof burden synthesis")
            try:
                decision = request_remaining_proof_burden_assessment(
                    backend=planning_backend,
                    graph=graph,
                    parent_node_id=parent_id,
                )
            except BackendError as exc:
                progress(
                    f"node {parent_id}: remaining proof burden synthesis failed: "
                    f"{str(exc).splitlines()[0] if str(exc) else 'unknown backend error'}"
                )
                decision = None
            with local_cache.lock:
                local_cache.decisions[cache_key] = decision
        else:
            progress(f"node {parent_id}: remaining proof burden cache hit")

        if decision is None:
            progress(f"node {parent_id}: no remaining proof burden annotation available")
            continue

        verified_child_ids = [
            edge.target_id
            for edge in graph.edges
            if edge.source_id == parent_id
            and any(
                child.id == edge.target_id
                and child.status == "formal_verified"
                and child.formal_artifact is not None
                for child in graph.nodes
            )
        ]
        appended_text = decision.remaining_proof_burden.strip()
        updated_nodes[node_index[parent_id]] = parent.model_copy(
            update={"remaining_proof_burden": appended_text}
        )
        append_remaining_proof_burden_to_progress_log(
            node_id=parent_id,
            verified_child_ids=verified_child_ids,
            remaining_proof_burden=appended_text[:240],
        )
        progress(
            f"node {parent_id}: remaining proof burden synthesized using children "
            f"{', '.join(verified_child_ids) if verified_child_ids else '[]'}"
        )

    return graph.model_copy(update={"nodes": updated_nodes})
