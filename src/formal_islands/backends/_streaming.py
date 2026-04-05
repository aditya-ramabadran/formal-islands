"""Shared helpers for streaming CLI subprocess output."""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StreamingCommandResult:
    """Captured result from a subprocess streamed line by line."""

    returncode: int
    raw_stdout: str
    raw_stderr: str
    stdout_lines: list[str]
    stderr_lines: list[str]
    timed_out: bool = False


def run_streaming_command(
    command: list[str],
    *,
    input_text: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> StreamingCommandResult:
    """Run a subprocess and capture stdout/stderr incrementally."""

    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )

    if process.stdin is not None:
        try:
            process.stdin.write(input_text)
        finally:
            process.stdin.close()

    output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def reader(stream_name: str, stream: Any) -> None:
        try:
            for line in iter(stream.readline, ""):
                output_queue.put((stream_name, line))
        finally:
            output_queue.put((stream_name, None))

    stdout_thread = threading.Thread(
        target=reader,
        args=("stdout", process.stdout),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=reader,
        args=("stderr", process.stderr),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    started_at = time.monotonic()
    stdout_done = False
    stderr_done = False
    timed_out = False
    timeout_deadline = None if timeout_seconds is None else started_at + timeout_seconds

    while not (stdout_done and stderr_done):
        if timeout_deadline is not None and not timed_out and time.monotonic() >= timeout_deadline:
            timed_out = True
            _terminate_process_group(process)
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                _kill_process_group(process)
                process.wait(timeout=5.0)
            break

        try:
            stream_name, line = output_queue.get(timeout=0.1)
        except queue.Empty:
            if timed_out and process.poll() is not None:
                break
            continue

        if line is None:
            if stream_name == "stdout":
                stdout_done = True
            else:
                stderr_done = True
            continue

        if stream_name == "stdout":
            stdout_lines.append(line)
        else:
            stderr_lines.append(line)

    if timed_out:
        _drain_queue(output_queue, stdout_lines, stderr_lines)

    stdout_thread.join(timeout=1.0)
    stderr_thread.join(timeout=1.0)
    returncode = process.poll()
    if returncode is None:
        returncode = process.wait()

    return StreamingCommandResult(
        returncode=returncode,
        raw_stdout="".join(stdout_lines),
        raw_stderr="".join(stderr_lines),
        stdout_lines=stdout_lines,
        stderr_lines=stderr_lines,
        timed_out=timed_out,
    )


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except Exception:
        process.terminate()


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except Exception:
        process.kill()


def _drain_queue(
    output_queue: queue.Queue[tuple[str, str | None]],
    stdout_lines: list[str],
    stderr_lines: list[str],
) -> None:
    while True:
        try:
            stream_name, line = output_queue.get_nowait()
        except queue.Empty:
            break

        if line is None:
            continue
        if stream_name == "stdout":
            stdout_lines.append(line)
        else:
            stderr_lines.append(line)
