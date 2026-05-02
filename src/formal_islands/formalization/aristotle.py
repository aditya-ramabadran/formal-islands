"""Aristotle-specific formalization helpers."""

from __future__ import annotations

import json
import hashlib
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from formal_islands.backends.aristotle import AristotleBackend
from formal_islands.backends.base import BackendOutputError
from formal_islands.continuation import extract_continuation_instructions
from formal_islands.fixed_spec import fixed_root_spec_prompt_block, fixed_root_spec_skeleton
from formal_islands.formalization.agentic import recover_agentic_artifact_from_scratch_file
from formal_islands.formalization.pipeline import (
    build_local_proof_context,
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
    source_artifact_path: str | None
    lean_code: str
    usage_mode: str = "reference"
    import_module: str | None = None


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
    if (
        graph.fixed_root_lean_spec is not None
        and node_id == graph.root_node_id
        and graph.fixed_root_lean_spec.theorem_name
    ):
        desired_theorem_name = graph.fixed_root_lean_spec.theorem_name
    relative_scratch_path = scratch_path.relative_to(workspace_root)

    modular_child_support_attempt = _should_use_importable_verified_child_support(
        graph=graph,
        node_id=node_id,
    )
    support_files = _build_verified_child_support_files(
        graph=graph,
        node_id=node_id,
        workspace_root=workspace_root,
        prefer_importable_modules=modular_child_support_attempt,
    )
    _materialize_workspace_verified_child_support_files(
        workspace_root=workspace_root,
        support_files=support_files,
    )

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
            copied_auxiliary_paths = _copy_extracted_generated_lean_files(
                extracted_root=extracted_root,
                workspace_root=workspace_root,
                primary_destination=scratch_path,
            )
            if copied_auxiliary_paths:
                progress(
                    f"Aristotle project {run.project_id} for node {node_id}: copied "
                    f"{len(copied_auxiliary_paths)} auxiliary generated Lean file(s) into the workspace"
                )

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
    local_context = build_local_proof_context(graph, node.id)
    direct_child_context = build_verified_direct_child_context(graph, node.id)
    continuation_instructions = extract_continuation_instructions(node.formalization_rationale)
    fixed_spec_block = fixed_root_spec_prompt_block(graph, node.id)
    importable_support_files = [
        support for support in (verified_child_support_files or []) if support.usage_mode == "importable"
    ]
    reference_support_files = [
        support for support in (verified_child_support_files or []) if support.usage_mode != "importable"
    ]
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
        fixed_spec_block or "",
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
        (
            "User continuation instructions (must follow):\n"
            f"{continuation_instructions}\n\n"
            "These instructions were supplied by the user for this continuation attempt. "
            "Treat them as high-priority theorem-shape and proof-strategy constraints. "
            "If they conflict with generic fallback advice, follow the user "
            "continuation instructions while still avoiding sorrys, axioms, "
            "or semantically weaker side facts."
        )
        if continuation_instructions
        else "",
        "Local proof neighborhood:",
        format_local_proof_context(local_context),
        "Verified direct child lemmas:",
        format_verified_direct_child_context(direct_child_context),
        (
            "Proof-goal instruction: the verified direct children already cover part of this node's burden. "
            "Your main theorem should certify only the remaining parent-level delta / remaining parent-level step, not restate a verified child "
            "theorem or a trivial corollary that merely duplicates child coverage."
        ),
        (
            "If you use the verified child results, use them only as helper lemmas while proving a new theorem "
            "for the current node. The designated main theorem must be a genuinely new parent-level theorem for "
            "this target, not just a resubmission of one child theorem under a new filename."
        ),
        (
            "Packaging instruction for this child-aware attempt: use the importable verified direct-child modules as "
            "the default packaging path. Do not inline, restate, or locally duplicate those verified child lemmas "
            "inside the scratch file when exact import modules are available."
        )
        if importable_support_files
        else (
            "Packaging instruction: for ordinary node attempts, prefer a self-contained final scratch file. Treat "
            "reference-only support files as material to inspect, then copy or adapt only the minimal helper lemmas "
            "you need into the scratch file itself."
        ),
        (
            "Dependency direction note: the verified child lemmas are outgoing dependencies of the target node. "
            "Treat them as already established support, not as parents or as claims that depend on the target."
        ),
        (
            "Core constraints: rewrite only the designated scratch file into a Lean 4 theorem named "
            f"`{desired_theorem_name}`; keep the same concrete mathematical setting and local proof role; "
            "do not convert a difficult intermediate identity, estimate, or proof step from the informal proof "
            "into a new hypothesis unless it is already stated in the target node; do not make a major shrink "
            "to a proxy theorem, lower-dimensional analogue, or weak side "
            "fact; avoid sorrys, axioms, and arbitrary abstraction."
        ),
        (
            "Lean hygiene: `λ` is a reserved keyword in declaration headers, so keep theorem headers and binders "
            "ASCII-safe (`lambda1` rather than Unicode binder names "
            "such as `λ₁`), modify no unrelated files, and prefer specific imports when practical."
        ),
        (
            "Fallback rule: if the full node is too hard, the designated theorem may be a smaller concrete core only "
            "when it is explicit in the Lean file, stays in the same theorem family, is genuinely nontrivial, and carries meaningful inferential "
            "load from this parent proof. If no such core is possible, fail rather than returning a trivial or over-shrunk theorem."
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
                            "usage_mode": support.usage_mode,
                            "import_module": support.import_module,
                            "snapshot_file": str(support.relative_path),
                            "source_artifact_path": support.source_artifact_path,
                        }
                        for support in verified_child_support_files
                    ],
                    indent=2,
                ),
                (
                    "The designated main theorem must still be "
                    f"`{desired_theorem_name}` and must certify the current node's parent-level delta."
                ),
            ]
        )
    if reference_support_files:
        prompt_parts.append(
            "Reference-only support files should be inspected and copied from when useful; they are not importable modules."
        )
    if importable_support_files:
        prompt_parts.extend(
            [
                (
                    "Some verified direct-child support files in this snapshot are importable Lean modules. Because "
                    "they are explicit verified dependencies of the current node, you may import those child modules "
                    "directly in the final artifact."
                ),
                (
                    "When you import a verified direct-child module, use the exact `import_module` string provided "
                    "above verbatim. Do not invent alternate module names or reach back to stale worker imports."
                ),
                (
                    "If you import a verified direct-child module, call the actual verified theorem from that module. "
                    "Do not restate a verified child theorem locally with `:= by sorry`, `axiom`, or any other placeholder."
                ),
                (
                    "These importable modules are already materialized in this snapshot and in the local verification "
                    "workspace. There is no naming or packaging constraint that prevents importing them under the "
                    "exact module names listed above."
                ),
                (
                    "Preferred import block for this attempt:\n"
                    + "\n".join(
                        f"import {support.import_module}"
                        for support in importable_support_files
                        if support.import_module
                    )
                ),
            ]
        )
    if importable_support_files:
        prompt_parts.extend(
            [
                "This attempt already has verified direct-child theorem dependencies available.",
                (
                    "Start from the verified support theorem(s) above, reuse them aggressively as helpers, and prove the "
                    "missing parent-level assembly or enlargement step. Do not submit a file whose only substantial theorem "
                    "is one of the support theorems unchanged."
                ),
                (
                    "The cleanest default is to import each verified direct-child module whose exact import path is listed "
                    "above, and then prove the new theorem for the current node from those verified children."
                ),
                (
                    "Only fall back to copying helper code when a needed support file is reference-only or when import "
                    "would be impossible. Never use local `sorry` stubs to fake already-verified child lemmas."
                ),
                (
                    "Do not create local stand-ins such as `_local` lemmas, primed duplicates, or theorem re-statements "
                    "for any verified direct child theorem listed above. Import the provided module and call the verified "
                    "child theorem directly instead."
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
                (
                    "Revise the current scratch file already present in the submitted project snapshot. "
                    "Do not ask for the file contents; inspect and edit the snapshot file in place."
                )
            ]
        )
        if importable_support_files:
            prompt_parts.append(
                "If the scratch file contains local declarations whose theorem names match verified direct-child "
                "theorems above, or local stand-ins such as `_local`/primed variants of those child lemmas, delete "
                "those declarations and replace them with the exact imports listed above."
            )

    prompt_parts = [part for part in prompt_parts if part]

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
    local_context = build_local_proof_context(graph, node.id)
    direct_child_context = build_verified_direct_child_context(graph, node.id)
    continuation_instructions = extract_continuation_instructions(node.formalization_rationale)
    fixed_spec_block = fixed_root_spec_prompt_block(graph, node.id)
    importable_support_files = [
        support for support in (verified_child_support_files or []) if support.usage_mode == "importable"
    ]
    reference_support_files = [
        support for support in (verified_child_support_files or []) if support.usage_mode != "importable"
    ]
    lines: list[str] = []
    root_fixed_spec_attempt = graph.fixed_root_lean_spec is not None and node.id == graph.root_node_id
    if importable_support_files:
        lines.extend(
            [
                "import Mathlib",
                *[
                    f"import {support.import_module}"
                    for support in importable_support_files
                    if support.import_module
                ],
                "",
            ]
        )
    elif root_fixed_spec_attempt:
        lines.extend(
            [
                "import Mathlib",
                "",
            ]
        )
    if root_fixed_spec_attempt:
        lines.extend(
            [
                "set_option maxHeartbeats 1600000",
                "",
                "open Classical",
                "",
                "noncomputable section",
                "",
            ]
        )

    lines.extend(
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
            *([fixed_spec_block, ""] if fixed_spec_block else []),
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
            *(
                [
                    "User continuation instructions (must follow):",
                    continuation_instructions,
                    "",
                    (
                        "These instructions were supplied by the user for this "
                        "continuation attempt. Treat them as high-priority "
                        "theorem-shape and proof-strategy constraints."
                    ),
                    "",
                ]
                if continuation_instructions
                else []
            ),
            "Local proof neighborhood:",
            format_local_proof_context(local_context),
            "",
            "Verified direct child lemmas:",
            format_verified_direct_child_context(direct_child_context),
            "",
            "These verified children are already available. The theorem should prove only the remaining parent-level delta,",
            "not a restatement of any verified child or a close corollary that duplicates it.",
            "If you use them, treat them as helper lemmas for a new theorem for the current node.",
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
    )
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
                    f"  usage mode: {support.usage_mode}",
                    (f"  import module: {support.import_module}" if support.import_module else "  import module: (none)"),
                    f"  reference file: {support.relative_path}",
                ]
            )
    if reference_support_files:
        lines.extend(
            [
                "",
                "Reference-only support files should be inspected and copied from when useful; they are not importable modules.",
            ]
        )
    if importable_support_files:
        lines.extend(
            [
                "",
                "Some support files are importable verified direct-child modules.",
                "You may import those child modules directly because they are explicit verified dependencies of this node.",
                "Use the exact provided import module string; do not invent alternate stable names or stale worker imports.",
                "If you import a child module, call the actual verified theorem from that module.",
                "Do not restate a verified child theorem locally with `:= by sorry` or any other placeholder.",
                "These importable modules are already materialized in this snapshot; there is no naming constraint preventing those imports.",
            ]
        )
    if importable_support_files:
        lines.extend(
            [
                "",
                "This attempt already has verified direct-child theorem dependencies available.",
                "Reuse the support theorem(s) above as helpers and prove the parent-level enlargement or assembly step.",
                "Do not leave the file with only a support theorem copied unchanged.",
                "When a verified direct-child support file is importable, prefer importing that child module and proving the new theorem for the current node from it.",
                "Only copy helper material when a needed support file is reference-only.",
                "Never use local `sorry` stubs to stand in for already-verified child lemmas.",
                "Do not define `_local` or primed stand-ins for verified direct-child theorems when exact import modules are already listed above.",
            ]
        )
    lines.append("-/")
    if root_fixed_spec_attempt:
        skeleton = fixed_root_spec_skeleton(graph.fixed_root_lean_spec)
        if skeleton is not None:
            lines.extend(
                [
                    "",
                    skeleton,
                ]
            )
    return "\n".join(lines)


def _build_verified_child_support_files(
    *,
    graph: ProofGraph,
    node_id: str,
    workspace_root: Path | None = None,
    prefer_importable_modules: bool = False,
) -> list[VerifiedChildSupportFile]:
    children = {edge.target_id for edge in graph.edges if edge.source_id == node_id}
    support_files: list[VerifiedChildSupportFile] = []
    for child in sorted((node for node in graph.nodes if node.id in children), key=lambda node: node.id):
        artifact = child.formal_artifact
        if child.status != "formal_verified" or artifact is None:
            continue
        module_stem = _sanitize_file_stem(child.id)
        source_path = (
            Path(artifact.verification.artifact_path).expanduser().resolve()
            if artifact.verification.artifact_path
            else None
        )
        usage_mode = "reference"
        import_module: str | None = None
        relative_path = Path("FormalIslands") / "Generated" / "SupportReference" / f"{module_stem}.lean.txt"
        source_text = (
            source_path.read_text(encoding="utf-8")
            if source_path is not None and source_path.is_file()
            else artifact.lean_code
        )
        if prefer_importable_modules:
            usage_mode = "importable"
            relative_path = (
                Path("FormalIslands")
                / "Generated"
                / "VerifiedSupport"
                / f"{_stable_support_module_stem(child.id, source_text)}.lean"
            )
            import_module = _relative_lean_path_to_module_name(relative_path)
        support_files.append(
            VerifiedChildSupportFile(
                child_id=child.id,
                child_title=child.title,
                theorem_name=artifact.lean_theorem_name,
                lean_statement=artifact.lean_statement,
                relative_path=relative_path,
                source_artifact_path=artifact.verification.artifact_path,
                lean_code=artifact.lean_code,
                usage_mode=usage_mode,
                import_module=import_module,
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
        source_text = (
            source_path.read_text(encoding="utf-8")
            if source_path is not None and source_path.is_file()
            else support.lean_code
        )
        if support.usage_mode == "importable":
            _write_importable_support_modules(
                root=snapshot_root,
                support=support,
                source_text=source_text,
            )
            continue
        reference_text = "\n".join(
            [
                f"-- Verified child id: {support.child_id}",
                f"-- Theorem: {support.theorem_name}",
                "-- Statement:",
                support.lean_statement,
                "",
                "-- Full source artifact below. This file is reference material only; copy or adapt",
                "-- what you need into the active scratch file instead of importing it.",
                "",
                source_text,
            ]
        )
        destination.write_text(reference_text, encoding="utf-8")


def _materialize_workspace_verified_child_support_files(
    *,
    workspace_root: Path,
    support_files: list[VerifiedChildSupportFile],
) -> None:
    for support in support_files:
        if support.usage_mode != "importable":
            continue
        source_path = Path(support.source_artifact_path).expanduser().resolve() if support.source_artifact_path else None
        source_text = (
            source_path.read_text(encoding="utf-8")
            if source_path is not None and source_path.is_file()
            else support.lean_code
        )
        _write_importable_support_modules(
            root=workspace_root,
            support=support,
            source_text=source_text,
        )


def _write_importable_support_modules(
    *,
    root: Path,
    support: VerifiedChildSupportFile,
    source_text: str,
) -> None:
    destination = root / support.relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "\n".join(
            [
                f"/- Stable module for verified child node `{support.child_id}`. -/",
                "",
                source_text.rstrip(),
            ]
        ).strip()
        + "\n",
        encoding="utf-8",
    )

def _should_use_importable_verified_child_support(
    *,
    graph: ProofGraph,
    node_id: str,
) -> bool:
    children = {edge.target_id for edge in graph.edges if edge.source_id == node_id}
    return any(
        node.id in children and node.status == "formal_verified" and node.formal_artifact is not None
        for node in graph.nodes
    )


def _desired_aristotle_theorem_name(node_id: str) -> str:
    return f"{_sanitize_file_stem(node_id)}_aristotle"


def _sanitize_file_stem(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_") or "aristotle_node"


def _stable_support_module_stem(node_id: str, source_text: str) -> str:
    sanitized = _sanitize_file_stem(node_id)
    parts = [part for part in sanitized.split("_") if part]
    base = "".join(part[:1].upper() + part[1:] for part in parts) if parts else "VerifiedChild"
    digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()[:10]
    return f"{base}_{digest}"


def _relative_lean_path_to_module_name(relative_path: Path) -> str:
    module_path = relative_path.with_suffix("")
    return ".".join(module_path.parts)


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


def _copy_extracted_generated_lean_files(
    *,
    extracted_root: Path,
    workspace_root: Path,
    primary_destination: Path,
) -> list[Path]:
    copied_paths: list[Path] = []
    generated_parts = ("FormalIslands", "Generated")

    for source_path in sorted(extracted_root.rglob("*.lean")):
        try:
            generated_index = source_path.parts.index(generated_parts[0])
        except ValueError:
            continue
        remaining_parts = source_path.parts[generated_index:]
        if tuple(remaining_parts[:2]) != generated_parts:
            continue
        if len(remaining_parts) >= 3 and remaining_parts[2] == "VerifiedSupport":
            # Verified support modules are trusted local artifacts materialized from
            # already-verified children. Aristotle receives them as inputs, but its
            # returned project may contain edited or placeholder copies. Never copy
            # those back over the local support cache.
            continue

        destination_path = workspace_root.joinpath(*remaining_parts)
        if destination_path == primary_destination:
            continue
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)
        copied_paths.append(destination_path)

    return copied_paths


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
