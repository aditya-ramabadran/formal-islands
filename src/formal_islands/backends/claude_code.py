"""Claude Code subprocess backend with structured and agentic modes."""

from __future__ import annotations

import json
import os
import shutil
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
from formal_islands.backends._streaming import run_streaming_command


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

        command = [
            str(executable_path),
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--input-format",
            "text",
        ]
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

        stream_result = run_streaming_command(
            command,
            input_text=request.prompt,
            cwd=request.cwd,
            env=env,
            timeout_seconds=timeout_seconds,
        )

        stream_events = self._build_stream_events(stream_result.stdout_lines)

        if stream_result.timed_out:
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
                    "stream_events": stream_events,
                    "raw_stdout": stream_result.raw_stdout,
                    "raw_stderr": stream_result.raw_stderr,
                    "error": (
                        "Claude CLI timed out while waiting for structured output "
                        f"for task '{request.task_name}' after {timeout_seconds} seconds."
                    ),
                },
            )
            raise BackendInvocationError(
                "Claude CLI timed out while waiting for structured output "
                f"for task '{request.task_name}' after {timeout_seconds} seconds."
            )

        if stream_result.returncode != 0:
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
                    "exit_code": stream_result.returncode,
                    "stream_events": stream_events,
                    "raw_stdout": stream_result.raw_stdout,
                    "raw_stderr": stream_result.raw_stderr,
                    "error": (
                        f"Claude CLI failed with exit code {stream_result.returncode}: "
                        f"{stream_result.raw_stderr.strip()}"
                    ),
                },
            )
            raise BackendInvocationError(
                f"Claude CLI failed with exit code {stream_result.returncode}: {stream_result.raw_stderr.strip()}"
            )

        try:
            payload = self._parse_stream_payload(stream_result.stdout_lines)
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
                    "exit_code": stream_result.returncode,
                    "stream_events": stream_events,
                    "raw_stdout": stream_result.raw_stdout,
                    "raw_stderr": stream_result.raw_stderr,
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
                "exit_code": stream_result.returncode,
                "stream_events": stream_events,
                "raw_stdout": stream_result.raw_stdout,
                "raw_stderr": stream_result.raw_stderr,
                "payload": payload,
            },
        )

        return StructuredBackendResponse(
            payload=payload,
            raw_stdout=stream_result.raw_stdout,
            raw_stderr=stream_result.raw_stderr,
            command=tuple(command),
            exit_code=stream_result.returncode,
            backend_name="claude_code",
            metadata={
                "executable_path": str(executable_path),
                "stream_events": stream_events,
            },
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

    @classmethod
    def _parse_stream_payload(cls, stdout_lines: list[str]) -> dict[str, Any]:
        assembled_chunks: list[str] = []

        for line in stdout_lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue

            payload = cls._extract_payload(event)
            if payload is not None:
                return payload

            assembled_chunks.extend(cls._extract_text_chunks(event))

        if assembled_chunks:
            reconstructed = "".join(assembled_chunks).strip()
            if reconstructed:
                payload = cls._parse_payload_text(reconstructed)
                if payload is not None:
                    return payload

        joined = "".join(stdout_lines)
        return cls._parse_payload_text(joined)

    @classmethod
    def _parse_payload_text(cls, stdout: str) -> dict[str, Any]:
        try:
            raw = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise BackendOutputError("Claude CLI returned invalid JSON") from exc

        payload = cls._extract_payload(raw)
        if payload is None:
            raise BackendOutputError("Claude CLI output did not include structured output")

        return payload

    @classmethod
    def _extract_payload(cls, value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            if "structured_output" in value:
                payload = value["structured_output"]
                if isinstance(payload, dict):
                    return payload
                raise BackendOutputError("Claude CLI structured output must be a JSON object")

            if "result" in value:
                payload = cls._decode_json_candidate(value["result"])
                if payload is None:
                    raise BackendOutputError("Claude CLI result field did not contain valid JSON")
                if isinstance(payload, dict):
                    return cls._extract_payload(payload) or payload
                raise BackendOutputError("Claude CLI structured output must be a JSON object")

            if "response" in value:
                payload = cls._decode_json_candidate(value["response"])
                if payload is None:
                    raise BackendOutputError("Claude CLI response field did not contain valid JSON")
                if isinstance(payload, dict):
                    return cls._extract_payload(payload) or payload
                raise BackendOutputError("Claude CLI structured output must be a JSON object")

            if not cls._looks_like_stream_event(value):
                return value
            return None

        if isinstance(value, str):
            payload = cls._decode_json_candidate(value)
            if isinstance(payload, dict):
                return cls._extract_payload(payload) or payload
            return None

        return None

    @staticmethod
    def _decode_json_candidate(value: Any) -> Any | None:
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _looks_like_stream_event(value: dict[str, Any]) -> bool:
        return any(key in value for key in ("type", "delta", "message", "content_block"))

    @classmethod
    def _extract_text_chunks(cls, event: Any) -> list[str]:
        if not isinstance(event, dict):
            return []

        chunks: list[str] = []
        for key in ("text", "partial_json"):
            value = event.get(key)
            if isinstance(value, str) and value:
                chunks.append(value)

        delta = event.get("delta")
        if isinstance(delta, dict):
            for key in ("text", "partial_json"):
                value = delta.get(key)
                if isinstance(value, str) and value:
                    chunks.append(value)

        content = event.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text:
                        chunks.append(text)

        message = event.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text")
                        if isinstance(text, str) and text:
                            chunks.append(text)

        return chunks

    @classmethod
    def _build_stream_events(cls, stdout_lines: list[str]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for index, line in enumerate(stdout_lines, start=1):
            stripped = line.strip()
            parsed: Any | None
            if stripped:
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    parsed = None
            else:
                parsed = None

            events.append(
                {
                    "line_number": index,
                    "raw": line.rstrip("\n"),
                    "event": parsed,
                }
            )
        return events
