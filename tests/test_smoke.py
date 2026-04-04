from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path

from formal_islands.backends import BackendInvocationError, MockBackend
from formal_islands.formalization.lean import LeanVerifier, LeanWorkspace
from formal_islands.models import ProofEdge, ProofGraph, ProofNode
from formal_islands.review import derive_review_obligations
from formal_islands.smoke import (
    _backend_failure_outcome,
    ensure_output_dir,
    load_graph,
    load_input_payload,
    select_candidate_node_id,
    write_graph,
)
from formal_islands.report import export_report_bundle, render_html_report
from formal_islands.extraction import extract_proof_graph, select_formalization_candidates
from formal_islands.formalization import formalize_candidate_node
from formal_islands.backends import CodexCLIBackend


def build_workspace(root: Path) -> LeanWorkspace:
    (root / "FormalIslands").mkdir(parents=True)
    (root / "FormalIslands" / "Generated").mkdir(parents=True)
    (root / "lean-toolchain").write_text("leanprover/lean4:v4.29.0", encoding="utf-8")
    (root / "lakefile.toml").write_text('name = "FormalIslands"\n', encoding="utf-8")
    (root / "FormalIslands.lean").write_text("import FormalIslands.Basic\n", encoding="utf-8")
    return LeanWorkspace(root=root)


def test_load_input_payload_reads_example_json(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "theorem_title_hint": "Toy theorem",
                "theorem_statement": "If A then B.",
                "raw_proof_text": "Assume A. Then B.",
            }
        ),
        encoding="utf-8",
    )

    payload = load_input_payload(input_path)

    assert payload["theorem_statement"] == "If A then B."


def test_select_candidate_node_id_uses_highest_priority_then_id() -> None:
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="If A then B.",
        root_node_id="n1",
        nodes=[
            ProofNode(
                id="n1",
                title="Main claim",
                informal_statement="B",
                informal_proof_text="Use lemma",
            ),
            ProofNode(
                id="n2",
                title="Lemma 2",
                informal_statement="A -> B",
                informal_proof_text="...",
                status="candidate_formal",
                formalization_priority=2,
                formalization_rationale="Candidate",
            ),
            ProofNode(
                id="n0",
                title="Lemma 0",
                informal_statement="A -> B",
                informal_proof_text="...",
                status="candidate_formal",
                formalization_priority=2,
                formalization_rationale="Candidate",
            ),
            ProofNode(
                id="n3",
                title="Lemma 3",
                informal_statement="A -> B",
                informal_proof_text="...",
                status="candidate_formal",
                formalization_priority=3,
                formalization_rationale="Best",
            ),
        ],
        edges=[ProofEdge(source_id="n1", target_id="n2")],
    )

    assert select_candidate_node_id(graph) == "n3"


def test_smoke_stage_orchestration_writes_expected_outputs(tmp_path: Path) -> None:
    backend = MockBackend(
        queued_payloads=[
            {
                "theorem_title": "Nonnegative sum",
                "theorem_statement": "If a and b are nonnegative, then a + b is nonnegative.",
                "root_node_id": "n1",
                "nodes": [
                    {
                        "id": "n1",
                        "title": "Main conclusion",
                        "informal_statement": "0 <= a + b.",
                        "informal_proof_text": "Use n2.",
                    },
                    {
                        "id": "n2",
                        "title": "Arithmetic lemma",
                        "informal_statement": "0 <= a + b.",
                        "informal_proof_text": "Local arithmetic fact.",
                    },
                ],
                "edges": [{"source_id": "n1", "target_id": "n2"}],
            },
            {
                "candidates": [
                    {
                        "node_id": "n1",
                        "priority": 1,
                        "rationale": "Tiny root theorem.",
                    }
                ]
            },
            {
                "lean_theorem_name": "sum_nonneg",
                "lean_statement": "0 <= a + b",
                "lean_code": "import Mathlib\n\ntheorem sum_nonneg : 0 <= a + b := by\n  nlinarith",
            },
        ]
    )
    output_dir = ensure_output_dir(tmp_path / "artifacts")
    workspace = build_workspace(tmp_path / "lean_project")

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        cwd: Path,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    verifier = LeanVerifier(workspace=workspace, command_runner=fake_run)
    graph = extract_proof_graph(
        backend=backend,
        theorem_statement="If a and b are nonnegative, then a + b is nonnegative.",
        raw_proof_text="Assume 0 <= a and 0 <= b. Then 0 <= a + b.",
        theorem_title_hint="Nonnegative sum",
    )
    write_graph(graph, output_dir / "01_extracted_graph.json")

    candidate_graph = select_formalization_candidates(backend=backend, graph=graph)
    write_graph(candidate_graph, output_dir / "02_candidate_graph.json")

    outcome = formalize_candidate_node(
        backend=backend,
        verifier=verifier,
        graph=candidate_graph,
        node_id=select_candidate_node_id(candidate_graph),
        max_attempts=1,
    )
    write_graph(outcome.graph, output_dir / "03_formalized_graph.json")

    obligations = derive_review_obligations(outcome.graph)
    bundle = export_report_bundle(outcome.graph, obligations)
    (output_dir / "04_report_bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    (output_dir / "04_report.html").write_text(
        render_html_report(outcome.graph, obligations),
        encoding="utf-8",
    )

    final_graph = load_graph(output_dir / "03_formalized_graph.json")
    assert any(node.status == "formal_verified" for node in final_graph.nodes)
    assert (output_dir / "04_report.html").exists()


def test_backend_failure_outcome_marks_node_and_captures_logs() -> None:
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="If A then B.",
        root_node_id="n1",
        nodes=[
            ProofNode(
                id="n1",
                title="Main claim",
                informal_statement="B",
                informal_proof_text="Use lemma",
            ),
            ProofNode(
                id="n2",
                title="Lemma",
                informal_statement="A -> B",
                informal_proof_text="Local technical fact.",
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="Candidate",
            ),
        ],
        edges=[ProofEdge(source_id="n1", target_id="n2")],
    )

    outcome = _backend_failure_outcome(
        graph=graph,
        node_id="n2",
        error=BackendInvocationError("Codex timed out"),
    )

    updated_node = next(node for node in outcome.graph.nodes if node.id == "n2")
    assert updated_node.status == "formal_failed"
    assert updated_node.formal_artifact is not None
    assert "Codex timed out" in updated_node.formal_artifact.verification.stderr


def test_build_backend_configures_backend_logs(tmp_path: Path) -> None:
    from formal_islands.smoke import build_backend

    backend = build_backend("codex", "gpt-5.4", tmp_path / "_backend_logs")

    assert isinstance(backend, CodexCLIBackend)
    assert backend.model == "gpt-5.4"
    assert backend.log_dir == tmp_path / "_backend_logs"
    assert backend.timeout_seconds == 180.0


def test_build_backend_allows_formalization_timeout_override(tmp_path: Path) -> None:
    from formal_islands.smoke import build_backend

    backend = build_backend(
        "codex",
        "gpt-5.4",
        tmp_path / "_backend_logs",
        timeout_seconds=420.0,
    )

    assert backend.timeout_seconds == 420.0
