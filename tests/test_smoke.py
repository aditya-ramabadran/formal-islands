from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from formal_islands.backends import (
    AristotleBackend,
    BackendInvocationError,
    BackendUnavailableError,
    MockBackend,
)
from formal_islands.formalization.lean import LeanVerifier, LeanWorkspace
from formal_islands.formalization import FormalizationOutcome, formalize_candidate_nodes
from formal_islands.models import ProofEdge, ProofGraph, ProofNode
from formal_islands.progress import progress, use_progress_log
from formal_islands.review import derive_review_obligations
from formal_islands.progress import append_graph_summary_to_progress_log
from formal_islands.smoke import (
    _backend_failure_outcome,
    _cleanup_archive_artifacts,
    cmd_plan,
    cmd_run_benchmark,
    cmd_formalize_all_candidates,
    default_output_dir_for_input,
    ensure_output_dir,
    load_graph,
    load_input_payload,
    select_candidate_node_id,
    write_graph,
)
from formal_islands.report import export_report_bundle, render_html_report
from formal_islands.extraction import extract_proof_graph, select_formalization_candidates
from formal_islands.formalization import formalize_candidate_node
from formal_islands.backends import ClaudeCodeBackend, CodexCLIBackend, GeminiCLIBackend


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
    assert payload["theorem_title"] == "Toy theorem"


def test_load_input_payload_backfills_theorem_title_from_legacy_hint(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "theorem_title_hint": "Legacy title",
                "theorem_statement": "If A then B.",
                "raw_proof_text": "Assume A. Then B.",
            }
        ),
        encoding="utf-8",
    )

    payload = load_input_payload(input_path)

    assert payload["theorem_title"] == "Legacy title"


def test_select_candidate_node_id_uses_lowest_priority_number_then_id() -> None:
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

    assert select_candidate_node_id(graph) == "n0"


def test_progress_log_appends_without_replacing_existing_content(tmp_path: Path) -> None:
    progress_log = tmp_path / "_progress.log"
    progress_log.write_text("preexisting line\n", encoding="utf-8")

    with use_progress_log(progress_log):
        progress("first message")
    with use_progress_log(progress_log):
        progress("second message")

    log_text = progress_log.read_text(encoding="utf-8")
    assert "preexisting line" in log_text
    assert "first message" in log_text
    assert "second message" in log_text


def test_progress_log_normalizes_prefixed_messages(tmp_path: Path) -> None:
    progress_log = tmp_path / "_progress.log"

    with use_progress_log(progress_log):
        progress("[formal-islands] already-prefixed message")

    log_text = progress_log.read_text(encoding="utf-8")
    assert log_text.count("[formal-islands]") == 1
    assert "already-prefixed message" in log_text


def test_graph_summary_logging_records_nodes_and_edges(tmp_path: Path) -> None:
    progress_log = tmp_path / "_progress.log"
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="A implies B.",
        root_node_id="n1",
        nodes=[
            ProofNode(
                id="n1",
                title="Root",
                informal_statement="A implies B.",
                informal_proof_text="Use n2.",
            ),
            ProofNode(
                id="n2",
                title="Lemma",
                informal_statement="A.",
                informal_proof_text="Given.",
                status="candidate_formal",
                formalization_priority=2,
                formalization_rationale="Useful local fact.",
            ),
        ],
        edges=[ProofEdge(source_id="n1", target_id="n2", label="uses")],
    )

    with use_progress_log(progress_log):
        append_graph_summary_to_progress_log(graph, label="02_candidate_graph.json")

    log_text = progress_log.read_text(encoding="utf-8")
    assert "02_candidate_graph.json: graph summary" in log_text
    assert "n1 -> n2" in log_text
    assert "[uses]" not in log_text
    assert "[candidate_formal] n2" in log_text
    assert "stmt: A." in log_text


def test_cleanup_archive_artifacts_deletes_tarballs_and_logs(tmp_path: Path) -> None:
    output_dir = tmp_path / "artifacts"
    backend_logs = output_dir / "_backend_logs"
    backend_logs.mkdir(parents=True)
    archive_one = backend_logs / "one.tar.gz"
    archive_two = output_dir / "two.tar.gz"
    archive_one.write_text("payload", encoding="utf-8")
    archive_two.write_text("payload", encoding="utf-8")
    progress_log = output_dir / "_progress.log"

    with use_progress_log(progress_log):
        removed = _cleanup_archive_artifacts(output_dir)

    assert archive_one.exists() is False
    assert archive_two.exists() is False
    assert len(removed) == 2
    log_text = progress_log.read_text(encoding="utf-8")
    assert "cleaned up 2 archive artifact(s)" in log_text


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


def test_cmd_plan_writes_both_planning_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
                "candidates": [
                    {
                        "node_id": "n2",
                        "priority": 2,
                        "rationale": "Leaf technical node.",
                    }
                ],
            }
        ]
    )

    monkeypatch.setattr("formal_islands.smoke.build_backend", lambda *args, **kwargs: backend)
    output_dir = tmp_path / "artifacts"
    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "theorem_title_hint": "Nonnegative sum",
                "theorem_statement": "If a and b are nonnegative, then a + b is nonnegative.",
                "raw_proof_text": "Assume 0 <= a and 0 <= b. Then 0 <= a + b.",
            }
        ),
        encoding="utf-8",
    )

    exit_code = cmd_plan(
        Namespace(
            backend="codex",
            model=None,
            input=str(input_path),
            output_dir=str(output_dir),
        )
    )

    assert exit_code == 0
    extracted_graph = load_graph(output_dir / "01_extracted_graph.json")
    candidate_graph = load_graph(output_dir / "02_candidate_graph.json")
    assert all(node.status == "informal" for node in extracted_graph.nodes)
    assert any(node.status == "candidate_formal" for node in candidate_graph.nodes)


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
    assert backend.timeout_seconds == 360.0


def test_build_backend_supports_claude(tmp_path: Path) -> None:
    from formal_islands.smoke import build_backend

    backend = build_backend("claude", "sonnet", tmp_path / "_backend_logs")

    assert isinstance(backend, ClaudeCodeBackend)
    assert backend.model == "sonnet"
    assert backend.log_dir == tmp_path / "_backend_logs"
    assert backend.timeout_seconds == 360.0


def test_build_backend_supports_gemini(tmp_path: Path) -> None:
    from formal_islands.smoke import build_backend

    backend = build_backend("gemini", "gemini-2.5-flash", tmp_path / "_backend_logs")

    assert isinstance(backend, GeminiCLIBackend)
    assert backend.model == "gemini-2.5-flash"
    assert backend.log_dir == tmp_path / "_backend_logs"
    assert backend.timeout_seconds == 360.0


def test_build_backend_supports_aristotle_for_formalization(tmp_path: Path) -> None:
    from formal_islands.smoke import build_backend

    backend = build_backend(
        "aristotle",
        None,
        tmp_path / "_backend_logs",
        formalization=True,
        timeout_seconds=900.0,
    )

    assert isinstance(backend, AristotleBackend)
    assert backend.log_dir == tmp_path / "_backend_logs"
    assert backend.timeout_seconds == 900.0


def test_build_backend_defaults_aristotle_timeout_to_none(tmp_path: Path) -> None:
    from formal_islands.smoke import build_backend

    backend = build_backend("aristotle", None, tmp_path / "_backend_logs", formalization=True)

    assert isinstance(backend, AristotleBackend)
    assert backend.timeout_seconds is None


def test_build_backend_rejects_aristotle_for_planning(tmp_path: Path) -> None:
    from formal_islands.smoke import build_backend

    with pytest.raises(ValueError, match="formalization backends"):
        build_backend("aristotle", None, tmp_path / "_backend_logs")


def test_aristotle_backend_requires_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from formal_islands.backends.aristotle import AristotleBackend

    monkeypatch.delenv("ARISTOTLE_API_KEY", raising=False)
    backend = AristotleBackend(log_dir=tmp_path / "_backend_logs", timeout_seconds=1.0)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    with pytest.raises(BackendUnavailableError, match="ARISTOTLE_API_KEY"):
        backend.submit_project(
            prompt="Prove 1 = 1.",
            project_dir=project_dir,
            task_name="test_task",
        )


def test_build_backend_allows_formalization_timeout_override(tmp_path: Path) -> None:
    from formal_islands.smoke import build_backend

    backend = build_backend(
        "codex",
        "gpt-5.4",
        tmp_path / "_backend_logs",
        timeout_seconds=420.0,
    )

    assert backend.timeout_seconds == 420.0


def test_default_output_dir_for_input_uses_manual_testing_slug() -> None:
    path = Path("examples/manual-testing/run11_two_point_log_sobolev.json")

    assert default_output_dir_for_input(path) == Path(
        "artifacts/manual-testing/run11-two-point-log-sobolev"
    )


def test_cmd_run_benchmark_orchestrates_pipeline_with_default_output_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    seen_timeout: list[float] = []
    input_path = tmp_path / "run11_two_point_log_sobolev.json"
    input_path.write_text(
        json.dumps(
            {
                "theorem_title": "Run 11",
                "theorem_statement": "If A then B.",
                "raw_proof_text": "Proof.",
            }
        ),
        encoding="utf-8",
    )

    def fake_plan(args: Namespace) -> int:
        calls.append(("plan", args.output_dir))
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        write_graph(
            ProofGraph(
                theorem_title="Run 11",
                theorem_statement="If A then B.",
                root_node_id="n1",
                nodes=[
                    ProofNode(
                        id="n1",
                        title="Main claim",
                        informal_statement="B",
                        informal_proof_text="Use n2",
                    ),
                    ProofNode(
                        id="n2",
                        title="Leaf",
                        informal_statement="A -> B",
                        informal_proof_text="...",
                        status="candidate_formal",
                        formalization_priority=1,
                        formalization_rationale="leaf",
                    ),
                ],
                edges=[ProofEdge(source_id="n1", target_id="n2")],
            ),
            out / "02_candidate_graph.json",
        )
        return 0

    def fake_formalize(args: Namespace) -> int:
        calls.append(("formalize", args.output_dir))
        seen_timeout.append(args.formalization_timeout_seconds)
        out = Path(args.output_dir)
        write_graph(load_graph(out / "02_candidate_graph.json"), out / "03_formalized_graph.json")
        return 0

    def fake_report(args: Namespace) -> int:
        calls.append(("report", args.output_dir))
        out = Path(args.output_dir)
        (out / "04_report.html").write_text("<html></html>", encoding="utf-8")
        return 0

    monkeypatch.setattr("formal_islands.smoke.cmd_plan", fake_plan)
    monkeypatch.setattr("formal_islands.smoke.cmd_formalize_all_candidates", fake_formalize)
    monkeypatch.setattr("formal_islands.smoke.cmd_report", fake_report)

    exit_code = cmd_run_benchmark(
        Namespace(
            backend="codex",
            model=None,
            input=str(input_path),
            output_dir=None,
            workspace="lean_project",
            node_id="auto",
            max_attempts=4,
            formalization_mode="agentic",
            formalization_timeout_seconds=900.0,
        )
    )

    expected_output_dir = Path("artifacts/manual-testing/run11-two-point-log-sobolev")
    assert exit_code == 0
    assert calls == [
        ("plan", str(expected_output_dir)),
        ("formalize", str(expected_output_dir)),
        ("report", str(expected_output_dir)),
    ]
    assert seen_timeout == [900.0]
    progress_log = expected_output_dir / "_progress.log"
    assert progress_log.exists()
    log_text = progress_log.read_text(encoding="utf-8")
    assert "running benchmark end-to-end" in log_text
    assert "benchmark planning stage starting" in log_text
    assert "benchmark formalization stage starting" in log_text
    assert "benchmark report stage starting" in log_text


def test_cmd_plan_and_formalize_support_split_backends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from formal_islands.smoke import cmd_formalize_one, cmd_plan

    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "theorem_title": "Split backends",
                "theorem_statement": "If A then B.",
                "raw_proof_text": "Proof.",
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "artifacts"
    graph = ProofGraph(
        theorem_title="Split backends",
        theorem_statement="If A then B.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Main",
                informal_statement="B",
                informal_proof_text="Use n1.",
            ),
            ProofNode(
                id="n1",
                title="Candidate",
                informal_statement="A -> B",
                informal_proof_text="...",
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="Candidate",
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1")],
    )

    build_calls: list[tuple[str, bool]] = []

    def fake_build_backend(name: str, model: str | None, log_dir: Path | None = None, timeout_seconds: float = 0.0, formalization: bool = False):
        build_calls.append((name, formalization))
        return object()

    def fake_plan_proof_graph(**kwargs):
        return type(
            "Artifacts",
            (),
            {
                "extracted_graph": graph.model_copy(),
                "candidate_graph": graph.model_copy(
                    update={
                        "nodes": [
                            node.model_copy(update={"status": "candidate_formal"})
                            if node.id == "n1"
                            else node
                            for node in graph.nodes
                        ]
                    }
                ),
            },
        )()

    def fake_formalize_candidate_node(**kwargs):
        return type(
            "Outcome",
            (),
            {
                "graph": graph.model_copy(),
                "node_id": "n1",
                "artifact": type(
                    "Artifact",
                    (),
                    {
                        "verification": type(
                            "Verification",
                            (),
                            {
                                "status": "verified",
                                "artifact_path": "path",
                                "attempt_count": 1,
                                "stderr": "",
                            },
                        )(),
                        "faithfulness_classification": "full_node",
                        "lean_theorem_name": "t",
                    },
                )(),
            },
        )()

    monkeypatch.setattr("formal_islands.smoke.build_backend", fake_build_backend)
    monkeypatch.setattr("formal_islands.smoke.plan_proof_graph", fake_plan_proof_graph)
    monkeypatch.setattr("formal_islands.smoke.formalize_candidate_node", fake_formalize_candidate_node)

    plan_exit = cmd_plan(
        Namespace(
            backend=None,
            model=None,
            planning_backend="claude",
            planning_model=None,
            formalization_backend="aristotle",
            formalization_model=None,
            input=str(input_path),
            output_dir=str(output_dir),
        )
    )
    formalize_exit = cmd_formalize_one(
        Namespace(
            backend=None,
            model=None,
            planning_backend="claude",
            planning_model=None,
            formalization_backend="aristotle",
            formalization_model=None,
            graph=str(output_dir / "02_candidate_graph.json"),
            output_dir=str(output_dir),
            workspace="lean_project",
            node_id="n1",
            max_attempts=1,
            formalization_mode="agentic",
            formalization_timeout_seconds=900.0,
        )
    )

    assert plan_exit == 0
    assert formalize_exit == 0
    assert build_calls == [
        ("claude", False),
        ("claude", False),
        ("aristotle", True),
    ]


def test_formalize_all_candidates_runs_aristotle_jobs_in_parallel_and_preserves_diffs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    import time

    from formal_islands.backends.aristotle import AristotleBackend
    from formal_islands.formalization.loop import formalize_candidate_nodes
    from formal_islands.models import FormalArtifact, VerificationResult

    graph = ProofGraph(
        theorem_title="Parallel Aristotle",
        theorem_statement="If A then B.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Root",
                informal_statement="B.",
                informal_proof_text="Use both leaves.",
            ),
            ProofNode(
                id="n1",
                title="Leaf 1",
                informal_statement="A -> B.",
                informal_proof_text="...",
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="First leaf.",
            ),
            ProofNode(
                id="n2",
                title="Leaf 2",
                informal_statement="A -> B.",
                informal_proof_text="...",
                status="candidate_formal",
                formalization_priority=2,
                formalization_rationale="Second leaf.",
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1"), ProofEdge(source_id="n0", target_id="n2")],
    )

    start_times: list[tuple[str, float]] = []
    start_lock = threading.Lock()

    def fake_formalize_candidate_node(**kwargs):
        node_id = kwargs["node_id"]
        base_graph = kwargs["graph"]
        with start_lock:
            start_times.append((node_id, time.perf_counter()))
        time.sleep(0.25)

        updated_nodes = []
        for node in base_graph.nodes:
            if node.id == node_id:
                updated_nodes.append(
                    node.model_copy(
                        update={
                            "status": "formal_verified",
                            "formal_artifact": FormalArtifact(
                                lean_theorem_name=f"{node_id}_thm",
                                lean_statement="theorem t : True",
                                lean_code="theorem t : True := by trivial",
                                verification=VerificationResult(
                                    status="verified",
                                    command="lake env lean test.lean",
                                    attempt_count=1,
                                    artifact_path="test.lean",
                                ),
                            ),
                        }
                    )
                )
            elif node.id == "n0" and node_id == "n1":
                updated_nodes.append(
                    node.model_copy(
                        update={
                            "status": "candidate_formal",
                            "formalization_priority": 3,
                            "formalization_rationale": "Promoted by n1.",
                        }
                    )
                )
            else:
                updated_nodes.append(node)

        updated_graph = base_graph.model_copy(update={"nodes": updated_nodes})
        return FormalizationOutcome(
            graph=updated_graph,
            node_id=node_id,
            artifact=next(node.formal_artifact for node in updated_graph.nodes if node.id == node_id),
        )

    monkeypatch.setattr("formal_islands.formalization.loop.formalize_candidate_node", fake_formalize_candidate_node)

    outcome = formalize_candidate_nodes(
        backend=AristotleBackend(log_dir=None, timeout_seconds=None),
        verifier=LeanVerifier(workspace=build_workspace(tmp_path / "lean_project")),
        graph=graph,
        node_ids=None,
        max_attempts=1,
        mode="agentic",
    )

    root = next(node for node in outcome.graph.nodes if node.id == "n0")
    assert len(start_times) == 3
    first_two = sorted(start_times, key=lambda item: item[1])[:2]
    third = sorted(start_times, key=lambda item: item[1])[2]
    assert {node_id for node_id, _ in start_times} == {"n0", "n1", "n2"}
    assert max(t for _, t in first_two) - min(t for _, t in first_two) < 0.15
    assert third[1] - max(t for _, t in first_two) > 0.15
    assert root.status == "formal_verified"
    assert root.formalization_priority == 3
    assert {node.id for node in outcome.graph.nodes if node.status == "formal_verified"} == {"n0", "n1", "n2"}


def test_cmd_formalize_all_candidates_writes_batch_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "artifacts"
    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="If A then B.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Main",
                informal_statement="Main.",
                informal_proof_text="Use leaves.",
            ),
            ProofNode(
                id="n1",
                title="Leaf 1",
                informal_statement="L1.",
                informal_proof_text="...",
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="first",
            ),
            ProofNode(
                id="n2",
                title="Leaf 2",
                informal_statement="L2.",
                informal_proof_text="...",
                status="candidate_formal",
                formalization_priority=2,
                formalization_rationale="second",
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1"), ProofEdge(source_id="n0", target_id="n2")],
    )
    output_dir.mkdir(parents=True)
    write_graph(graph, output_dir / "02_candidate_graph.json")

    class FakeOutcome:
        def __init__(self, graph, node_id):
            from formal_islands.models import FormalArtifact, VerificationResult

            self.graph = graph
            self.node_id = node_id
            self.artifact = FormalArtifact(
                lean_theorem_name=f"{node_id}_thm",
                lean_statement="theorem t : True",
                lean_code="theorem t : True := by trivial",
                verification=VerificationResult(
                    status="verified",
                    command="lake env lean test.lean",
                    attempt_count=1,
                    artifact_path="test.lean",
                ),
            )

    class FakeBatch:
        def __init__(self, graph):
            self.graph = graph
            self.outcomes = [FakeOutcome(graph, "n1"), FakeOutcome(graph, "n2")]

    monkeypatch.setattr("formal_islands.smoke.build_backend", lambda *args, **kwargs: object())
    monkeypatch.setattr("formal_islands.smoke.LeanVerifier", lambda *args, **kwargs: object())
    monkeypatch.setattr("formal_islands.smoke.LeanWorkspace", lambda *args, **kwargs: object())
    monkeypatch.setattr("formal_islands.smoke.formalize_candidate_nodes", lambda **kwargs: FakeBatch(graph))

    exit_code = cmd_formalize_all_candidates(
        Namespace(
            backend="codex",
            model=None,
            graph=str(output_dir / "02_candidate_graph.json"),
            output_dir=str(output_dir),
            workspace="lean_project",
            max_attempts=4,
            formalization_mode="agentic",
        )
    )

    summaries = json.loads((output_dir / "03_formalization_summaries.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert [item["node_id"] for item in summaries] == ["n1", "n2"]
