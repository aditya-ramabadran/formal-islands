"""Claude Code subprocess backend with structured and agentic modes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from formal_islands.backends.base import (
    BackendInvocationError,
    BackendOutputError,
    BackendUnavailableError,
    StructuredBackendRequest,
    StructuredBackendResponse,
)


@dataclass(frozen=True)
class ClaudeCodeBackend:
    """One-shot structured-output adapter for the local `claude` CLI."""

    executable: str = "claude"
    model: str | None = None
    max_output_tokens: int | None = None
    effort: str | None = None
    timeout_seconds: float | None = 180.0
    log_dir: Path | None = None
    use_no_session_persistence: bool = True

    def run_structured(self, request: StructuredBackendRequest) -> StructuredBackendResponse:
        return self._run_claude_print(
            request=request,
            timeout_seconds=self.timeout_seconds,
            agentic=False,
        )

    def run_agentic_structured(
        self,
        request: StructuredBackendRequest,
        *,
        timeout_seconds: float | None = None,
    ) -> StructuredBackendResponse:
        return self._run_claude_print(
            request=request,
            timeout_seconds=timeout_seconds if timeout_seconds is not None else self.timeout_seconds,
            agentic=True,
        )

    def _run_claude_print(
        self,
        *,
        request: StructuredBackendRequest,
        timeout_seconds: float | None,
        agentic: bool,
    ) -> StructuredBackendResponse:
        executable_path = self._resolve_executable()
        if executable_path is None:
            raise BackendUnavailableError(
                "Claude Code CLI is not available on PATH. Install `claude` separately to use this backend."
            )

        command = [str(executable_path), "-p", "--output-format", "json", "--input-format", "text"]
        if self.use_no_session_persistence:
            command.append("--no-session-persistence")
        if self.model:
            command.extend(["--model", self.model])
        if request.system_prompt:
            command.extend(["--system-prompt", request.system_prompt])
        if agentic:
            command.extend(
                [
                    "--tools",
                    "default",
                    "--permission-mode",
                    "bypassPermissions",
                    "--dangerously-skip-permissions",
                    "--setting-sources",
                    "",
                ]
            )
        else:
            command.extend(["--tools", ""])
        command.extend(["--json-schema", json.dumps(request.json_schema)])

        env = os.environ.copy()
        if self.max_output_tokens is not None:
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(self.max_output_tokens)
        if self.effort is not None:
            env["CLAUDE_CODE_EFFORT_LEVEL"] = self.effort

        log_path = self._prepare_log_path(request.task_name)
        started_at = time.time()
        self._write_log(
            log_path,
            {
                "status": "started",
                "task_name": request.task_name,
                "backend_name": "claude_code",
                "command": command,
                "cwd": str(request.cwd) if request.cwd else None,
                "system_prompt": request.system_prompt,
                "prompt": request.prompt,
                "json_schema": request.json_schema,
                "model": self.model,
                "max_output_tokens": self.max_output_tokens,
                "effort": self.effort,
                "agentic": agentic,
                "timeout_seconds": timeout_seconds,
                "started_at_epoch_seconds": started_at,
            },
        )

        try:
            completed = subprocess.run(
                command,
                input=request.prompt,
                capture_output=True,
                text=True,
                cwd=request.cwd,
                env=env,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            self._write_log(
                log_path,
                {
                    "status": "timeout",
                    "task_name": request.task_name,
                    "backend_name": "claude_code",
                    "command": command,
                    "cwd": str(request.cwd) if request.cwd else None,
                    "system_prompt": request.system_prompt,
                    "prompt": request.prompt,
                    "json_schema": request.json_schema,
                    "model": self.model,
                    "max_output_tokens": self.max_output_tokens,
                    "effort": self.effort,
                    "agentic": agentic,
                    "timeout_seconds": timeout_seconds,
                    "started_at_epoch_seconds": started_at,
                    "elapsed_seconds": time.time() - started_at,
                    "error": (
                        "Claude CLI timed out while waiting for structured output "
                        f"for task '{request.task_name}' after {timeout_seconds} seconds."
                    ),
                },
            )
            raise BackendInvocationError(
                "Claude CLI timed out while waiting for structured output "
                f"for task '{request.task_name}' after {timeout_seconds} seconds."
            ) from exc

        if completed.returncode != 0:
            self._write_log(
                log_path,
                {
                    "status": "failed",
                    "task_name": request.task_name,
                    "backend_name": "claude_code",
                    "command": command,
                    "cwd": str(request.cwd) if request.cwd else None,
                    "system_prompt": request.system_prompt,
                    "prompt": request.prompt,
                    "json_schema": request.json_schema,
                    "model": self.model,
                    "max_output_tokens": self.max_output_tokens,
                    "effort": self.effort,
                    "agentic": agentic,
                    "timeout_seconds": timeout_seconds,
                    "started_at_epoch_seconds": started_at,
                    "elapsed_seconds": time.time() - started_at,
                    "exit_code": completed.returncode,
                    "raw_stdout": completed.stdout,
                    "raw_stderr": completed.stderr,
                    "error": f"Claude CLI failed with exit code {completed.returncode}: {completed.stderr.strip()}",
                },
            )
            raise BackendInvocationError(
                f"Claude CLI failed with exit code {completed.returncode}: {completed.stderr.strip()}"
            )

        try:
            payload = self._parse_payload(completed.stdout)
        except BackendOutputError as exc:
            self._write_log(
                log_path,
                {
                    "status": "failed",
                    "task_name": request.task_name,
                    "backend_name": "claude_code",
                    "command": command,
                    "cwd": str(request.cwd) if request.cwd else None,
                    "system_prompt": request.system_prompt,
                    "prompt": request.prompt,
                    "json_schema": request.json_schema,
                    "model": self.model,
                    "max_output_tokens": self.max_output_tokens,
                    "effort": self.effort,
                    "agentic": agentic,
                    "timeout_seconds": timeout_seconds,
                    "started_at_epoch_seconds": started_at,
                    "elapsed_seconds": time.time() - started_at,
                    "exit_code": completed.returncode,
                    "raw_stdout": completed.stdout,
                    "raw_stderr": completed.stderr,
                    "error": str(exc),
                },
            )
            raise

        self._write_log(
            log_path,
            {
                "status": "completed",
                "task_name": request.task_name,
                "backend_name": "claude_code",
                "command": command,
                "cwd": str(request.cwd) if request.cwd else None,
                "system_prompt": request.system_prompt,
                "prompt": request.prompt,
                "json_schema": request.json_schema,
                "model": self.model,
                "max_output_tokens": self.max_output_tokens,
                "effort": self.effort,
                "agentic": agentic,
                "timeout_seconds": timeout_seconds,
                "started_at_epoch_seconds": started_at,
                "elapsed_seconds": time.time() - started_at,
                "exit_code": completed.returncode,
                "raw_stdout": completed.stdout,
                "raw_stderr": completed.stderr,
                "payload": payload,
            },
        )

        return StructuredBackendResponse(
            payload=payload,
            raw_stdout=completed.stdout,
            raw_stderr=completed.stderr,
            command=tuple(command),
            exit_code=completed.returncode,
            backend_name="claude_code",
            metadata={"executable_path": str(executable_path)},
        )

    def _resolve_executable(self) -> Path | None:
        if "/" in self.executable:
            candidate = Path(self.executable).expanduser()
            return candidate if candidate.exists() and os.access(candidate, os.X_OK) else None

        resolved = shutil.which(self.executable)
        if resolved is not None:
            return Path(resolved)

        for fallback in (
            Path.home() / ".local" / "bin" / self.executable,
            Path("/opt/homebrew/bin") / self.executable,
            Path("/usr/local/bin") / self.executable,
        ):
            if fallback.exists() and os.access(fallback, os.X_OK):
                return fallback
        return None

    def _prepare_log_path(self, task_name: str) -> Path | None:
        if self.log_dir is None:
            return None
        self.log_dir.mkdir(parents=True, exist_ok=True)
        return self.log_dir / f"{task_name}_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}.json"

    @staticmethod
    def _write_log(path: Path | None, payload: dict[str, Any]) -> None:
        if path is None:
            return
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _parse_payload(stdout: str) -> dict[str, Any]:
        try:
            raw = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise BackendOutputError("Claude CLI returned invalid JSON") from exc

        if "structured_output" in raw:
            payload = raw["structured_output"]
        elif "result" in raw:
            try:
                payload = json.loads(raw["result"])
            except json.JSONDecodeError as exc:
                raise BackendOutputError("Claude CLI result field did not contain valid JSON") from exc
        else:
            raise BackendOutputError("Claude CLI output did not include structured output")

        if not isinstance(payload, dict):
            raise BackendOutputError("Claude CLI structured output must be a JSON object")
        return payload
