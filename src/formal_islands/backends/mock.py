"""Mock backend for deterministic tests."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from formal_islands.backends.base import (
    BackendInvocationError,
    StructuredBackendRequest,
    StructuredBackendResponse,
)


@dataclass
class MockBackend:
    """Queue-backed mock implementation of the backend protocol."""

    queued_payloads: list[dict]
    name: str = "mock"
    requests: list[StructuredBackendRequest] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._payloads = deque(self.queued_payloads)

    def run_structured(self, request: StructuredBackendRequest) -> StructuredBackendResponse:
        self.requests.append(request)
        if not self._payloads:
            raise BackendInvocationError("MockBackend has no queued payloads left")

        payload = self._payloads.popleft()
        return StructuredBackendResponse(
            payload=payload,
            raw_stdout="",
            raw_stderr="",
            command=("mock", request.task_name),
            exit_code=0,
            backend_name=self.name,
        )
