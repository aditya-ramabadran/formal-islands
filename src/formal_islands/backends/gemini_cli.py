"""Gemini CLI subprocess backend with structured and agentic modes."""

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

from formal_islands.backends._streaming import run_streaming_command
from formal_islands.backends.base import (
    BackendInvocationError,
    BackendOutputError,
    BackendUnavailableError,
    StructuredBackendRequest,
    StructuredBackendResponse,
)

GEMINI_AGENTIC_FORMALIZATION_PROMPT_ADDITION = (
    "Gemini-specific agentic guidance: no sorry, no admits, no TODOs, and no unfinished code. "
    "Prefer a smaller concrete but still nontrivial theorem if the full target is too ambitious, "
    "and make the Lean file compile cleanly before returning."
)


@dataclass(frozen=True)
class GeminiCLIBackend:
    """One-shot structured-output adapter for the local `gemini` CLI."""

    executable: str = "gemini"
    model: str | None = None
    timeout_seconds: float | None = 180.0
    log_dir: Path | None = None
    use_yolo: bool = False
    approval_mode: str | None = "auto_edit"

    def run_structured(self, request: StructuredBackendRequest) -> StructuredBackendResponse:
        return self._run_gemini_prompt(
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
        return self._run_gemini_prompt(
            request=request,
            timeout_seconds=timeout_seconds if timeout_seconds is not None else self.timeout_seconds,
            agentic=True,
        )

    def _run_gemini_prompt(
        self,
        *,
        request: StructuredBackendRequest,
        timeout_seconds: float | None,
        agentic: bool,
    ) -> StructuredBackendResponse:
        executable_path = self._resolve_executable()
        if executable_path is None:
            raise BackendUnavailableError(
                "Gemini CLI is not available on PATH. Install `gemini` separately to use this backend."
            )

        rendered_prompt = self._render_prompt(request, agentic=agentic)
        command = [str(executable_path), "-p", rendered_prompt]
        if self.model:
            command.extend(["--model", self.model])
        command.extend(["--output-format", "stream-json" if agentic else "json"])
        effective_approval_mode = self._approval_mode_for_request(request, agentic=agentic)
        if agentic:
            if effective_approval_mode is not None:
                command.extend(["--approval-mode", effective_approval_mode])

        env = os.environ.copy()
        log_path = self._prepare_log_path(request.task_name)
        started_at = time.time()
        self._write_log(
            log_path,
            {
                "status": "started",
                "task_name": request.task_name,
                "backend_name": "gemini_cli",
                "command": command,
                "cwd": str(request.cwd) if request.cwd else None,
                "system_prompt": request.system_prompt,
                "prompt": request.prompt,
                "rendered_prompt": rendered_prompt,
                "json_schema": request.json_schema,
                "model": self.model,
                "agentic": agentic,
                "use_yolo": self.use_yolo,
                "approval_mode": effective_approval_mode,
                "timeout_seconds": timeout_seconds,
                "started_at_epoch_seconds": started_at,
            },
        )

        if agentic:
            stream_result = run_streaming_command(
                command,
                input_text="",
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
                        "backend_name": "gemini_cli",
                        "command": command,
                        "cwd": str(request.cwd) if request.cwd else None,
                        "system_prompt": request.system_prompt,
                        "prompt": request.prompt,
                        "rendered_prompt": rendered_prompt,
                        "json_schema": request.json_schema,
                        "model": self.model,
                        "agentic": agentic,
                        "use_yolo": self.use_yolo,
                        "approval_mode": effective_approval_mode,
                        "timeout_seconds": timeout_seconds,
                        "started_at_epoch_seconds": started_at,
                        "elapsed_seconds": time.time() - started_at,
                        "stream_events": stream_events,
                        "raw_stdout": stream_result.raw_stdout,
                        "raw_stderr": stream_result.raw_stderr,
                        "error": (
                            "Gemini CLI timed out while waiting for structured output "
                            f"for task '{request.task_name}' after {timeout_seconds} seconds."
                        ),
                    },
                )
                raise BackendInvocationError(
                    "Gemini CLI timed out while waiting for structured output "
                    f"for task '{request.task_name}' after {timeout_seconds} seconds."
                )

            if stream_result.returncode != 0:
                self._write_log(
                    log_path,
                    {
                        "status": "failed",
                        "task_name": request.task_name,
                        "backend_name": "gemini_cli",
                        "command": command,
                        "cwd": str(request.cwd) if request.cwd else None,
                        "system_prompt": request.system_prompt,
                        "prompt": request.prompt,
                        "rendered_prompt": rendered_prompt,
                        "json_schema": request.json_schema,
                        "model": self.model,
                        "agentic": agentic,
                        "use_yolo": self.use_yolo,
                        "approval_mode": effective_approval_mode,
                        "timeout_seconds": timeout_seconds,
                        "started_at_epoch_seconds": started_at,
                        "elapsed_seconds": time.time() - started_at,
                        "exit_code": stream_result.returncode,
                        "stream_events": stream_events,
                        "raw_stdout": stream_result.raw_stdout,
                        "raw_stderr": stream_result.raw_stderr,
                        "error": (
                            f"Gemini CLI failed with exit code {stream_result.returncode}: "
                            f"{stream_result.raw_stderr.strip()}"
                        ),
                    },
                )
                raise BackendInvocationError(
                    f"Gemini CLI failed with exit code {stream_result.returncode}: "
                    f"{stream_result.raw_stderr.strip()}"
                )

            try:
                payload, response_wrapper = self._parse_streamed_response(stream_result.stdout_lines)
            except BackendOutputError as exc:
                self._write_log(
                    log_path,
                    {
                        "status": "failed",
                        "task_name": request.task_name,
                        "backend_name": "gemini_cli",
                        "command": command,
                        "cwd": str(request.cwd) if request.cwd else None,
                        "system_prompt": request.system_prompt,
                        "prompt": request.prompt,
                        "rendered_prompt": rendered_prompt,
                        "json_schema": request.json_schema,
                        "model": self.model,
                        "agentic": agentic,
                        "use_yolo": self.use_yolo,
                        "approval_mode": effective_approval_mode,
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
                    "backend_name": "gemini_cli",
                    "command": command,
                    "cwd": str(request.cwd) if request.cwd else None,
                    "system_prompt": request.system_prompt,
                    "prompt": request.prompt,
                    "rendered_prompt": rendered_prompt,
                    "json_schema": request.json_schema,
                    "model": self.model,
                    "agentic": agentic,
                    "use_yolo": self.use_yolo,
                    "approval_mode": effective_approval_mode,
                    "timeout_seconds": timeout_seconds,
                    "started_at_epoch_seconds": started_at,
                    "elapsed_seconds": time.time() - started_at,
                    "exit_code": stream_result.returncode,
                    "stream_events": stream_events,
                    "raw_stdout": stream_result.raw_stdout,
                    "raw_stderr": stream_result.raw_stderr,
                    "payload": payload,
                    "response_wrapper": response_wrapper,
                },
            )

            return StructuredBackendResponse(
                payload=payload,
                raw_stdout=stream_result.raw_stdout,
                raw_stderr=stream_result.raw_stderr,
                command=tuple(command),
                exit_code=stream_result.returncode,
                backend_name="gemini_cli",
                metadata={
                    "executable_path": str(executable_path),
                    "stream_events": stream_events,
                    "response_wrapper": response_wrapper,
                },
            )

        try:
            completed = subprocess.run(
                command,
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
                    "backend_name": "gemini_cli",
                    "command": command,
                    "cwd": str(request.cwd) if request.cwd else None,
                    "system_prompt": request.system_prompt,
                    "prompt": request.prompt,
                    "rendered_prompt": rendered_prompt,
                    "json_schema": request.json_schema,
                    "model": self.model,
                    "agentic": agentic,
                    "use_yolo": self.use_yolo,
                    "approval_mode": effective_approval_mode,
                    "timeout_seconds": timeout_seconds,
                    "started_at_epoch_seconds": started_at,
                    "elapsed_seconds": time.time() - started_at,
                    "raw_stdout": exc.stdout or "",
                    "raw_stderr": exc.stderr or "",
                    "error": (
                        "Gemini CLI timed out while waiting for structured output "
                        f"for task '{request.task_name}' after {timeout_seconds} seconds."
                    ),
                },
            )
            raise BackendInvocationError(
                "Gemini CLI timed out while waiting for structured output "
                f"for task '{request.task_name}' after {timeout_seconds} seconds."
            ) from exc

        if completed.returncode != 0:
            self._write_log(
                log_path,
                {
                    "status": "failed",
                    "task_name": request.task_name,
                    "backend_name": "gemini_cli",
                    "command": command,
                    "cwd": str(request.cwd) if request.cwd else None,
                    "system_prompt": request.system_prompt,
                    "prompt": request.prompt,
                    "rendered_prompt": rendered_prompt,
                    "json_schema": request.json_schema,
                    "model": self.model,
                    "agentic": agentic,
                    "use_yolo": self.use_yolo,
                    "approval_mode": effective_approval_mode,
                    "timeout_seconds": timeout_seconds,
                    "started_at_epoch_seconds": started_at,
                    "elapsed_seconds": time.time() - started_at,
                    "exit_code": completed.returncode,
                    "raw_stdout": completed.stdout,
                    "raw_stderr": completed.stderr,
                    "error": f"Gemini CLI failed with exit code {completed.returncode}: {completed.stderr.strip()}",
                },
            )
            raise BackendInvocationError(
                f"Gemini CLI failed with exit code {completed.returncode}: {completed.stderr.strip()}"
            )

        try:
            payload, response_wrapper = self._parse_json_response(completed.stdout)
        except BackendOutputError as exc:
            self._write_log(
                log_path,
                {
                    "status": "failed",
                    "task_name": request.task_name,
                    "backend_name": "gemini_cli",
                    "command": command,
                    "cwd": str(request.cwd) if request.cwd else None,
                    "system_prompt": request.system_prompt,
                    "prompt": request.prompt,
                    "rendered_prompt": rendered_prompt,
                    "json_schema": request.json_schema,
                    "model": self.model,
                    "agentic": agentic,
                    "use_yolo": self.use_yolo,
                    "approval_mode": effective_approval_mode,
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
                "backend_name": "gemini_cli",
                "command": command,
                "cwd": str(request.cwd) if request.cwd else None,
                "system_prompt": request.system_prompt,
                "prompt": request.prompt,
                "rendered_prompt": rendered_prompt,
                "json_schema": request.json_schema,
                "model": self.model,
                "agentic": agentic,
                "use_yolo": self.use_yolo,
                "approval_mode": effective_approval_mode,
                "timeout_seconds": timeout_seconds,
                "started_at_epoch_seconds": started_at,
                "elapsed_seconds": time.time() - started_at,
                "exit_code": completed.returncode,
                "raw_stdout": completed.stdout,
                "raw_stderr": completed.stderr,
                "payload": payload,
                "response_wrapper": response_wrapper,
            },
        )

        return StructuredBackendResponse(
            payload=payload,
            raw_stdout=completed.stdout,
            raw_stderr=completed.stderr,
            command=tuple(command),
            exit_code=completed.returncode,
            backend_name="gemini_cli",
            metadata={
                "executable_path": str(executable_path),
                "response_wrapper": response_wrapper,
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

    @staticmethod
    def _render_prompt(request: StructuredBackendRequest, *, agentic: bool) -> str:
        if not request.system_prompt:
            system_prompt = ""
        else:
            system_prompt = request.system_prompt

        if agentic:
            system_prompt = "\n\n".join(
                part for part in [system_prompt, GEMINI_AGENTIC_FORMALIZATION_PROMPT_ADDITION] if part
            )

        if not system_prompt:
            return request.prompt

        return "\n\n".join(
            [
                "System instructions:",
                system_prompt,
                "User task:",
                request.prompt,
            ]
        )

    def _approval_mode_for_request(
        self,
        request: StructuredBackendRequest,
        *,
        agentic: bool,
    ) -> str | None:
        if not agentic:
            return None
        if request.task_name == "formalize_node_agentic":
            return "yolo"
        if self.approval_mode is not None:
            return self.approval_mode
        if self.use_yolo:
            return "yolo"
        return None

    @classmethod
    def _parse_json_response(cls, stdout: str) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            raw = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise BackendOutputError("Gemini CLI returned invalid JSON") from exc

        return cls._extract_payload_and_wrapper(raw)

    @classmethod
    def _parse_streamed_response(cls, stdout_lines: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
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
                return payload, cls._coerce_wrapper(event)

            assembled_chunks.extend(cls._extract_text_chunks(event))

        if assembled_chunks:
            reconstructed = "".join(assembled_chunks).strip()
            if reconstructed:
                raw = cls._decode_json_candidate(reconstructed)
                if raw is not None:
                    return cls._extract_payload_and_wrapper(raw)

        joined = "".join(stdout_lines)
        try:
            raw = json.loads(joined)
        except json.JSONDecodeError as exc:
            raise BackendOutputError("Gemini CLI stream output did not include valid JSON") from exc
        return cls._extract_payload_and_wrapper(raw)

    @classmethod
    def _extract_payload_and_wrapper(cls, raw: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        wrapper = cls._coerce_wrapper(raw)
        payload = cls._extract_payload(raw)
        if payload is None:
            raise BackendOutputError("Gemini CLI output did not include structured response")
        return payload, wrapper

    @classmethod
    def _coerce_wrapper(cls, raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        raise BackendOutputError("Gemini CLI output must be a JSON object")

    @classmethod
    def _extract_payload(cls, value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            error_value = value.get("error")
            if error_value not in (None, "", False):
                raise BackendInvocationError(f"Gemini CLI reported an error: {error_value}")

            response = value.get("response")
            if response is not None:
                decoded = cls._decode_json_candidate(response)
                if isinstance(decoded, dict):
                    return decoded
                if isinstance(response, dict):
                    return response
                raise BackendOutputError("Gemini CLI response field did not contain a JSON object")

            if not cls._looks_like_stream_event(value):
                return value

            return None

        if isinstance(value, str):
            decoded = cls._decode_json_candidate(value)
            if isinstance(decoded, dict):
                return decoded
            return None

        return None

    @staticmethod
    def _decode_json_candidate(value: Any) -> Any | None:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            inner = stripped.strip("`")
            first_newline = inner.find("\n")
            if first_newline >= 0:
                inner = inner[first_newline + 1 :]
            stripped = inner.strip()
        try:
            return json.loads(stripped)
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
        content = event.get("content")
        if isinstance(content, str) and content:
            chunks.append(content)

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
