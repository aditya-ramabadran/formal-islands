"""Shared progress logging for terminal output and per-run log files."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Iterator, TextIO

from formal_islands.models import ProofGraph

_PROGRESS_PREFIX = "[formal-islands]"


@dataclass
class _ProgressLogState:
    path: Path | None = None
    file: TextIO | None = None
    depth: int = 0


_STATE = _ProgressLogState()
_LOCK = RLock()


def progress(message: str) -> None:
    """Print a progress message to the terminal and tee it into the active log file."""

    normalized_message = _normalize_progress_text(message)
    line = f"{_PROGRESS_PREFIX} {normalized_message}"
    print(line, flush=True)
    _write_to_active_log(normalized_message)


@contextmanager
def use_progress_log(path: Path, *, overwrite: bool = False) -> Iterator[Path]:
    """Open a per-run progress log file, reusing the same file across nested scopes."""

    del overwrite  # append-only behavior is intentional and enforced
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        if _STATE.depth == 0:
            _STATE.file = resolved.open("a", encoding="utf-8")
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


def append_to_progress_log(message: str) -> None:
    """Append text to the active progress log without printing it to the terminal."""

    _write_to_active_log(message)


def append_graph_summary_to_progress_log(
    graph: ProofGraph,
    *,
    label: str,
    previous_graph: ProofGraph | None = None,
    max_nodes: int = 12,
    max_edges: int = 12,
) -> None:
    """Append a compact graph preview to the active progress log."""

    if previous_graph is not None and graph.model_dump(mode="json") == previous_graph.model_dump(mode="json"):
        return

    lines: list[str] = [f"{label}: graph summary ({len(graph.nodes)} nodes, {len(graph.edges)} edges)"]
    if previous_graph is not None:
        lines.extend(_format_graph_diff(previous_graph, graph))
    lines.append("Edges:")
    if graph.edges:
        for edge in graph.edges[:max_edges]:
            label_text = f" [{edge.label}]" if edge.label else ""
            lines.append(f"  {edge.source_id} -> {edge.target_id}{label_text}")
        if len(graph.edges) > max_edges:
            lines.append(f"  ... ({len(graph.edges) - max_edges} more)")
    else:
        lines.append("  (none)")

    lines.append("Nodes:")
    if graph.nodes:
        for node in graph.nodes[:max_nodes]:
            priority_text = f"priority={node.formalization_priority}" if node.formalization_priority is not None else "priority=None"
            lines.append(f"  [{node.status}] {node.id} ({priority_text})")
            lines.append(f"    stmt: {node.informal_statement[:120]}")
        if len(graph.nodes) > max_nodes:
            lines.append(f"  ... ({len(graph.nodes) - max_nodes} more)")
    else:
        lines.append("  (none)")

    append_to_progress_log("\n".join(lines))


def append_formalization_assessment_to_progress_log(
    *,
    node_id: str,
    result_kind: str,
    reason: str,
    coverage_score: int | None = None,
    certifies_main_burden: bool | None = None,
    expansion_warranted: bool | None = None,
    worth_retrying_later: bool | None = None,
) -> None:
    """Append a concise semantic-review summary to the active progress log."""

    lines = [f"node {node_id}: formalization assessment result_kind={result_kind}"]
    if coverage_score is not None:
        lines.append(f"coverage_score={coverage_score}")
    if certifies_main_burden is not None:
        lines.append(f"certifies_main_burden={certifies_main_burden}")
    if expansion_warranted is not None:
        lines.append(f"expansion_warranted={expansion_warranted}")
    if worth_retrying_later is not None:
        lines.append(f"worth_retrying_later={worth_retrying_later}")
    lines.append(f"reason: {reason}")
    append_to_progress_log("\n".join(lines))


def _write_to_active_log(message: str) -> None:
    with _LOCK:
        if _STATE.file is None:
            return
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        for line in message.splitlines() or [""]:
            normalized = _normalize_progress_text(line)
            _STATE.file.write(f"{timestamp} {_PROGRESS_PREFIX} {normalized}\n")
        _STATE.file.flush()


def _normalize_progress_text(message: str) -> str:
    if message.startswith(f"{_PROGRESS_PREFIX} "):
        return message[len(_PROGRESS_PREFIX) + 1 :]
    if message == _PROGRESS_PREFIX:
        return ""
    return message


def _format_graph_diff(previous_graph: ProofGraph, current_graph: ProofGraph) -> list[str]:
    previous_nodes = {node.id: node for node in previous_graph.nodes}
    current_nodes = {node.id: node for node in current_graph.nodes}
    previous_edges = {
        (edge.source_id, edge.target_id, edge.label, edge.explanation): edge
        for edge in previous_graph.edges
    }
    current_edges = {
        (edge.source_id, edge.target_id, edge.label, edge.explanation): edge
        for edge in current_graph.edges
    }

    added_nodes = [current_nodes[node_id] for node_id in sorted(current_nodes.keys() - previous_nodes.keys())]
    removed_nodes = [previous_nodes[node_id] for node_id in sorted(previous_nodes.keys() - current_nodes.keys())]
    changed_nodes = [
        (previous_nodes[node_id], current_nodes[node_id])
        for node_id in sorted(previous_nodes.keys() & current_nodes.keys())
        if previous_nodes[node_id].model_dump(mode="json") != current_nodes[node_id].model_dump(mode="json")
    ]
    added_edges = [current_edges[key] for key in sorted(current_edges.keys() - previous_edges.keys())]
    removed_edges = [previous_edges[key] for key in sorted(previous_edges.keys() - current_edges.keys())]

    if not any((added_nodes, removed_nodes, changed_nodes, added_edges, removed_edges)):
        return []

    lines = ["Graph diff:"]
    if added_nodes:
        lines.append("  Added nodes:")
        for node in added_nodes:
            lines.append(f"    + [{node.status}] {node.id}")
    if removed_nodes:
        lines.append("  Removed nodes:")
        for node in removed_nodes:
            lines.append(f"    - [{node.status}] {node.id}")
    if changed_nodes:
        lines.append("  Updated nodes:")
        for before, after in changed_nodes:
            lines.append(
                f"    ~ [{before.status}] {before.id} -> [{after.status}] {after.id}"
            )
    if added_edges:
        lines.append("  Added edges:")
        for edge in added_edges:
            label_text = f" [{edge.label}]" if edge.label else ""
            lines.append(f"    + {edge.source_id} -> {edge.target_id}{label_text}")
    if removed_edges:
        lines.append("  Removed edges:")
        for edge in removed_edges:
            label_text = f" [{edge.label}]" if edge.label else ""
            lines.append(f"    - {edge.source_id} -> {edge.target_id}{label_text}")
    return lines
