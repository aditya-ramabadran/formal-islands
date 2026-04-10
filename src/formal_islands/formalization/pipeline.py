"""Prompt builders and faithfulness assessment for single-node formalization requests."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from collections import defaultdict, deque
from dataclasses import asdict, dataclass

from formal_islands.backends import StructuredBackend, StructuredBackendRequest
from formal_islands.formalization.schemas import FormalizationResult
from formal_islands.models import (
    FaithfulnessClassification,
    FormalArtifact,
    ProofNode,
    ProofGraph,
    VerificationResult,
)
from formal_islands.progress import run_structured_with_progress


FORMALIZATION_SYSTEM_PROMPT = (
    "You are formalizing a single proof node in Lean 4 with Mathlib. "
    "Return only JSON matching the schema. "
    "Keep the formalization local and conservative. "
    "Stay close to the node's actual mathematical content, avoid gratuitous abstraction, "
    "prefer the most concrete faithful theorem you can manage, "
    "and do not game the task by replacing the node with an easier but low-value nearby fact. "
    "Keep theorem headers and binders Lean-safe: Lean treats `λ` as a reserved keyword, so prefer "
    "ASCII identifiers like `lambda1` or `lambda_1` instead of Unicode binder names like `λ₁`. "
    "Treat the local Lean workspace as the source of truth for available imports and prefer "
    "small, concrete, stable import lists over broad or speculative boilerplate."
)

RELATION_MARKERS = ("\\le", "\\ge", "\\to", "\\Rightarrow", "\\implies", "≤", "≥", "=", "<", ">")
DIMENSION_DOWNGRADE_NODE_MARKERS = (
    "integral",
    "gradient",
    "boundary",
    "domain",
    "variational",
    "weak",
    "compactness",
    "minimizing sequence",
    "function space",
    "hilbert",
    "banach",
    "convergence",
)
DIMENSION_DOWNGRADE_LEAN_MARKERS = (
    "EuclideanSpace",
    "FiniteDimensional",
    "Matrix",
    "Icc",
    "interval",
    "one-dimensional",
    "one dimensional",
    "1d",
    "concave",
    "concavity",
    "convex",
    "convexity",
    "proxy",
    "analogue",
    "lower-dimensional",
)
FAITHFULNESS_GUARD_FAILURE_MARKERS = (
    "faithfulness guard",
    "over abstract",
    "too abstract",
    "arbitrary type",
    "formalization drifted too far",
    "different mathematical setting",
)
SETTING_FAILURE_MARKERS = (
    "finite-dimensional",
    "euclideanspace",
    "measure-space theorem",
    "measure space theorem",
    "inner-product space",
    "hilbert",
    "banach",
    "function space",
    "ambient universe",
    "wrong mathematical setting",
    "different mathematical setting",
    "dimension profile",
    "lower-dimensional",
    "one-dimensional",
    "one dimensional",
    "1d",
    "proxy model",
    "proxy theorem",
    "analogue",
)
THEOREM_SHAPE_FAILURE_MARKERS = (
    "downstream consequence",
    "side consequence",
    "assumed the key identity",
    "instead of proving",
    "wrong logical claim",
    "abstract proxy",
    "generic theorem",
    "too abstract",
    "over abstract",
    "arbitrary type",
)
LEAN_PACKAGING_FAILURE_MARKERS = (
    "unknown identifier",
    "unknown constant",
    "unknown namespace",
    "expected token",
    "failed to synthesize instance",
    "invalid field",
    "unknown type",
    "syntax error",
)
PROOF_STRATEGY_FAILURE_MARKERS = (
    "unsolved goals",
    "rewrite",
    "simp",
    "linarith",
    "nlinarith",
    "ring",
    "omega",
)
SMALLER_SUBLEMMA_FAILURE_MARKERS = (
    "smaller",
    "sublemma",
    "local core",
    "narrower",
    "coverage",
    "fallback",
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
    coverage_score: int = 0


@dataclass(frozen=True)
class ConcreteSublemmaSummary:
    """Short informal rendering of a verified supporting sublemma."""

    informal_statement: str
    informal_proof_text: str


@dataclass(frozen=True)
class CombinedFormalizationAssessment:
    """Planning-backend judgment about the quality and recoverability of a verified theorem."""

    result_kind: str
    certifies_main_burden: bool
    coverage_score: int
    expansion_warranted: bool
    worth_retrying_later: bool
    reason: str


@dataclass(frozen=True)
class ParentPromotionAssessment:
    """Planning-backend judgment about whether an informal parent should now be promoted."""

    promote_parent: bool
    recommended_priority: int | None
    reason: str


@dataclass(frozen=True)
class BlockerPromotionAssessment:
    """Planning-backend judgment about whether an informal blocker node should now be promoted."""

    promote_node: bool
    recommended_priority: int | None
    reason: str


class RepairCategory(StrEnum):
    """Structured repair buckets for retry guidance."""

    SETTING_FIX = "setting_fix"
    THEOREM_SHAPE_FIX = "theorem_shape_fix"
    LEAN_PACKAGING_FIX = "lean_packaging_fix"
    PROOF_STRATEGY_FIX = "proof_strategy_fix"
    TRY_SMALLER_SUBLEMMA = "try_smaller_sublemma"
    TRY_LARGER_CORE = "try_larger_core"


@dataclass(frozen=True)
class RepairAssessment:
    """Planning-backend or heuristic diagnosis of a failed attempt."""

    category: RepairCategory
    note: str


class AbstractionReviewCategory(StrEnum):
    """Whether a repeated abstraction looks canonical or like real drift."""

    CANONICAL_ENCODING = "canonical_encoding"
    REAL_DRIFT = "real_drift"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class AbstractionReviewAssessment:
    """Planning-backend judgment about a repeated over-abstract theorem encoding."""

    category: AbstractionReviewCategory
    note: str


@dataclass(frozen=True)
class CoverageExpansionAssessment:
    """Planning-backend judgment about whether a concrete sublemma still needs expansion."""

    already_matches_target: bool
    reason: str


@dataclass(frozen=True)
class CoverageComponent:
    """A small local piece of a node's proof burden."""

    kind: str
    text: str


@dataclass(frozen=True)
class CoverageSketch:
    """A lightweight decomposition of the target node's coverage structure."""

    summary: str
    components: list[CoverageComponent]


@dataclass(frozen=True)
class LocalProofContext:
    """Nearby nodes split into verified supporting lemmas and context-only siblings."""

    verified_supporting_nodes: list[ProofNode]
    context_only_nodes: list[ProofNode]


@dataclass(frozen=True)
class VerifiedDirectChildContext:
    """Direct child nodes of the target that are already verified."""

    child_nodes: list[ProofNode]


def build_node_coverage_sketch(node, *, max_components: int = 4) -> CoverageSketch:
    """Summarize a node into a few concrete local coverage components."""

    text = "\n".join([node.title, node.informal_statement, node.informal_proof_text])
    components: list[CoverageComponent] = []
    for clause in _split_coverage_clauses(text):
        kind = _coverage_component_kind(clause)
        if kind is None:
            continue
        components.append(CoverageComponent(kind=kind, text=clause))
        if len(components) >= max_components:
            break

    if not components:
        components = [CoverageComponent(kind="goal", text=_normalize_coverage_text(node.informal_statement))]

    return CoverageSketch(
        summary=_normalize_coverage_text(node.informal_statement),
        components=components,
    )


def build_local_proof_context(
    graph: ProofGraph,
    node_id: str,
    *,
    max_verified: int = 5,
    max_context: int = 5,
) -> LocalProofContext:
    """Collect nearby nodes and split them into verified support versus context only."""

    node_by_id = {node.id: node for node in graph.nodes}
    if node_id not in node_by_id:
        raise ValueError(f"node '{node_id}' was not found in the graph")

    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        adjacency[edge.source_id].add(edge.target_id)
        adjacency[edge.target_id].add(edge.source_id)

    distances = {node_id: 0}
    queue = deque([node_id])
    while queue:
        current_id = queue.popleft()
        current_distance = distances[current_id]
        for adjacent_id in adjacency.get(current_id, set()):
            if adjacent_id in distances:
                continue
            distances[adjacent_id] = current_distance + 1
            queue.append(adjacent_id)

    neighborhood = [
        node
        for node in graph.nodes
        if node.id != node_id and node.id in distances
    ]
    ordered = sorted(neighborhood, key=lambda node: (distances[node.id], node.id))

    verified = [node for node in ordered if node.status == "formal_verified"][:max_verified]
    context = [node for node in ordered if node.status != "formal_verified"][:max_context]
    return LocalProofContext(verified_supporting_nodes=verified, context_only_nodes=context)


def build_verified_direct_child_context(graph: ProofGraph, node_id: str) -> VerifiedDirectChildContext:
    """Collect the target node's direct children that are already verified."""

    node_by_id = {node.id: node for node in graph.nodes}
    if node_id not in node_by_id:
        raise ValueError(f"node '{node_id}' was not found in the graph")

    child_ids = [edge.target_id for edge in graph.edges if edge.source_id == node_id]
    child_nodes = [
        node
        for node in graph.nodes
        if node.id in child_ids and node.status == "formal_verified" and node.formal_artifact is not None
    ]
    child_nodes = sorted(child_nodes, key=lambda node: node.id)
    return VerifiedDirectChildContext(child_nodes=child_nodes)


def format_local_proof_context(context: LocalProofContext) -> str:
    """Render local proof context as plain text for prompt injection."""

    sections: list[str] = []
    lines = [
        "Nearby verified context for orientation only:",
        (
            "These nearby verified results are included to help preserve the right theorem family, notation, "
            "and ambient setting. Do not rely on them unless they are also explicit dependencies of the target "
            "node."
        ),
    ]
    if context.verified_supporting_nodes:
        for node in context.verified_supporting_nodes:
            theorem_name = node.formal_artifact.lean_theorem_name if node.formal_artifact else "(no theorem name)"
            lean_statement = node.formal_artifact.lean_statement if node.formal_artifact else ""
            lines.extend(
                [
                    f"- id: {node.id}",
                    f"  title: {node.title}",
                    f"  Lean theorem: {theorem_name}",
                    f"  Lean statement: {lean_statement}",
                ]
            )
    else:
        lines.append("  - none listed")
    sections.append("\n".join(lines))

    lines = [
        "Context-only sibling ingredients in the same proof neighborhood:",
        (
            "These nodes are only there to orient the proof. Do not assume their statements "
            "unless they are separately listed above as verified supporting lemmas."
        ),
    ]
    if context.context_only_nodes:
        for node in context.context_only_nodes:
            lines.extend(
                [
                    f"- id: {node.id}",
                    f"  title: {node.title}",
                    f"  informal statement: {node.informal_statement}",
                ]
            )
    else:
        lines.append("  - none listed")
    sections.append("\n".join(lines))

    sections.append(
        "Dependency note: every edge goes from a claim to one of the claims it depends on. "
        "A refinement edge marks a narrower dependency carved out from a broader proof step."
    )
    return "\n\n".join(sections)


def format_verified_direct_child_context(context: VerifiedDirectChildContext) -> str:
    """Render verified direct children as prompt-ready text."""

    lines = [
        "Verified direct child lemmas already certified in this run:",
        (
            "These are the target node's own direct child theorems, so you may use them as established "
            "dependency lemmas when they are listed here. They are direct outgoing dependencies of the target node."
        ),
    ]
    if context.child_nodes:
        for node in context.child_nodes:
            theorem_name = node.formal_artifact.lean_theorem_name if node.formal_artifact else "(no theorem name)"
            lean_statement = node.formal_artifact.lean_statement if node.formal_artifact else ""
            lines.extend(
                [
                    f"- id: {node.id}",
                    f"  title: {node.title}",
                    f"  informal statement: {node.informal_statement}",
                    f"  Lean theorem: {theorem_name}",
                    f"  Lean statement: {lean_statement}",
                ]
            )
    else:
        lines.append("  - none listed")
    return "\n".join(lines)


def _format_coverage_sketch_for_prompt(sketch: CoverageSketch) -> str:
    lines = [f"Summary: {sketch.summary}", "Components:"]
    for component in sketch.components:
        lines.append(f"- [{component.kind}] {component.text}")
    return "\n".join(lines)


def format_faithfulness_notes(result_kind: str, reason: str) -> str:
    """Encode semantic assessment notes in a compact, human-readable format."""

    return f"[{result_kind}] {reason}".strip()


def parse_faithfulness_notes(notes: str | None) -> tuple[str | None, str | None]:
    """Parse the compact faithfulness note format when available."""

    if not notes:
        return None, None
    match = re.match(r"^\[(?P<kind>[^\]]+)\]\s*(?P<reason>.*)$", notes, flags=re.S)
    if match is None:
        return None, notes
    return match.group("kind").strip() or None, match.group("reason").strip() or None


def _split_coverage_clauses(text: str) -> list[str]:
    clauses = re.split(r"(?<=[.;])\s+|\n+", text)
    return [clause.strip() for clause in clauses if clause.strip()]


def _normalize_coverage_text(text: str) -> str:
    return " ".join(text.split())


def _coverage_component_kind(clause: str) -> str | None:
    lowered = clause.lower()
    if len(clause.split()) <= 3 and not _has_relation_marker(clause):
        return None
    if any(word in lowered for word in ("define", "set", "write")):
        return "setup"
    if any(word in lowered for word in ("expand", "rewrite", "simplify", "reduce", "cancel")):
        return "algebraic_step"
    if any(word in lowered for word in ("differentiate", "derive", "compute")):
        return "calculus_step"
    if any(word in lowered for word in ("apply", "use", "combine", "substitute", "specialize")):
        return "local_inference"
    if any(word in lowered for word in ("hence", "thus", "therefore", "conclude", "obtain", "gives", "yields")):
        return "conclusion"
    if _has_relation_marker(clause):
        return "identity_or_estimate"
    return "local_step"


def _has_relation_marker(text: str) -> bool:
    return any(marker in text for marker in RELATION_MARKERS)


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
    ]
    local_context = build_local_proof_context(graph, node_id)
    direct_child_context = build_verified_direct_child_context(graph, node_id)

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
            "Coverage sketch:",
            json.dumps(asdict(build_node_coverage_sketch(node)), indent=2),
            "Local proof neighborhood:",
            format_local_proof_context(local_context),
            (
                "Immediate parent summary:\n" + json.dumps(parent_summaries[0], indent=2)
                if parent_summaries
                else "Immediate parent summary:\n[]"
            ),
            (
                "Verified child context:\n" + json.dumps(child_summaries, indent=2)
                if child_summaries
                else "Verified child context:\n[]"
            ),
            format_verified_direct_child_context(direct_child_context),
            (
                "Dependency direction note: the target node depends on the verified child lemmas listed here. "
                "Do not treat those verified children as parents or as claims that depend on the target."
            ),
            (
                "These verified children are already available. The theorem you produce should be only the "
                "remaining parent-level delta, not a restatement of any verified child or a cosmetic corollary "
                "that duplicates their work."
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
                "Keep theorem names, binder names, and hypotheses ASCII-safe. Lean treats `λ` as a reserved "
                "keyword in theorem headers and binders, so do not use Unicode binder names like `λ₁`; prefer "
                "plain names such as `lambda1` or `lambda_1` instead."
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
                "ambient setting. Do not switch theorem family, dimension, or proof universe just to get a theorem "
                "that compiles. Prefer a concrete sublemma about the same named quantities, variables, operators, "
                "or integrals over a theorem about an arbitrary type, arbitrary measure, or unrelated families of functions."
            ),
            (
                "Use the coverage sketch to decide what the theorem is supposed to cover. If you only prove one "
                "component of the sketch, keep the result honest and avoid pretending to certify the whole node."
            ),
            (
                "If local context lists nearby verified results, treat them as orientation only unless the graph "
                "also lists them as explicit dependencies of the target node. Only verified direct child context "
                "should be used as established dependency lemmas for this job."
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
                    "Preserve the theorem statement and overall structure unless the compiler error forces a change. "
                    "If the error is about a Unicode binder or theorem-header identifier, keep the theorem shape fixed "
                    "and only rename the binder or hypothesis to a Lean-safe ASCII identifier such as `lambda1`."
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


def build_combined_verification_assessment_request(
    *,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
) -> StructuredBackendRequest:
    node = next(node for node in graph.nodes if node.id == node_id)
    local_context = build_local_proof_context(graph, node_id)
    sketch = build_node_coverage_sketch(node)
    prompt = "\n\n".join(
        [
            f"Theorem title: {graph.theorem_title}",
            (
                "Target node:\n"
                f"- id: {node.id}\n"
                f"- title: {node.title}\n"
                f"- informal statement: {node.informal_statement}\n"
                f"- informal proof text: {node.informal_proof_text}\n"
                f"- formalization priority: {node.formalization_priority if node.formalization_priority is not None else 'unset'}\n"
                f"- formalization rationale: {node.formalization_rationale or '(no rationale recorded)'}"
            ),
            (
                "Verified Lean theorem to assess:\n"
                f"- theorem name: {artifact.lean_theorem_name}\n"
                f"- Lean statement: {artifact.lean_statement}\n"
                "Only use the theorem statement for the semantic judgment; the proof text is not part of the comparison."
            ),
            (
                "Prior heuristic faithfulness assessment (advisory only; the planning backend has the final say on "
                "borderline cases):\n"
                f"- classification: {artifact.faithfulness_classification}\n"
                f"- notes: {artifact.faithfulness_notes or '(none)'}"
            ),
            "Coverage sketch:",
            _format_coverage_sketch_for_prompt(sketch),
            "Local proof neighborhood:",
            format_local_proof_context(local_context),
            (
                "Assess the relationship between the verified Lean theorem and the target node. "
                "Be conservative: if the theorem is only a consequence, an analogue, or a smaller shard, say so. "
                "If the theorem already matches the target node closely enough that growing it further would be redundant, "
                "call it full_match. Distinguish faithful_core from certifies_main_burden: faithful_core means the same "
                "setting and same proof path, while certifies_main_burden means the theorem covers the hardest inferential "
                "step in the node. Coverage score: give a number from 0 to 10 describing how much of the node's proof burden "
                "this theorem already covers. If the prior heuristic assessment suggested a possible downgrade but the theorem "
                "still looks faithful, override the heuristic and say so explicitly in the reason."
            ),
            (
                "Return JSON with keys result_kind, certifies_main_burden, coverage_score, expansion_warranted, "
                "worth_retrying_later, and reason."
            ),
        ]
    )
    return StructuredBackendRequest(
        prompt=prompt,
        system_prompt=(
            "You are a conservative semantic reviewer for a verified Lean theorem. Return only JSON matching "
            "the schema."
        ),
        json_schema={
            "type": "object",
            "properties": {
                "result_kind": {
                    "type": "string",
                    "enum": [
                        "full_match",
                        "faithful_core",
                        "downstream_consequence",
                        "dimensional_analogue",
                        "helper_shard",
                    ],
                },
                "certifies_main_burden": {"type": "boolean"},
                "coverage_score": {"type": "integer", "minimum": 0, "maximum": 10},
                "expansion_warranted": {"type": "boolean"},
                "worth_retrying_later": {"type": "boolean"},
                "reason": {"type": "string", "minLength": 1},
            },
            "required": [
                "result_kind",
                "certifies_main_burden",
                "coverage_score",
                "expansion_warranted",
                "worth_retrying_later",
                "reason",
            ],
            "additionalProperties": False,
        },
        task_name="assess_verified_formalization",
    )


def request_combined_verification_assessment(
    *,
    backend: StructuredBackend,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
) -> CombinedFormalizationAssessment:
    response = run_structured_with_progress(
        backend,
        build_combined_verification_assessment_request(
            graph=graph,
            node_id=node_id,
            artifact=artifact,
        ),
    )
    payload = response.payload
    if "result_kind" not in payload:
        already_matches_target = bool(payload.get("already_matches_target", False))
        reason = str(payload.get("reason", "")).strip()
        if not reason:
            reason = "Legacy compatibility payload."
        return CombinedFormalizationAssessment(
            result_kind="full_match" if already_matches_target else "helper_shard",
            certifies_main_burden=already_matches_target,
            coverage_score=10 if already_matches_target else int(payload.get("coverage_score", 0)),
            expansion_warranted=not already_matches_target,
            worth_retrying_later=False,
            reason=reason,
        )
    return CombinedFormalizationAssessment(
        result_kind=str(payload["result_kind"]).strip(),
        certifies_main_burden=bool(payload["certifies_main_burden"]),
        coverage_score=int(payload["coverage_score"]),
        expansion_warranted=bool(payload["expansion_warranted"]),
        worth_retrying_later=bool(payload["worth_retrying_later"]),
        reason=str(payload["reason"]).strip(),
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

    response = run_structured_with_progress(
        backend,
        build_formalization_request(
            graph=graph,
            node_id=node_id,
            compiler_feedback=compiler_feedback,
            previous_lean_code=previous_lean_code,
        ),
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
    response = run_structured_with_progress(
        backend,
        build_concrete_sublemma_summary_request(
            graph=graph,
            parent_node_id=parent_node_id,
            artifact=artifact,
        ),
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


def build_parent_promotion_assessment_request(
    *,
    graph: ProofGraph,
    parent_node_id: str,
) -> StructuredBackendRequest:
    parent = next(node for node in graph.nodes if node.id == parent_node_id)
    local_context = build_local_proof_context(graph, parent_node_id)
    sketch = build_node_coverage_sketch(parent)
    verified_children = build_verified_direct_child_context(graph, parent_node_id)
    direct_child_records = []
    node_by_id = {node.id: node for node in graph.nodes}
    for edge in sorted(
        (candidate for candidate in graph.edges if candidate.source_id == parent_node_id),
        key=lambda candidate: (candidate.target_id, candidate.label or ""),
    ):
        child = node_by_id.get(edge.target_id)
        if child is None:
            continue
        direct_child_records.append(
            {
                "id": child.id,
                "title": child.title,
                "status": child.status,
                "edge_label": edge.label,
                "is_supporting_formal_core": edge.label == "formal_sublemma_for",
                "lean_theorem_name": (
                    child.formal_artifact.lean_theorem_name if child.formal_artifact else None
                ),
                "lean_statement": (
                    child.formal_artifact.lean_statement if child.formal_artifact else None
                ),
            }
        )
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
            "Verified direct child lemmas already available:",
            format_verified_direct_child_context(verified_children),
            "Direct child inventory (use this to understand whether promotion would absorb a supporting core or simply duplicate it):",
            json.dumps(direct_child_records, indent=2),
            "Parent coverage sketch:",
            _format_coverage_sketch_for_prompt(sketch),
            "Local proof neighborhood:",
            format_local_proof_context(local_context),
            (
                "The target node is still informal, but all of its direct children are already verified. Decide whether "
                "the parent is now reasonable to formalize as a parent-assembly theorem, or whether it should remain "
                "informal for now. Distinguish pure duplication from worthwhile closure: if promoting the parent would "
                "mainly certify a clean parent-level assembly, packaging, or representational enlargement theorem and "
                "would likely absorb a supporting formal-core child into a cleaner final graph, that is a real reason "
                "to promote it."
            ),
            (
                "If you do promote it, return a recommended_priority from 1 to 3, where 1 means it should be tried "
                "very soon and 3 means it can wait behind other candidates. If you do not promote it, return null for "
                "recommended_priority. Do not promote the parent if the strongest likely formal statement would be a "
                "pure duplicate or near-verbatim restatement of one verified child, invert the dependency direction, "
                "or package a child result as a fake parent theorem without adding any real parent-level closure."
            ),
            (
                "Focus on whether the verified children now cover the hard proof burden, leaving only parent-level "
                "assembly, rewriting, side-condition discharge, packaging into the parent statement, or a short "
                "representational translation. If the remaining work is still the main burden, or the children merely "
                "suggest an analogue in a different theorem family, do not promote it. But if the remaining delta "
                "looks like a short concrete closure theorem that would turn the parent into a meaningful full-node "
                "result, prefer promotion even when the mathematics overlaps heavily with the support core."
            ),
            "Return JSON with keys promote_parent, recommended_priority, and reason.",
        ]
    )
    return StructuredBackendRequest(
        prompt=prompt,
        system_prompt=(
            "You are a proof-graph planner deciding whether an informal parent should now be promoted. "
            "Bias toward promotion when the remaining work is a short, concrete parent-level closure theorem that would simplify the final artifact. "
            "Return only JSON matching the schema."
        ),
        json_schema={
            "type": "object",
            "properties": {
                "promote_parent": {"type": "boolean"},
                "recommended_priority": {
                    "anyOf": [
                        {"type": "integer", "minimum": 1, "maximum": 3},
                        {"type": "null"},
                    ]
                },
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["promote_parent", "recommended_priority", "reason"],
            "additionalProperties": False,
        },
        task_name="assess_parent_promotion",
    )


def request_parent_promotion_assessment(
    *,
    backend: StructuredBackend,
    graph: ProofGraph,
    parent_node_id: str,
) -> ParentPromotionAssessment:
    response = run_structured_with_progress(
        backend,
        build_parent_promotion_assessment_request(graph=graph, parent_node_id=parent_node_id),
    )
    payload = response.payload
    recommended_priority = payload.get("recommended_priority")
    if recommended_priority is not None:
        recommended_priority = int(recommended_priority)
    return ParentPromotionAssessment(
        promote_parent=bool(payload["promote_parent"]),
        recommended_priority=recommended_priority,
        reason=str(payload["reason"]).strip(),
    )


def build_blocker_promotion_assessment_request(
    *,
    graph: ProofGraph,
    blocker_node_id: str,
) -> StructuredBackendRequest:
    blocker = next(node for node in graph.nodes if node.id == blocker_node_id)
    local_context = build_local_proof_context(graph, blocker_node_id)
    sketch = build_node_coverage_sketch(blocker)
    parents = [node for node in graph.nodes if any(edge.source_id == node.id and edge.target_id == blocker_node_id for edge in graph.edges)]
    parent_records: list[dict[str, object]] = []
    node_by_id = {node.id: node for node in graph.nodes}
    for parent in parents:
        child_ids = [edge.target_id for edge in graph.edges if edge.source_id == parent.id]
        child_inventory = [
            {
                "id": child_id,
                "title": node_by_id[child_id].title,
                "status": node_by_id[child_id].status,
                "is_target_blocker": child_id == blocker_node_id,
                "is_verified": node_by_id[child_id].status == "formal_verified",
            }
            for child_id in child_ids
            if child_id in node_by_id
        ]
        remaining_informal = [
            child_id
            for child_id in child_ids
            if child_id in node_by_id and node_by_id[child_id].status != "formal_verified"
        ]
        parent_records.append(
            {
                "id": parent.id,
                "title": parent.title,
                "status": parent.status,
                "informal_statement": parent.informal_statement,
                "remaining_unverified_child_ids": remaining_informal,
                "is_last_remaining_unverified_child": remaining_informal == [blocker_node_id],
                "child_inventory": child_inventory,
            }
        )

    prompt = "\n\n".join(
        [
            f"Theorem title: {graph.theorem_title}",
            (
                "Target informal blocker node:\n"
                f"- id: {blocker.id}\n"
                f"- title: {blocker.title}\n"
                f"- informal statement: {blocker.informal_statement}\n"
                f"- informal proof text: {blocker.informal_proof_text}\n"
                f"- current status: {blocker.status}\n"
                f"- prior formalization outcome: {blocker.last_formalization_outcome or '(none)'}"
            ),
            "Blocker coverage sketch:",
            _format_coverage_sketch_for_prompt(sketch),
            "Parent nodes for which this blocker may be the last remaining obstacle:",
            json.dumps(parent_records, indent=2),
            "Local proof neighborhood:",
            format_local_proof_context(local_context),
            (
                "Decide whether this blocker node should now be promoted to candidate_formal. "
                "Bias toward promotion when the node looks like a concrete endpoint case, base case, side branch, "
                "or short local lemma that has become the last remaining obstacle to a meaningful parent/root closure theorem."
            ),
            (
                "Do not promote it if the node still looks too broad, too abstract, or likely to duplicate work already "
                "captured by a verified sibling. Prefer promotion when the node now appears to be within reach because "
                "its verified siblings already discharged the hard interior proof burden."
            ),
            (
                "If you do promote it, return a recommended_priority from 1 to 3, where 1 means it should be tried "
                "very soon and 3 means it can wait. If you do not promote it, return null for recommended_priority."
            ),
            "Return JSON with keys promote_node, recommended_priority, and reason.",
        ]
    )
    return StructuredBackendRequest(
        prompt=prompt,
        system_prompt=(
            "You are a proof-graph planner deciding whether one remaining informal blocker node should now be promoted. "
            "Bias toward promotion when it is the last concrete obstacle to a worthwhile parent/root closure theorem. "
            "Return only JSON matching the schema."
        ),
        json_schema={
            "type": "object",
            "properties": {
                "promote_node": {"type": "boolean"},
                "recommended_priority": {
                    "anyOf": [
                        {"type": "integer", "minimum": 1, "maximum": 3},
                        {"type": "null"},
                    ]
                },
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["promote_node", "recommended_priority", "reason"],
            "additionalProperties": False,
        },
        task_name="assess_blocker_promotion",
    )


def request_blocker_promotion_assessment(
    *,
    backend: StructuredBackend,
    graph: ProofGraph,
    blocker_node_id: str,
) -> BlockerPromotionAssessment:
    response = run_structured_with_progress(
        backend,
        build_blocker_promotion_assessment_request(graph=graph, blocker_node_id=blocker_node_id),
    )
    payload = response.payload
    recommended_priority = payload.get("recommended_priority")
    if recommended_priority is not None:
        recommended_priority = int(recommended_priority)
    return BlockerPromotionAssessment(
        promote_node=bool(payload["promote_node"]),
        recommended_priority=recommended_priority,
        reason=str(payload["reason"]).strip(),
    )


def request_coverage_expansion_assessment(
    *,
    backend: StructuredBackend,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
) -> CombinedFormalizationAssessment:
    return request_combined_verification_assessment(
        backend=backend,
        graph=graph,
        node_id=node_id,
        artifact=artifact,
    )


def build_coverage_expansion_assessment_request(
    *,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
) -> StructuredBackendRequest:
    return build_combined_verification_assessment_request(
        graph=graph,
        node_id=node_id,
        artifact=artifact,
    )


def build_repair_assessment_request(
    *,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
    failure_text: str,
) -> StructuredBackendRequest:
    node = next(node for node in graph.nodes if node.id == node_id)
    local_context = build_local_proof_context(graph, node_id)
    prompt = "\n\n".join(
        [
            f"Theorem title: {graph.theorem_title}",
            (
                "Target node:\n"
                f"- id: {node.id}\n"
                f"- title: {node.title}\n"
                f"- informal statement: {node.informal_statement}\n"
                f"- informal proof text: {node.informal_proof_text}"
            ),
            (
            "Current Lean theorem:\n"
                f"- theorem name: {artifact.lean_theorem_name}\n"
                f"- Lean statement: {artifact.lean_statement}"
            ),
            "Local proof neighborhood:",
            format_local_proof_context(local_context),
            (
                "Prior heuristic faithfulness assessment:\n"
                f"- classification: {artifact.faithfulness_classification}\n"
                f"- notes: {artifact.faithfulness_notes or '(none)'}"
            ),
            (
                "Failure text:\n"
                f"{failure_text}\n\n"
                "Classify the most specific next repair step. Prefer the narrowest category that actually fits the "
                "failure. If the theorem is in the wrong mathematical setting, use setting_fix. If it proves the wrong "
                "logical claim, use theorem_shape_fix. If Lean engineering is the main issue, use lean_packaging_fix. "
                "If the theorem shape is correct but the proof approach is brittle, use proof_strategy_fix. "
                "If the target should be smaller, use try_smaller_sublemma only for a real smaller honest local core, "
                "not a bookkeeping identity. If a broader concrete core should be tried, use try_larger_core only in a "
                "later bonus pass when the current theorem is already a faithful core."
            ),
            (
                "When a faithfulness guard rejection mentions a different mathematical universe, a finite-dimensional "
                "analogue, or an arbitrary type/measurable-space proxy, prefer setting_fix over theorem_shape_fix. "
                "When the theorem already has the right setting and only the proof script is failing, prefer "
                "proof_strategy_fix or lean_packaging_fix rather than theorem_shape_fix. If the failure is a Lean "
                "binder-name or Unicode syntax issue, treat it as lean_packaging_fix and keep the theorem family fixed. "
                "Use a plain ASCII replacement such as `lambda1` instead of a Unicode binder like `λ₁`."
            ),
            "Return JSON with keys repair_category and repair_note.",
        ]
    )
    return StructuredBackendRequest(
        prompt=prompt,
        system_prompt=(
            "You are a conservative Lean repair reviewer. Return only JSON matching the schema."
        ),
        json_schema={
            "type": "object",
            "properties": {
                "repair_category": {
                    "type": "string",
                    "enum": [category.value for category in RepairCategory],
                },
                "repair_note": {"type": "string", "minLength": 1},
            },
            "required": ["repair_category", "repair_note"],
            "additionalProperties": False,
        },
        task_name="assess_repair",
    )


def request_repair_assessment(
    *,
    backend: StructuredBackend,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
    failure_text: str,
) -> RepairAssessment:
    response = run_structured_with_progress(
        backend,
        build_repair_assessment_request(
            graph=graph,
            node_id=node_id,
            artifact=artifact,
            failure_text=failure_text,
        ),
    )
    payload = response.payload
    return RepairAssessment(
        category=RepairCategory(str(payload["repair_category"]).strip()),
        note=str(payload["repair_note"]).strip(),
    )


def build_abstraction_review_request(
    *,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
    failure_text: str,
) -> StructuredBackendRequest:
    node = next(node for node in graph.nodes if node.id == node_id)
    local_context = build_local_proof_context(graph=graph, node_id=node_id)
    prompt = "\n\n".join(
        [
            (
                "Target node:\n"
                f"- id: {node.id}\n"
                f"- title: {node.title}\n"
                f"- informal statement: {node.informal_statement}\n"
                f"- informal proof text: {node.informal_proof_text}"
            ),
            (
                "Current Lean theorem under review:\n"
                f"- theorem name: {artifact.lean_theorem_name}\n"
                f"- Lean statement: {artifact.lean_statement}"
            ),
            "Local proof neighborhood:",
            format_local_proof_context(local_context),
            (
                "Prior heuristic faithfulness assessment:\n"
                f"- classification: {artifact.faithfulness_classification}\n"
                f"- notes: {artifact.faithfulness_notes or '(none)'}"
            ),
            (
                "Failure text:\n"
                f"{failure_text}\n\n"
                "The heuristic faithfulness guard has repeatedly rejected this theorem for over-abstraction, "
                "especially because it introduces `Type*`-style parameters or a more generic ambient setting."
            ),
            (
                "Answer the narrow question: is this abstraction actually the canonical Lean/Mathlib encoding of "
                "the same local claim, or is it a real drift away from the intended benchmark node?"
            ),
            (
                "Choose exactly one category:\n"
                "- canonical_encoding: the type-parametric or abstract-looking statement is still the right theorem "
                "family and is how this local claim should naturally be represented in Lean.\n"
                "- real_drift: the theorem has genuinely shifted away from the intended node into a broader or "
                "different mathematical claim.\n"
                "- uncertain: the theorem is borderline and should not automatically bypass the heuristic guard."
            ),
            (
                "Be conservative. Do not choose canonical_encoding merely because Lean often uses structures over "
                "`Type*`; only choose it when the theorem still matches the node's mathematical burden and local role."
            ),
            "Return JSON with keys abstraction_category and abstraction_note.",
        ]
    )
    return StructuredBackendRequest(
        prompt=prompt,
        system_prompt=(
            "You are a conservative reviewer of Lean theorem encodings. Return only JSON matching the schema."
        ),
        json_schema={
            "type": "object",
            "properties": {
                "abstraction_category": {
                    "type": "string",
                    "enum": [category.value for category in AbstractionReviewCategory],
                },
                "abstraction_note": {"type": "string", "minLength": 1},
            },
            "required": ["abstraction_category", "abstraction_note"],
            "additionalProperties": False,
        },
        task_name="assess_abstraction_review",
    )


def request_abstraction_review_assessment(
    *,
    backend: StructuredBackend,
    graph: ProofGraph,
    node_id: str,
    artifact: FormalArtifact,
    failure_text: str,
) -> AbstractionReviewAssessment:
    response = run_structured_with_progress(
        backend,
        build_abstraction_review_request(
            graph=graph,
            node_id=node_id,
            artifact=artifact,
            failure_text=failure_text,
        ),
    )
    payload = response.payload
    return AbstractionReviewAssessment(
        category=AbstractionReviewCategory(str(payload["abstraction_category"]).strip()),
        note=str(payload["abstraction_note"]).strip(),
    )


def classify_heuristic_repair_assessment(
    *,
    previous_result: VerificationResult,
    extra_guidance: str | None = None,
) -> RepairAssessment:
    """Classify a retry without consulting a planning backend."""

    failure_text = "\n".join(part for part in [previous_result.stderr, previous_result.stdout] if part).lower()
    guidance_text = (extra_guidance or "").lower()
    text = failure_text
    if previous_result.command == "faithfulness_guard" or any(
        marker in failure_text for marker in FAITHFULNESS_GUARD_FAILURE_MARKERS
    ):
        text = "\n".join(part for part in [failure_text, guidance_text] if part)
    is_faithfulness_guard_failure = (
        previous_result.command == "faithfulness_guard"
        or any(marker in text for marker in FAITHFULNESS_GUARD_FAILURE_MARKERS)
    )

    if is_faithfulness_guard_failure and any(marker in text for marker in SETTING_FAILURE_MARKERS):
        return RepairAssessment(
            category=RepairCategory.SETTING_FIX,
            note=(
                "The current attempt appears to have shifted into a different mathematical setting. "
                "Keep the same ambient universe, dimension profile, and proof role."
            ),
        )

    if any(marker in text for marker in LEAN_PACKAGING_FAILURE_MARKERS):
        return RepairAssessment(
            category=RepairCategory.LEAN_PACKAGING_FIX,
            note=(
                "Fix the Lean packaging or syntax first: check imports, namespaces, typeclass instances, "
                "and ASCII-safe identifiers before changing the theorem shape."
            ),
        )

    if "type mismatch" in text:
        if any(marker in text for marker in SETTING_FAILURE_MARKERS):
            return RepairAssessment(
                category=RepairCategory.SETTING_FIX,
                note=(
                    "The type mismatch suggests the theorem moved into the wrong mathematical setting. "
                    "Keep the same space, operators, and ambient structure."
                ),
            )
        if any(marker in text for marker in THEOREM_SHAPE_FAILURE_MARKERS):
            return RepairAssessment(
                category=RepairCategory.THEOREM_SHAPE_FIX,
                note=(
                    "The mismatch suggests the theorem statement no longer matches the intended logical claim. "
                    "Preserve the original claim more literally."
                ),
            )
        return RepairAssessment(
            category=RepairCategory.THEOREM_SHAPE_FIX,
            note=(
                "The compiler mismatch suggests the theorem statement does not yet line up with the intended "
                "target shape. Preserve the original claim more literally."
            ),
        )

    if is_faithfulness_guard_failure:
        if any(marker in text for marker in SMALLER_SUBLEMMA_FAILURE_MARKERS):
            return RepairAssessment(
                category=RepairCategory.TRY_SMALLER_SUBLEMMA,
                note=(
                    "The faithfulness check suggests the current target is still too broad. "
                    "Carve out a smaller honest local core in the same setting."
                ),
            )
        if any(marker in text for marker in THEOREM_SHAPE_FAILURE_MARKERS):
            return RepairAssessment(
                category=RepairCategory.THEOREM_SHAPE_FIX,
                note=(
                    "The attempt drifted away from the intended theorem shape. Re-center on the node's concrete "
                    "statement and proof role."
                ),
            )
        return RepairAssessment(
            category=RepairCategory.THEOREM_SHAPE_FIX,
            note=(
                "The faithfulness guard rejected the theorem shape. Re-center on the node's concrete statement "
                "and proof role, and keep the same mathematical universe."
            ),
        )

    if any(marker in text for marker in PROOF_STRATEGY_FAILURE_MARKERS):
        return RepairAssessment(
            category=RepairCategory.PROOF_STRATEGY_FIX,
            note=(
                "The statement may be close enough, but the proof strategy needs to be simplified or redirected. "
                "Try a more direct lemma chain or a smaller local argument."
            ),
        )

    if any(marker in text for marker in SMALLER_SUBLEMMA_FAILURE_MARKERS):
        return RepairAssessment(
            category=RepairCategory.TRY_SMALLER_SUBLEMMA,
            note=(
                "The current theorem seems larger than the workable local core. Extract a smaller honest sublemma "
                "in the same mathematical setting."
            ),
        )

    if any(marker in text for marker in THEOREM_SHAPE_FAILURE_MARKERS):
        return RepairAssessment(
            category=RepairCategory.THEOREM_SHAPE_FIX,
            note=(
                "The attempt drifted away from the intended theorem shape. Re-center on the node's concrete "
                "statement and proof role."
            ),
        )

    return RepairAssessment(
        category=RepairCategory.PROOF_STRATEGY_FIX,
        note=(
            "Keep the theorem close to the target node and simplify the proof path. If needed, isolate a smaller "
            "but still honest local step."
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
            coverage_score=0,
        )

    borderline_signals = _collect_borderline_faithfulness_signals(node, artifact)
    coverage_score = _coverage_match_score(node, artifact)

    if borderline_signals:
        message = " ".join(
            [
                "Borderline heuristic signal only; let the planning backend make the final faithfulness call.",
                *borderline_signals,
            ]
        )
        if _looks_like_concrete_sublemma(node, artifact) or _looks_undercovered_for_node_complexity(
            node=node,
            artifact=artifact,
            coverage_score=coverage_score,
        ):
            return FaithfulnessAssessment(
                classification=FaithfulnessClassification.CONCRETE_SUBLEMMA,
                message=(
                    message
                    + " Accepted locally as a narrower concrete core in the same setting pending planner confirmation."
                ),
                coverage_score=coverage_score,
            )
        return FaithfulnessAssessment(
            classification=FaithfulnessClassification.CONCRETE_SUBLEMMA,
            message=message,
            coverage_score=coverage_score,
        )

    if _looks_like_concrete_sublemma(node, artifact) or _looks_undercovered_for_node_complexity(
        node=node,
        artifact=artifact,
        coverage_score=coverage_score,
    ):
        return FaithfulnessAssessment(
            classification=FaithfulnessClassification.CONCRETE_SUBLEMMA,
            message=(
                "Accepted as a narrower concrete local core in the same ambient setting; "
                "it should support the parent node rather than count as full-node certification."
            ),
            coverage_score=coverage_score,
        )

    return FaithfulnessAssessment(
        classification=FaithfulnessClassification.FULL_NODE,
        coverage_score=coverage_score,
    )


def _collect_over_abstract_issues(node, artifact: FormalArtifact) -> list[str]:
    issues: list[str] = []
    lean_text = f"{artifact.lean_statement}\n{artifact.lean_code}"
    node_text = " ".join([node.title, node.informal_statement, node.informal_proof_text]).lower()
    statement = artifact.lean_statement

    if "Type*" in lean_text or re.search(r"\bType u\b|\bType v\b", lean_text):
        issues.append(
            "Avoid introducing arbitrary `Type*` parameters when the node describes a concrete local claim."
        )

    if _has_multiple_unrelated_function_families(statement, node_text):
        issues.append(
            "Avoid replacing the node with unrelated families of functions or indexed maps absent from the original claim."
        )

    return issues


def _collect_borderline_faithfulness_signals(node, artifact: FormalArtifact) -> list[str]:
    signals: list[str] = []
    lean_text = f"{artifact.lean_statement}\n{artifact.lean_code}"
    node_text = " ".join([node.title, node.informal_statement, node.informal_proof_text]).lower()
    statement = artifact.lean_statement

    if (
        ("[InnerProductSpace" in lean_text or "[NormedAddCommGroup" in lean_text)
        and not any(marker in node_text for marker in ("inner product", "hilbert", "normed"))
    ):
        signals.append(
            "Possible normed/inner-product-space abstraction; planner should confirm whether the theorem is still in the right concrete setting."
        )

    if _node_does_not_invite_measure_abstraction(node_text) and _looks_like_arbitrary_measure_abstraction(statement):
        signals.append(
            "Possible measure-space abstraction; planner should confirm whether this is still the intended local setting."
        )

    if _looks_like_dimension_downgrade(node_text=node_text, lean_text=lean_text):
        signals.append(
            "Possible dimension or universe downgrade; planner should confirm whether this is a faithful core or merely a simpler analogue."
        )

    return signals


def _looks_like_dimension_downgrade(*, node_text: str, lean_text: str) -> bool:
    node_hits = sum(1 for marker in DIMENSION_DOWNGRADE_NODE_MARKERS if marker in node_text)
    if node_hits < 2:
        return False

    lean_text_lower = lean_text.lower()
    if any(marker.lower() in lean_text_lower for marker in DIMENSION_DOWNGRADE_LEAN_MARKERS):
        return True

    return bool(re.search(r"\bFin\s+[A-Za-z_][A-Za-z0-9_']*\b", lean_text))


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


GENERIC_STEP_WORDS = (
    "define",
    "set",
    "write",
    "rewrite",
    "expand",
    "compute",
    "differentiate",
    "derive",
    "show",
    "prove",
    "deduce",
    "conclude",
    "substitute",
    "specialize",
    "apply",
    "use",
    "evaluate",
    "normalize",
    "reduce",
    "combine",
    "test",
    "split",
    "identify",
    "construct",
)

STOPWORDS = {
    "the",
    "and",
    "if",
    "by",
    "for",
    "with",
    "that",
    "this",
    "from",
    "then",
    "have",
    "using",
    "gives",
    "gives",
    "hence",
    "thus",
    "therefore",
    "over",
    "into",
    "onto",
    "when",
    "where",
    "under",
    "after",
    "before",
    "which",
    "because",
    "claim",
    "node",
    "proof",
    "local",
    "step",
    "same",
    "showing",
    "proving",
}


def _looks_undercovered_for_node_complexity(
    *,
    node,
    artifact: FormalArtifact,
    coverage_score: int,
) -> bool:
    node_text = " ".join([node.title, node.informal_statement, node.informal_proof_text])
    theorem_text = f"{artifact.lean_statement}\n{artifact.lean_code}"
    node_complexity = _node_complexity_score(node_text)
    theorem_relation_count = _relation_count(theorem_text)
    node_relation_count = _relation_count(node_text)
    node_math_span_count = _math_span_count(node_text)
    overlap = _meaningful_token_overlap(node_text.lower(), theorem_text.lower())
    theorem_step_count = _node_complexity_score(theorem_text)
    theorem_word_count = len(re.findall(r"[A-Za-z_][A-Za-z0-9_']*", theorem_text))
    node_word_count = len(re.findall(r"[A-Za-z_][A-Za-z0-9_']*", node_text))
    missing_keywords = _missing_node_keywords(node_text.lower(), theorem_text.lower())
    omitted_named_ingredients = _omitted_named_ingredients(node_text.lower(), theorem_text.lower())
    missing_short_symbols = _missing_short_symbols(node_text.lower(), theorem_text.lower())

    if (
        node_complexity >= 5
        and coverage_score >= 4
        and theorem_relation_count >= max(1, node_relation_count - 1)
        and missing_short_symbols == 0
    ):
        return False

    if node_complexity >= 6 and theorem_relation_count <= 1:
        return True
    if node_relation_count >= 3 and theorem_relation_count <= 1:
        return True
    if node_math_span_count >= 3 and coverage_score <= 2:
        return True
    if node_complexity >= 4 and coverage_score <= 1:
        return True
    if node_complexity >= 8 and overlap <= 3 and theorem_step_count <= 3:
        return True
    if node_word_count >= 20 and theorem_word_count * 2 < node_word_count and coverage_score <= 3:
        return True
    if node_complexity >= 5 and missing_keywords >= 3:
        return True
    if node_relation_count >= 2 and node_complexity >= 5 and theorem_relation_count <= 3 and missing_keywords >= 2:
        return True
    if node_complexity >= 5 and omitted_named_ingredients >= 2 and coverage_score <= 4:
        return True
    if omitted_named_ingredients >= 1 and node_complexity >= 5 and theorem_step_count <= 2:
        return True
    return False


def _coverage_match_score(node, artifact: FormalArtifact) -> int:
    node_text = " ".join([node.title, node.informal_statement, node.informal_proof_text]).lower()
    theorem_text = f"{artifact.lean_statement}\n{artifact.lean_code}".lower()
    overlap = _meaningful_token_overlap(node_text, theorem_text)
    theorem_relation_count = _relation_count(theorem_text)
    theorem_math_span_count = _math_span_count(artifact.lean_statement)
    short_symbol_overlap = _short_symbol_overlap(node_text, theorem_text)

    score = 0
    if overlap >= 2:
        score += 1
    if overlap >= 4:
        score += 1
    if overlap >= 6:
        score += 1
    if theorem_relation_count >= 2:
        score += 1
    if theorem_relation_count >= 3:
        score += 1
    if theorem_math_span_count >= 1:
        score += 1
    if short_symbol_overlap >= 2:
        score += 1
    if short_symbol_overlap >= 3:
        score += 1
    return score


def _node_complexity_score(text: str) -> int:
    lowered = text.lower()
    step_word_hits = sum(lowered.count(word) for word in GENERIC_STEP_WORDS)
    return step_word_hits + _relation_count(text) + _math_span_count(text)


def _relation_count(text: str) -> int:
    return len(re.findall(r"\\le|\\ge|≤|≥|<=|>=|=|<|>", text))


def _math_span_count(text: str) -> int:
    return len(re.findall(r"\\\(|\\\[", text))


def _meaningful_token_overlap(node_text: str, theorem_text: str) -> int:
    node_tokens = _meaningful_tokens(node_text)
    theorem_tokens = _meaningful_tokens(theorem_text)
    return len(node_tokens & theorem_tokens)


def _missing_node_keywords(node_text: str, theorem_text: str) -> int:
    return len(_meaningful_tokens(node_text) - _meaningful_tokens(theorem_text))


def _missing_short_symbols(node_text: str, theorem_text: str) -> int:
    return len(_short_symbols(node_text) - _short_symbols(theorem_text))


def _omitted_named_ingredients(node_text: str, theorem_text: str) -> int:
    node_tokens = _meaningful_tokens(node_text)
    theorem_tokens = _meaningful_tokens(theorem_text)
    named = {
        token
        for token in node_tokens
        if token not in INFERENTIAL_WORDS and not _looks_generic_math_word(token)
    }
    return len(named - theorem_tokens)


def _short_symbol_overlap(node_text: str, theorem_text: str) -> int:
    return len(_short_symbols(node_text) & _short_symbols(theorem_text))


def _meaningful_tokens(text: str) -> set[str]:
    normalized = text.lower().replace("_", " ")
    tokens = {
        token
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_']*", normalized)
        if len(token) >= 3 and token not in STOPWORDS and token not in GENERIC_STEP_WORDS
    }
    return tokens


def _short_symbols(text: str) -> set[str]:
    normalized = text.lower().replace("_", " ")
    return {
        token
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_']*", normalized)
        if len(token) <= 2
        and token not in STOPWORDS
        and token not in INFERENTIAL_WORDS
        and not token.startswith("h")
    }


INFERENTIAL_WORDS = {
    "proof",
    "step",
    "claim",
    "theorem",
    "local",
    "core",
    "result",
    "show",
    "prove",
    "deduce",
    "conclude",
    "define",
    "compute",
    "rewrite",
    "expand",
    "apply",
    "use",
    "then",
    "thus",
    "hence",
    "therefore",
}


def _looks_generic_math_word(token: str) -> bool:
    generic_prefixes = (
        "nonneg",
        "nonnegative",
        "convex",
        "minimum",
        "minimizer",
        "global",
        "local",
        "equal",
        "equality",
        "identity",
        "equation",
        "lemma",
        "theorem",
        "proof",
        "function",
        "derivative",
        "second",
        "first",
        "bounded",
        "continu",
        "coerc",
        "estimate",
        "inequal",
        "positiv",
        "negative",
        "value",
    )
    return token.startswith(generic_prefixes)


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
