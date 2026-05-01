#!/usr/bin/env python3
"""Summarize Formal Islands runs for paper-facing proof-review audits.

The script is intentionally read-only with respect to run artifacts.  It
prefers ``04_report_bundle.json`` because that is the public report payload, and
falls back to ``03_formalized_graph.json`` for older runs.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ACCEPTED_CLASSES = {"full_node", "concrete_sublemma"}
PACKAGING_MARKERS = (
    ".olean",
    "object file",
    "unknown identifier",
    "unknown constant",
    "unknown namespace",
    "expected token",
    "failed to synthesize instance",
    "invalid field",
    "unknown type",
    "syntax error",
)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _resolve_path(path_text: str | None, *, base: Path = REPO_ROOT) -> Path | None:
    if not path_text:
        return None
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = base / path
    return path


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _load_run_payload(run_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    bundle_path = run_dir / "04_report_bundle.json"
    if bundle_path.exists():
        bundle = _read_json(bundle_path)
        graph = bundle.get("graph")
        if not isinstance(graph, dict):
            raise ValueError(f"{bundle_path} does not contain a graph object")
        obligations = bundle.get("review_obligations", [])
        if not isinstance(obligations, list):
            obligations = []
        return graph, obligations, _display_path(bundle_path)

    graph_path = run_dir / "03_formalized_graph.json"
    if graph_path.exists():
        return _read_json(graph_path), [], _display_path(graph_path)

    raise FileNotFoundError(
        f"expected 04_report_bundle.json or 03_formalized_graph.json in {run_dir}"
    )


def _nodes_by_id(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes = graph.get("nodes", [])
    if not isinstance(nodes, list):
        raise ValueError("graph.nodes must be a list")
    return {str(node.get("id")): node for node in nodes if isinstance(node, dict)}


def _artifact(node: dict[str, Any]) -> dict[str, Any] | None:
    artifact = node.get("formal_artifact")
    return artifact if isinstance(artifact, dict) else None


def _verification(artifact: dict[str, Any] | None) -> dict[str, Any]:
    if artifact is None:
        return {}
    verification = artifact.get("verification")
    return verification if isinstance(verification, dict) else {}


def _artifact_class(node: dict[str, Any]) -> str:
    artifact = _artifact(node)
    if artifact is None:
        return ""
    classification = artifact.get("faithfulness_classification")
    return str(classification or "")


def _artifact_verified(node: dict[str, Any]) -> bool:
    return _verification(_artifact(node)).get("status") == "verified"


def _accepted_certified(node: dict[str, Any]) -> bool:
    return _artifact_verified(node) and _artifact_class(node) in ACCEPTED_CLASSES


def _squash(text: object, *, limit: int = 260) -> str:
    if not isinstance(text, str):
        return ""
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _attempt_category(attempt: dict[str, Any]) -> str:
    command = str(attempt.get("command") or "").lower()
    stdout = str(attempt.get("stdout") or "")
    stderr = str(attempt.get("stderr") or "")
    text = f"{stdout}\n{stderr}".lower()
    status = str(attempt.get("status") or "")
    if status == "verified":
        return "verified_attempt"
    if command == "backend_request":
        return "backend_failure"
    if command == "faithfulness_guard":
        return "faithfulness_guard_failure"
    if any(marker in text for marker in PACKAGING_MARKERS):
        return "packaging_failure"
    if status == "failed":
        return "lean_failure"
    return "other"


def _iter_attempts(nodes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for node in nodes.values():
        artifact = _artifact(node)
        if artifact is None:
            continue
        history = artifact.get("attempt_history", [])
        if isinstance(history, list):
            attempts.extend(item for item in history if isinstance(item, dict))
        verification = artifact.get("verification")
        if isinstance(verification, dict) and verification not in attempts:
            attempts.append(verification)
    return attempts


def _load_manual_audits(path: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    data = _read_json(path)
    audits = data.get("audits", [])
    if not isinstance(audits, list):
        return {}
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for audit in audits:
        if not isinstance(audit, dict):
            continue
        example_id = str(audit.get("example_id") or "")
        node_id = str(audit.get("node_id") or "")
        if example_id and node_id:
            by_key[(example_id, node_id)] = audit
    return by_key


def _manual_value(
    manual: dict[tuple[str, str], dict[str, Any]], example_id: str, node_id: str, key: str
) -> str:
    value = manual.get((example_id, node_id), {}).get(key)
    return str(value or "")


def _node_role(
    manual: dict[tuple[str, str], dict[str, Any]], example_id: str, node: dict[str, Any]
) -> str:
    node_id = str(node.get("id") or "")
    role = _manual_value(manual, example_id, node_id, "certified_role")
    if role:
        return role
    title = str(node.get("title") or node_id)
    classification = _artifact_class(node)
    if classification == "concrete_sublemma":
        return f"faithful supporting core for {title}"
    return "pending manual role label"


def _formal_islands_level(
    *,
    graph: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    root_id: str,
) -> int:
    root = nodes.get(root_id, {})
    if _accepted_certified(root) and _artifact_class(root) == "full_node":
        return 4

    accepted_ids = {node_id for node_id, node in nodes.items() if _accepted_certified(node)}
    edges = graph.get("edges", [])
    if isinstance(edges, list):
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("source_id") or "")
            target = str(edge.get("target_id") or "")
            if source in accepted_ids and target in accepted_ids and source != root_id:
                return 3

    if any(_artifact_class(node) == "concrete_sublemma" for node in nodes.values() if _accepted_certified(node)):
        return 2
    if accepted_ids:
        return 1
    return 0


def _summarize_run(
    manifest_entry: dict[str, Any],
    manual: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    example_id = str(manifest_entry["example_id"])
    run_dir = _resolve_path(str(manifest_entry["run_dir"]))
    if run_dir is None:
        raise ValueError(f"{example_id}: missing run_dir")
    graph, review_obligations, source_file = _load_run_payload(run_dir)
    nodes = _nodes_by_id(graph)
    root_id = str(graph.get("root_node_id") or "")
    root = nodes.get(root_id, {})

    accepted_nodes = {
        node_id: node for node_id, node in nodes.items() if _accepted_certified(node)
    }
    full_node_matches = [
        node_id for node_id, node in accepted_nodes.items() if _artifact_class(node) == "full_node"
    ]
    faithful_cores = [
        node_id
        for node_id, node in accepted_nodes.items()
        if _artifact_class(node) == "concrete_sublemma"
    ]
    compiled_but_downgraded = [
        node_id
        for node_id, node in nodes.items()
        if _artifact_verified(node) and _artifact_class(node) != "full_node"
    ]

    attempt_counts = {
        "verified_attempt": 0,
        "backend_failure": 0,
        "faithfulness_guard_failure": 0,
        "packaging_failure": 0,
        "lean_failure": 0,
        "other": 0,
    }
    for attempt in _iter_attempts(nodes):
        attempt_counts[_attempt_category(attempt)] += 1

    remaining_items = [
        f"{node_id}: {_squash(node.get('remaining_proof_burden'), limit=180)}"
        for node_id, node in nodes.items()
        if not (_accepted_certified(node) and _artifact_class(node) == "full_node")
        and _squash(node.get("remaining_proof_burden"), limit=180)
    ]
    accepted_text = "; ".join(
        f"{node_id} ({_artifact_class(node)})" for node_id, node in accepted_nodes.items()
    )
    certified_roles = "; ".join(
        f"{node_id}: {_node_role(manual, example_id, node)}"
        for node_id, node in accepted_nodes.items()
    )
    manual_statuses = [
        _manual_value(manual, example_id, node_id, "manual_faithfulness") or "not_audited"
        for node_id in accepted_nodes
    ]

    root_verified = _accepted_certified(root) and _artifact_class(root) == "full_node"
    semantic_attrition = (
        attempt_counts["faithfulness_guard_failure"] + len(compiled_but_downgraded)
    )
    return {
        "example_id": example_id,
        "theorem": manifest_entry.get("theorem", graph.get("theorem_title", "")),
        "domain": manifest_entry.get("domain", ""),
        "selection_bucket": manifest_entry.get("selection_bucket", ""),
        "reason_included": manifest_entry.get("reason_included", ""),
        "run_dir": str(Path(str(manifest_entry["run_dir"]))),
        "source_file": source_file,
        "direct_root_result": manifest_entry.get("direct_root_result", "not_run"),
        "formal_islands_level": _formal_islands_level(
            graph=graph, nodes=nodes, root_id=root_id
        ),
        "root_verified": root_verified,
        "total_nodes": len(nodes),
        "formal_verified_nodes": sum(
            1 for node in nodes.values() if node.get("status") == "formal_verified"
        ),
        "accepted_certified_obligations_count": len(accepted_nodes),
        "accepted_certified_obligations": accepted_text,
        "accepted_full_node_matches": len(full_node_matches),
        "faithful_cores": len(faithful_cores),
        "compiled_but_downgraded_or_core": len(compiled_but_downgraded),
        "semantic_attrition_events": semantic_attrition,
        "faithfulness_guard_failures": attempt_counts["faithfulness_guard_failure"],
        "lean_failures": attempt_counts["lean_failure"],
        "packaging_failures": attempt_counts["packaging_failure"],
        "backend_failures": attempt_counts["backend_failure"],
        "verified_attempts": attempt_counts["verified_attempt"],
        "review_obligations": len(review_obligations),
        "certified_roles": certified_roles,
        "manual_audit_statuses": "; ".join(manual_statuses),
        "remaining_burden": " | ".join(remaining_items),
        "main_lesson": manifest_entry.get("main_lesson", ""),
    }


def _aggregate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"metric": "examples", "value": len(rows)},
        {
            "metric": "formal_islands_root_verified",
            "value": sum(1 for row in rows if row["root_verified"]),
        },
        {
            "metric": "examples_with_non_root_certified_islands",
            "value": sum(
                1
                for row in rows
                if row["accepted_certified_obligations_count"]
                > (1 if row["root_verified"] else 0)
            ),
        },
        {
            "metric": "accepted_certified_obligations",
            "value": sum(row["accepted_certified_obligations_count"] for row in rows),
        },
        {
            "metric": "accepted_full_node_matches",
            "value": sum(row["accepted_full_node_matches"] for row in rows),
        },
        {"metric": "faithful_cores", "value": sum(row["faithful_cores"] for row in rows)},
        {
            "metric": "semantic_attrition_events",
            "value": sum(row["semantic_attrition_events"] for row in rows),
        },
        {
            "metric": "faithfulness_guard_failures",
            "value": sum(row["faithfulness_guard_failures"] for row in rows),
        },
        {"metric": "lean_failures", "value": sum(row["lean_failures"] for row in rows)},
        {
            "metric": "examples_with_explicit_remaining_burden",
            "value": sum(1 for row in rows if row["remaining_burden"]),
        },
    ]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("paper/notes/eval_manifest.json"),
        help="JSON manifest listing run directories and evaluation metadata.",
    )
    parser.add_argument(
        "--manual-audit",
        type=Path,
        default=Path("paper/notes/manual_audit.json"),
        help="Optional JSON overlay for manual semantic audit and certified-role labels.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("paper/tables"),
        help="Directory for generated CSV/JSON summaries.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest_path = _resolve_path(str(args.manifest))
    audit_path = _resolve_path(str(args.manual_audit))
    output_dir = _resolve_path(str(args.output_dir))
    if manifest_path is None or output_dir is None:
        raise ValueError("manifest and output-dir are required")
    manifest = _read_json(manifest_path)
    runs = manifest.get("runs", [])
    if not isinstance(runs, list):
        raise ValueError("manifest.runs must be a list")

    manual = _load_manual_audits(audit_path)
    rows = [_summarize_run(entry, manual) for entry in runs if isinstance(entry, dict)]
    aggregate = _aggregate(rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "artifact_audit.csv", rows)
    _write_csv(output_dir / "aggregate_summary.csv", aggregate)
    _write_json(output_dir / "artifact_audit.json", rows)
    _write_json(output_dir / "aggregate_summary.json", aggregate)

    print(f"wrote {len(rows)} run summaries to {_display_path(output_dir)}")


if __name__ == "__main__":
    main()
