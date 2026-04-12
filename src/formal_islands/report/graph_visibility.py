"""Helpers for deciding which graph nodes should be hidden from displays."""

from __future__ import annotations

from formal_islands.models import ProofGraph


def subsumed_informal_node_ids(graph: ProofGraph) -> set[str]:
    """Identify informal child nodes that appear subsumed by a verified parent theorem."""

    parent_ids_by_child: dict[str, set[str]] = {}
    node_by_id = {node.id: node for node in graph.nodes}
    for edge in graph.edges:
        parent_ids_by_child.setdefault(edge.target_id, set()).add(edge.source_id)

    hidden_ids: set[str] = set()
    for node in graph.nodes:
        if node.status != "informal":
            continue
        if node.formal_artifact is not None or node.last_formalization_outcome is not None:
            continue
        parent_ids = parent_ids_by_child.get(node.id, set())
        if not parent_ids:
            continue
        if any(
            parent_id in node_by_id and node_by_id[parent_id].status == "formal_verified"
            for parent_id in parent_ids
        ):
            hidden_ids.add(node.id)

    # If an informal node is hidden because a verified parent discharged it, then any downstream
    # informal dependency chain that is only reachable through already-hidden parents should also
    # disappear from the final display. Otherwise the report can show disconnected informal nodes
    # whose only purpose was to support the now-hidden subsumed node.
    changed = True
    while changed:
        changed = False
        for node in graph.nodes:
            if node.id in hidden_ids:
                continue
            if node.status != "informal":
                continue
            if node.formal_artifact is not None or node.last_formalization_outcome is not None:
                continue
            parent_ids = parent_ids_by_child.get(node.id, set())
            if parent_ids and parent_ids.issubset(hidden_ids):
                hidden_ids.add(node.id)
                changed = True
    return hidden_ids


def display_graph_without_hidden_subsumed_nodes(
    graph: ProofGraph,
    hidden_node_ids: set[str] | None = None,
) -> ProofGraph:
    """Return a copy of the graph with hidden subsumed nodes removed."""

    hidden_node_ids = subsumed_informal_node_ids(graph) if hidden_node_ids is None else hidden_node_ids
    if not hidden_node_ids:
        return graph
    visible_nodes = [node for node in graph.nodes if node.id not in hidden_node_ids]
    visible_edges = [
        edge
        for edge in graph.edges
        if edge.source_id not in hidden_node_ids and edge.target_id not in hidden_node_ids
    ]
    return graph.model_copy(update={"nodes": visible_nodes, "edges": visible_edges})
