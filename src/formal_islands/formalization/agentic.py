"""One-shot agentic worker for local Lean formalization."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from formal_islands.backends import (
    AgenticStructuredBackend,
    BackendOutputError,
    StructuredBackendRequest,
)
from formal_islands.formalization.pipeline import (
    FormalizationFaithfulnessError,
    enforce_formalization_faithfulness,
    build_node_coverage_sketch,
    build_local_proof_context,
    build_verified_direct_child_context,
    format_local_proof_context,
    format_verified_direct_child_context,
)
from formal_islands.formalization.schemas import AgenticFormalizationResult
from formal_islands.models import FormalArtifact, ProofGraph, VerificationResult


AGENTIC_FORMALIZATION_SYSTEM_PROMPT = (
    "You are a focused Lean 4 formalization worker operating inside a local Mathlib project. "
    "You may edit files and run local commands within this one session. "
    "Begin with a brief planning pass (try keeping the plan file under 15 lines), then move quickly to writing Lean code. "
    "Work on exactly one scratch file, keep the formalization local and faithful to the target node, "
    "prefer the most concrete faithful theorem you can manage, "
    "and stop when the scratch file is in its best current state. "
    "Return only JSON matching the supplied schema."
)

AGENTIC_WORKER_PLACEHOLDER = "-- agentic formalization worker scratch file\n"


def agentic_worker_plan_path(scratch_file_path: Path) -> Path:
    """Return the sibling markdown plan file used by the agentic worker."""

    return scratch_file_path.with_name(f"{scratch_file_path.stem}_plan.md")


def build_agentic_formalization_request(
    *,
    graph: ProofGraph,
    node_id: str,
    workspace_root: Path,
    scratch_file_path: Path,
    faithfulness_feedback: str | None = None,
    previous_lean_code: str | None = None,
) -> StructuredBackendRequest:
    node = next((candidate for candidate in graph.nodes if candidate.id == node_id), None)
    if node is None:
        raise ValueError(f"node '{node_id}' was not found in the graph")
    if node.status != "candidate_formal":
        raise ValueError(f"node '{node_id}' must be candidate_formal before formalization")

    plan_file_path = agentic_worker_plan_path(scratch_file_path).resolve()

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
            "Mathlib lives under the workspace's `.lake/packages/mathlib/Mathlib` directory. "
            "Do not assume a `lean_project/mathlib/Mathlib` path. If you need to inspect Mathlib source, "
            "use the workspace root and the `.lake/packages` layout that actually exists on disk."
        ),
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
            "These verified children are already available. Your theorem should capture only the remaining "
            "parent-level delta, not a restatement of any verified child or a near-equivalent corollary."
        ),
        (
            "Dependency direction note: the verified child lemmas are outgoing dependencies of the target node. "
            "Treat them as already established support, not as parents or as claims that depend on the target."
        ),
        f"Lean workspace root: {workspace_root}",
        f"Scratch file to create and edit: {scratch_file_path}",
        f"Plan markdown file to create and maintain: {plan_file_path}",
        (
            "Operate only inside the Lean workspace above, and only edit the specified scratch file. "
            "Do not modify other repository files, except for the required plan markdown file above."
        ),
        (
            "Within this single run, you may inspect local files, write the scratch file, run "
            "`lake env lean <scratch_file_path>` from the Lean workspace root, read compiler feedback, "
            "and revise the same file until it succeeds or you run out of time."
        ),
        (
            "Start with a lightweight planning pass before serious Lean formalization. Create the plan markdown "
            "file above first, then use it to decide the concrete theorem shape you will actually target."
        ),
        (
            "Keep the plan brief — aim for under 10 lines total. Cover only: target theorem shape, key symbols "
            "to preserve, intended proof route, and two or three likely Mathlib lemmas. Do not write a long "
            "multi-section document."
        ),
        (
            "Structure the Lean file around one designated main theorem that represents the certified result for this node. "
            "Additional lemmas are allowed, but they should be clearly subordinate helper lemmas used to prove the main theorem."
        ),
        (
            "The `lean_theorem_name` and `lean_statement` you return must correspond to that single main theorem, "
            "not to a helper lemma. If you include helper lemmas, make sure the main theorem remains the primary claim in the file."
        ),
        (
            "Default to the most literal whole-node theorem shape that directly mirrors the target node's stated "
            "mathematical claim. Treat that literal whole-node target as the real starting point, not as an optional stretch goal "
            "or a theorem you only mention briefly before falling back."
        ),
        (
            "Only fall back to a narrower concrete sublemma if your local scouting or compiler experiments show that "
            "the literal whole-node target is genuinely infeasible in the available time or Mathlib surface area, after a "
            "real attempt to prove the literal node-level statement or a very close transcription of it."
        ),
        (
            "If you do fall back, record that explicitly in the plan file: note the literal whole-node target you tried, "
            "why it looked infeasible, and why the narrower replacement still captures meaningful inferential load."
        ),
        (
            "A local `formal-islands-search` helper is available if you truly need it. Use it sparingly: "
            "at most 2 additional highly targeted searches total, preferably one exact Loogle-shaped query "
            "and one LeanSearch natural-language query. Do not do broad filesystem scouting, repeated broad "
            "`grep` sweeps, or repeated `lake env lean` compilation passes before you have committed to a theorem shape."
        ),
        (
            "If you substantially change direction during the run, preserve visible plan history by appending a "
            "new labeled section to the same markdown file instead of silently overwriting the old plan."
        ),
        (
            "Prefer narrow, specific imports that match the identifiers actually used in the theorem. "
            "Do not default to `import Mathlib` for a small local theorem when a few focused imports "
            "would do, and do not guess speculative deep module paths."
        ),
        (
            "Keep the Lean output syntactically conservative. Prefer ASCII identifiers in theorem names, "
            "binder names, and hypotheses unless a non-ASCII symbol is clearly unavoidable. Lean treats `λ` "
            "as a reserved keyword in theorem headers and binders, so do not use Unicode binder names like "
            "`λ₁`; prefer plain names such as `lambda1` or `lambda_1`. Do not invent fancy notation when "
            "ordinary Lean identifiers and explicit expressions work."
        ),
        (
            "Prefer simple theorem signatures and straightforward binder lists over elaborate notation-heavy "
            "declarations. When in doubt, choose the most boring Lean surface syntax that still states the right theorem."
        ),
        (
            "Bias strongly toward faithfulness to the target node. Reuse the node's concrete variables and "
            "hypotheses when reasonable. Do not introduce arbitrary index types, unrelated function families, "
            "or a much more generic theorem unless the node text clearly requires that abstraction."
        ),
        (
            "Preserve the ambient mathematical setting of the theorem and node. If the node is stated in a concrete "
            "setting, keep that same setting in the Lean theorem unless the node itself explicitly states a more abstract generality."
        ),
        (
            "If you simplify, simplify the local inferential step while keeping the same concrete objects and "
            "ambient setting. Prefer a concrete sublemma about the same named quantities, variables, operators, "
            "or integrals over a theorem about an arbitrary type, arbitrary measure, or unrelated families of functions."
        ),
        (
            "Use the coverage sketch to decide what the theorem is supposed to cover. If you only prove one "
            "component of the sketch, keep the plan and Lean file honest about that partial coverage rather than "
            "pretending to certify the whole node."
        ),
        (
            "If the local proof neighborhood lists verified supporting lemmas, you may rely on those statements "
            "as established facts for this job. Context-only sibling ingredients are only orientation, not "
            "assumptions."
        ),
        (
            "Do not replace a concrete statement with a generic measure-space or arbitrary ambient-type theorem "
            "unless the original node is already phrased in that abstract setting."
        ),
        (
            "Do not game the task by collapsing the node to an easy nearby side fact. If you simplify, the "
            "replacement should still carry meaningful inferential load in the parent proof."
        ),
        (
            "Before settling on a fallback theorem, spend a short but genuine attempt on the literal node-level statement or the "
            "closest direct transcription that seems syntactically realistic. Do not jump immediately to a more abstract "
            "or indirect theorem just because it is familiar, do not switch theorem family to a simpler proxy universe, "
            "and do not treat a tiny side lemma as an acceptable fallback unless it really is the best reachable core after "
            "that attempt."
        ),
        (
            "Return a JSON object with keys lean_theorem_name, lean_statement, final_file_path, and plan_file_path. "
            "The final_file_path must be exactly the scratch file path above, and the plan_file_path must be exactly "
            "the plan markdown path above."
        ),
    ]
    if faithfulness_feedback:
        prompt_parts.extend(
            [
                "Previous faithfulness failure: the prior theorem/file was too abstract. Continue from the current "
                "scratch file instead of starting over. Revise it in place to stay much closer to the target node's "
                "concrete setting.",
                faithfulness_feedback,
            ]
        )
    if previous_lean_code:
        prompt_parts.extend(
            [
                "Current scratch file to revise:",
                f"```lean\n{previous_lean_code}\n```",
            ]
        )
    prompt = "\n\n".join(prompt_parts)

    return StructuredBackendRequest(
        prompt=prompt,
        system_prompt=AGENTIC_FORMALIZATION_SYSTEM_PROMPT,
        json_schema=AgenticFormalizationResult.model_json_schema(),
        task_name="formalize_node_agentic",
        cwd=workspace_root,
    )


def request_agentic_formalization(
    *,
    backend: AgenticStructuredBackend,
    graph: ProofGraph,
    node_id: str,
    workspace_root: Path,
    scratch_file_path: Path,
    faithfulness_feedback: str | None = None,
    previous_lean_code: str | None = None,
) -> FormalArtifact:
    response = backend.run_agentic_structured(
        build_agentic_formalization_request(
            graph=graph,
            node_id=node_id,
            workspace_root=workspace_root,
            scratch_file_path=scratch_file_path,
            faithfulness_feedback=faithfulness_feedback,
            previous_lean_code=previous_lean_code,
        ),
        timeout_seconds=backend.timeout_seconds,
    )
    formalization = AgenticFormalizationResult.model_validate(response.payload)

    final_path = Path(formalization.final_file_path).resolve()
    expected_path = scratch_file_path.resolve()
    plan_path = Path(formalization.plan_file_path).resolve()
    expected_plan_path = agentic_worker_plan_path(scratch_file_path).resolve()
    if final_path != expected_path:
        raise BackendOutputError(
            "Agentic formalization returned an unexpected final file path: "
            f"{formalization.final_file_path}"
        )
    if plan_path != expected_plan_path:
        raise BackendOutputError(
            "Agentic formalization returned an unexpected plan file path: "
            f"{formalization.plan_file_path}"
        )
    if not final_path.exists():
        raise BackendOutputError(
            f"Agentic formalization did not produce the expected scratch file: {final_path}"
        )
    if not plan_path.exists():
        raise BackendOutputError(
            f"Agentic formalization did not produce the expected plan markdown file: {plan_path}"
        )

    lean_code = final_path.read_text(encoding="utf-8")
    extracted_name, extracted_statement = _extract_named_lean_theorem(
        lean_code,
        formalization.lean_theorem_name,
    )
    if extracted_name is None or extracted_statement is None:
        raise BackendOutputError(
            "Agentic formalization returned a main theorem name that does not appear in the final Lean file: "
            f"{formalization.lean_theorem_name}"
        )
    artifact = FormalArtifact(
        lean_theorem_name=extracted_name,
        lean_statement=extracted_statement,
        lean_code=lean_code,
        verification=VerificationResult(),
        attempt_history=[],
    )
    artifact = enforce_formalization_faithfulness(
        node=next(candidate for candidate in graph.nodes if candidate.id == node_id),
        artifact=artifact,
    )
    return artifact


def recover_agentic_artifact_from_scratch_file(
    *,
    graph: ProofGraph,
    node_id: str,
    scratch_file_path: Path,
    expected_theorem_name: str | None = None,
) -> FormalArtifact | None:
    resolved_path = scratch_file_path.resolve()
    if not resolved_path.exists():
        return None

    lean_code = resolved_path.read_text(encoding="utf-8")
    if lean_code == AGENTIC_WORKER_PLACEHOLDER:
        return None

    theorem_name, theorem_statement = _extract_primary_lean_theorem(
        lean_code,
        preferred_name=expected_theorem_name,
    )
    if theorem_name is None or theorem_statement is None:
        return None

    artifact = FormalArtifact(
        lean_theorem_name=theorem_name,
        lean_statement=theorem_statement,
        lean_code=lean_code,
        verification=VerificationResult(),
        attempt_history=[],
    )
    return enforce_formalization_faithfulness(
        node=next(candidate for candidate in graph.nodes if candidate.id == node_id),
        artifact=artifact,
    )


def _extract_primary_lean_theorem(
    lean_code: str,
    *,
    preferred_name: str | None = None,
) -> tuple[str | None, str | None]:
    return _select_primary_lean_theorem(lean_code, preferred_name=preferred_name)


def _extract_named_lean_theorem(
    lean_code: str,
    theorem_name: str,
) -> tuple[str | None, str | None]:
    candidate_names = {theorem_name, theorem_name.split(".")[-1]}
    for declaration in _extract_lean_declarations(lean_code):
        if declaration[1] in candidate_names:
            return declaration[1], declaration[2]
    return None, None


def _select_primary_lean_theorem(
    lean_code: str,
    *,
    preferred_name: str | None = None,
) -> tuple[str | None, str | None]:
    declarations = _extract_lean_declarations(lean_code)
    if not declarations:
        return None, None
    if preferred_name is not None:
        named = _extract_named_lean_theorem(lean_code, preferred_name)
        if named != (None, None):
            return named

    def score(declaration: tuple[str, str, str, int]) -> tuple[int, int, int]:
        _, _name, statement, ordinal = declaration
        relation_count = len(re.findall(r"\\le|\\ge|≤|≥|<=|>=|=|<|>", statement))
        binder_count = statement.count("(") + statement.count("{")
        return (relation_count + binder_count, len(statement), ordinal)

    best = max(declarations, key=score)
    return best[1], best[2]


def _extract_lean_declarations(lean_code: str) -> list[tuple[str, str, str, int]]:
    pattern = re.compile(
        r"(?ms)^\s*(theorem|lemma|example)\s+([A-Za-z0-9_'.]+)(.*?)(:=\s*by|:=|where\b)"
    )
    declarations: list[tuple[str, str, str, int]] = []
    for ordinal, match in enumerate(pattern.finditer(lean_code)):
        keyword = match.group(1)
        theorem_name = match.group(2)
        signature_tail = match.group(3).rstrip()
        statement = f"{keyword} {theorem_name}{signature_tail}".strip()
        statement = re.sub(r"\s+\n", "\n", statement)
        declarations.append((keyword, theorem_name, statement, ordinal))
    return declarations
