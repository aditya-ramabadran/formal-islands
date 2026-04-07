"""Deterministic review-obligation extraction."""

from __future__ import annotations

from formal_islands.models import ProofGraph, ReviewObligation


INFORMAL_REVIEW_STATUSES = {"informal", "candidate_formal", "formal_failed"}


def derive_review_obligations(graph: ProofGraph) -> list[ReviewObligation]:
    """Derive the prototype's explicit human-review surface from the graph."""

    obligations: list[ReviewObligation] = []
    node_by_id = {node.id: node for node in graph.nodes}

    for node in graph.nodes:
        if node.status in INFORMAL_REVIEW_STATUSES:
            obligations.append(
                ReviewObligation(
                    id=f"informal-proof-{node.id}",
                    kind="informal_proof_check",
                    text=(
                        f"Check that node '{node.id}' ({node.title}) has an informal proof that "
                        "establishes its informal statement, assuming its child dependencies."
                    ),
                    node_ids=[node.id],
                )
            )

        if node.status == "formal_verified" and node.formal_artifact is not None:
            if node.formal_artifact.faithfulness_classification == "concrete_sublemma":
                text = (
                    f"Check that node '{node.id}' ({node.title}) honestly represents a narrower verified Lean "
                    f"supporting sublemma with statement '{node.formal_artifact.lean_statement}', rather than "
                    "overclaiming full coverage of its parent informal step."
                )
            else:
                text = (
                    f"Check that node '{node.id}' ({node.title}) matches the verified Lean "
                    f"statement '{node.formal_artifact.lean_statement}'."
                )
            obligations.append(
                ReviewObligation(
                    id=f"semantic-match-{node.id}",
                    kind="formal_semantic_match_check",
                    text=text,
                    node_ids=[node.id],
                )
            )

    for edge in graph.edges:
        parent = node_by_id[edge.source_id]
        child = node_by_id[edge.target_id]
        if parent.status in INFORMAL_REVIEW_STATUSES and child.status == "formal_verified":
            if edge.label == "formal_sublemma_for":
                text = (
                    f"Check that verified supporting sublemma '{child.id}' certifies the specific concrete local core "
                    f"that informal parent '{parent.id}' relies on, without overclaiming full coverage of the parent node."
                )
            else:
                text = (
                    f"Check that formal child '{child.id}' proves exactly what informal parent "
                    f"'{parent.id}' uses across their dependency boundary."
                )
            obligations.append(
                ReviewObligation(
                    id=f"boundary-{parent.id}-{child.id}",
                    kind="boundary_interface_check",
                    text=text,
                    node_ids=[parent.id, child.id],
                )
            )

    return obligations
