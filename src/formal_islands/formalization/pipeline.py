"""Prompt builders and faithfulness assessment for single-node formalization requests."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from formal_islands.backends import StructuredBackend, StructuredBackendRequest
from formal_islands.formalization.schemas import FormalizationResult
from formal_islands.models import (
    FaithfulnessClassification,
    FormalArtifact,
    ProofGraph,
    VerificationResult,
)


FORMALIZATION_SYSTEM_PROMPT = (
    "You are formalizing a single proof node in Lean 4 with Mathlib. "
    "Return only JSON matching the schema. "
    "Keep the formalization local and conservative. "
    "Stay close to the node's actual mathematical content, avoid gratuitous abstraction, "
    "prefer the most concrete faithful theorem you can manage, "
    "and do not game the task by replacing the node with an easier but low-value nearby fact. "
    "Treat the local Lean workspace as the source of truth for available imports and prefer "
    "small, concrete, stable import lists over broad or speculative boilerplate."
)


class FormalizationFaithfulnessError(ValueError):
    """Raised when a proposed formalization drifts too far from the node text."""

    def __init__(self, message: str, artifact: FormalArtifact):
        super().__init__(message)
        self.artifact = artifact


@dataclass(frozen=True)
class FaithfulnessAssessment:
    """Lightweight post-generation faithfulness classification."""

    classification: FaithfulnessClassification
    message: str | None = None


@dataclass(frozen=True)
class ConcreteSublemmaSummary:
    """Short informal rendering of a verified supporting sublemma."""

    informal_statement: str
    informal_proof_text: str


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
            f"Ambient theorem statement:\n{graph.theorem_statement}",
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
                "Preserve the ambient mathematical setting of the theorem and node. If the node is stated in a concrete "
                "setting, keep that same setting in the Lean theorem unless the node itself explicitly states a more abstract generality."
            ),
            (
                "If the full analytic statement is too heavy, prefer a smaller faithful local theorem "
                "or a concrete algebraic consequence that still matches the node, rather than a highly "
                "abstract schematic statement."
            ),
            (
                "If you simplify, simplify the local inferential step while keeping the same concrete objects and "
                "ambient setting. Prefer a concrete sublemma about the same named quantities, variables, operators, "
                "or integrals over a theorem about an arbitrary type, arbitrary measure, or unrelated families of functions."
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
    return enforce_formalization_faithfulness(node=node, artifact=artifact)


def build_concrete_sublemma_summary_request(
    *,
    graph: ProofGraph,
    parent_node_id: str,
    artifact: FormalArtifact,
) -> StructuredBackendRequest:
    parent = next(node for node in graph.nodes if node.id == parent_node_id)
    prompt = "\n\n".join(
        [
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
            "Verified Lean sublemma:",
            json.dumps(
                {
                    "lean_theorem_name": artifact.lean_theorem_name,
                    "lean_statement": artifact.lean_statement,
                },
                indent=2,
            ),
            (
                "Write a short informal statement and a short informal proof text for a supporting local sublemma "
                "that matches the verified Lean theorem. Keep it concrete, close to the parent node's mathematical setting, "
                "and honest about being narrower than the parent node if it is narrower."
            ),
            (
                "Do not introduce arbitrary ambient abstraction not already present in the Lean theorem. "
                "Do not claim this sublemma proves the whole parent node."
            ),
            (
                "Formatting guidance: use LaTeX math delimiters like \\(...\\) or \\[...\\] for mathematical expressions. "
                "Use backticks only for literal Lean identifiers, theorem names, or simple variable names such as `horth` or `grad_u`. "
                "Do not put LaTeX commands like \\int, \\lVert, \\Omega, \\langle, or \\cdot inside backticks."
            ),
            (
                "Return a JSON object with keys informal_statement and informal_proof_text."
            ),
        ]
    )
    return StructuredBackendRequest(
        prompt=prompt,
        system_prompt=(
            "You are summarizing a verified Lean theorem as a concrete supporting informal sublemma. "
            "Return only JSON matching the schema."
        ),
        json_schema={
            "type": "object",
            "properties": {
                "informal_statement": {"type": "string", "minLength": 1},
                "informal_proof_text": {"type": "string", "minLength": 1},
            },
            "required": ["informal_statement", "informal_proof_text"],
            "additionalProperties": False,
        },
        task_name="summarize_concrete_sublemma",
    )


def request_concrete_sublemma_summary(
    *,
    backend: StructuredBackend,
    graph: ProofGraph,
    parent_node_id: str,
    artifact: FormalArtifact,
) -> ConcreteSublemmaSummary:
    response = backend.run_structured(
        build_concrete_sublemma_summary_request(
            graph=graph,
            parent_node_id=parent_node_id,
            artifact=artifact,
        )
    )
    payload = response.payload
    return ConcreteSublemmaSummary(
        informal_statement=_normalize_concrete_sublemma_summary_text(
            str(payload["informal_statement"]).strip()
        ),
        informal_proof_text=_normalize_concrete_sublemma_summary_text(
            str(payload["informal_proof_text"]).strip()
        ),
    )


def _normalize_concrete_sublemma_summary_text(text: str) -> str:
    text = "".join(ch for ch in text if ch in ("\n", "\t") or ord(ch) >= 32)
    text = text.replace("\\\\(", r"\(").replace("\\\\)", r"\)")
    text = text.replace("\\\\[", r"\[").replace("\\\\]", r"\]")

    def replace(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        if "\\" in inner:
            return rf"\({inner}\)"
        return match.group(0)

    return re.sub(r"`([^`]+)`", replace, text)


def enforce_formalization_faithfulness(node, artifact: FormalArtifact) -> FormalArtifact:
    assessment = assess_formalization_faithfulness(node=node, artifact=artifact)
    updated_artifact = artifact.model_copy(
        update={
            "faithfulness_classification": assessment.classification,
            "faithfulness_notes": assessment.message,
        }
    )
    if assessment.classification != FaithfulnessClassification.OVER_ABSTRACT:
        return updated_artifact
    raise FormalizationFaithfulnessError(
        " ".join(
            [
                "Formalization drifted too far from the target node.",
                assessment.message or "",
            ]
        ).strip(),
        artifact=updated_artifact,
    )


def assess_formalization_faithfulness(node, artifact: FormalArtifact) -> FaithfulnessAssessment:
    issues = _collect_over_abstract_issues(node, artifact)
    if issues:
        return FaithfulnessAssessment(
            classification=FaithfulnessClassification.OVER_ABSTRACT,
            message=" ".join(issues),
        )

    if _looks_like_concrete_sublemma(node, artifact):
        return FaithfulnessAssessment(
            classification=FaithfulnessClassification.CONCRETE_SUBLEMMA,
            message=(
                "Accepted as a narrower concrete local core in the same ambient setting; "
                "it should support the parent node rather than count as full-node certification."
            ),
        )

    return FaithfulnessAssessment(classification=FaithfulnessClassification.FULL_NODE)


def _collect_over_abstract_issues(node, artifact: FormalArtifact) -> list[str]:
    issues: list[str] = []
    lean_text = f"{artifact.lean_statement}\n{artifact.lean_code}"
    node_text = " ".join([node.title, node.informal_statement, node.informal_proof_text]).lower()
    statement = artifact.lean_statement

    if "Type*" in lean_text or re.search(r"\bType u\b|\bType v\b", lean_text):
        issues.append(
            "Avoid introducing arbitrary `Type*` parameters when the node describes a concrete local claim."
        )

    if (
        ("[InnerProductSpace" in lean_text or "[NormedAddCommGroup" in lean_text)
        and not any(marker in node_text for marker in ("inner product", "hilbert", "normed"))
    ):
        issues.append(
            "Avoid translating the node into an arbitrary normed/inner-product space unless the node itself calls for that abstraction."
        )

    if _node_does_not_invite_measure_abstraction(node_text) and _looks_like_arbitrary_measure_abstraction(statement):
        issues.append(
            "Avoid replacing a concrete local claim with an arbitrary measure-space theorem."
        )

    if _has_multiple_unrelated_function_families(statement, node_text):
        issues.append(
            "Avoid replacing the node with unrelated families of functions or indexed maps absent from the original claim."
        )

    return issues


def _node_does_not_invite_measure_abstraction(node_text: str) -> bool:
    abstract_markers = (
        "measure",
        "measurable",
        "almost everywhere",
        "a.e.",
        "integrable",
        "measure space",
    )
    return not any(marker in node_text for marker in abstract_markers)


def _looks_like_arbitrary_measure_abstraction(lean_text: str) -> bool:
    markers = (
        "MeasurableSpace",
        "μ : Measure",
        "(μ : Measure",
        "∂μ",
    )
    return any(marker in lean_text for marker in markers)


def _has_multiple_unrelated_function_families(statement: str, node_text: str) -> bool:
    suspicious_function_families: list[str] = []
    for match in re.finditer(r"[\(\{]([^:\)\}]+)\s*:\s*([^\)\}]+)[\)\}]", statement):
        names = [name for name in match.group(1).split() if name]
        annotation = match.group(2)
        if "→" not in annotation and "->" not in annotation:
            continue
        for name in names:
            lowered = name.lower()
            if len(name) <= 1 or lowered in node_text or lowered.startswith("h"):
                continue
            suspicious_function_families.append(name)
    return len(set(suspicious_function_families)) >= 2


def _looks_like_concrete_sublemma(node, artifact: FormalArtifact) -> bool:
    statement = artifact.lean_statement
    lean_text = f"{artifact.lean_statement}\n{artifact.lean_code}"
    node_text = " ".join([node.title, node.informal_statement, node.informal_proof_text]).lower()

    fresh_scalar_count = _count_fresh_scalar_placeholders(statement, node_text)
    fresh_function_count = _count_fresh_function_placeholders(statement, node_text)
    fresh_named_count = _count_fresh_named_placeholders(statement, node_text)
    structural_hypotheses = len(re.findall(r"\b[hH][A-Za-z0-9_']*\b", statement))

    node_concrete_markers = _count_concrete_markers(node_text)
    theorem_concrete_markers = _count_concrete_markers(lean_text.lower())

    if fresh_function_count >= 1:
        return True
    if fresh_scalar_count >= 2:
        return True
    if fresh_named_count >= 2:
        return True
    if structural_hypotheses >= 2 and fresh_scalar_count >= 1:
        return True
    if node_concrete_markers >= 2 and theorem_concrete_markers == 0:
        return True
    return False


def _count_fresh_scalar_placeholders(statement: str, node_text: str) -> int:
    fresh: set[str] = set()
    for match in re.finditer(r"[\(\{]([^:\)\}]+)\s*:\s*ℝ[\)\}]", statement):
        for name in [name for name in match.group(1).split() if name]:
            lowered = name.lower()
            if len(name) <= 1 or lowered.startswith("h") or lowered in node_text:
                continue
            fresh.add(name)
    return len(fresh)


def _count_fresh_function_placeholders(statement: str, node_text: str) -> int:
    fresh: set[str] = set()
    for match in re.finditer(r"[\(\{]([^:\)\}]+)\s*:\s*([^\)\}]+)[\)\}]", statement):
        annotation = match.group(2)
        if "→" not in annotation and "->" not in annotation:
            continue
        for name in [name for name in match.group(1).split() if name]:
            lowered = name.lower()
            if len(name) <= 1 or lowered.startswith("h") or lowered in node_text:
                continue
            fresh.add(name)
    return len(fresh)


def _count_fresh_named_placeholders(statement: str, node_text: str) -> int:
    fresh: set[str] = set()
    ambient_allowlist = {"Ω", "omega", "d", "p", "t", "x", "u", "v", "w", "f", "g", "e"}
    for match in re.finditer(r"[\(\{]([^:\)\}]+)\s*:\s*([^\)\}]+)[\)\}]", statement):
        for name in [name for name in match.group(1).split() if name]:
            lowered = name.lower()
            if (
                len(name) <= 1
                or lowered.startswith("h")
                or lowered in node_text
                or name in ambient_allowlist
                or lowered in ambient_allowlist
            ):
                continue
            fresh.add(name)
    return len(fresh)


def _count_concrete_markers(text: str) -> int:
    markers = (
        "∫",
        "\\int",
        "gradient",
        "grad",
        "\\nabla",
        "∇",
        "\\delta",
        "Δ",
        "laplac",
        "volume.restrict",
        "euclideanspace",
        "domain",
        "boundary",
    )
    return sum(1 for marker in markers if marker in text)
