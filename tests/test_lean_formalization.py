from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from formal_islands.backends import MockBackend
from formal_islands.formalization.lean import LeanVerifier, LeanWorkspace
from formal_islands.formalization.loop import formalize_candidate_node
from formal_islands.models import ProofEdge, ProofGraph, ProofNode


def build_graph() -> ProofGraph:
    return ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="If a and b are nonnegative, then a + b is nonnegative.",
        root_node_id="n1",
        nodes=[
            ProofNode(
                id="n1",
                title="Main claim",
                informal_statement="a + b is nonnegative.",
                informal_proof_text="It follows from the arithmetic lemma n2.",
            ),
            ProofNode(
                id="n2",
                title="Arithmetic lemma",
                informal_statement="0 <= a + b when 0 <= a and 0 <= b.",
                informal_proof_text="This is a local technical fact.",
                status="candidate_formal",
                formalization_priority=3,
                formalization_rationale="Leaf arithmetic lemma.",
            ),
        ],
        edges=[ProofEdge(source_id="n1", target_id="n2")],
    )


def create_workspace(root: Path) -> LeanWorkspace:
    (root / "FormalIslands").mkdir(parents=True)
    (root / "FormalIslands" / "Generated").mkdir(parents=True)
    (root / "lean-toolchain").write_text("leanprover/lean4:v4.29.0", encoding="utf-8")
    (root / "lakefile.toml").write_text('name = "FormalIslands"\n', encoding="utf-8")
    (root / "FormalIslands.lean").write_text("import FormalIslands.Basic\n", encoding="utf-8")
    return LeanWorkspace(root=root)


def test_lean_workspace_writes_scratch_file(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)

    scratch_path = workspace.write_scratch_file("node/1", 2, "theorem t : True := by trivial")

    assert scratch_path.name == "node_1_attempt_2.lean"
    assert scratch_path.read_text(encoding="utf-8") == "theorem t : True := by trivial"


def test_lean_verifier_captures_command_result(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        cwd: Path,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert args[:3] == ["lake", "env", "lean"]
        assert cwd == tmp_path.resolve()
        assert args[3] == str((tmp_path / "FormalIslands" / "Generated" / "n2_attempt_1.lean").resolve())
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    verifier = LeanVerifier(workspace=workspace, command_runner=fake_run)
    result = verifier.verify_code(
        lean_code="theorem t : True := by trivial",
        node_id="n2",
        attempt_number=1,
    )

    assert result.status == "verified"
    assert result.exit_code == 0
    assert result.artifact_path is not None


def test_lean_verifier_handles_relative_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = create_workspace(Path("lean_project"))

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        cwd: Path,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == (tmp_path / "lean_project").resolve()
        assert args[3] == str(
            (tmp_path / "lean_project" / "FormalIslands" / "Generated" / "n2_attempt_1.lean").resolve()
        )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    verifier = LeanVerifier(workspace=workspace, command_runner=fake_run)
    result = verifier.verify_code(
        lean_code="theorem t : True := by trivial",
        node_id="n2",
        attempt_number=1,
    )

    assert result.status == "verified"


def test_formalize_candidate_node_records_retry_history(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)
    verifier_results = iter(
        [
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="unknown identifier"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
        ]
    )

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        cwd: Path,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return next(verifier_results)

    verifier = LeanVerifier(workspace=workspace, command_runner=fake_run)
    backend = MockBackend(
        queued_payloads=[
            {
                "lean_theorem_name": "sum_nonneg_attempt_1",
                "lean_statement": "0 <= a + b",
                "lean_code": "import Mathlib\n\ntheorem sum_nonneg_attempt_1 : 0 <= a + b := by\n  simp",
            },
            {
                "lean_theorem_name": "sum_nonneg",
                "lean_statement": "0 <= a + b",
                "lean_code": "import Mathlib\n\ntheorem sum_nonneg : 0 <= a + b := by\n  nlinarith",
            },
        ]
    )

    outcome = formalize_candidate_node(
        backend=backend,
        verifier=verifier,
        graph=build_graph(),
        node_id="n2",
        max_attempts=2,
    )

    updated_node = next(node for node in outcome.graph.nodes if node.id == "n2")

    assert updated_node.status == "formal_verified"
    assert updated_node.formal_artifact is not None
    assert len(updated_node.formal_artifact.attempt_history) == 2
    assert "Compiler feedback from the previous attempt:" in backend.requests[1].prompt


def test_formalize_candidate_node_marks_failure_after_bound(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        cwd: Path,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="type mismatch")

    verifier = LeanVerifier(workspace=workspace, command_runner=fake_run)
    backend = MockBackend(
        queued_payloads=[
            {
                "lean_theorem_name": "sum_nonneg_attempt_1",
                "lean_statement": "0 <= a + b",
                "lean_code": "import Mathlib\n\ntheorem sum_nonneg_attempt_1 : 0 <= a + b := by\n  simp",
            },
            {
                "lean_theorem_name": "sum_nonneg_attempt_2",
                "lean_statement": "0 <= a + b",
                "lean_code": "import Mathlib\n\ntheorem sum_nonneg_attempt_2 : 0 <= a + b := by\n  simp",
            },
        ]
    )

    outcome = formalize_candidate_node(
        backend=backend,
        verifier=verifier,
        graph=build_graph(),
        node_id="n2",
        max_attempts=2,
    )

    updated_node = next(node for node in outcome.graph.nodes if node.id == "n2")

    assert updated_node.status == "formal_failed"
    assert updated_node.formal_artifact is not None
    assert updated_node.formal_artifact.verification.status == "failed"
    assert len(updated_node.formal_artifact.attempt_history) == 2


def test_formalize_candidate_node_rejects_invalid_attempt_bound(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        cwd: Path,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    verifier = LeanVerifier(workspace=workspace, command_runner=fake_run)
    backend = MockBackend(queued_payloads=[])

    with pytest.raises(ValueError, match="max_attempts"):
        formalize_candidate_node(
            backend=backend,
            verifier=verifier,
            graph=build_graph(),
            node_id="n2",
            max_attempts=0,
        )
