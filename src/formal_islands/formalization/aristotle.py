"""Aristotle-specific formalization helpers."""

from __future__ import annotations

import re
import shutil
import tarfile
import tempfile
from pathlib import Path

from formal_islands.backends.aristotle import AristotleBackend
from formal_islands.backends.base import BackendOutputError
from formal_islands.formalization.agentic import recover_agentic_artifact_from_scratch_file
from formal_islands.formalization.pipeline import (
    build_local_proof_context,
    build_node_coverage_sketch,
    format_local_proof_context,
)
from formal_islands.models import FormalArtifact, ProofGraph
from formal_islands.progress import progress


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

    with tempfile.TemporaryDirectory(prefix="formal-islands-aristotle-") as temp_dir_name:
        snapshot_root = Path(temp_dir_name)
        shutil.copytree(
            workspace_root,
            snapshot_root,
            dirs_exist_ok=True,
            ignore=_aristotle_snapshot_ignore,
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
                ),
                encoding="utf-8",
            )

        prompt = build_aristotle_formalization_prompt(
            graph=graph,
            node=node,
            desired_theorem_name=desired_theorem_name,
            relative_scratch_path=relative_scratch_path,
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

        extracted_root = Path(tempfile.mkdtemp(prefix="formal-islands-aristotle-result-"))
        try:
            _extract_tarball(run.result_tar_path, extracted_root)
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
            shutil.copy2(result_lean_path, scratch_path)

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
            progress(f"Aristotle completed node {node_id}")
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
    faithfulness_feedback: str | None = None,
    previous_lean_code: str | None = None,
    compiler_feedback: str | None = None,
) -> str:
    sketch = build_node_coverage_sketch(node)
    local_context = build_local_proof_context(graph, node.id)
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
            "Do not modify unrelated files. Keep the file self-contained and include any imports you need. "
            "Do not make a major shrink in the mathematical setting, dimension, ambient structure, or variable "
            "scope just to make the theorem easier."
        ),
        (
            "If the full node is too hard, prefer a smaller but still genuinely nontrivial concrete theorem in the "
            "same setting. The fallback must still carry meaningful inferential load from the parent proof."
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
) -> str:
    sketch = build_node_coverage_sketch(node)
    local_context = build_local_proof_context(graph, node.id)
    return "\n".join(
        [
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
            "Instructions:",
            "- Rewrite this file into a compilable Lean 4 theorem and proof for the target node above.",
            "- Keep the theorem faithful to the target node's concrete setting.",
            "- Do not promote an unproven intermediate identity or estimate to a hypothesis.",
            "- Do not make a major shrink in the mathematical setting, dimension, ambient structure, or variable scope.",
            "- Prefer the most concrete faithful theorem you can manage.",
            "- Avoid sorrys and avoid unrelated abstraction.",
            "- Use any imports you need, but prefer specific imports to broad ones like import Mathlib.",
            "- If a smaller theorem is the best reachable core, it must still be genuinely nontrivial and carry meaningful inferential load.",
            "- If you cannot produce a genuinely nontrivial fallback, fail rather than returning a trivial shrink.",
            "-/",
        ]
    )


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
