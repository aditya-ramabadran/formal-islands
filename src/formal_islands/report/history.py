"""Graph-history loading, summarization, and slideshow rendering."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from formal_islands.models import ProofGraph
from formal_islands.progress import GraphHistoryEntry, GraphHistoryEventKind, parse_graph_history_entry
from formal_islands.report.graph_widget import graph_visual_status, render_graph_widget
from formal_islands.report.rendering import render_inline_code_html


def load_graph_history_entries(path: Path) -> list[GraphHistoryEntry]:
    """Load best-effort typed graph-history entries from a JSONL file."""

    if not path.exists():
        return []

    entries: list[GraphHistoryEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        entry = parse_graph_history_entry(payload)
        if entry is not None:
            entries.append(entry)
    return entries


def graph_history_bundle(entries: list[GraphHistoryEntry | dict[str, object]]) -> list[dict[str, object]]:
    frames = build_graph_history_frames(entries)
    return [
        {
            "timestamp": frame["timestamp"],
            "event": frame["event"],
            "label": frame["label"],
            "node_id": frame["node_id"],
            "caption": frame["caption"],
            "node_count": frame["node_count"],
            "edge_count": frame["edge_count"],
        }
        for frame in frames
    ]


def build_graph_history_frames(
    entries: list[GraphHistoryEntry | dict[str, object]],
    *,
    visually_distinct_only: bool = True,
) -> list[dict[str, object]]:
    frames: list[dict[str, object]] = []
    previous_visual_signature: tuple | None = None
    for index, raw_entry in enumerate(entries):
        entry = raw_entry if isinstance(raw_entry, GraphHistoryEntry) else parse_graph_history_entry(raw_entry)
        if entry is None:
            continue
        visual_signature = graph_visual_signature(entry.graph)
        if visually_distinct_only and previous_visual_signature is not None and visual_signature == previous_visual_signature:
            continue
        caption = graph_history_caption(entry)
        frames.append(
            {
                "index": index,
                "timestamp": entry.timestamp,
                "event": str(entry.event_kind),
                "label": entry.label,
                "node_id": entry.node_id,
                "caption": caption,
                "caption_html": render_inline_code_html(caption),
                "node_count": len(entry.graph.nodes),
                "edge_count": len(entry.graph.edges),
                "graph": entry.graph,
            }
        )
        previous_visual_signature = visual_signature
    return frames


def graph_visual_signature(graph: ProofGraph) -> tuple:
    node_signature = tuple(
        (
            node.id,
            node.display_label or node.title,
            graph_visual_status(node),
        )
        for node in sorted(graph.nodes, key=lambda candidate: candidate.id)
    )
    edge_signature = tuple(
        sorted(
            (
                edge.source_id,
                edge.target_id,
                edge.label or "",
            )
            for edge in graph.edges
        )
    )
    return (graph.root_node_id, node_signature, edge_signature)


def graph_history_caption(entry: GraphHistoryEntry) -> str:
    event = entry.event_kind
    label = entry.label
    node_id = entry.node_id or ""
    diff = entry.diff if isinstance(entry.diff, dict) else {}
    changed_nodes = diff.get("changed_nodes")
    added_nodes = diff.get("added_nodes")
    removed_nodes = diff.get("removed_nodes")
    if not isinstance(changed_nodes, list):
        changed_nodes = []
    if not isinstance(added_nodes, list):
        added_nodes = []
    if not isinstance(removed_nodes, list):
        removed_nodes = []

    if event in {GraphHistoryEventKind.EXTRACT_STAGE_OUTPUT, GraphHistoryEventKind.PLAN_STAGE_EXTRACTED_GRAPH}:
        return "Initial extracted proof graph."
    if event == GraphHistoryEventKind.CONTINUATION_REQUEST:
        requested = entry.metadata.get("requested_nodes")
        if isinstance(requested, list):
            formatted = ", ".join(f"`{str(node_id)}`" for node_id in requested)
            if formatted:
                return f"Continuation request promoted {formatted} for another formalization pass."
        if node_id:
            return f"Continuation request promoted `{node_id}` for another formalization pass."
        return "Continuation request updated the graph for another formalization pass."
    if event in {GraphHistoryEventKind.CANDIDATE_SELECTION_OUTPUT, GraphHistoryEventKind.PLAN_STAGE_CANDIDATE_GRAPH}:
        promoted = [
            f"`{change.get('id')}`"
            for change in changed_nodes
            if isinstance(change, dict) and change.get("after_status") == "candidate_formal"
        ]
        if promoted:
            return "Candidate selection marked " + ", ".join(promoted) + " for formalization."
        return "Candidate selection updated the proof graph."
    if event == GraphHistoryEventKind.BLOCKER_PROMOTION:
        promoted_changes = [
            change
            for change in changed_nodes
            if isinstance(change, dict) and change.get("after_status") == "candidate_formal"
        ]
        if promoted_changes:
            change = promoted_changes[0]
            changed_id = str(change.get("id") or node_id or "node")
            priority = change.get("after_priority")
            priority_suffix = f" at priority `{priority}`" if priority is not None else ""
            return (
                f"Node `{changed_id}` was promoted from `informal` to `candidate_formal`"
                f"{priority_suffix} as the last remaining blocker to a broader parent/root theorem."
            )
        if node_id:
            return f"Node `{node_id}` was promoted as a last-blocker formalization target."
        return "A blocker node was promoted for formalization."
    if event == GraphHistoryEventKind.PARENT_PROMOTION:
        promoted_changes = [
            change
            for change in changed_nodes
            if isinstance(change, dict) and change.get("after_status") == "candidate_formal"
        ]
        if promoted_changes:
            change = promoted_changes[0]
            changed_id = str(change.get("id") or node_id or "node")
            priority = change.get("after_priority")
            priority_suffix = f" at priority `{priority}`" if priority is not None else ""
            return (
                f"Node `{changed_id}` was promoted from `informal` to `candidate_formal`"
                f"{priority_suffix} after its direct children were verified."
            )
        if node_id:
            return f"Node `{node_id}` was promoted for a parent-level formalization attempt."
        return "A parent node was promoted for a parent-level formalization attempt."
    if event == GraphHistoryEventKind.FORMALIZATION_UPDATE:
        support_nodes = [node for node in added_nodes if isinstance(node, str) and node.endswith("__formal_core")]
        if node_id and support_nodes:
            return (
                f"Node `{node_id}` was attempted and narrowed to verified supporting core "
                f"`{support_nodes[0]}`."
            )
        for change in changed_nodes:
            if not isinstance(change, dict):
                continue
            changed_id = change.get("id")
            before_status = change.get("before_status")
            after_status = change.get("after_status")
            if changed_id == node_id and after_status == "formal_verified":
                return (
                    f"Node `{changed_id}` was successfully formalized, status upgraded from "
                    f"`{before_status}` to `formal_verified`."
                )
            if changed_id == node_id and after_status == "formal_failed":
                failure_kind = change.get("after_last_formalization_failure_kind")
                suffix = f" (`{failure_kind}`)." if failure_kind else "."
                return (
                    f"Formalization of node `{changed_id}` failed, status changed from "
                    f"`{before_status}` to `formal_failed`{suffix}"
                )
            if (
                changed_id == node_id
                and change.get("remaining_proof_burden_changed")
                and after_status == before_status
            ):
                return f"Node `{changed_id}` was updated with a refined remaining proof burden."
        if added_nodes:
            formatted = ", ".join(f"`{node}`" for node in added_nodes if isinstance(node, str))
            if formatted:
                return f"Graph update added {formatted}."
        if removed_nodes:
            formatted = ", ".join(f"`{node}`" for node in removed_nodes if isinstance(node, str))
            if formatted:
                return f"Graph update removed {formatted}."
        return (
            f"Formalization updated the proof graph for node `{node_id}`."
            if node_id
            else "Formalization updated the proof graph."
        )
    if event == GraphHistoryEventKind.REPORT_STAGE_GRAPH:
        changed_burdens = [
            f"`{change.get('id')}`"
            for change in changed_nodes
            if isinstance(change, dict) and change.get("remaining_proof_burden_changed")
        ]
        if changed_burdens:
            return "Report generation synthesized remaining proof burden text for " + ", ".join(changed_burdens) + "."
        return "Report generation refreshed the graph with review-oriented annotations."
    if label:
        return f"Graph snapshot recorded at `{label}`."
    return "Graph snapshot recorded."


def render_graph_history_widget(entries: list[GraphHistoryEntry | dict[str, object]]) -> str:
    visual_frames = build_graph_history_frames(entries, visually_distinct_only=True)
    all_frames = build_graph_history_frames(entries, visually_distinct_only=False)
    return _render_graph_history_collections(visual_frames=visual_frames, all_frames=all_frames)


def render_graph_history_widget_with_cleanup(
    entries: list[GraphHistoryEntry | dict[str, object]],
    *,
    cleaned_graph: ProofGraph,
    cleaned_caption: str,
    cleaned_label: str = "final display cleanup",
) -> str:
    visual_frames = build_graph_history_frames(entries, visually_distinct_only=True)
    all_frames = build_graph_history_frames(entries, visually_distinct_only=False)
    visual_frames = _append_synthetic_cleanup_frame(
        visual_frames,
        cleaned_graph=cleaned_graph,
        cleaned_caption=cleaned_caption,
        cleaned_label=cleaned_label,
    )
    all_frames = _append_synthetic_cleanup_frame(
        all_frames,
        cleaned_graph=cleaned_graph,
        cleaned_caption=cleaned_caption,
        cleaned_label=cleaned_label,
    )
    return _render_graph_history_collections(visual_frames=visual_frames, all_frames=all_frames)


def _append_synthetic_cleanup_frame(
    frames: list[dict[str, object]],
    *,
    cleaned_graph: ProofGraph,
    cleaned_caption: str,
    cleaned_label: str,
) -> list[dict[str, object]]:
    if not frames:
        return frames
    last_graph = frames[-1]["graph"]
    if not isinstance(last_graph, ProofGraph):
        return frames
    if graph_visual_signature(last_graph) == graph_visual_signature(cleaned_graph):
        return frames
    synthetic = {
        "index": len(frames),
        "timestamp": str(frames[-1]["timestamp"]),
        "event": "report_display_cleanup",
        "label": cleaned_label,
        "node_id": None,
        "caption": cleaned_caption,
        "caption_html": render_inline_code_html(cleaned_caption),
        "node_count": len(cleaned_graph.nodes),
        "edge_count": len(cleaned_graph.edges),
        "graph": cleaned_graph,
    }
    return [*frames, synthetic]


def _render_graph_history_collections(
    *,
    visual_frames: list[dict[str, object]],
    all_frames: list[dict[str, object]],
) -> str:
    if len(visual_frames) < 2:
        return render_graph_widget(visual_frames[-1]["graph"], widget_key="current") if visual_frames else ""

    latest_visual_index = len(visual_frames) - 1
    latest_all_index = len(all_frames) - 1
    frame_html = "\n".join(
        _render_history_frame(frame, collection="visual", active_index=latest_visual_index)
        for frame in visual_frames
    )
    show_all_toggle = ""
    if len(all_frames) > len(visual_frames):
        frame_html += "\n" + "\n".join(
            _render_history_frame(frame, collection="all", active_index=latest_all_index, hidden_collection=True)
            for frame in all_frames
        )
        show_all_toggle = """
        <label class="graph-history-toggle">
          <input type="checkbox" data-history-show-all />
          Show all history
        </label>
        """
    latest_caption = str(visual_frames[latest_visual_index]["caption_html"])
    latest_label = escape(str(visual_frames[latest_visual_index]["label"]))
    latest_timestamp = escape(str(visual_frames[latest_visual_index]["timestamp"]))
    return f"""
    <div class="graph-history" data-graph-history data-history-default-collection="visual">
      <div class="graph-history-controls">
        <div class="graph-history-buttons">
          <button type="button" class="graph-history-button" data-history-action="start" aria-label="Go to first graph snapshot" title="Go to first graph snapshot">⏮</button>
          <button type="button" class="graph-history-button" data-history-action="prev" aria-label="Go to previous graph snapshot" title="Go to previous graph snapshot">◀</button>
          <button type="button" class="graph-history-button" data-history-action="next" aria-label="Go to next graph snapshot" title="Go to next graph snapshot">▶</button>
          <button type="button" class="graph-history-button" data-history-action="end" aria-label="Go to latest graph snapshot" title="Go to latest graph snapshot">⏭</button>
        </div>
        {show_all_toggle}
        <div class="graph-history-status">
          Snapshot <span data-history-index>{latest_visual_index + 1}</span> of <span data-history-count>{len(visual_frames)}</span>
        </div>
      </div>
      <p class="graph-history-caption" data-history-caption>{latest_caption}</p>
      <p class="graph-history-meta" data-history-meta>{latest_label} · {latest_timestamp}</p>
      {frame_html}
    </div>
    """


def render_graph_history_script() -> str:
    return """
  <script>
    document.querySelectorAll('[data-graph-history]').forEach((root) => {
      const collections = {
        visual: Array.from(root.querySelectorAll('[data-history-frame][data-history-collection="visual"]')),
        all: Array.from(root.querySelectorAll('[data-history-frame][data-history-collection="all"]')),
      };
      const showAllToggle = root.querySelector('[data-history-show-all]');
      const captionEl = root.querySelector('[data-history-caption]');
      const metaEl = root.querySelector('[data-history-meta]');
      const indexEl = root.querySelector('[data-history-index]');
      const countEl = root.querySelector('[data-history-count]');
      const startBtn = root.querySelector('[data-history-action="start"]');
      const prevBtn = root.querySelector('[data-history-action="prev"]');
      const nextBtn = root.querySelector('[data-history-action="next"]');
      const endBtn = root.querySelector('[data-history-action="end"]');
      let collection = (root.dataset.historyDefaultCollection || 'visual');
      const indexes = {
        visual: Math.max(collections.visual.length - 1, 0),
        all: Math.max(collections.all.length - 1, 0),
      };

      const activeFrames = () => {
        const frames = collections[collection] || [];
        return frames.length > 0 ? frames : collections.visual;
      };

      const update = () => {
        Object.entries(collections).forEach(([key, frames]) => {
          frames.forEach((frame, frameIndex) => {
            const active = key === collection && frameIndex === indexes[collection];
            frame.hidden = !active;
          });
        });
        const frames = activeFrames();
        const safeIndex = Math.min(indexes[collection], frames.length - 1);
        indexes[collection] = Math.max(safeIndex, 0);
        const active = frames[indexes[collection]];
        if (!active) return;
        if (captionEl) captionEl.innerHTML = active.dataset.captionHtml || '';
        if (metaEl) {
          const label = active.dataset.label || '';
          const timestamp = active.dataset.timestamp || '';
          metaEl.textContent = label && timestamp ? `${label} · ${timestamp}` : (label || timestamp);
        }
        if (indexEl) indexEl.textContent = String(indexes[collection] + 1);
        if (countEl) countEl.textContent = String(frames.length);
        if (startBtn) startBtn.disabled = indexes[collection] === 0;
        if (prevBtn) prevBtn.disabled = indexes[collection] === 0;
        if (nextBtn) nextBtn.disabled = indexes[collection] === frames.length - 1;
        if (endBtn) endBtn.disabled = indexes[collection] === frames.length - 1;
      };

      startBtn?.addEventListener('click', () => {
        indexes[collection] = 0;
        update();
      });
      prevBtn?.addEventListener('click', () => {
        if (indexes[collection] > 0) {
          indexes[collection] -= 1;
          update();
        }
      });
      nextBtn?.addEventListener('click', () => {
        const frames = activeFrames();
        if (indexes[collection] < frames.length - 1) {
          indexes[collection] += 1;
          update();
        }
      });
      endBtn?.addEventListener('click', () => {
        const frames = activeFrames();
        indexes[collection] = Math.max(frames.length - 1, 0);
        update();
      });
      showAllToggle?.addEventListener('change', () => {
        collection = showAllToggle.checked && collections.all.length > 0 ? 'all' : 'visual';
        update();
      });

      update();
    });
  </script>
    """


def _render_history_frame(
    frame: dict[str, object],
    *,
    collection: str,
    active_index: int,
    hidden_collection: bool = False,
) -> str:
    hidden = hidden_collection or int(frame["index"]) != active_index
    return f'''
        <div class="graph-history-frame" data-history-frame data-history-collection="{collection}" data-caption-html="{escape(str(frame["caption_html"]))}" data-label="{escape(str(frame["label"]))}" data-timestamp="{escape(str(frame["timestamp"]))}" {"hidden" if hidden else ""}>
          {render_graph_widget(frame["graph"], widget_key=f"history-{collection}-{frame['index']}")}
        </div>
        '''
