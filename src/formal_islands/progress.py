"""Shared progress logging for terminal output and per-run log files."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Iterator, TextIO


@dataclass
class _ProgressLogState:
    path: Path | None = None
    file: TextIO | None = None
    depth: int = 0


_STATE = _ProgressLogState()
_LOCK = RLock()


def progress(message: str) -> None:
    """Print a progress message to the terminal and tee it into the active log file."""

    line = f"[formal-islands] {message}"
    print(line, flush=True)
    with _LOCK:
        if _STATE.file is None:
            return
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        _STATE.file.write(f"{timestamp} {line}\n")
        _STATE.file.flush()


@contextmanager
def use_progress_log(path: Path, *, overwrite: bool = True) -> Iterator[Path]:
    """Open a per-run progress log file, reusing the same file across nested scopes."""

    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        if _STATE.depth == 0:
            mode = "w" if overwrite else "a"
            _STATE.file = resolved.open(mode, encoding="utf-8")
            _STATE.path = resolved
        elif _STATE.path != resolved:
            raise ValueError(
                f"progress log already open at {_STATE.path}, cannot open a nested log at {resolved}"
            )
        _STATE.depth += 1
    try:
        yield resolved
    finally:
        with _LOCK:
            if _STATE.depth <= 0:
                raise RuntimeError("progress log depth underflow")
            _STATE.depth -= 1
            if _STATE.depth == 0 and _STATE.file is not None:
                _STATE.file.close()
                _STATE.file = None
                _STATE.path = None
