"""Common backend request/response types and error classes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class StructuredBackendRequest:
    """A single structured-output request sent to a backend."""

    prompt: str
    json_schema: dict[str, Any]
    system_prompt: str = ""
    cwd: Path | None = None
    task_name: str = "structured_request"


@dataclass(frozen=True)
class StructuredBackendResponse:
    """Normalized backend output for downstream deterministic stages."""

    payload: dict[str, Any]
    raw_stdout: str
    raw_stderr: str
    command: tuple[str, ...]
    exit_code: int
    backend_name: str
    metadata: dict[str, Any] = field(default_factory=dict)


class BackendError(RuntimeError):
    """Base class for backend adapter failures."""


class BackendUnavailableError(BackendError):
    """Raised when a required local CLI or auth state is missing."""


class BackendInvocationError(BackendError):
    """Raised when a subprocess exits unsuccessfully."""


class BackendOutputError(BackendError):
    """Raised when backend output is missing or malformed."""


class StructuredBackend(Protocol):
    """Narrow interface shared by all local model backends."""

    def run_structured(self, request: StructuredBackendRequest) -> StructuredBackendResponse:
        """Execute a one-shot prompt and return validated JSON-compatible data."""
