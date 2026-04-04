from pydantic import ValidationError
import pytest

from formal_islands.backends import MockBackend
from formal_islands.extraction.pipeline import (
    build_candidate_selection_request,
    build_extraction_request,
    extract_proof_graph,
    select_formalization_candidates,
)
from formal_islands.models import ProofEdge, ProofGraph, ProofNode


def build_graph() -> ProofGraph:
    return ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="If A then B.",
        root_node_id="n1",
        nodes=[
            ProofNode(
                id="n1",
                title="Main claim",
                informal_statement="B holds.",
                informal_proof_text="Use n2.",
            ),
            ProofNode(
                id="n2",
                title="Technical lemma",
                informal_statement="A implies B.",
                informal_proof_text="By a direct argument.",
            ),
        ],
        edges=[ProofEdge(source_id="n1", target_id="n2")],
    )


def test_build_extraction_request_uses_explicit_schema() -> None:
    request = build_extraction_request(
        theorem_statement="If A then B.",
        raw_proof_text="Assume A and deduce B.",
        theorem_title_hint="Lemma",
    )

    assert request.task_name == "extract_graph"
    assert request.json_schema["type"] == "object"
    assert "theorem_statement" in request.prompt


def test_extract_proof_graph_validates_schema_and_maps_to_internal_graph() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "theorem_title": "Toy theorem",
                "theorem_statement": "If A then B.",
                "root_node_id": "n1",
                "nodes": [
                    {
                        "id": "n1",
                        "title": "Main claim",
                        "informal_statement": "B holds.",
                        "informal_proof_text": "Use n2.",
                    },
                    {
                        "id": "n2",
                        "title": "Lemma",
                        "informal_statement": "A implies B.",
                        "informal_proof_text": "By inspection.",
                        "display_label": "technical estimate",
                    },
                ],
                "edges": [{"source_id": "n1", "target_id": "n2"}],
            }
        ]
    )

    graph = extract_proof_graph(
        backend=backend,
        theorem_statement="If A then B.",
        raw_proof_text="Assume A and deduce B.",
    )

    assert graph.root_node_id == "n1"
    assert graph.nodes[0].status == "informal"
    assert graph.nodes[1].display_label == "technical estimate"


def test_extract_proof_graph_rejects_malformed_payload() -> None:
    backend = MockBackend(queued_payloads=[{"theorem_title": "Missing fields"}])

    with pytest.raises(ValidationError):
        extract_proof_graph(
            backend=backend,
            theorem_statement="If A then B.",
            raw_proof_text="Assume A and deduce B.",
        )


def test_build_candidate_selection_request_uses_graph_json() -> None:
    request = build_candidate_selection_request(build_graph())

    assert request.task_name == "select_candidates"
    assert request.json_schema["type"] == "object"
    assert '"theorem_title": "Toy theorem"' in request.prompt


def test_select_formalization_candidates_updates_matching_nodes() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "candidates": [
                    {
                        "node_id": "n2",
                        "priority": 3,
                        "rationale": "Self-contained implication lemma.",
                    }
                ]
            }
        ]
    )

    updated = select_formalization_candidates(backend=backend, graph=build_graph())

    assert updated.nodes[0].status == "informal"
    assert updated.nodes[1].status == "candidate_formal"
    assert updated.nodes[1].formalization_priority == 3


def test_select_formalization_candidates_rejects_unknown_node_ids() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "candidates": [
                    {"node_id": "missing", "priority": 2, "rationale": "No such node."}
                ]
            }
        ]
    )

    with pytest.raises(ValueError, match="unknown node ids"):
        select_formalization_candidates(backend=backend, graph=build_graph())
