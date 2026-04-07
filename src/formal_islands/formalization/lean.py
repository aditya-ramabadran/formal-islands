"""Lean workspace management and local verification helpers."""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from formal_islands.formalization.agentic import AGENTIC_WORKER_PLACEHOLDER
from formal_islands.models import VerificationResult
from formal_islands.progress import progress


class CommandRunner(Protocol):
    """Callable subprocess interface used by LeanVerifier."""

    def __call__(
        self,
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        cwd: Path,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command and return a CompletedProcess."""


@dataclass(frozen=True)
class LeanWorkspace:
    """Local Lean project layout used for scratch verification files."""

    root: Path
    generated_subdir: str = "FormalIslands/Generated"

    @property
    def generated_dir(self) -> Path:
        return self.root / self.generated_subdir

    def validate(self) -> None:
        """Ensure the workspace has the minimum committed skeleton."""

        required_paths = [
            self.root / "lean-toolchain",
            self.root / "lakefile.toml",
            self.root / "FormalIslands.lean",
            self.root / "FormalIslands",
        ]
        missing = [path for path in required_paths if not path.exists()]
        if missing:
            missing_text = ", ".join(str(path) for path in missing)
            raise FileNotFoundError(f"Lean workspace is missing required paths: {missing_text}")

    def write_scratch_file(self, node_id: str, attempt_number: int, lean_code: str) -> Path:
        """Write a generated scratch file for local Lean verification."""

        self.validate()
        self.generated_dir.mkdir(parents=True, exist_ok=True)
        safe_node_id = node_id.replace("/", "_")
        scratch_path = self.generated_dir / self._unique_generated_filename(
            f"{safe_node_id}_attempt_{attempt_number}",
            "lean",
        )
        scratch_path.write_text(lean_code, encoding="utf-8")
        return scratch_path

    def prepare_worker_file(self, node_id: str) -> Path:
        """Reserve the single-file workspace used by the one-shot agentic worker."""

        self.validate()
        self.generated_dir.mkdir(parents=True, exist_ok=True)
        safe_node_id = node_id.replace("/", "_")
        scratch_path = self.generated_dir / self._unique_generated_filename(
            f"{safe_node_id}_worker",
            "lean",
        )
        scratch_path.write_text(AGENTIC_WORKER_PLACEHOLDER, encoding="utf-8")
        return scratch_path

    @staticmethod
    def _unique_generated_filename(stem: str, suffix: str) -> str:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        nonce = uuid.uuid4().hex[:8]
        return f"{stem}_{timestamp}_{nonce}.{suffix}"


@dataclass(frozen=True)
class LeanVerifier:
    """Deterministic local wrapper around `lake env lean`."""

    workspace: LeanWorkspace
    timeout_seconds: float | None = 240.0
    command_runner: CommandRunner = subprocess.run

    @staticmethod
    def _lake_executable() -> str:
        """Resolve the lake executable, falling back to ~/.elan/bin if not on PATH."""
        if shutil.which("lake") is not None:
            return "lake"
        elan_lake = Path.home() / ".elan" / "bin" / "lake"
        if elan_lake.is_file():
            return str(elan_lake)
        return "lake"

    def verify_code(self, *, lean_code: str, node_id: str, attempt_number: int) -> VerificationResult:
        """Write Lean code into the workspace and verify it locally."""

        progress(f"running local Lean verification for node {node_id} (attempt {attempt_number})")
        workspace_root = self.workspace.root.resolve()
        scratch_path = self.workspace.write_scratch_file(
            node_id=node_id,
            attempt_number=attempt_number,
            lean_code=lean_code,
        ).resolve()
        command = [self._lake_executable(), "env", "lean", str(scratch_path)]
        display_command = [
            self._lake_executable(),
            "env",
            "lean",
            self._repo_relative_path(scratch_path, workspace_root),
        ]

        start = time.monotonic()
        try:
            completed = self.command_runner(
                command,
                capture_output=True,
                text=True,
                cwd=workspace_root,
                check=False,
                timeout=self.timeout_seconds,
            )
            elapsed_seconds = time.monotonic() - start
        except subprocess.TimeoutExpired as exc:
            elapsed_seconds = time.monotonic() - start
            result = VerificationResult(
                status="failed",
                command=" ".join(command),
                exit_code=None,
                stdout=exc.stdout or "",
                stderr=(
                    f"Lean verification timed out after {self.timeout_seconds} seconds."
                    + (f"\n{exc.stderr}" if exc.stderr else "")
                ),
                elapsed_seconds=elapsed_seconds,
                attempt_count=attempt_number,
                artifact_path=str(scratch_path),
            )
            progress(
                f"finished local Lean verification for node {node_id} (attempt {attempt_number}) "
                f"with status {result.status} after timeout"
            )
            return result

        result = VerificationResult(
            status="verified" if completed.returncode == 0 else "failed",
            command=" ".join(display_command),
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            elapsed_seconds=elapsed_seconds,
            attempt_count=attempt_number,
            artifact_path=str(scratch_path),
        )
        progress(
            f"finished local Lean verification for node {node_id} (attempt {attempt_number}) "
            f"with status {result.status}"
        )
        return result

    def verify_existing_file(self, *, file_path: Path, attempt_number: int) -> VerificationResult:
        """Verify an existing Lean scratch file without rewriting it."""

        workspace_root = self.workspace.root.resolve()
        resolved_path = file_path.resolve()
        progress(
            f"running local Lean verification for {resolved_path} (attempt {attempt_number})"
        )
        command = [self._lake_executable(), "env", "lean", str(resolved_path)]
        display_command = [
            self._lake_executable(),
            "env",
            "lean",
            self._repo_relative_path(resolved_path, workspace_root),
        ]

        start = time.monotonic()
        try:
            completed = self.command_runner(
                command,
                capture_output=True,
                text=True,
                cwd=workspace_root,
                check=False,
                timeout=self.timeout_seconds,
            )
            elapsed_seconds = time.monotonic() - start
        except subprocess.TimeoutExpired as exc:
            elapsed_seconds = time.monotonic() - start
            result = VerificationResult(
                status="failed",
                command=" ".join(command),
                exit_code=None,
                stdout=exc.stdout or "",
                stderr=(
                    f"Lean verification timed out after {self.timeout_seconds} seconds."
                    + (f"\n{exc.stderr}" if exc.stderr else "")
                ),
                elapsed_seconds=elapsed_seconds,
                attempt_count=attempt_number,
                artifact_path=str(resolved_path),
            )
            progress(
                f"finished local Lean verification for {resolved_path} (attempt {attempt_number}) "
                f"with status {result.status} after timeout"
            )
            return result

        result = VerificationResult(
            status="verified" if completed.returncode == 0 else "failed",
            command=" ".join(display_command),
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            elapsed_seconds=elapsed_seconds,
            attempt_count=attempt_number,
            artifact_path=str(resolved_path),
        )
        progress(
            f"finished local Lean verification for {resolved_path} (attempt {attempt_number}) "
            f"with status {result.status}"
        )
        return result

    @staticmethod
    def _repo_relative_path(path: Path, workspace_root: Path) -> str:
        """Render a path relative to the repo root for public-facing display."""

        try:
            relative_to_workspace = path.relative_to(workspace_root)
        except ValueError:
            return path.as_posix()
        return (Path("lean_project") / relative_to_workspace).as_posix()
