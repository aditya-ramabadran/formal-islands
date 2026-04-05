"""Backend adapters for local model CLIs."""

from formal_islands.backends.base import (
    AgenticStructuredBackend,
    BackendError,
    BackendInvocationError,
    BackendOutputError,
    BackendUnavailableError,
    StructuredBackend,
    StructuredBackendRequest,
    StructuredBackendResponse,
)
from formal_islands.backends.claude_code import ClaudeCodeBackend
from formal_islands.backends.codex_cli import CodexCLIBackend
from formal_islands.backends.gemini_cli import GeminiCLIBackend
from formal_islands.backends.mock import MockBackend

__all__ = [
    "BackendError",
    "BackendInvocationError",
    "BackendOutputError",
    "BackendUnavailableError",
    "ClaudeCodeBackend",
    "CodexCLIBackend",
    "GeminiCLIBackend",
    "MockBackend",
    "AgenticStructuredBackend",
    "StructuredBackend",
    "StructuredBackendRequest",
    "StructuredBackendResponse",
]
