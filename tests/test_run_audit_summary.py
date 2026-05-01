from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "summarize_formalization_runs.py"


def load_summary_module():
    spec = importlib.util.spec_from_file_location("summarize_formalization_runs", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_report_bundle_is_preferred_and_attrition_is_counted(tmp_path: Path) -> None:
    module = load_summary_module()
    run_dir = tmp_path / "run"
    graph = {
        "theorem_title": "Synthetic theorem",
        "theorem_statement": "Synthetic statement",
        "root_node_id": "root",
        "edges": [{"source_id": "root", "target_id": "child"}],
        "nodes": [
            {
                "id": "root",
                "title": "Root",
                "informal_statement": "Root statement",
                "informal_proof_text": "Root proof",
                "status": "formal_verified",
                "remaining_proof_burden": "stale root burden that should be ignored",
                "formal_artifact": {
                    "lean_theorem_name": "root_thm",
                    "lean_statement": "theorem root_thm : True",
                    "lean_code": "theorem root_thm : True := by trivial",
                    "faithfulness_classification": "full_node",
                    "verification": {"status": "verified", "command": "lake env lean"},
                    "attempt_history": [
                        {
                            "status": "failed",
                            "command": "faithfulness_guard",
                            "stderr": "Formalization drifted too far.",
                        },
                        {"status": "verified", "command": "lake env lean"},
                    ],
                },
            },
            {
                "id": "child",
                "title": "Child",
                "informal_statement": "Child statement",
                "informal_proof_text": "Child proof",
                "status": "formal_verified",
                "formal_artifact": {
                    "lean_theorem_name": "child_core",
                    "lean_statement": "theorem child_core : True",
                    "lean_code": "theorem child_core : True := by trivial",
                    "faithfulness_classification": "concrete_sublemma",
                    "verification": {"status": "verified", "command": "lake env lean"},
                    "attempt_history": [
                        {
                            "status": "failed",
                            "command": "lake env lean synthetic.lean",
                            "stdout": "error: unsolved goals",
                        },
                        {"status": "verified", "command": "lake env lean"},
                    ],
                },
            },
        ],
    }
    write_json(run_dir / "04_report_bundle.json", {"graph": graph, "review_obligations": []})

    manual = {
        ("synthetic", "child"): {
            "manual_faithfulness": "pending_manual_audit",
            "certified_role": "synthetic certified role",
        }
    }
    row = module._summarize_run(
        {
            "example_id": "synthetic",
            "theorem": "Synthetic theorem",
            "domain": "test",
            "selection_bucket": "fixture",
            "run_dir": str(run_dir),
        },
        manual,
    )

    assert row["source_file"].endswith("04_report_bundle.json")
    assert row["root_verified"] is True
    assert row["formal_islands_level"] == 4
    assert row["faithfulness_guard_failures"] == 1
    assert row["lean_failures"] == 1
    assert row["faithful_cores"] == 1
    assert row["compiled_but_downgraded_or_core"] == 1
    assert "synthetic certified role" in row["certified_roles"]
    assert "stale root burden" not in row["remaining_burden"]


def test_graph_fallback_works_for_older_runs(tmp_path: Path) -> None:
    module = load_summary_module()
    run_dir = tmp_path / "old-run"
    graph = {
        "theorem_title": "Old theorem",
        "theorem_statement": "Old statement",
        "root_node_id": "root",
        "edges": [],
        "nodes": [
            {
                "id": "root",
                "title": "Root",
                "informal_statement": "Root statement",
                "informal_proof_text": "Root proof",
                "status": "informal",
            }
        ],
    }
    write_json(run_dir / "03_formalized_graph.json", graph)

    row = module._summarize_run(
        {
            "example_id": "old",
            "theorem": "Old theorem",
            "domain": "test",
            "selection_bucket": "fixture",
            "run_dir": str(run_dir),
        },
        {},
    )

    assert row["source_file"].endswith("03_formalized_graph.json")
    assert row["formal_islands_level"] == 0
    assert row["accepted_certified_obligations_count"] == 0
