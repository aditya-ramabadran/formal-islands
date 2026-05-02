"""Direct-root Aristotle diagnostics for theorem/proof JSON examples."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from formal_islands.backends import AristotleBackend
from formal_islands.backends.base import BackendError, BackendUnavailableError
from formal_islands.fixed_spec import (
    extract_decl_header,
    extract_decl_name,
    fixed_spec_exact_header_matches,
    fixed_spec_mismatch_message,
    fixed_root_spec_skeleton,
)
from formal_islands.formalization.aristotle import (
    _append_aristotle_summary_files,
    _aristotle_snapshot_ignore,
    _copy_extracted_generated_lean_files,
    _extract_tarball,
    _find_result_lean_file,
    _sanitize_file_stem,
)
from formal_islands.formalization.lean import LeanVerifier, LeanWorkspace
from formal_islands.models import FixedRootLeanSpec, FormalArtifact, VerificationResult
from formal_islands.progress import progress


DIRECT_ROOT_THEOREM_NAME = "direct_root_aristotle"


@dataclass(frozen=True)
class DirectRootDiagnostic:
    """Captured output from a direct-root diagnostic attempt."""

    theorem_title: str
    desired_theorem_name: str
    prompt_path: Path
    scratch_path: Path
    result_lean_path: Path | None
    extracted_result_dir: Path | None
    aristotle_project_id: str | None
    aristotle_status: str | None
    aristotle_log_path: Path | None
    aristotle_result_tar_path: Path | None
    verification: VerificationResult
    contains_desired_theorem: bool
    fixed_root_lean_spec: FixedRootLeanSpec | None
    fixed_spec_exact_header_present: bool | None
    copied_auxiliary_paths: list[Path]
    attempt_history: list[dict[str, Any]]

    @property
    def verified_root(self) -> bool:
        return (
            self.contains_desired_theorem
            and self.verification.status == "verified"
            and self.fixed_spec_exact_header_present is not False
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "theorem_title": self.theorem_title,
            "desired_theorem_name": self.desired_theorem_name,
            "verified_root": self.verified_root,
            "contains_desired_theorem": self.contains_desired_theorem,
            "fixed_root_lean_spec": (
                self.fixed_root_lean_spec.model_dump(mode="json")
                if self.fixed_root_lean_spec
                else None
            ),
            "fixed_spec_exact_header_present": self.fixed_spec_exact_header_present,
            "prompt_path": str(self.prompt_path),
            "scratch_path": str(self.scratch_path),
            "result_lean_path": str(self.result_lean_path) if self.result_lean_path else None,
            "extracted_result_dir": (
                str(self.extracted_result_dir) if self.extracted_result_dir else None
            ),
            "aristotle_project_id": self.aristotle_project_id,
            "aristotle_status": self.aristotle_status,
            "aristotle_log_path": str(self.aristotle_log_path)
            if self.aristotle_log_path
            else None,
            "aristotle_result_tar_path": str(self.aristotle_result_tar_path)
            if self.aristotle_result_tar_path
            else None,
            "copied_auxiliary_paths": [str(path) for path in self.copied_auxiliary_paths],
            "verification": self.verification.model_dump(mode="json"),
            "attempt_count": len(self.attempt_history),
            "attempt_history": self.attempt_history,
        }


def build_direct_root_aristotle_prompt(
    *,
    theorem_title: str,
    theorem_statement: str,
    raw_proof_text: str,
    desired_theorem_name: str = DIRECT_ROOT_THEOREM_NAME,
    relative_scratch_path: Path,
    fixed_root_lean_spec: FixedRootLeanSpec | None = None,
) -> str:
    """Build the compact direct-root prompt used for Aristotle diagnostics."""

    fixed_spec_parts: list[str] = []
    if fixed_root_lean_spec is not None:
        fixed_spec_parts = [
            "Fixed Lean root specification:",
            (
                "The theorem below is an exact external/root target. The designated main theorem "
                "must preserve this theorem name, binders, hypotheses, and conclusion. Helper lemmas "
                "are welcome, but changing this theorem header does not count as a direct-root success. "
                "The scratch file starts with a matching theorem skeleton; complete that theorem rather "
                "than replacing it with a different declaration."
            ),
            "```lean",
            fixed_root_lean_spec.lean_statement,
            "```",
        ]

    return "\n\n".join(
        [
            f"Theorem title: {theorem_title}",
            "Direct-root diagnostic task:",
            (
                "Rewrite the designated Lean scratch file into a Lean 4 proof of the theorem "
                "statement below, using the informal proof text as guidance. The designated main "
                "theorem should target the full theorem statement, not a smaller local subclaim."
            ),
            "Theorem statement to formalize and prove:",
            theorem_statement.strip(),
            *fixed_spec_parts,
            "Informal proof text:",
            raw_proof_text.strip(),
            "Lean output requirements:",
            "\n".join(
                [
                    f"- Scratch file to rewrite: {relative_scratch_path.as_posix()}",
                    f"- The designated main theorem must be named `{desired_theorem_name}`.",
                    *(
                        [
                            "- Because a fixed Lean root specification was supplied, the designated main theorem must preserve the exact fixed theorem header."
                        ]
                        if fixed_root_lean_spec is not None
                        else []
                    ),
                    "- The file should compile with `lake env lean` in the submitted Lean project.",
                    "- Avoid `sorry`, `admit`, axioms, or opaque placeholder lemmas.",
                    "- Helper lemmas in the same file are welcome when they make the proof clearer.",
                    "- `import Mathlib` is acceptable for this diagnostic if it avoids import-hunting noise.",
                ]
            ),
            "Faithfulness and fairness requirements:",
            "\n".join(
                [
                    "- Keep the same theorem family, mathematical setting, and object types as the statement.",
                    "- Do not replace the theorem by a toy variant, lower-dimensional analogue, or unrelated proxy.",
                    "- Do not prove only an intermediate sublemma as the designated main theorem.",
                    "- Do not convert a difficult intermediate identity, estimate, or proof step from the informal proof into a new hypothesis unless that step is already stated in the theorem itself.",
                    "- If the informal proof derives a fact, treat it as something to prove, not something to assume.",
                    "- If the full root theorem is not honestly provable under the stated assumptions, prefer a transparent compile failure over a misleading theorem that proves a different claim.",
                    "- Keep theorem headers and binder names ASCII-safe; mathematical notation in comments is fine.",
                ]
            ),
        ]
    )


def run_direct_root_aristotle_diagnostic(
    *,
    backend: AristotleBackend,
    verifier: LeanVerifier,
    input_payload: dict[str, Any],
    output_dir: Path,
    max_attempts: int,
    fixed_root_lean_spec: FixedRootLeanSpec | None = None,
) -> DirectRootDiagnostic:
    """Submit an input theorem/proof directly to Aristotle and verify the returned root file."""

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    theorem_title = str(input_payload.get("theorem_title") or "Untitled theorem")
    theorem_statement = str(input_payload["theorem_statement"])
    raw_proof_text = str(input_payload["raw_proof_text"])
    desired_theorem_name = (
        fixed_root_lean_spec.theorem_name
        if fixed_root_lean_spec is not None and fixed_root_lean_spec.theorem_name
        else DIRECT_ROOT_THEOREM_NAME
    )
    workspace_root = verifier.workspace.root.resolve()
    verifier.workspace.validate()

    output_dir.mkdir(parents=True, exist_ok=True)
    scratch_path = _prepare_direct_root_scratch_file(
        workspace=verifier.workspace,
        theorem_title=theorem_title,
        fixed_root_lean_spec=fixed_root_lean_spec,
    ).resolve()
    relative_scratch_path = scratch_path.relative_to(workspace_root)
    prompt = build_direct_root_aristotle_prompt(
        theorem_title=theorem_title,
        theorem_statement=theorem_statement,
        raw_proof_text=raw_proof_text,
        desired_theorem_name=desired_theorem_name,
        relative_scratch_path=relative_scratch_path,
        fixed_root_lean_spec=fixed_root_lean_spec,
    )
    prompt_path = output_dir / "direct_root_prompt.txt"
    prompt_path.write_text(prompt + "\n", encoding="utf-8")

    progress(
        f"direct-root diagnostic: submitting {theorem_title!r} to Aristotle "
        f"with up to {max_attempts} attempt(s)"
    )
    attempt_history: list[dict[str, Any]] = []
    latest_diagnostic: DirectRootDiagnostic | None = None

    for attempt_number in range(1, max_attempts + 1):
        progress(
            "direct-root diagnostic: Aristotle attempt "
            f"{attempt_number}/{max_attempts}"
        )
        try:
            diagnostic = _run_direct_root_aristotle_attempt(
                backend=backend,
                verifier=verifier,
                theorem_title=theorem_title,
                output_dir=output_dir,
                scratch_path=scratch_path,
                relative_scratch_path=relative_scratch_path,
                prompt=prompt,
                prompt_path=prompt_path,
                attempt_number=attempt_number,
                desired_theorem_name=desired_theorem_name,
                fixed_root_lean_spec=fixed_root_lean_spec,
            )
        except BackendUnavailableError:
            raise
        except BackendError as exc:
            verification = VerificationResult(
                status="failed",
                command="backend_request",
                exit_code=None,
                stdout="",
                stderr=str(exc),
                attempt_count=attempt_number,
                artifact_path=str(scratch_path) if scratch_path.exists() else None,
            )
            diagnostic = DirectRootDiagnostic(
                theorem_title=theorem_title,
                desired_theorem_name=desired_theorem_name,
                prompt_path=prompt_path,
                scratch_path=scratch_path,
                result_lean_path=None,
                extracted_result_dir=None,
                aristotle_project_id=None,
                aristotle_status=None,
                aristotle_log_path=None,
                aristotle_result_tar_path=None,
                verification=verification,
                contains_desired_theorem=False,
                fixed_root_lean_spec=fixed_root_lean_spec,
                fixed_spec_exact_header_present=None if fixed_root_lean_spec is None else False,
                copied_auxiliary_paths=[],
                attempt_history=[],
            )

        attempt_summary = _attempt_summary(diagnostic, attempt_number=attempt_number)
        attempt_history.append(attempt_summary)
        latest_diagnostic = diagnostic
        progress(
            "direct-root diagnostic: attempt "
            f"{attempt_number}/{max_attempts} completed with "
            f"verified_root={diagnostic.verified_root}"
        )
        if diagnostic.verified_root:
            break

    assert latest_diagnostic is not None
    return replace(latest_diagnostic, attempt_history=attempt_history)


def _run_direct_root_aristotle_attempt(
    *,
    backend: AristotleBackend,
    verifier: LeanVerifier,
    theorem_title: str,
    output_dir: Path,
    scratch_path: Path,
    relative_scratch_path: Path,
    prompt: str,
    prompt_path: Path,
    attempt_number: int,
    desired_theorem_name: str,
    fixed_root_lean_spec: FixedRootLeanSpec | None,
) -> DirectRootDiagnostic:
    workspace_root = verifier.workspace.root.resolve()
    with tempfile.TemporaryDirectory(prefix="formal-islands-direct-root-") as temp_dir_name:
        snapshot_root = Path(temp_dir_name)
        shutil.copytree(
            workspace_root,
            snapshot_root,
            dirs_exist_ok=True,
            ignore=_aristotle_snapshot_ignore,
        )
        snapshot_scratch_path = snapshot_root / relative_scratch_path
        snapshot_scratch_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_scratch_path.write_text(
            _render_direct_root_scratch_header(
                theorem_title=theorem_title,
                fixed_root_lean_spec=fixed_root_lean_spec,
            ),
            encoding="utf-8",
        )

        run = backend.submit_project(
            prompt=prompt,
            project_dir=snapshot_root,
            task_name=(
                f"direct_root_aristotle_{_sanitize_file_stem(theorem_title)}"
                f"_attempt_{attempt_number}"
            ),
        )

    if run.result_tar_path is None:
        verification = VerificationResult(
            status="failed",
            command="aristotle_result_recovery",
            exit_code=None,
            stdout="",
            stderr="Aristotle did not return a downloadable solution tarball.",
            attempt_count=attempt_number,
            artifact_path=str(scratch_path) if scratch_path.exists() else None,
        )
        return DirectRootDiagnostic(
            theorem_title=theorem_title,
            desired_theorem_name=desired_theorem_name,
            prompt_path=prompt_path,
            scratch_path=scratch_path,
            result_lean_path=None,
            extracted_result_dir=None,
            aristotle_project_id=run.project_id,
            aristotle_status=run.status,
            aristotle_log_path=run.log_path,
            aristotle_result_tar_path=None,
            verification=verification,
            contains_desired_theorem=False,
            fixed_root_lean_spec=fixed_root_lean_spec,
            fixed_spec_exact_header_present=None if fixed_root_lean_spec is None else False,
            copied_auxiliary_paths=[],
            attempt_history=[],
        )

    extracted_root = output_dir / f"aristotle_result_attempt_{attempt_number}"
    if extracted_root.exists():
        shutil.rmtree(extracted_root)
    progress(
        f"direct-root diagnostic: extracting Aristotle project {run.project_id} result to {extracted_root}"
    )
    _extract_tarball(run.result_tar_path, extracted_root)
    _append_aristotle_summary_files(extracted_root)

    result_lean_path = _find_result_lean_file(
        extracted_root=extracted_root,
        preferred_relative_path=relative_scratch_path,
        desired_theorem_name=desired_theorem_name,
    )
    if result_lean_path is None:
        verification = VerificationResult(
            status="failed",
            command="aristotle_result_recovery",
            exit_code=None,
            stdout="",
            stderr="Aristotle did not return any Lean file.",
            attempt_count=attempt_number,
            artifact_path=None,
        )
        return DirectRootDiagnostic(
            theorem_title=theorem_title,
            desired_theorem_name=desired_theorem_name,
            prompt_path=prompt_path,
            scratch_path=scratch_path,
            result_lean_path=None,
            extracted_result_dir=extracted_root,
            aristotle_project_id=run.project_id,
            aristotle_status=run.status,
            aristotle_log_path=run.log_path,
            aristotle_result_tar_path=run.result_tar_path,
            verification=verification,
            contains_desired_theorem=False,
            fixed_root_lean_spec=fixed_root_lean_spec,
            fixed_spec_exact_header_present=None if fixed_root_lean_spec is None else False,
            copied_auxiliary_paths=[],
            attempt_history=[],
        )

    progress(f"direct-root diagnostic: copying result Lean file {result_lean_path} to {scratch_path}")
    scratch_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(result_lean_path, scratch_path)
    copied_auxiliary_paths = _copy_extracted_generated_lean_files(
        extracted_root=extracted_root,
        workspace_root=workspace_root,
        primary_destination=scratch_path,
    )
    contains_desired_theorem = _contains_theorem_declaration(
        scratch_path.read_text(encoding="utf-8"),
        desired_theorem_name,
    )
    fixed_spec_exact_header_present = None
    if fixed_root_lean_spec is not None:
        lean_code = scratch_path.read_text(encoding="utf-8")
        fixed_spec_exact_header_present = fixed_spec_exact_header_matches(
            FormalArtifact(
                lean_theorem_name=desired_theorem_name,
                lean_statement=extract_decl_header(
                    lean_code,
                    preferred_name=desired_theorem_name,
                )
                or "-- theorem header not found",
                lean_code=lean_code,
            ),
            fixed_root_lean_spec,
        )

    verification = verifier.verify_existing_file(file_path=scratch_path, attempt_number=attempt_number)
    if verification.status == "verified" and not contains_desired_theorem:
        verification = verification.model_copy(
            update={
                "status": "failed",
                "stderr": (
                    verification.stderr
                    + "\nDirect-root diagnostic rejected the file because it did not contain "
                    f"a theorem declaration named `{desired_theorem_name}`."
                ).strip(),
            }
        )
    if verification.status == "verified" and fixed_spec_exact_header_present is False:
        lean_code = scratch_path.read_text(encoding="utf-8")
        artifact = FormalArtifact(
            lean_theorem_name=desired_theorem_name,
            lean_statement=extract_decl_header(
                lean_code,
                preferred_name=desired_theorem_name,
            )
            or "-- theorem header not found",
            lean_code=lean_code,
        )
        verification = verification.model_copy(
            update={
                "status": "failed",
                "stderr": (
                    verification.stderr
                    + "\n"
                    + fixed_spec_mismatch_message(fixed_root_lean_spec, artifact)
                ).strip(),
            }
        )

    return DirectRootDiagnostic(
        theorem_title=theorem_title,
        desired_theorem_name=desired_theorem_name,
        prompt_path=prompt_path,
        scratch_path=scratch_path,
        result_lean_path=result_lean_path,
        extracted_result_dir=extracted_root,
        aristotle_project_id=run.project_id,
        aristotle_status=run.status,
        aristotle_log_path=run.log_path,
        aristotle_result_tar_path=run.result_tar_path,
        verification=verification,
        contains_desired_theorem=contains_desired_theorem,
        fixed_root_lean_spec=fixed_root_lean_spec,
        fixed_spec_exact_header_present=fixed_spec_exact_header_present,
        copied_auxiliary_paths=copied_auxiliary_paths,
        attempt_history=[],
    )


def _attempt_summary(
    diagnostic: DirectRootDiagnostic,
    *,
    attempt_number: int,
) -> dict[str, Any]:
    return {
        "attempt_number": attempt_number,
        "verified_root": diagnostic.verified_root,
        "contains_desired_theorem": diagnostic.contains_desired_theorem,
        "fixed_spec_exact_header_present": diagnostic.fixed_spec_exact_header_present,
        "aristotle_project_id": diagnostic.aristotle_project_id,
        "aristotle_status": diagnostic.aristotle_status,
        "aristotle_log_path": str(diagnostic.aristotle_log_path)
        if diagnostic.aristotle_log_path
        else None,
        "aristotle_result_tar_path": str(diagnostic.aristotle_result_tar_path)
        if diagnostic.aristotle_result_tar_path
        else None,
        "result_lean_path": str(diagnostic.result_lean_path)
        if diagnostic.result_lean_path
        else None,
        "extracted_result_dir": str(diagnostic.extracted_result_dir)
        if diagnostic.extracted_result_dir
        else None,
        "copied_auxiliary_paths": [str(path) for path in diagnostic.copied_auxiliary_paths],
        "verification": diagnostic.verification.model_dump(mode="json"),
    }


def _prepare_direct_root_scratch_file(
    *,
    workspace: LeanWorkspace,
    theorem_title: str,
    fixed_root_lean_spec: FixedRootLeanSpec | None = None,
) -> Path:
    workspace.validate()
    workspace.generated_dir.mkdir(parents=True, exist_ok=True)
    safe_title = _sanitize_file_stem(theorem_title.lower())[:48] or "direct_root"
    scratch_path = workspace.generated_dir / workspace._unique_generated_filename(
        f"direct_root_{safe_title}",
        "lean",
    )
    scratch_path.write_text(
        _render_direct_root_scratch_header(
            theorem_title=theorem_title,
            fixed_root_lean_spec=fixed_root_lean_spec,
        ),
        encoding="utf-8",
    )
    return scratch_path


def _render_direct_root_scratch_header(
    *,
    theorem_title: str,
    fixed_root_lean_spec: FixedRootLeanSpec | None = None,
) -> str:
    fixed_spec_text = ""
    fixed_spec_skeleton = ""
    if fixed_root_lean_spec is not None:
        fixed_spec_text = (
            "Fixed Lean root specification supplied.\n"
            f"Expected theorem name: {extract_decl_name(fixed_root_lean_spec.lean_statement) or '(could not extract)'}\n"
            f"Statement hash: {fixed_root_lean_spec.statement_hash}\n"
            "Exact statement:\n"
            f"{fixed_root_lean_spec.lean_statement}\n"
        )
        skeleton = fixed_root_spec_skeleton(fixed_root_lean_spec)
        if skeleton is not None:
            fixed_spec_skeleton = "\n" + skeleton + "\n"
    return (
        "/-\n"
        "Direct-root diagnostic scratch file.\n"
        f"Theorem title: {theorem_title}\n"
        f"{fixed_spec_text}"
        "Aristotle should replace this file with a proof of the root theorem.\n"
        "-/\n"
        "import Mathlib\n\n"
        "set_option maxHeartbeats 1600000\n\n"
        "open Classical\n\n"
        "noncomputable section\n"
        f"{fixed_spec_skeleton}"
    )


def _contains_theorem_declaration(text: str, theorem_name: str) -> bool:
    escaped = re.escape(theorem_name)
    return bool(re.search(rf"(?m)^\s*(?:theorem|lemma)\s+{escaped}\b", text))


def direct_root_diagnostic_to_artifact(
    diagnostic: DirectRootDiagnostic,
) -> FormalArtifact | None:
    """Recover the verified direct-root theorem as a regular FormalArtifact."""

    if diagnostic.result_lean_path is None or not diagnostic.scratch_path.exists():
        return None

    lean_code = diagnostic.scratch_path.read_text(encoding="utf-8")
    lean_statement = extract_decl_header(
        lean_code,
        preferred_name=diagnostic.desired_theorem_name,
    )
    if lean_statement is None:
        return None

    return FormalArtifact(
        lean_theorem_name=diagnostic.desired_theorem_name,
        lean_statement=lean_statement,
        lean_code=lean_code,
        verification=diagnostic.verification,
        attempt_history=[diagnostic.verification],
    )


def write_direct_root_diagnostic_summary(
    diagnostic: DirectRootDiagnostic,
    path: Path,
) -> None:
    path.write_text(json.dumps(diagnostic.to_json_dict(), indent=2) + "\n", encoding="utf-8")
