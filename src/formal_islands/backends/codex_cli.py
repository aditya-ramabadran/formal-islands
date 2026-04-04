"""Minimal Codex CLI subprocess backend."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from formal_islands.backends.base import (
    BackendInvocationError,
    BackendOutputError,
    BackendUnavailableError,
    StructuredBackendRequest,
    StructuredBackendResponse,
)


@dataclass(frozen=True)
class CodexCLIBackend:
    """One-shot structured-output adapter for the local `codex` CLI."""

    executable: str = "codex"
    model: str | None = None
    sandbox: str = "read-only"
    use_ephemeral_session: bool = True
    timeout_seconds: float | None = 180.0
    log_dir: Path | None = None

    def run_structured(self, request: StructuredBackendRequest) -> StructuredBackendResponse:
        executable_path = shutil.which(self.executable)
        if executable_path is None:
            raise BackendUnavailableError(
                "Codex CLI is not available on PATH. Install `codex` separately to use this backend."
            )

        self._ensure_auth_available()

        with tempfile.TemporaryDirectory(prefix="formal-islands-codex-") as temp_dir:
            temp_path = Path(temp_dir)
            schema_path = temp_path / "schema.json"
            output_path = temp_path / "output.json"
            schema_path.write_text(
                json.dumps(self._normalize_schema_for_codex(request.json_schema)),
                encoding="utf-8",
            )

            command = [
                self.executable,
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                self.sandbox,
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
            ]
            if self.model:
                command.extend(["--model", self.model])
            if self.use_ephemeral_session:
                command.append("--ephemeral")
            rendered_prompt = self._render_prompt(request)
            command.append(rendered_prompt)
            log_path = self._prepare_log_path(request.task_name)
            self._write_log(
                log_path,
                {
                    "status": "started",
                    "task_name": request.task_name,
                    "backend_name": "codex_cli",
                    "command": command,
                    "cwd": str(request.cwd) if request.cwd else None,
                    "system_prompt": request.system_prompt,
                    "prompt": request.prompt,
                    "rendered_prompt": rendered_prompt,
                    "json_schema": self._normalize_schema_for_codex(request.json_schema),
                    "model": self.model,
                    "sandbox": self.sandbox,
                    "use_ephemeral_session": self.use_ephemeral_session,
                    "timeout_seconds": self.timeout_seconds,
                    "started_at_epoch_seconds": time.time(),
                },
            )
            started_at = time.time()

            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    cwd=request.cwd,
                    check=False,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                self._write_log(
                    log_path,
                    {
                        "status": "timeout",
                        "task_name": request.task_name,
                        "backend_name": "codex_cli",
                        "command": command,
                        "cwd": str(request.cwd) if request.cwd else None,
                        "system_prompt": request.system_prompt,
                        "prompt": request.prompt,
                        "rendered_prompt": rendered_prompt,
                        "json_schema": self._normalize_schema_for_codex(request.json_schema),
                        "model": self.model,
                        "sandbox": self.sandbox,
                        "use_ephemeral_session": self.use_ephemeral_session,
                        "timeout_seconds": self.timeout_seconds,
                        "started_at_epoch_seconds": started_at,
                        "elapsed_seconds": time.time() - started_at,
                        "error": (
                            "Codex CLI timed out while waiting for structured output "
                            f"for task '{request.task_name}' after {self.timeout_seconds} seconds."
                        ),
                    },
                )
                raise BackendInvocationError(
                    "Codex CLI timed out while waiting for structured output "
                    f"for task '{request.task_name}' after {self.timeout_seconds} seconds."
                ) from exc
            if completed.returncode != 0:
                self._write_log(
                    log_path,
                    {
                        "status": "failed",
                        "task_name": request.task_name,
                        "backend_name": "codex_cli",
                        "command": command,
                        "cwd": str(request.cwd) if request.cwd else None,
                        "system_prompt": request.system_prompt,
                        "prompt": request.prompt,
                        "rendered_prompt": rendered_prompt,
                        "json_schema": self._normalize_schema_for_codex(request.json_schema),
                        "model": self.model,
                        "sandbox": self.sandbox,
                        "use_ephemeral_session": self.use_ephemeral_session,
                        "timeout_seconds": self.timeout_seconds,
                        "started_at_epoch_seconds": started_at,
                        "elapsed_seconds": time.time() - started_at,
                        "exit_code": completed.returncode,
                        "raw_stdout": completed.stdout,
                        "raw_stderr": completed.stderr,
                        "error": f"Codex CLI failed with exit code {completed.returncode}: {completed.stderr.strip()}",
                    },
                )
                raise BackendInvocationError(
                    f"Codex CLI failed with exit code {completed.returncode}: {completed.stderr.strip()}"
                )

            if not output_path.exists():
                raise BackendOutputError("Codex CLI did not write the structured output file")

            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise BackendOutputError("Codex CLI output file did not contain valid JSON") from exc

        if not isinstance(payload, dict):
            raise BackendOutputError("Codex CLI structured output must be a JSON object")

        self._write_log(
            log_path,
            {
                "status": "completed",
                "task_name": request.task_name,
                "backend_name": "codex_cli",
                "command": command,
                "cwd": str(request.cwd) if request.cwd else None,
                "system_prompt": request.system_prompt,
                "prompt": request.prompt,
                "rendered_prompt": rendered_prompt,
                "json_schema": self._normalize_schema_for_codex(request.json_schema),
                "model": self.model,
                "sandbox": self.sandbox,
                "use_ephemeral_session": self.use_ephemeral_session,
                "timeout_seconds": self.timeout_seconds,
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
            backend_name="codex_cli",
            metadata={"executable_path": executable_path},
        )

    def _prepare_log_path(self, task_name: str) -> Path | None:
        if self.log_dir is None:
            return None
        self.log_dir.mkdir(parents=True, exist_ok=True)
        return self.log_dir / f"{task_name}_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}.json"

    @staticmethod
    def _write_log(path: Path | None, payload: dict) -> None:
        if path is None:
            return
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _render_prompt(request: StructuredBackendRequest) -> str:
        if not request.system_prompt:
            return request.prompt
        return "\n\n".join(
            [
                "System instructions:",
                request.system_prompt,
                "User task:",
                request.prompt,
            ]
        )

    @staticmethod
    def _ensure_auth_available() -> None:
        if os.environ.get("CODEX_API_KEY"):
            return

        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        auth_path = codex_home / "auth.json"
        if auth_path.exists():
            return

        raise BackendUnavailableError(
            "Codex CLI auth was not found. Run `codex` and choose 'Sign in with ChatGPT', "
            "or set CODEX_API_KEY for non-interactive runs."
        )

    @classmethod
    def _normalize_schema_for_codex(cls, schema: dict) -> dict:
        """Adjust JSON Schema to match Codex CLI's stricter response-format expectations."""

        normalized = json.loads(json.dumps(schema))
        return cls._normalize_schema_node(normalized)

    @classmethod
    def _normalize_schema_node(cls, node: object) -> object:
        if isinstance(node, dict):
            normalized = {key: cls._normalize_schema_node(value) for key, value in node.items()}

            properties = normalized.get("properties")
            if isinstance(properties, dict):
                normalized["required"] = list(properties.keys())
                if "additionalProperties" not in normalized:
                    normalized["additionalProperties"] = False

            return normalized

        if isinstance(node, list):
            return [cls._normalize_schema_node(item) for item in node]

        return node
