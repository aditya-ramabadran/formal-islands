"""Minimal Claude Code subprocess backend."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
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

    model: str | None = None
    max_output_tokens: int | None = None
    effort: str | None = None
    executable: str = "claude"

    def run_structured(self, request: StructuredBackendRequest) -> StructuredBackendResponse:
        executable_path = shutil.which(self.executable)
        if executable_path is None:
            raise BackendUnavailableError(
                "Claude Code CLI is not available on PATH. Install `claude` separately to use this backend."
            )

        command = [self.executable, "-p", "--output-format", "json"]
        if self.model:
            command.extend(["--model", self.model])
        if request.system_prompt:
            command.extend(["--system-prompt", request.system_prompt])
        command.extend(["--tools", ""])
        command.extend(["--json-schema", json.dumps(request.json_schema)])

        env = os.environ.copy()
        if self.max_output_tokens is not None:
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(self.max_output_tokens)
        if self.effort is not None:
            env["CLAUDE_CODE_EFFORT_LEVEL"] = self.effort

        completed = subprocess.run(
            command,
            input=request.prompt,
            capture_output=True,
            text=True,
            cwd=request.cwd,
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            raise BackendInvocationError(
                f"Claude CLI failed with exit code {completed.returncode}: {completed.stderr.strip()}"
            )

        payload = self._parse_payload(completed.stdout)
        return StructuredBackendResponse(
            payload=payload,
            raw_stdout=completed.stdout,
            raw_stderr=completed.stderr,
            command=tuple(command),
            exit_code=completed.returncode,
            backend_name="claude_code",
            metadata={"executable_path": executable_path},
        )

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
