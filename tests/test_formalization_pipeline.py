from pydantic import ValidationError
import pytest

from formal_islands.backends import MockBackend
from formal_islands.formalization.pipeline import (
    FaithfulnessClassification,
    FormalizationFaithfulnessError,
    build_coverage_expansion_assessment_request,
    build_combined_verification_assessment_request,
    _normalize_concrete_sublemma_summary_text,
    assess_formalization_faithfulness,
    build_concrete_sublemma_summary_request,
    build_formalization_request,
    classify_heuristic_repair_assessment,
    format_faithfulness_notes,
    parse_faithfulness_notes,
    request_concrete_sublemma_summary,
    request_coverage_expansion_assessment,
    request_node_formalization,
    request_repair_assessment,
)
from formal_islands.models import FormalArtifact, ProofEdge, ProofGraph, ProofNode


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


def build_pde_graph() -> ProofGraph:
    return ProofGraph(
        theorem_title="Weak maximum principle",
        theorem_statement="If -Δu ≥ 0 in Ω and u ≥ 0 on ∂Ω, then u ≥ 0 in Ω.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Weak maximum principle",
                informal_statement="If -Δu ≥ 0 in Ω and u ≥ 0 on ∂Ω, then u ≥ 0 in Ω.",
                informal_proof_text="Use the negative part identity.",
            ),
            ProofNode(
                id="n1",
                title="Negative-part identity",
                informal_statement=(
                    "On Ω, one has ∫_Ω ∇u · ∇u_- = -∫_Ω |∇u_-|^2 after splitting into the regions "
                    "{u ≥ 0} and {u < 0}."
                ),
                informal_proof_text=(
                    "Use the concrete domain Ω, the negative part u_-, and the pointwise identities "
                    "for gradients on {u ≥ 0} and {u < 0}."
                ),
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="Concrete local PDE identity.",
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1")],
    )


def test_build_formalization_request_includes_local_context() -> None:
    request = build_formalization_request(build_graph(), "n2")

    assert request.task_name == "formalize_node"
    assert request.json_schema["type"] == "object"
    assert "Immediate parent summary" in request.prompt
    assert "Coverage sketch" in request.prompt
    assert "Arithmetic lemma" in request.prompt
    assert "local proof neighborhood" in request.prompt.lower()
    assert "verified supporting lemmas already certified in this run" in request.prompt.lower()
    assert "context-only sibling ingredients" in request.prompt.lower()
    assert "provenance note" in request.prompt.lower()
    assert "arbitrary index types" in request.prompt.lower()
    assert "easy side consequence" in request.prompt.lower()
    assert "do not default to `import mathlib`" in request.prompt.lower()
    assert "do not guess deep or speculative module paths" in request.prompt.lower()
    assert "Ambient theorem statement:" in request.prompt
    assert "preserve the ambient mathematical setting" in request.prompt.lower()
    assert "coverage sketch" in request.prompt.lower()
    assert "mathlib search results" not in request.prompt.lower()


def test_request_node_formalization_rejects_measure_space_abstraction_for_concrete_node() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "lean_theorem_name": "energy_identity",
                "lean_statement": (
                    "theorem energy_identity {α : Type*} [MeasurableSpace α] (μ : Measure α) "
                    "{f g : α → ℝ} (h : ∀ x, f x = - g x) : "
                    "∫ x, f x ∂μ = - ∫ x, g x ∂μ"
                ),
                "lean_code": (
                    "import Mathlib.MeasureTheory.Integral.Bochner.Basic\n\n"
                    "open MeasureTheory\n\n"
                    "theorem energy_identity {α : Type*} [MeasurableSpace α] (μ : Measure α) "
                    "{f g : α → ℝ} (h : ∀ x, f x = - g x) : "
                    "∫ x, f x ∂μ = - ∫ x, g x ∂μ := by\n"
                    "  sorry\n"
                ),
            }
        ]
    )

    with pytest.raises(FormalizationFaithfulnessError, match="measure-space theorem"):
        request_node_formalization(backend=backend, graph=build_pde_graph(), node_id="n1")


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


def test_build_concrete_sublemma_summary_request_mentions_parent_and_lean_theorem() -> None:
    graph = build_graph()
    artifact = FormalArtifact(
        lean_theorem_name="sum_nonneg",
        lean_statement="theorem sum_nonneg (a b : ℝ) : 0 ≤ a + b",
        lean_code="theorem sum_nonneg (a b : ℝ) : 0 ≤ a + b := by\n  nlinarith",
    )

    request = build_concrete_sublemma_summary_request(
        graph=graph,
        parent_node_id="n2",
        artifact=artifact,
    )

    assert "Parent informal node:" in request.prompt
    assert "Verified Lean sublemma:" in request.prompt
    assert "sum_nonneg" in request.prompt
    assert "narrower than the parent node" in request.prompt.lower()
    assert "use latex math delimiters" in request.prompt.lower()
    assert "do not put latex commands" in request.prompt.lower()


def test_build_coverage_expansion_assessment_request_mentions_target_and_verified_theorem() -> None:
    graph = build_graph()
    artifact = FormalArtifact(
        lean_theorem_name="sum_nonneg",
        lean_statement="theorem sum_nonneg (a b : ℝ) : 0 <= a + b",
        lean_code="theorem sum_nonneg (a b : ℝ) : 0 <= a + b := by\n  nlinarith",
    )

    request = build_coverage_expansion_assessment_request(
        graph=graph,
        node_id="n2",
        artifact=artifact,
    )

    assert request.task_name == "assess_verified_formalization"
    assert "result_kind" in request.prompt
    assert "verified lean theorem to assess" in request.prompt.lower()
    assert "same setting and same proof path" in request.prompt.lower()
    assert "coverage score" in request.prompt.lower()


def test_build_combined_verification_assessment_request_mentions_recoverability_fields() -> None:
    graph = build_graph()
    artifact = FormalArtifact(
        lean_theorem_name="sum_nonneg",
        lean_statement="theorem sum_nonneg (a b : ℝ) : 0 <= a + b",
        lean_code="theorem sum_nonneg (a b : ℝ) : 0 <= a + b := by\n  nlinarith",
    )

    request = build_combined_verification_assessment_request(
        graph=graph,
        node_id="n2",
        artifact=artifact,
    )

    assert request.task_name == "assess_verified_formalization"
    assert "certifies_main_burden" in request.prompt
    assert "worth_retrying_later" in request.prompt


def test_request_coverage_expansion_assessment_returns_boolean_gate() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "result_kind": "full_match",
                "certifies_main_burden": True,
                "coverage_score": 10,
                "expansion_warranted": False,
                "worth_retrying_later": False,
                "reason": "The theorem already states the target inequality on the same domain.",
            }
        ]
    )
    graph = build_graph()
    artifact = FormalArtifact(
        lean_theorem_name="sum_nonneg",
        lean_statement="theorem sum_nonneg (a b : ℝ) : 0 <= a + b",
        lean_code="theorem sum_nonneg (a b : ℝ) : 0 <= a + b := by\n  nlinarith",
    )

    assessment = request_coverage_expansion_assessment(
        backend=backend,
        graph=graph,
        node_id="n2",
        artifact=artifact,
    )

    assert assessment.result_kind == "full_match"
    assert assessment.certifies_main_burden is True
    assert assessment.coverage_score == 10
    assert assessment.expansion_warranted is False
    assert assessment.worth_retrying_later is False
    assert "target inequality" in assessment.reason.lower()


def test_format_and_parse_faithfulness_notes_round_trip() -> None:
    notes = format_faithfulness_notes("faithful_core", "same setting and same proof path")

    result_kind, reason = parse_faithfulness_notes(notes)

    assert result_kind == "faithful_core"
    assert reason == "same setting and same proof path"


def test_classify_heuristic_repair_assessment_prefers_setting_fix_for_dimension_downgrade() -> None:
    verification = FormalArtifact(
        lean_theorem_name="narrow_energy_split",
        lean_statement="theorem narrow_energy_split : True",
        lean_code="theorem narrow_energy_split : True := by trivial",
    ).verification.model_copy(
        update={
            "stderr": "type mismatch in EuclideanSpace Fin",
            "stdout": "",
        }
    )

    assessment = classify_heuristic_repair_assessment(previous_result=verification)

    assert assessment.category.value == "setting_fix"


def test_classify_heuristic_repair_assessment_prefers_packaging_fix_for_unknown_identifier() -> None:
    verification = FormalArtifact(
        lean_theorem_name="sum_nonneg",
        lean_statement="theorem sum_nonneg (a b : ℝ) : 0 ≤ a + b",
        lean_code="theorem sum_nonneg (a b : ℝ) : 0 ≤ a + b := by\n  exact le_refl _",
    ).verification.model_copy(
        update={
            "stderr": "unknown identifier 'le_refl'",
            "stdout": "",
        }
    )

    assessment = classify_heuristic_repair_assessment(previous_result=verification)

    assert assessment.category.value == "lean_packaging_fix"


def test_classify_heuristic_repair_assessment_prefers_theorem_shape_fix_for_faithfulness_guard_drift() -> None:
    verification = FormalArtifact(
        lean_theorem_name="sum_nonneg",
        lean_statement="theorem sum_nonneg (a b : ℝ) : 0 ≤ a + b",
        lean_code="theorem sum_nonneg (a b : ℝ) : 0 ≤ a + b := by\n  nlinarith",
    ).verification.model_copy(
        update={
            "command": "faithfulness_guard",
            "stderr": "Formalization drifted too far from the target node. Avoid replacing it with a more abstract theorem.",
            "stdout": "",
        }
    )

    assessment = classify_heuristic_repair_assessment(previous_result=verification)

    assert assessment.category.value == "theorem_shape_fix"


def test_request_repair_assessment_returns_category() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "repair_category": "theorem_shape_fix",
                "repair_note": "The attempt assumed the key identity instead of proving it.",
            }
        ]
    )
    graph = build_graph()
    artifact = FormalArtifact(
        lean_theorem_name="sum_nonneg",
        lean_statement="theorem sum_nonneg (a b : ℝ) : 0 <= a + b",
        lean_code="theorem sum_nonneg (a b : ℝ) : 0 <= a + b := by\n  nlinarith",
    )

    assessment = request_repair_assessment(
        backend=backend,
        graph=graph,
        node_id="n2",
        artifact=artifact,
        failure_text="type mismatch",
    )

    assert assessment.category.value == "theorem_shape_fix"
    assert "assumed the key identity" in assessment.note


def test_request_concrete_sublemma_summary_returns_generated_text() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "informal_statement": "For nonnegative real numbers a and b, one has 0 ≤ a + b.",
                "informal_proof_text": "Add the two nonnegative quantities and use basic order properties of ℝ.",
            }
        ]
    )
    graph = build_graph()
    artifact = FormalArtifact(
        lean_theorem_name="sum_nonneg",
        lean_statement="theorem sum_nonneg (a b : ℝ) (ha : 0 ≤ a) (hb : 0 ≤ b) : 0 ≤ a + b",
        lean_code="theorem sum_nonneg (a b : ℝ) (ha : 0 ≤ a) (hb : 0 ≤ b) : 0 ≤ a + b := by\n  nlinarith",
    )

    summary = request_concrete_sublemma_summary(
        backend=backend,
        graph=graph,
        parent_node_id="n2",
        artifact=artifact,
    )

    assert summary.informal_statement.startswith("For nonnegative real numbers")
    assert "basic order properties" in summary.informal_proof_text


def test_normalize_concrete_sublemma_summary_text_converts_tex_inside_backticks_to_math() -> None:
    text = (
        "Expand `\\lVert a+b\\rVert^2 = \\lVert a\\rVert^2 + 2\\langle a,b\\rangle + \\lVert b\\rVert^2` "
        "and keep `grad_u` as an identifier."
    )

    normalized = _normalize_concrete_sublemma_summary_text(text)

    assert r"\(\lVert a+b\rVert^2 = \lVert a\rVert^2 + 2\langle a,b\rangle + \lVert b\rVert^2\)" in normalized
    assert "`grad_u`" in normalized


def test_normalize_concrete_sublemma_summary_text_strips_control_characters() -> None:
    normalized = _normalize_concrete_sublemma_summary_text("On \x00`grad_u`\x01 over \x02\\(\\Omega\\)")

    assert "\x00" not in normalized
    assert "\x01" not in normalized
    assert "\x02" not in normalized
    assert "`grad_u`" in normalized


def test_normalize_concrete_sublemma_summary_text_unescapes_double_latex_delimiters() -> None:
    normalized = _normalize_concrete_sublemma_summary_text(r"If `grad_u` lives on \\(\Omega\\), proceed.")

    assert r"\(\Omega\)" in normalized
    assert r"\\(\Omega\\)" not in normalized


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
    assert artifact.faithfulness_classification == FaithfulnessClassification.FULL_NODE


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


def test_request_node_formalization_accepts_concrete_narrower_sublemma() -> None:
    graph = ProofGraph(
        theorem_title="Two-point logarithmic Sobolev inequality",
        theorem_statement="The root theorem is G(u) ≥ 0.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Normalized inequality",
                informal_statement="Main theorem.",
                informal_proof_text="Use n1.",
            ),
            ProofNode(
                id="n1",
                title="Center value and symmetry",
                informal_statement=(
                    "The function G satisfies G(1) = 0 and is symmetric under u ↦ 2-u."
                ),
                informal_proof_text="Check the center value and the symmetry relation directly.",
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="Concrete local calculus fact.",
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1")],
    )
    backend = MockBackend(
        queued_payloads=[
            {
                "lean_theorem_name": "G_center_zero",
                "lean_statement": "theorem G_center_zero : G 1 = 0",
                "lean_code": "theorem G_center_zero : G 1 = 0 := by\n  sorry\n",
            }
        ]
    )

    artifact = request_node_formalization(backend=backend, graph=graph, node_id="n1")

    assert artifact.faithfulness_classification == FaithfulnessClassification.CONCRETE_SUBLEMMA
    assert artifact.faithfulness_notes is not None


def test_request_node_formalization_rejects_dimension_downgrade() -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "lean_theorem_name": "narrow_energy_split",
                "lean_statement": (
                    "theorem narrow_energy_split "
                    "(d : ℕ) (Ω : Set (EuclideanSpace ℝ (Fin d))) "
                    "(grad_u grad_w : EuclideanSpace ℝ (Fin d) → EuclideanSpace ℝ (Fin d)) "
                    "(horth : ∫ x, @inner ℝ _ _ (grad_u x) (grad_w x) ∂(volume.restrict Ω) = 0) : "
                    "(∫ x, ‖grad_u x + grad_w x‖ ^ 2 ∂(volume.restrict Ω)) = "
                    "(∫ x, ‖grad_u x‖ ^ 2 ∂(volume.restrict Ω)) + "
                    "∫ x, ‖grad_w x‖ ^ 2 ∂(volume.restrict Ω)"
                ),
                "lean_code": (
                    "import Mathlib.MeasureTheory.Integral.Bochner.Basic\n"
                    "import Mathlib.Analysis.InnerProductSpace.Basic\n\n"
                    "namespace FormalIslands\n\n"
                    "theorem narrow_energy_split "
                    "(d : ℕ) (Ω : Set (EuclideanSpace ℝ (Fin d))) "
                    "(grad_u grad_w : EuclideanSpace ℝ (Fin d) → EuclideanSpace ℝ (Fin d)) "
                    "(horth : ∫ x, @inner ℝ _ _ (grad_u x) (grad_w x) ∂(volume.restrict Ω) = 0) : "
                    "(∫ x, ‖grad_u x + grad_w x‖ ^ 2 ∂(volume.restrict Ω)) = "
                    "(∫ x, ‖grad_u x‖ ^ 2 ∂(volume.restrict Ω)) + "
                    "∫ x, ‖grad_w x‖ ^ 2 ∂(volume.restrict Ω) := by\n"
                    "  sorry\n\nend FormalIslands\n"
                ),
            }
        ]
    )

    with pytest.raises(FormalizationFaithfulnessError, match="finite-dimensional analogue"):
        request_node_formalization(backend=backend, graph=build_pde_graph(), node_id="n1")


def test_assess_formalization_faithfulness_marks_scalarized_core_as_sublemma() -> None:
    node = ProofNode(
        id="n1",
        title="Differential inequality for Y",
        informal_statement="One rewrites the exact identity for 1/2 Y'(t) using E(t) and concludes a lower bound.",
        informal_proof_text="Differentiate Y, rewrite with the energy, and use E(t) ≤ 0.",
        status="candidate_formal",
        formalization_priority=1,
        formalization_rationale="Concrete algebraic core.",
    )
    artifact = FormalArtifact(
        lean_theorem_name="scalar_core",
        lean_statement=(
            "theorem scalar_core {p Y' gradIntegral nonlinIntegral E : ℝ} "
            "(hp : 1 < p) (hY : (1 / 2 : ℝ) * Y' = -gradIntegral + nonlinIntegral) "
            "(hE : E = (1 / 2 : ℝ) * gradIntegral - (1 / (p + 1)) * nonlinIntegral) "
            "(hEnonpos : E ≤ 0) : (1 / 2 : ℝ) * Y' ≥ ((p - 1) / (p + 1)) * nonlinIntegral"
        ),
        lean_code=(
            "import Mathlib.Data.Real.Basic\n\n"
            "theorem scalar_core {p Y' gradIntegral nonlinIntegral E : ℝ} "
            "(hp : 1 < p) (hY : (1 / 2 : ℝ) * Y' = -gradIntegral + nonlinIntegral) "
            "(hE : E = (1 / 2 : ℝ) * gradIntegral - (1 / (p + 1)) * nonlinIntegral) "
            "(hEnonpos : E ≤ 0) : (1 / 2 : ℝ) * Y' ≥ ((p - 1) / (p + 1)) * nonlinIntegral := by\n"
            "  nlinarith [hY, hE, hEnonpos]\n"
        ),
    )

    assessment = assess_formalization_faithfulness(node=node, artifact=artifact)

    assert assessment.classification == FaithfulnessClassification.CONCRETE_SUBLEMMA


def test_assess_formalization_faithfulness_marks_broad_multistep_node_as_sublemma() -> None:
    node = ProofNode(
        id="n3",
        title="Convexity of the one-variable function",
        informal_statement=(
            "Define G, compute G' and G'', prove the explicit second-derivative formula, show it is nonnegative, "
            "deduce convexity, and conclude the global minimum and nonnegativity."
        ),
        informal_proof_text=(
            "Define G, compute derivatives, rewrite the explicit formula, show the sign, then conclude convexity "
            "and the minimum at the base point."
        ),
        status="candidate_formal",
        formalization_priority=1,
        formalization_rationale="Concrete calculus core.",
    )
    artifact = FormalArtifact(
        lean_theorem_name="second_derivative_rhs_nonneg",
        lean_statement=(
            "theorem second_derivative_rhs_nonneg (u : ℝ) (hu0 : 0 < u) (hu2 : u < 2) : "
            "0 <= 2 * (1 - Real.sqrt (u * (2 - u))) / ((u * (2 - u)) * Real.sqrt (u * (2 - u)))"
        ),
        lean_code="theorem second_derivative_rhs_nonneg (u : ℝ) (hu0 : 0 < u) (hu2 : u < 2) : 0 <= 1 := by nlinarith",
    )

    assessment = assess_formalization_faithfulness(node=node, artifact=artifact)

    assert assessment.classification == FaithfulnessClassification.CONCRETE_SUBLEMMA
