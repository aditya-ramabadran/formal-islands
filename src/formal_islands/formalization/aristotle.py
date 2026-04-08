"""Aristotle-specific formalization helpers."""

from __future__ import annotations

import json
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from formal_islands.backends.aristotle import AristotleBackend
from formal_islands.backends.base import BackendOutputError
from formal_islands.formalization.agentic import recover_agentic_artifact_from_scratch_file
from formal_islands.formalization.pipeline import (
    build_local_proof_context,
    build_node_coverage_sketch,
    build_verified_direct_child_context,
    format_local_proof_context,
    format_verified_direct_child_context,
)
from formal_islands.models import FormalArtifact, ProofGraph
from formal_islands.progress import append_to_progress_log, progress


@dataclass(frozen=True)
class VerifiedChildSupportFile:
    """A verified direct-child artifact materialized into the Aristotle snapshot."""

    child_id: str
    child_title: str
    theorem_name: str
    lean_statement: str
    relative_path: Path
    import_path: str
    source_artifact_path: str | None
    lean_code: str


def request_aristotle_formalization(
    *,
    backend: AristotleBackend,
    graph: ProofGraph,
    node_id: str,
    workspace_root: Path,
    scratch_file_path: Path,
    faithfulness_feedback: str | None = None,
    previous_lean_code: str | None = None,
    compiler_feedback: str | None = None,
) -> FormalArtifact:
    """Submit a project snapshot to Aristotle and recover a Lean artifact from the result."""

    node = next((candidate for candidate in graph.nodes if candidate.id == node_id), None)
    if node is None:
        raise ValueError(f"node '{node_id}' was not found in the graph")
    if node.status != "candidate_formal":
        raise ValueError(f"node '{node_id}' must be candidate_formal before formalization")

    scratch_path = scratch_file_path.resolve()
    workspace_root = workspace_root.resolve()
    if not scratch_path.is_relative_to(workspace_root):
        raise ValueError("scratch_file_path must live inside the Lean workspace root")

    desired_theorem_name = _desired_aristotle_theorem_name(node_id)
    relative_scratch_path = scratch_path.relative_to(workspace_root)

    support_files = _build_verified_child_support_files(graph=graph, node_id=node_id)

    with tempfile.TemporaryDirectory(prefix="formal-islands-aristotle-") as temp_dir_name:
        snapshot_root = Path(temp_dir_name)
        shutil.copytree(
            workspace_root,
            snapshot_root,
            dirs_exist_ok=True,
            ignore=_aristotle_snapshot_ignore,
        )
        _materialize_verified_child_support_files(
            snapshot_root=snapshot_root,
            support_files=support_files,
        )

        snapshot_scratch_path = snapshot_root / relative_scratch_path
        snapshot_scratch_path.parent.mkdir(parents=True, exist_ok=True)
        if previous_lean_code:
            snapshot_scratch_path.write_text(previous_lean_code, encoding="utf-8")
        else:
            snapshot_scratch_path.write_text(
                _render_aristotle_scratch_header(
                    graph=graph,
                    node=node,
                    desired_theorem_name=desired_theorem_name,
                    relative_scratch_path=relative_scratch_path,
                    verified_child_support_files=support_files,
                ),
                encoding="utf-8",
            )

        prompt = build_aristotle_formalization_prompt(
            graph=graph,
            node=node,
            desired_theorem_name=desired_theorem_name,
            relative_scratch_path=relative_scratch_path,
            verified_child_support_files=support_files,
            faithfulness_feedback=faithfulness_feedback,
            previous_lean_code=previous_lean_code,
            compiler_feedback=compiler_feedback,
        )
        progress(f"Aristotle submitting node {node_id}")
        run = backend.submit_project(
            prompt=prompt,
            project_dir=snapshot_root,
            task_name=f"formalize_node_aristotle_{_sanitize_file_stem(node_id)}",
        )
        progress(
            f"Aristotle project {run.project_id} for node {node_id} completed with status {run.status}; "
            "recovering result artifact"
        )

        extracted_root = Path(tempfile.mkdtemp(prefix="formal-islands-aristotle-result-"))
        try:
            progress(
                f"Aristotle project {run.project_id} for node {node_id}: extracting result tarball "
                f"to {extracted_root}"
            )
            _extract_tarball(run.result_tar_path, extracted_root)
            progress(
                f"Aristotle project {run.project_id} for node {node_id}: extraction complete; "
                "appending summary files"
            )
            _append_aristotle_summary_files(extracted_root)
            progress(
                f"Aristotle project {run.project_id} for node {node_id}: searching extracted tree "
                "for a Lean file containing the target theorem"
            )
            result_lean_path = _find_result_lean_file(
                extracted_root=extracted_root,
                preferred_relative_path=relative_scratch_path,
                desired_theorem_name=desired_theorem_name,
            )
            if result_lean_path is None:
                raise BackendOutputError(
                    "Aristotle did not return a Lean file containing the target theorem."
                )

            scratch_path.parent.mkdir(parents=True, exist_ok=True)
            progress(
                f"Aristotle project {run.project_id} for node {node_id}: copying result Lean file "
                f"from {result_lean_path} to {scratch_path}"
            )
            shutil.copy2(result_lean_path, scratch_path)

            progress(
                f"Aristotle project {run.project_id} for node {node_id}: recovering formal artifact "
                "from copied Lean file"
            )
            artifact = recover_agentic_artifact_from_scratch_file(
                graph=graph,
                node_id=node_id,
                scratch_file_path=scratch_path,
                expected_theorem_name=desired_theorem_name,
            )
            if artifact is None:
                raise BackendOutputError(
                    "Aristotle returned a Lean file, but no theorem could be recovered from it."
                )
            progress(
                f"Aristotle project {run.project_id} for node {node_id}: recovered theorem "
                f"{artifact.lean_theorem_name}; finalizing"
            )
            progress(f"Aristotle completed node {node_id} with status {run.status}")
            return artifact
        finally:
            shutil.rmtree(extracted_root, ignore_errors=True)
            if backend.log_dir is None:
                try:
                    run.result_tar_path.unlink(missing_ok=True)
                except Exception:
                    pass


def build_aristotle_formalization_prompt(
    *,
    graph: ProofGraph,
    node,
    desired_theorem_name: str,
    relative_scratch_path: Path,
    verified_child_support_files: list[VerifiedChildSupportFile] | None = None,
    faithfulness_feedback: str | None = None,
    previous_lean_code: str | None = None,
    compiler_feedback: str | None = None,
) -> str:
    sketch = build_node_coverage_sketch(node)
    local_context = build_local_proof_context(graph, node.id)
    direct_child_context = build_verified_direct_child_context(graph, node.id)
    children = {edge.target_id for edge in graph.edges if edge.source_id == node.id}
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
    promoted_parent_attempt = _is_promoted_parent_attempt(node)
    prompt_parts = [
        f"Theorem title: {graph.theorem_title}",
        (
            "Ambient theorem statement (context only; do not formalize this whole statement unless it exactly "
            "matches the target node):\n"
            f"{graph.theorem_statement}"
        ),
        (
            "Primary formalization target: the target node's informal statement and informal proof text below. "
            "Do not try to prove the ambient theorem statement itself unless it is identical to the target node."
        ),
        f"Target theorem name: {desired_theorem_name}",
        f"Scratch file to rewrite: {relative_scratch_path}",
        "Target node:",
        _format_node_context(
            node_id=node.id,
            title=node.title,
            informal_statement=node.informal_statement,
            informal_proof_text=node.informal_proof_text,
            formalization_priority=node.formalization_priority,
            formalization_rationale=node.formalization_rationale,
        ),
        "Coverage sketch:",
        _format_coverage_sketch(sketch),
        "Local proof neighborhood:",
        format_local_proof_context(local_context),
        "Verified direct child lemmas:",
        json.dumps(child_summaries, indent=2) if child_summaries else "[]",
        format_verified_direct_child_context(direct_child_context),
        (
            "These verified children are already available. The theorem should prove only the remaining "
            "parent-level delta, not a restatement of any verified child or a close corollary that duplicates it."
        ),
        (
            "If you use the verified child results, treat them as helper lemmas for the current node. "
            "Your main theorem must be a new theorem for the current parent target, not just a resubmission "
            "of one child theorem under a new filename."
        ),
        (
            "Prefer a self-contained final scratch file. Treat the materialized support files as reference material "
            "to inspect, then copy or adapt the minimal helper lemmas you need into the scratch file itself. "
            "Do not make the final artifact depend on cross-file generated-support imports."
        ),
        (
            "Dependency direction note: the verified child lemmas are outgoing dependencies of the target node. "
            "Treat them as already established support, not as parents or as claims that depend on the target."
        ),
        (
            "Rewrite the designated scratch file into a Lean 4 theorem and proof that formalize the node. "
            "Use the same concrete setting as the node whenever possible, and keep the theorem faithful to the "
            "local inferential role described above."
        ),
        (
            "Do not convert a difficult intermediate identity, estimate, or proof step from the informal proof "
            "into a new hypothesis unless that step is already stated in the node itself. If the informal proof "
            "derives a fact, treat it as something to prove, not something to assume."
        ),
        (
            "Do not modify unrelated files. Keep the file self-contained and include necessary imports, but prefer specific imports to broad ones like `import Mathlib`."
            "Do not make a major shrink in the mathematical setting, dimension, ambient structure, or variable "
            "scope just to make the theorem easier."
        ),
        (
            "Keep theorem headers and binders ASCII-safe. Lean treats `λ` as a reserved keyword in declarations, "
            "so do not use Unicode binder names like `λ₁`; use plain names such as `lambda1` or `lambda_1` "
            "instead. Preserve the mathematical notation in comments and statements, but keep Lean identifiers "
            "plain when possible."
        ),
        (
            "If the full node is too hard, prefer a smaller but still genuinely nontrivial concrete theorem in the "
            "same setting. The fallback must still carry meaningful inferential load from the parent proof, and it "
            "must not switch theorem family to a simpler proxy or lower-dimensional analogue."
        ),
        (
            "If the local proof neighborhood lists verified supporting lemmas, you may rely on their statements "
            "as established facts for this job. Context-only sibling ingredients are only orientation, not "
            "assumptions."
        ),
        (
            "Avoid sorrys, avoid arbitrary abstraction, and avoid replacing the node with a weak side fact that carries "
            "little inferential load."
        ),
        (
            "If you need a fallback, make it explicit in the Lean file and keep it as close as possible to the original "
            "node. If you cannot keep the fallback meaningfully nontrivial, fail rather than returning a trivial or "
            "over-shrunk theorem."
        ),
    ]
    if verified_child_support_files:
        prompt_parts.extend(
            [
                "Materialized verified support files already placed in this Aristotle snapshot:",
                json.dumps(
                    [
                        {
                            "child_id": support.child_id,
                            "child_title": support.child_title,
                            "theorem_name": support.theorem_name,
                            "lean_statement": support.lean_statement,
                            "snapshot_file": str(support.relative_path),
                            "import_path": support.import_path,
                            "source_artifact_path": support.source_artifact_path,
                        }
                        for support in verified_child_support_files
                    ],
                    indent=2,
                ),
                (
                    "Use these support files primarily as reference material to inspect and copy from. "
                    "The designated main theorem must still be "
                    f"`{desired_theorem_name}` and must certify the current node's parent-level delta."
                ),
                (
                    "Prefer copying or adapting the minimal helper material you need into the scratch file so that the "
                    "final certified artifact stays self-contained. Do not import a generated support file in the final "
                    "artifact; copy or adapt what you need into the scratch file."
                ),
            ]
        )
    if promoted_parent_attempt and verified_child_support_files:
        prompt_parts.extend(
            [
                "This is a promoted parent-assembly attempt.",
                (
                    "Start from the verified support theorem(s) above, reuse them aggressively as helpers, and prove the "
                    "missing parent-level assembly or enlargement step. Do not submit a file whose only substantial theorem "
                    "is one of the support theorems unchanged."
                ),
                (
                    "Default to a self-contained parent file that copies or adapts helper lemmas from the support material. "
                    "Do not rely on `FormalIslands.Generated.Support.*` imports in the final artifact."
                ),
            ]
        )
    if faithfulness_feedback:
        prompt_parts.extend(
            [
                "Faithfulness feedback from a previous attempt:",
                faithfulness_feedback,
            ]
        )
    if compiler_feedback:
        prompt_parts.extend(
            [
                "Compiler feedback from the previous attempt:",
                compiler_feedback,
            ]
        )
    if previous_lean_code:
        prompt_parts.extend(
            [
                "Current scratch file to revise:",
                f"```lean\n{previous_lean_code}\n```",
            ]
        )

    prompt_parts.append(
        (
            "Return the completed Lean file in the submitted project snapshot. The file should compile locally "
            "when verified with `lake env lean`."
        )
    )
    return "\n\n".join(prompt_parts)


def _render_aristotle_scratch_header(
    *,
    graph: ProofGraph,
    node,
    desired_theorem_name: str,
    relative_scratch_path: Path,
    verified_child_support_files: list[VerifiedChildSupportFile] | None = None,
) -> str:
    sketch = build_node_coverage_sketch(node)
    local_context = build_local_proof_context(graph, node.id)
    direct_child_context = build_verified_direct_child_context(graph, node.id)
    lines = [
            "/--",
            "Aristotle formalization target.",
            f"Target theorem name: {desired_theorem_name}",
            f"Scratch file: {relative_scratch_path}",
            "",
            f"Theorem title: {graph.theorem_title}",
            "Ambient theorem statement (context only; do not formalize this whole statement unless it exactly matches the target node):",
            graph.theorem_statement,
            "",
            "Primary formalization target:",
            "The target node's informal statement and informal proof text below.",
            "Target node:",
            _format_node_context(
                node_id=node.id,
                title=node.title,
                informal_statement=node.informal_statement,
                informal_proof_text=node.informal_proof_text,
                formalization_priority=node.formalization_priority,
                formalization_rationale=node.formalization_rationale,
            ),
            "",
            "Coverage sketch:",
            _format_coverage_sketch(sketch),
            "",
            "Local proof neighborhood:",
            format_local_proof_context(local_context),
            "",
            "Verified direct child lemmas:",
            format_verified_direct_child_context(direct_child_context),
            "",
            "These verified children are already available. The theorem should prove only the remaining parent-level delta,",
            "not a restatement of any verified child or a close corollary that duplicates it.",
            "If you use them, treat them as helper lemmas for a new theorem for the current node.",
            "Prefer a self-contained final file: inspect the support files and copy or adapt only the minimal helper material you need.",
            "Treat the support files as reference material first, not as modules that should be imported in the final artifact.",
            "",
            "Dependency direction note: the verified child lemmas are outgoing dependencies of the target node.",
            "Treat them as already established support, not as parents or as claims that depend on the target.",
            "",
            "Instructions:",
            "- Rewrite this file into a compilable Lean 4 theorem and proof for the target node above.",
            "- Keep the theorem faithful to the target node's concrete setting.",
            "- Do not promote an unproven intermediate identity or estimate to a hypothesis.",
            "- Do not make a major shrink in the mathematical setting, dimension, ambient structure, or variable scope.",
            "- Keep theorem headers and binders ASCII-safe. Lean treats `λ` as a reserved keyword in declarations, so do not use Unicode binder names like `λ₁`; use plain names such as `lambda1` or `lambda_1` instead.",
            "- Prefer the most concrete faithful theorem you can manage.",
            "- The designated main theorem in this file must be a new theorem for the current node, not just a copied child theorem.",
            "- Avoid sorrys and avoid unrelated abstraction.",
            "- Use any imports you need, but prefer specific imports to broad ones like `import Mathlib`.",
            "- If a smaller theorem is the best reachable core, it must still be genuinely nontrivial and carry meaningful inferential load.",
            "- If you cannot produce a genuinely nontrivial fallback, fail rather than returning a trivial shrink.",
    ]
    if verified_child_support_files:
        lines.extend(
            [
                "",
                "Materialized verified support files in this snapshot:",
            ]
        )
        for support in verified_child_support_files:
            lines.extend(
                [
                    f"- child id: {support.child_id}",
                    f"  theorem: {support.theorem_name}",
                    f"  statement: {support.lean_statement}",
                    f"  snapshot file: {support.relative_path}",
                    f"  import path: {support.import_path}",
                ]
            )
    if _is_promoted_parent_attempt(node) and verified_child_support_files:
        lines.extend(
            [
                "",
                "This is a promoted parent-assembly attempt.",
                "Reuse the support theorem(s) above as helpers and prove the parent-level enlargement or assembly step.",
                "Do not leave the file with only a support theorem copied unchanged.",
                "Copy or adapt the needed helper material into this scratch file instead of importing generated support modules.",
            ]
        )
    lines.append("-/")
    return "\n".join(lines)


def _build_verified_child_support_files(
    *,
    graph: ProofGraph,
    node_id: str,
) -> list[VerifiedChildSupportFile]:
    children = {edge.target_id for edge in graph.edges if edge.source_id == node_id}
    support_files: list[VerifiedChildSupportFile] = []
    for child in sorted((node for node in graph.nodes if node.id in children), key=lambda node: node.id):
        artifact = child.formal_artifact
        if child.status != "formal_verified" or artifact is None:
            continue
        module_stem = _sanitize_file_stem(child.id)
        relative_path = Path("FormalIslands") / "Generated" / "Support" / f"{module_stem}.lean"
        import_path = ".".join(relative_path.with_suffix("").parts)
        support_files.append(
            VerifiedChildSupportFile(
                child_id=child.id,
                child_title=child.title,
                theorem_name=artifact.lean_theorem_name,
                lean_statement=artifact.lean_statement,
                relative_path=relative_path,
                import_path=import_path,
                source_artifact_path=artifact.verification.artifact_path,
                lean_code=artifact.lean_code,
            )
        )
    return support_files


def _materialize_verified_child_support_files(
    *,
    snapshot_root: Path,
    support_files: list[VerifiedChildSupportFile],
) -> None:
    for support in support_files:
        destination = snapshot_root / support.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_path = Path(support.source_artifact_path).expanduser().resolve() if support.source_artifact_path else None
        if source_path is not None and source_path.is_file():
            shutil.copy2(source_path, destination)
        else:
            destination.write_text(support.lean_code, encoding="utf-8")


def _is_promoted_parent_attempt(node) -> bool:
    rationale = (node.formalization_rationale or "").lower()
    return "promoted after all direct children were verified" in rationale


def _desired_aristotle_theorem_name(node_id: str) -> str:
    return f"{_sanitize_file_stem(node_id)}_aristotle"


def _sanitize_file_stem(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_") or "aristotle_node"


def _format_node_context(
    *,
    node_id: str,
    title: str,
    informal_statement: str,
    informal_proof_text: str,
    formalization_priority: int | None,
    formalization_rationale: str | None,
) -> str:
    priority_text = str(formalization_priority) if formalization_priority is not None else "unset"
    rationale_text = formalization_rationale or "(no rationale recorded)"
    return "\n".join(
        [
            f"- id: {node_id}",
            f"- title: {title}",
            "- informal statement:",
            informal_statement,
            "- informal proof text:",
            informal_proof_text,
            f"- formalization priority: {priority_text}",
            "- formalization rationale:",
            rationale_text,
        ]
    )


def _format_coverage_sketch(sketch) -> str:
    lines = [f"- summary: {sketch.summary}", "- components:"]
    for component in sketch.components:
        lines.append(f"  - [{component.kind}] {component.text}")
    return "\n".join(lines)


def _aristotle_snapshot_ignore(directory: str, names: list[str]) -> set[str]:
    path = Path(directory)
    ignored: set[str] = set()
    for name in names:
        if name == ".lake" or name == ".DS_Store":
            ignored.add(name)
            continue
        if path.name == "FormalIslands" and name == "Generated":
            ignored.add(name)
            continue
        if name.startswith("test_") and name.endswith(".lean"):
            ignored.add(name)
    return ignored


def _extract_tarball(tar_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:gz") as tar:
        tar.extractall(destination)


def _find_result_lean_file(
    *,
    extracted_root: Path,
    preferred_relative_path: Path,
    desired_theorem_name: str,
) -> Path | None:
    preferred = extracted_root / preferred_relative_path
    if preferred.is_file():
        return preferred

    lean_files = sorted(extracted_root.rglob("*.lean"))
    for path in lean_files:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if desired_theorem_name in text:
            return path

    if lean_files:
        return lean_files[0]
    return None


def _append_aristotle_summary_files(extracted_root: Path) -> None:
    for summary_path in sorted(extracted_root.rglob("ARISTOTLE_SUMMARY_*.md")):
        try:
            summary_text = summary_path.read_text(encoding="utf-8").rstrip()
        except OSError:
            continue
        append_to_progress_log("-------")
        append_to_progress_log(f"Aristotle summary file: {summary_path.name}")
        if summary_text:
            append_to_progress_log(summary_text)
        else:
            append_to_progress_log("(summary file empty)")
        append_to_progress_log("-------")
