from pydantic import ValidationError
import pytest

from formal_islands.backends import MockBackend
from formal_islands.formalization.pipeline import (
    FormalizationFaithfulnessError,
    build_formalization_request,
    request_node_formalization,
)
from formal_islands.models import ProofEdge, ProofGraph, ProofNode


def build_graph() -> ProofGraph:
    return ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="If a and b are nonnegative, then a + b is nonnegative.",
        root_node_id="n1",
        nodes=[
            ProofNode(
                id="n1",
                title="Main claim",
                informal_statement="a + b is nonnegative.",
                informal_proof_text="It follows from the arithmetic lemma n2.",
            ),
            ProofNode(
                id="n2",
                title="Arithmetic lemma",
                informal_statement="0 <= a + b when 0 <= a and 0 <= b.",
                informal_proof_text="This is a local technical fact.",
                status="candidate_formal",
                formalization_priority=3,
                formalization_rationale="Leaf arithmetic lemma.",
            ),
        ],
        edges=[ProofEdge(source_id="n1", target_id="n2")],
    )


def test_build_formalization_request_includes_local_context() -> None:
    request = build_formalization_request(build_graph(), "n2")

    assert request.task_name == "formalize_node"
    assert request.json_schema["type"] == "object"
    assert "Immediate parent summary" in request.prompt
    assert "Arithmetic lemma" in request.prompt
    assert "arbitrary index types" in request.prompt.lower()
    assert "easy side consequence" in request.prompt.lower()
    assert "do not default to `import mathlib`" in request.prompt.lower()
    assert "do not guess deep or speculative module paths" in request.prompt.lower()
    assert "Theorem statement:" not in request.prompt


def test_build_formalization_request_includes_previous_lean_file_on_repair() -> None:
    request = build_formalization_request(
        build_graph(),
        "n2",
        compiler_feedback="error: unknown identifier",
        previous_lean_code="import Mathlib\n\ntheorem bad : 0 <= a + b := by\n  simp",
    )

    assert "previous failed lean file to revise" in request.prompt.lower()
    assert "make the smallest changes needed" in request.prompt.lower()
    assert "```lean\nimport Mathlib" in request.prompt


def test_request_node_formalization_returns_unverified_artifact() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "lean_theorem_name": "sum_nonneg",
                "lean_statement": "0 <= a + b",
                "lean_code": "theorem sum_nonneg : 0 <= a + b := by admit",
            }
        ]
    )

    artifact = request_node_formalization(backend=backend, graph=build_graph(), node_id="n2")

    assert artifact.lean_theorem_name == "sum_nonneg"
    assert artifact.verification.status == "not_attempted"


def test_request_node_formalization_rejects_non_candidate_nodes() -> None:
    with pytest.raises(ValueError, match="candidate_formal"):
        build_formalization_request(build_graph(), "n1")


def test_request_node_formalization_rejects_bad_payload() -> None:
    backend = MockBackend(queued_payloads=[{"lean_theorem_name": "missing fields"}])

    with pytest.raises(ValidationError):
        request_node_formalization(backend=backend, graph=build_graph(), node_id="n2")


def test_request_node_formalization_rejects_gratuitous_over_abstraction() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "lean_theorem_name": "abstract_sum_nonneg",
                "lean_statement": (
                    "theorem abstract_sum_nonneg {ι : Type*} "
                    "(lhs rhs total : ι → ℝ) : ∀ t, total t = lhs t + rhs t"
                ),
                "lean_code": (
                    "import Mathlib\n\n"
                    "theorem abstract_sum_nonneg {ι : Type*} "
                    "(lhs rhs total : ι → ℝ) : ∀ t, total t = lhs t + rhs t := by\n"
                    "  intro t\n"
                    "  sorry\n"
                ),
            }
        ]
    )

    with pytest.raises(FormalizationFaithfulnessError, match="Type\\*"):
        request_node_formalization(backend=backend, graph=build_graph(), node_id="n2")
