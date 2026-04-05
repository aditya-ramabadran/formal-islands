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
    coverage_score: int = 0


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
            coverage_score=0,
        )

    coverage_score = _coverage_match_score(node, artifact)

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
