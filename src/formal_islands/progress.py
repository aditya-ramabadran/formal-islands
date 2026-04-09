"""Shared progress logging for terminal output and per-run log files."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import json
from pathlib import Path
from threading import RLock
from typing import Iterator, TextIO

from formal_islands.backends.base import StructuredBackend, StructuredBackendRequest, StructuredBackendResponse
from formal_islands.models import ProofGraph, canonical_dependency_direction_warnings

_PROGRESS_PREFIX = "[formal-islands]"
_GRAPH_HISTORY_VERSION = 1


@dataclass
class _ProgressLogState:
    path: Path | None = None
    file: TextIO | None = None
    depth: int = 0


_STATE = _ProgressLogState()


@dataclass
class _GraphHistoryState:
    path: Path | None = None
    file: TextIO | None = None
    depth: int = 0


_GRAPH_HISTORY_STATE = _GraphHistoryState()
_LOCK = RLock()


class GraphHistoryEventKind(StrEnum):
    """Canonical event kinds for graph-history snapshots."""

    GRAPH_SNAPSHOT = "graph_snapshot"
    EXTRACT_STAGE_OUTPUT = "extract_stage_output"
    PLAN_STAGE_EXTRACTED_GRAPH = "plan_stage_extracted_graph"
    CANDIDATE_SELECTION_OUTPUT = "candidate_selection_output"
    PLAN_STAGE_CANDIDATE_GRAPH = "plan_stage_candidate_graph"
    FORMALIZATION_UPDATE = "formalization_update"
    PARENT_PROMOTION = "parent_promotion"
    REPORT_STAGE_GRAPH = "report_stage_graph"


@dataclass(frozen=True)
class GraphHistoryEntry:
    """Typed graph-history entry used by the report layer."""

    version: int
    timestamp: str
    event_kind: GraphHistoryEventKind
    label: str
    node_id: str | None
    theorem_title: str
    root_node_id: str
    node_count: int
    edge_count: int
    warnings: list[str]
    diff: dict[str, object] | None
    metadata: dict[str, object]
    graph: ProofGraph


def parse_graph_history_entry(payload: object) -> GraphHistoryEntry | None:
    """Best-effort parse for backward-compatible JSONL history entries."""

    if not isinstance(payload, dict):
        return None
    graph_payload = payload.get("graph")
    if not isinstance(graph_payload, dict):
        return None
    try:
        graph = ProofGraph.model_validate(graph_payload)
    except Exception:
        return None
    raw_kind = payload.get("event_kind") or payload.get("event") or GraphHistoryEventKind.GRAPH_SNAPSHOT
    try:
        event_kind = GraphHistoryEventKind(str(raw_kind))
    except ValueError:
        event_kind = GraphHistoryEventKind.GRAPH_SNAPSHOT
    diff = payload.get("diff")
    metadata = payload.get("metadata")
    warnings = payload.get("warnings")
    return GraphHistoryEntry(
        version=int(payload.get("version") or _GRAPH_HISTORY_VERSION),
        timestamp=str(payload.get("timestamp") or ""),
        event_kind=event_kind,
        label=str(payload.get("label") or ""),
        node_id=(str(payload.get("node_id")) if payload.get("node_id") else None),
        theorem_title=str(payload.get("theorem_title") or graph.theorem_title),
        root_node_id=str(payload.get("root_node_id") or graph.root_node_id),
        node_count=int(payload.get("node_count") or len(graph.nodes)),
        edge_count=int(payload.get("edge_count") or len(graph.edges)),
        warnings=[str(item) for item in warnings] if isinstance(warnings, list) else [],
        diff=diff if isinstance(diff, dict) else None,
        metadata=metadata if isinstance(metadata, dict) else {},
        graph=graph,
    )


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


@contextmanager
def use_graph_history_log(path: Path, *, overwrite: bool = False) -> Iterator[Path]:
    """Open a per-run append-only JSONL graph-history log."""

    del overwrite  # append-only behavior is intentional and enforced
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        if _GRAPH_HISTORY_STATE.depth == 0:
            _GRAPH_HISTORY_STATE.file = resolved.open("a", encoding="utf-8")
            _GRAPH_HISTORY_STATE.path = resolved
        elif _GRAPH_HISTORY_STATE.path != resolved:
            raise ValueError(
                "graph history log already open at "
                f"{_GRAPH_HISTORY_STATE.path}, cannot open a nested log at {resolved}"
            )
        _GRAPH_HISTORY_STATE.depth += 1
    try:
        yield resolved
    finally:
        with _LOCK:
            if _GRAPH_HISTORY_STATE.depth <= 0:
                raise RuntimeError("graph history log depth underflow")
            _GRAPH_HISTORY_STATE.depth -= 1
            if _GRAPH_HISTORY_STATE.depth == 0 and _GRAPH_HISTORY_STATE.file is not None:
                _GRAPH_HISTORY_STATE.file.close()
                _GRAPH_HISTORY_STATE.file = None
                _GRAPH_HISTORY_STATE.path = None


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

    warnings = canonical_dependency_direction_warnings(graph)
    if warnings:
        lines.append("Directionality warnings:")
        for warning in warnings:
            lines.append(f"  ! {warning}")

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


def append_graph_snapshot_to_history_log(
    graph: ProofGraph,
    *,
    label: str,
    previous_graph: ProofGraph | None = None,
    event: str = "graph_snapshot",
    node_id: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    """Append a structured graph snapshot to the active graph-history log."""

    if previous_graph is not None and graph.model_dump(mode="json") == previous_graph.model_dump(mode="json"):
        return

    with _LOCK:
        if _GRAPH_HISTORY_STATE.file is None:
            return
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            event_kind = GraphHistoryEventKind(event)
        except ValueError:
            event_kind = GraphHistoryEventKind.GRAPH_SNAPSHOT
        entry = {
            "version": _GRAPH_HISTORY_VERSION,
            "timestamp": timestamp,
            "event": event,
            "event_kind": event_kind,
            "label": label,
            "node_id": node_id,
            "theorem_title": graph.theorem_title,
            "root_node_id": graph.root_node_id,
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "warnings": canonical_dependency_direction_warnings(graph),
            "diff": _graph_diff_data(previous_graph, graph) if previous_graph is not None else None,
            "metadata": metadata or {},
            "graph": graph.model_dump(mode="json"),
        }
        _GRAPH_HISTORY_STATE.file.write(json.dumps(entry, ensure_ascii=True) + "\n")
        _GRAPH_HISTORY_STATE.file.flush()


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


def append_parent_promotion_assessment_to_progress_log(
    *,
    parent_node_id: str,
    promote_parent: bool,
    reason: str,
    recommended_priority: int | None = None,
    verified_child_count: int | None = None,
) -> None:
    """Append a concise parent-promotion review summary to the active progress log."""

    lines = [f"node {parent_node_id}: parent promotion assessment promote_parent={promote_parent}"]
    if recommended_priority is not None:
        lines.append(f"recommended_priority={recommended_priority}")
    if verified_child_count is not None:
        lines.append(f"verified_child_count={verified_child_count}")
    lines.append(f"reason: {reason}")
    append_to_progress_log("\n".join(lines))


def append_remaining_proof_burden_to_progress_log(
    *,
    node_id: str,
    verified_child_ids: list[str],
    remaining_proof_burden: str,
) -> None:
    """Append a concise report-synthesis summary to the active progress log."""

    lines = [f"node {node_id}: remaining proof burden synthesized"]
    if verified_child_ids:
        lines.append("verified_children=" + ", ".join(verified_child_ids))
    lines.append(f"text: {remaining_proof_burden}")
    append_to_progress_log("\n".join(lines))


def describe_backend(backend: object) -> str:
    """Return a short human-readable backend label for progress logging."""

    backend_name = backend.__class__.__name__
    mapping = {
        "ClaudeCodeBackend": "Claude",
        "CodexCLIBackend": "Codex",
        "GeminiCLIBackend": "Gemini",
        "AristotleBackend": "Aristotle",
        "MockBackend": "Mock",
    }
    if backend_name in mapping:
        return mapping[backend_name]
    if backend_name.endswith("Backend"):
        backend_name = backend_name[: -len("Backend")]
    return backend_name


def run_structured_with_progress(
    backend: StructuredBackend,
    request: StructuredBackendRequest,
) -> StructuredBackendResponse:
    """Run a structured backend call while logging the prompt / completion to progress output."""

    backend_label = describe_backend(backend)
    task_label = request.task_name or "structured_request"
    progress(f"prompting {backend_label} backend for {task_label}")
    try:
        response = backend.run_structured(request)
    except Exception as exc:
        progress(f"{backend_label} backend for {task_label} failed: {_truncate_error_summary(exc)}")
        raise
    progress(f"{backend_label} backend completed for {task_label}")
    return response


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


def _truncate_error_summary(error: Exception, *, max_length: int = 180) -> str:
    summary = " ".join(str(error).split())
    if len(summary) <= max_length:
        return summary
    return summary[: max_length - 1].rstrip() + "…"


def _format_graph_diff(previous_graph: ProofGraph, current_graph: ProofGraph) -> list[str]:
    diff_data = _graph_diff_data(previous_graph, current_graph)
    added_nodes = diff_data["added_nodes"]
    removed_nodes = diff_data["removed_nodes"]
    changed_nodes = diff_data["changed_nodes"]
    added_edges = diff_data["added_edges"]
    removed_edges = diff_data["removed_edges"]

    if not any((added_nodes, removed_nodes, changed_nodes, added_edges, removed_edges)):
        return []

    lines = ["Graph diff:"]
    if added_nodes:
        lines.append("  Added nodes:")
        for node_id in added_nodes:
            lines.append(f"    + {node_id}")
    if removed_nodes:
        lines.append("  Removed nodes:")
        for node_id in removed_nodes:
            lines.append(f"    - {node_id}")
    if changed_nodes:
        lines.append("  Updated nodes:")
        for change in changed_nodes:
            lines.append(
                f"    ~ [{change['before_status']}] {change['id']} -> [{change['after_status']}] {change['id']}"
            )
    if added_edges:
        lines.append("  Added edges:")
        for edge in added_edges:
            label_text = f" [{edge['label']}]" if edge["label"] else ""
            lines.append(f"    + {edge['source_id']} -> {edge['target_id']}{label_text}")
    if removed_edges:
        lines.append("  Removed edges:")
        for edge in removed_edges:
            label_text = f" [{edge['label']}]" if edge["label"] else ""
            lines.append(f"    - {edge['source_id']} -> {edge['target_id']}{label_text}")
    return lines


def _graph_diff_data(previous_graph: ProofGraph, current_graph: ProofGraph) -> dict[str, object]:
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
    return {
        "added_nodes": [node.id for node in added_nodes],
        "removed_nodes": [node.id for node in removed_nodes],
        "changed_nodes": [
            {
                "id": after.id,
                "before_status": str(before.status),
                "after_status": str(after.status),
                "before_priority": before.formalization_priority,
                "after_priority": after.formalization_priority,
                "before_last_formalization_outcome": (
                    str(before.last_formalization_outcome)
                    if before.last_formalization_outcome is not None
                    else None
                ),
                "after_last_formalization_outcome": (
                    str(after.last_formalization_outcome)
                    if after.last_formalization_outcome is not None
                    else None
                ),
                "before_last_formalization_failure_kind": (
                    str(before.last_formalization_failure_kind)
                    if before.last_formalization_failure_kind is not None
                    else None
                ),
                "after_last_formalization_failure_kind": (
                    str(after.last_formalization_failure_kind)
                    if after.last_formalization_failure_kind is not None
                    else None
                ),
                "remaining_proof_burden_changed": before.remaining_proof_burden != after.remaining_proof_burden,
                "formal_artifact_attached_changed": (before.formal_artifact is None) != (after.formal_artifact is None),
            }
            for before, after in changed_nodes
        ],
        "added_edges": [
            {
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "label": edge.label,
                "explanation": edge.explanation,
            }
            for edge in added_edges
        ],
        "removed_edges": [
            {
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "label": edge.label,
                "explanation": edge.explanation,
            }
            for edge in removed_edges
        ],
    }
