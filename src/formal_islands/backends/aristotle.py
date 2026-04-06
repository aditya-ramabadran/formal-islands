"""Aristotle project-based formalization backend."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aristotlelib import AristotleAPIError, Project, ProjectStatus

from formal_islands.backends.base import BackendInvocationError, BackendUnavailableError
from formal_islands.progress import progress


TERMINAL_STATUSES = {
    ProjectStatus.COMPLETE,
    ProjectStatus.COMPLETE_WITH_ERRORS,
    ProjectStatus.OUT_OF_BUDGET,
    ProjectStatus.FAILED,
    ProjectStatus.CANCELED,
}


@dataclass(frozen=True)
class AristotleProjectRun:
    """Metadata about a completed Aristotle project submission."""

    project_id: str
    status: str
    result_tar_path: Path | None
    status_history: list[str]
    log_path: Path | None
    elapsed_seconds: float
    project_snapshot_dir: Path
    prompt: str
    task_name: str


@dataclass(frozen=True)
class AristotleBackend:
    """Formalization-only adapter for the local Aristotle SDK."""

    log_dir: Path | None = None
    timeout_seconds: float | None = None
    polling_interval_seconds: float = 30.0
    cancel_on_timeout: bool = True

    def submit_project(
        self,
        *,
        prompt: str,
        project_dir: Path,
        task_name: str,
    ) -> AristotleProjectRun:
        """Submit a Lean project snapshot to Aristotle and wait for completion."""

        return asyncio.run(
            self._submit_project_async(
                prompt=prompt,
                project_dir=project_dir,
                task_name=task_name,
            )
        )

    async def _submit_project_async(
        self,
        *,
        prompt: str,
        project_dir: Path,
        task_name: str,
    ) -> AristotleProjectRun:
        project_dir = project_dir.resolve()
        if not project_dir.is_dir():
            raise BackendInvocationError(f"Aristotle project directory does not exist: {project_dir}")

        log_path = self._prepare_log_path(task_name)
        started_at = time.time()
        self._write_log(
            log_path,
            {
                "status": "started",
                "task_name": task_name,
                "backend_name": "aristotle",
                "project_dir": str(project_dir),
                "prompt": prompt,
                "timeout_seconds": self.timeout_seconds,
                "polling_interval_seconds": self.polling_interval_seconds,
                "started_at_epoch_seconds": started_at,
            },
        )
        progress(
            f"Aristotle backend: submitting project {task_name} from {project_dir} "
            f"(timeout={self.timeout_seconds}, poll={self.polling_interval_seconds})"
        )

        if os.getenv("ARISTOTLE_API_KEY") in {None, ""}:
            self._write_log(
                log_path,
                {
                    "status": "unavailable",
                    "task_name": task_name,
                    "backend_name": "aristotle",
                    "project_dir": str(project_dir),
                    "prompt": prompt,
                    "timeout_seconds": self.timeout_seconds,
                    "polling_interval_seconds": self.polling_interval_seconds,
                    "started_at_epoch_seconds": started_at,
                    "elapsed_seconds": time.time() - started_at,
                    "error": (
                        "ARISTOTLE_API_KEY is not set. Export it before using the Aristotle backend."
                    ),
                },
            )
            raise BackendUnavailableError(
                "ARISTOTLE_API_KEY is not set. Export it before using the Aristotle backend."
            )

        try:
            project = await Project.create_from_directory(prompt=prompt, project_dir=project_dir)
        except AristotleAPIError as exc:
            raise BackendInvocationError(f"Aristotle project submission failed: {exc}") from exc

        status_history = [project.status.name]
        progress(
            f"Aristotle backend: project {project.project_id} created with status {project.status.name}; "
            "waiting for terminal status"
        )
        response_payload: dict[str, Any] = {
            "project_id": project.project_id,
            "status": project.status.name,
            "status_history": status_history.copy(),
            "started_at_epoch_seconds": started_at,
        }

        try:
            await self._wait_for_terminal_status(
                project=project,
                status_history=status_history,
                started_at=started_at,
                task_name=task_name,
            )
        except BackendInvocationError as exc:
            self._write_log(
                log_path,
                {
                    "status": "timeout",
                    "task_name": task_name,
                    "backend_name": "aristotle",
                    "project_dir": str(project_dir),
                    "prompt": prompt,
                    "timeout_seconds": self.timeout_seconds,
                    "polling_interval_seconds": self.polling_interval_seconds,
                    "started_at_epoch_seconds": started_at,
                    "elapsed_seconds": time.time() - started_at,
                    "project_id": project.project_id,
                    "status_history": status_history,
                    "error": str(exc),
                },
            )
            raise

        terminal_status = project.status
        progress(
            f"Aristotle backend: project {project.project_id} reached terminal status {terminal_status.name}; "
            "downloading solution"
        )
        response_payload["status"] = terminal_status.name
        response_payload["status_history"] = status_history.copy()

        if terminal_status not in {
            ProjectStatus.COMPLETE,
            ProjectStatus.COMPLETE_WITH_ERRORS,
            ProjectStatus.OUT_OF_BUDGET,
        }:
            self._write_log(
                log_path,
                {
                    "status": "failed",
                    "task_name": task_name,
                    "backend_name": "aristotle",
                    "project_dir": str(project_dir),
                    "prompt": prompt,
                    "timeout_seconds": self.timeout_seconds,
                    "polling_interval_seconds": self.polling_interval_seconds,
                    "started_at_epoch_seconds": started_at,
                    "elapsed_seconds": time.time() - started_at,
                    "project_id": project.project_id,
                    "status_history": status_history,
                    "project_status": terminal_status.name,
                    "error": f"Aristotle project finished with status {terminal_status.name} and no downloadable solution.",
                },
            )
            raise BackendInvocationError(
                f"Aristotle project finished with status {terminal_status.name}"
            )

        result_destination = self._result_destination(task_name, project.project_id)
        try:
            progress(
                f"Aristotle backend: downloading solution for project {project.project_id} "
                f"to {result_destination}"
            )
            solution_path = await project.get_solution(destination=result_destination)
        except Exception as exc:  # pragma: no cover - defensive
            self._write_log(
                log_path,
                {
                    "status": "failed",
                    "task_name": task_name,
                    "backend_name": "aristotle",
                    "project_dir": str(project_dir),
                    "prompt": prompt,
                    "timeout_seconds": self.timeout_seconds,
                    "polling_interval_seconds": self.polling_interval_seconds,
                    "started_at_epoch_seconds": started_at,
                    "elapsed_seconds": time.time() - started_at,
                    "project_id": project.project_id,
                    "status_history": status_history,
                    "project_status": terminal_status.name,
                    "error": f"Failed to download Aristotle solution: {exc}",
                },
            )
            raise BackendInvocationError(f"Failed to download Aristotle solution: {exc}") from exc

        elapsed_seconds = time.time() - started_at
        progress(
            f"Aristotle backend: downloaded solution for project {project.project_id} to {solution_path}"
        )
        self._write_log(
            log_path,
            {
                "status": "completed",
                "task_name": task_name,
                "backend_name": "aristotle",
                "project_dir": str(project_dir),
                "prompt": prompt,
                "timeout_seconds": self.timeout_seconds,
                "polling_interval_seconds": self.polling_interval_seconds,
                "started_at_epoch_seconds": started_at,
                "elapsed_seconds": elapsed_seconds,
                "project_id": project.project_id,
                "status_history": status_history,
                "project_status": terminal_status.name,
                "result_tar_path": str(solution_path),
                "project_response": response_payload,
            },
        )

        return AristotleProjectRun(
            project_id=project.project_id,
            status=terminal_status.name,
            result_tar_path=solution_path,
            status_history=status_history,
            log_path=log_path,
            elapsed_seconds=elapsed_seconds,
            project_snapshot_dir=project_dir,
            prompt=prompt,
            task_name=task_name,
        )

    async def _wait_for_terminal_status(
        self,
        *,
        project: Project,
        status_history: list[str],
        started_at: float,
        task_name: str,
    ) -> None:
        poll_failures = 0
        last_status = project.status.name
        progress(
            f"Aristotle backend: polling project {project.project_id} for terminal status "
            f"for task {task_name}"
        )
        while True:
            if self.timeout_seconds is not None and (time.time() - started_at) >= self.timeout_seconds:
                if self.cancel_on_timeout:
                    try:
                        await project.cancel()
                    except Exception:
                        pass
                raise BackendInvocationError(
                    f"Aristotle project timed out after {self.timeout_seconds} seconds."
                )

            try:
                await project.refresh()
            except AristotleAPIError:
                poll_failures += 1
                backoff_seconds = min(15 * (2 ** (poll_failures - 1)), 120)
                if self.timeout_seconds is not None and (
                    time.time() - started_at + backoff_seconds
                ) >= self.timeout_seconds:
                    if self.cancel_on_timeout:
                        try:
                            await project.cancel()
                        except Exception:
                            pass
                    raise BackendInvocationError(
                        f"Aristotle project timed out after {self.timeout_seconds} seconds."
                    )
                await asyncio.sleep(backoff_seconds)
                continue

            poll_failures = 0
            if project.status.name != last_status:
                progress(
                    f"Aristotle backend: project {project.project_id} status changed "
                    f"{last_status} -> {project.status.name}"
                )
                last_status = project.status.name
            status_history.append(project.status.name)
            if project.status in TERMINAL_STATUSES:
                return

            await asyncio.sleep(self.polling_interval_seconds)

    def _result_destination(self, task_name: str, project_id: str) -> Path:
        if self.log_dir is not None:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            return self.log_dir / f"{task_name}_{project_id}_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}.tar.gz"
        return Path(tempfile.gettempdir()) / f"{task_name}_{project_id}_{uuid.uuid4().hex[:8]}.tar.gz"

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
