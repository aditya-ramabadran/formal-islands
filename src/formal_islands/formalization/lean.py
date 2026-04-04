"""Lean workspace management and local verification helpers."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from formal_islands.models import VerificationResult


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
        scratch_path = self.generated_dir / f"{safe_node_id}_attempt_{attempt_number}.lean"
        scratch_path.write_text(lean_code, encoding="utf-8")
        return scratch_path


@dataclass(frozen=True)
class LeanVerifier:
    """Deterministic local wrapper around `lake env lean`."""

    workspace: LeanWorkspace
    timeout_seconds: float | None = 120.0
    command_runner: CommandRunner = subprocess.run

    def verify_code(self, *, lean_code: str, node_id: str, attempt_number: int) -> VerificationResult:
        """Write Lean code into the workspace and verify it locally."""

        workspace_root = self.workspace.root.resolve()
        scratch_path = self.workspace.write_scratch_file(
            node_id=node_id,
            attempt_number=attempt_number,
            lean_code=lean_code,
        ).resolve()
        command = ["lake", "env", "lean", str(scratch_path)]

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
            return VerificationResult(
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

        return VerificationResult(
            status="verified" if completed.returncode == 0 else "failed",
            command=" ".join(command),
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            elapsed_seconds=elapsed_seconds,
            attempt_count=attempt_number,
            artifact_path=str(scratch_path),
        )
