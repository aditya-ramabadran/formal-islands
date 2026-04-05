from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from formal_islands.backends import BackendInvocationError, MockBackend
from formal_islands.formalization.agentic import (
    agentic_worker_plan_path,
    build_agentic_formalization_request,
    recover_agentic_artifact_from_scratch_file,
)
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


def test_lean_workspace_prepares_agentic_worker_file(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)

    scratch_path = workspace.prepare_worker_file("node/1")

    assert scratch_path.name == "node_1_worker.lean"
    assert "agentic formalization worker" in scratch_path.read_text(encoding="utf-8")


def test_build_agentic_formalization_request_includes_concrete_setting_guidance(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)
    scratch_path = workspace.prepare_worker_file("n2")
    plan_path = agentic_worker_plan_path(scratch_path)

    request = build_agentic_formalization_request(
        graph=build_graph(),
        node_id="n2",
        workspace_root=workspace.root,
        scratch_file_path=scratch_path,
    )

    assert "Ambient theorem statement:" in request.prompt
    assert "preserve the ambient mathematical setting" in request.prompt.lower()
    assert "arbitrary measure" in request.prompt.lower()
    assert "prefer ascii identifiers" in request.prompt.lower()
    assert "lambda1" in request.prompt
    assert "most boring lean surface syntax" in request.prompt.lower()
    assert str(plan_path) in request.prompt
    assert "start with a lightweight planning pass" in request.prompt.lower()
    assert "plan markdown file to create and maintain" in request.prompt.lower()
    assert "target node/theorem" in request.prompt.lower()
    assert "likely mathlib lemmas or apis to search for" in request.prompt.lower()
    assert "`#check`" in request.prompt
    assert "appending a new labeled section" in request.prompt.lower()
    assert "default to the most literal whole-node theorem shape" in request.prompt.lower()
    assert "only fall back to a narrower concrete sublemma" in request.prompt.lower()
    assert "do not jump immediately to a more abstract or indirect theorem" in request.prompt.lower()
    assert "one designated main theorem" in request.prompt.lower()
    assert "helper lemmas" in request.prompt.lower()
    assert "must correspond to that single main theorem" in request.prompt.lower()


def test_recover_agentic_artifact_prefers_expected_main_theorem(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)
    scratch_path = workspace.generated_dir / "n3_worker.lean"
    scratch_path.write_text(
        "import Mathlib.Data.Real.Basic\n\n"
        "theorem helper_small : 1 = 1 := by\n"
        "  decide\n\n"
        "theorem main_target (a b c : ℝ) (h : c = a + b) : c = a + b := by\n"
        "  simpa [h]\n",
        encoding="utf-8",
    )

    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="If c = a + b then c = a + b.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Main theorem",
                informal_statement="Main theorem.",
                informal_proof_text="Use n3.",
            ),
            ProofNode(
                id="n3",
                title="Transfer equality across a rewrite",
                informal_statement="If c = a + b, then c = a + b.",
                informal_proof_text="Rewrite using the given equality.",
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="Concrete local step.",
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n3")],
    )

    artifact = recover_agentic_artifact_from_scratch_file(
        graph=graph,
        node_id="n3",
        scratch_file_path=scratch_path,
        expected_theorem_name="main_target",
    )

    assert artifact is not None
    assert artifact.lean_theorem_name == "main_target"
    assert "theorem main_target" in artifact.lean_statement


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


def test_lean_verifier_verifies_existing_file(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)
    worker_file = workspace.prepare_worker_file("n2")
    worker_file.write_text("theorem t : True := by trivial", encoding="utf-8")

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        cwd: Path,
        check: bool,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert args[3] == str(worker_file.resolve())
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    verifier = LeanVerifier(workspace=workspace, command_runner=fake_run)
    result = verifier.verify_existing_file(file_path=worker_file, attempt_number=1)

    assert result.status == "verified"
    assert result.artifact_path == str(worker_file.resolve())


def test_lean_verifier_captures_timeout_as_failed_result(tmp_path: Path) -> None:
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
        exc = subprocess.TimeoutExpired(cmd=args, timeout=timeout or 0)
        exc.stdout = ""
        exc.stderr = ""
        raise exc

    verifier = LeanVerifier(workspace=workspace, command_runner=fake_run, timeout_seconds=5.0)
    result = verifier.verify_code(
        lean_code="theorem t : True := by trivial",
        node_id="n2",
        attempt_number=1,
    )

    assert result.status == "failed"
    assert "timed out" in result.stderr.lower()


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
    assert "avoid arbitrary `type*` parameters" in backend.requests[1].prompt.lower()
    assert "avoid both `import mathlib`" in backend.requests[1].prompt.lower()
    assert "previous failed lean file to revise" in backend.requests[1].prompt.lower()
    assert "theorem sum_nonneg_attempt_1" in backend.requests[1].prompt


def test_formalize_candidate_node_agentic_mode_reverifies_worker_file(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)
    worker_file = workspace.generated_dir / "n2_worker.lean"
    worker_file.parent.mkdir(parents=True, exist_ok=True)

    class FakeAgenticBackend:
        timeout_seconds = 420.0

        def __init__(self) -> None:
            self.summary_calls = 0

        def run_agentic_structured(self, request, *, timeout_seconds=None):
            plan_file = agentic_worker_plan_path(worker_file)
            plan_file.write_text(
                "# Plan\n\n- Target theorem: sum_nonneg\n- Intended theorem shape: whole node\n",
                encoding="utf-8",
            )
            worker_file.write_text(
                "import Mathlib.Data.Real.Basic\n\n"
                "theorem sum_nonneg (a b : ℝ) (ha : 0 ≤ a) (hb : 0 ≤ b) : 0 ≤ a + b := by\n"
                "  nlinarith\n",
                encoding="utf-8",
            )
            from formal_islands.backends.base import StructuredBackendResponse

            return StructuredBackendResponse(
                payload={
                    "lean_theorem_name": "sum_nonneg",
                    "lean_statement": (
                        "theorem sum_nonneg (a b : ℝ) (ha : 0 ≤ a) (hb : 0 ≤ b) : 0 ≤ a + b"
                    ),
                    "final_file_path": str(worker_file.resolve()),
                    "plan_file_path": str(plan_file.resolve()),
                },
                raw_stdout="",
                raw_stderr="",
                command=("codex", "exec"),
                exit_code=0,
                backend_name="codex_cli",
            )

        def run_structured(self, request):
            self.summary_calls += 1
            from formal_islands.backends.base import StructuredBackendResponse

            return StructuredBackendResponse(
                payload={
                    "informal_statement": (
                        "Assuming the scalar mass identity and the energy relation, one obtains the lower bound for the derivative of Y."
                    ),
                    "informal_proof_text": (
                        "Use the verified scalar rewrite and the sign condition on E to conclude the desired inequality."
                    ),
                },
                raw_stdout="",
                raw_stderr="",
                command=("codex", "exec"),
                exit_code=0,
                backend_name="codex_cli",
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
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    verifier = LeanVerifier(workspace=workspace, command_runner=fake_run)
    outcome = formalize_candidate_node(
        backend=FakeAgenticBackend(),
        verifier=verifier,
        graph=build_graph(),
        node_id="n2",
        mode="agentic",
    )

    updated_node = next(node for node in outcome.graph.nodes if node.id == "n2")
    assert updated_node.status == "formal_verified"
    assert updated_node.formal_artifact is not None
    assert updated_node.formal_artifact.lean_theorem_name == "sum_nonneg"
    assert updated_node.formal_artifact.verification.status == "verified"


def test_formalize_candidate_node_promotes_concrete_sublemma_to_child_node(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    worker_file = workspace.generated_dir / "n2_worker.lean"
    worker_file.parent.mkdir(parents=True, exist_ok=True)

    class FakeAgenticBackend:
        timeout_seconds = 420.0

        def __init__(self) -> None:
            self.summary_calls = 0

        def run_agentic_structured(self, request, *, timeout_seconds=None):
            plan_file = agentic_worker_plan_path(worker_file)
            plan_file.write_text(
                "# Plan\n\n- Target theorem: differential_inequality_for_Y\n- Intended theorem shape: concrete sublemma\n",
                encoding="utf-8",
            )
            worker_file.write_text(
                "import Mathlib.Data.Real.Basic\n\n"
                "namespace FormalIslands\n\n"
                "theorem differential_inequality_for_Y\n"
                "    {p Y' gradIntegral nonlinIntegral E : ℝ}\n"
                "    (hp : 1 < p)\n"
                "    (hY : (1 / 2 : ℝ) * Y' = -gradIntegral + nonlinIntegral)\n"
                "    (hE : E = (1 / 2 : ℝ) * gradIntegral - (1 / (p + 1)) * nonlinIntegral)\n"
                "    (hEnonpos : E ≤ 0) :\n"
                "    (1 / 2 : ℝ) * Y' ≥ ((p - 1) / (p + 1)) * nonlinIntegral := by\n"
                "  nlinarith [hY, hE, hEnonpos]\n\n"
                "end FormalIslands\n",
                encoding="utf-8",
            )
            from formal_islands.backends.base import StructuredBackendResponse

            return StructuredBackendResponse(
                payload={
                    "lean_theorem_name": "differential_inequality_for_Y",
                    "lean_statement": (
                        "theorem differential_inequality_for_Y "
                        "{p Y' gradIntegral nonlinIntegral E : ℝ} "
                        "(hp : 1 < p) "
                        "(hY : (1 / 2 : ℝ) * Y' = -gradIntegral + nonlinIntegral) "
                        "(hE : E = (1 / 2 : ℝ) * gradIntegral - (1 / (p + 1)) * nonlinIntegral) "
                        "(hEnonpos : E ≤ 0) : "
                        "(1 / 2 : ℝ) * Y' ≥ ((p - 1) / (p + 1)) * nonlinIntegral"
                    ),
                    "final_file_path": str(worker_file.resolve()),
                    "plan_file_path": str(plan_file.resolve()),
                },
                raw_stdout="",
                raw_stderr="",
                command=("codex", "exec"),
                exit_code=0,
                backend_name="codex_cli",
            )

        def run_structured(self, request):
            self.summary_calls += 1
            from formal_islands.backends.base import StructuredBackendResponse

            return StructuredBackendResponse(
                payload={
                    "informal_statement": (
                        "Assuming the scalar mass identity and the energy relation, one obtains the lower bound for the derivative of Y."
                    ),
                    "informal_proof_text": (
                        "Use the verified scalar rewrite and the sign condition on E to conclude the desired inequality."
                    ),
                },
                raw_stdout="",
                raw_stderr="",
                command=("codex", "exec"),
                exit_code=0,
                backend_name="codex_cli",
            )

    graph = ProofGraph(
        theorem_title="Toy blow-up theorem",
        theorem_statement="Main PDE theorem.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Toy blow-up theorem",
                informal_statement="Main theorem.",
                informal_proof_text="Use n2.",
            ),
            ProofNode(
                id="n2",
                title="Differential inequality for Y",
                informal_statement=(
                    "Differentiate Y, rewrite the identity using the energy, and conclude a lower bound."
                ),
                informal_proof_text="This is the concrete local core of the proof.",
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="Concrete local core.",
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n2")],
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
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    verifier = LeanVerifier(workspace=workspace, command_runner=fake_run)
    backend = FakeAgenticBackend()
    outcome = formalize_candidate_node(
        backend=backend,
        verifier=verifier,
        graph=graph,
        node_id="n2",
        mode="agentic",
    )

    parent = next(node for node in outcome.graph.nodes if node.id == "n2")
    child = next(node for node in outcome.graph.nodes if node.id.startswith("n2__formal_core"))
    support_edge = next(
        edge for edge in outcome.graph.edges if edge.source_id == child.id and edge.target_id == "n2"
    )

    assert parent.status == "informal"
    assert parent.formal_artifact is None
    assert child.status == "formal_verified"
    assert child.formal_artifact is not None
    assert child.formal_artifact.faithfulness_classification == "concrete_sublemma"
    assert "scalar mass identity" in child.informal_statement
    assert "verified supporting sublemma extracted from the formalization of parent node 'n2'." in child.informal_proof_text
    assert backend.summary_calls == 1
    assert support_edge.label == "formal_sublemma_for"


def test_formalize_candidate_node_agentic_retries_once_after_faithfulness_failure(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    worker_file = workspace.generated_dir / "n2_worker.lean"
    worker_file.parent.mkdir(parents=True, exist_ok=True)

    class FakeAgenticBackend:
        timeout_seconds = 420.0

        def __init__(self) -> None:
            self.calls = 0
            self.requests = []

        def run_agentic_structured(self, request, *, timeout_seconds=None):
            plan_file = agentic_worker_plan_path(worker_file)
            if not plan_file.exists():
                plan_file.write_text(
                    "# Initial plan\n\n- Intended theorem shape: whole node\n",
                    encoding="utf-8",
                )
            else:
                plan_file.write_text(
                    plan_file.read_text(encoding="utf-8")
                    + "\n## Revision 2\n\n- Previous draft was too abstract; stay concrete.\n",
                    encoding="utf-8",
                )
            self.calls += 1
            self.requests.append(request)
            if self.calls == 1:
                worker_file.write_text(
                    "import Mathlib.MeasureTheory.Integral.Bochner.Basic\n\n"
                    "open MeasureTheory\n\n"
                    "theorem too_abstract {α : Type*} [MeasurableSpace α] (μ : Measure α) "
                    "{f g : α → ℝ} (h : ∀ x, f x = - g x) : "
                    "∫ x, f x ∂μ = - ∫ x, g x ∂μ := by\n"
                    "  sorry\n",
                    encoding="utf-8",
                )
                theorem_name = "too_abstract"
                theorem_statement = (
                    "theorem too_abstract {α : Type*} [MeasurableSpace α] (μ : Measure α) "
                    "{f g : α → ℝ} (h : ∀ x, f x = - g x) : "
                    "∫ x, f x ∂μ = - ∫ x, g x ∂μ"
                )
            else:
                worker_file.write_text(
                    "import Mathlib.Data.Real.Basic\n\n"
                    "theorem sum_nonneg (a b : ℝ) (ha : 0 ≤ a) (hb : 0 ≤ b) : 0 ≤ a + b := by\n"
                    "  nlinarith\n",
                    encoding="utf-8",
                )
                theorem_name = "sum_nonneg"
                theorem_statement = (
                    "theorem sum_nonneg (a b : ℝ) (ha : 0 ≤ a) (hb : 0 ≤ b) : 0 ≤ a + b"
                )
            from formal_islands.backends.base import StructuredBackendResponse

            return StructuredBackendResponse(
                payload={
                    "lean_theorem_name": theorem_name,
                    "lean_statement": theorem_statement,
                    "final_file_path": str(worker_file.resolve()),
                    "plan_file_path": str(plan_file.resolve()),
                },
                raw_stdout="",
                raw_stderr="",
                command=("codex", "exec"),
                exit_code=0,
                backend_name="codex_cli",
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
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    backend = FakeAgenticBackend()
    verifier = LeanVerifier(workspace=workspace, command_runner=fake_run)
    updates = []
    outcome = formalize_candidate_node(
        backend=backend,
        verifier=verifier,
        graph=build_graph(),
        node_id="n2",
        mode="agentic",
        on_update=updates.append,
    )

    updated_node = next(node for node in outcome.graph.nodes if node.id == "n2")
    assert backend.calls == 2
    assert updated_node.status == "formal_verified"
    assert updated_node.formal_artifact is not None
    assert len(updated_node.formal_artifact.attempt_history) == 2
    assert "faithfulness feedback from the previous agentic attempt" in backend.requests[1].prompt.lower()
    assert "current scratch file to revise" in backend.requests[1].prompt.lower()
    assert "theorem too_abstract" in backend.requests[1].prompt
    assert "prefer ascii identifiers" in backend.requests[1].prompt.lower()
    assert "lambda1" in backend.requests[1].prompt
    assert "create the plan markdown file above first" in backend.requests[0].prompt.lower()
    assert "do brief local scouting before you commit to the final theorem" in backend.requests[0].prompt.lower()
    assert "appending a new labeled section" in backend.requests[1].prompt.lower()
    assert "explicitly reconsider the most literal whole-node theorem shape first" in backend.requests[1].prompt.lower()
    assert worker_file.read_text(encoding="utf-8").startswith("import Mathlib.Data.Real.Basic")
    assert len(updates) == 2


def test_formalize_candidate_node_agentic_uses_at_most_one_faithfulness_retry(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    worker_file = workspace.generated_dir / "n2_worker.lean"
    worker_file.parent.mkdir(parents=True, exist_ok=True)

    class FakeAgenticBackend:
        timeout_seconds = 420.0

        def __init__(self) -> None:
            self.calls = 0

        def run_agentic_structured(self, request, *, timeout_seconds=None):
            plan_file = agentic_worker_plan_path(worker_file)
            plan_file.write_text(
                "# Plan\n\n- Intended theorem shape: whole node\n",
                encoding="utf-8",
            )
            self.calls += 1
            worker_file.write_text(
                "import Mathlib.MeasureTheory.Integral.Bochner.Basic\n\n"
                "open MeasureTheory\n\n"
                "theorem too_abstract {α : Type*} [MeasurableSpace α] (μ : Measure α) "
                "{f g : α → ℝ} (h : ∀ x, f x = - g x) : "
                "∫ x, f x ∂μ = - ∫ x, g x ∂μ := by\n"
                "  sorry\n",
                encoding="utf-8",
            )
            from formal_islands.backends.base import StructuredBackendResponse

            return StructuredBackendResponse(
                payload={
                    "lean_theorem_name": "too_abstract",
                    "lean_statement": (
                        "theorem too_abstract {α : Type*} [MeasurableSpace α] (μ : Measure α) "
                        "{f g : α → ℝ} (h : ∀ x, f x = - g x) : "
                        "∫ x, f x ∂μ = - ∫ x, g x ∂μ"
                    ),
                    "final_file_path": str(worker_file.resolve()),
                    "plan_file_path": str(plan_file.resolve()),
                },
                raw_stdout="",
                raw_stderr="",
                command=("codex", "exec"),
                exit_code=0,
                backend_name="codex_cli",
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
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    backend = FakeAgenticBackend()
    verifier = LeanVerifier(workspace=workspace, command_runner=fake_run)
    outcome = formalize_candidate_node(
        backend=backend,
        verifier=verifier,
        graph=build_graph(),
        node_id="n2",
        mode="agentic",
    )

    updated_node = next(node for node in outcome.graph.nodes if node.id == "n2")
    assert backend.calls == 2
    assert updated_node.status == "formal_failed"
    assert updated_node.formal_artifact is not None
    assert len(updated_node.formal_artifact.attempt_history) == 2


def test_formalize_candidate_node_agentic_recovers_from_backend_failure_with_worker_file(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    worker_file = workspace.generated_dir / "n2_worker.lean"
    worker_file.parent.mkdir(parents=True, exist_ok=True)

    class FakeAgenticBackend:
        timeout_seconds = 420.0

        def run_agentic_structured(self, request, *, timeout_seconds=None):
            plan_file = agentic_worker_plan_path(worker_file)
            plan_file.write_text(
                "# Plan\n\n- Target theorem: sum_nonneg\n",
                encoding="utf-8",
            )
            worker_file.write_text(
                "import Mathlib.Data.Real.Basic\n\n"
                "theorem sum_nonneg (a b : ℝ) (ha : 0 ≤ a) (hb : 0 ≤ b) : 0 ≤ a + b := by\n"
                "  nlinarith\n",
                encoding="utf-8",
            )
            raise BackendInvocationError("structured output missing after worker run")

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
    outcome = formalize_candidate_node(
        backend=FakeAgenticBackend(),
        verifier=verifier,
        graph=build_graph(),
        node_id="n2",
        mode="agentic",
    )

    updated_node = next(node for node in outcome.graph.nodes if node.id == "n2")
    assert updated_node.status == "formal_verified"
    assert updated_node.formal_artifact is not None
    assert updated_node.formal_artifact.lean_theorem_name == "sum_nonneg"
    assert updated_node.formal_artifact.verification.status == "verified"
    assert len(updated_node.formal_artifact.attempt_history) == 2
    assert "Recovered from an agentic backend failure" in updated_node.formal_artifact.attempt_history[0].stderr


def test_formalize_candidate_node_agentic_expands_verified_sublemma_once(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    worker_file = workspace.generated_dir / "n2_worker.lean"
    worker_file.parent.mkdir(parents=True, exist_ok=True)

    graph = ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="If c = a + b and a,b are nonnegative, then c is nonnegative.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Main theorem",
                informal_statement="Main theorem.",
                informal_proof_text="Use n2.",
            ),
            ProofNode(
                id="n2",
                title="Transfer nonnegativity across a rewrite",
                informal_statement="If c = a + b and 0 <= a and 0 <= b, then 0 <= c.",
                informal_proof_text="First prove 0 <= a + b, then rewrite using c = a + b.",
                status="candidate_formal",
                formalization_priority=1,
                formalization_rationale="Concrete local step.",
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n2")],
    )

    class FakeAgenticBackend:
        timeout_seconds = 420.0

        def __init__(self) -> None:
            self.calls = 0

        def run_agentic_structured(self, request, *, timeout_seconds=None):
            self.calls += 1
            plan_file = agentic_worker_plan_path(worker_file)
            plan_file.write_text("# Plan\n", encoding="utf-8")
            if self.calls == 1:
                worker_file.write_text(
                    "import Mathlib.Data.Real.Basic\n\n"
                    "theorem sum_nonneg (a b : ℝ) (ha : 0 ≤ a) (hb : 0 ≤ b) : 0 ≤ a + b := by\n"
                    "  nlinarith\n",
                    encoding="utf-8",
                )
                theorem_name = "sum_nonneg"
                theorem_statement = "theorem sum_nonneg (a b : ℝ) (ha : 0 ≤ a) (hb : 0 ≤ b) : 0 ≤ a + b"
            else:
                worker_file.write_text(
                    "import Mathlib.Data.Real.Basic\n\n"
                    "theorem transfer_nonneg (a b c : ℝ) (hsum : c = a + b) (ha : 0 ≤ a) (hb : 0 ≤ b) : 0 ≤ c := by\n"
                    "  nlinarith [hsum, ha, hb]\n",
                    encoding="utf-8",
                )
                theorem_name = "transfer_nonneg"
                theorem_statement = (
                    "theorem transfer_nonneg (a b c : ℝ) (hsum : c = a + b) (ha : 0 ≤ a) (hb : 0 ≤ b) : 0 ≤ c"
                )
            from formal_islands.backends.base import StructuredBackendResponse

            return StructuredBackendResponse(
                payload={
                    "lean_theorem_name": theorem_name,
                    "lean_statement": theorem_statement,
                    "final_file_path": str(worker_file.resolve()),
                    "plan_file_path": str(plan_file.resolve()),
                },
                raw_stdout="",
                raw_stderr="",
                command=("codex", "exec"),
                exit_code=0,
                backend_name="codex_cli",
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
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    outcome = formalize_candidate_node(
        backend=FakeAgenticBackend(),
        verifier=LeanVerifier(workspace=workspace, command_runner=fake_run),
        graph=graph,
        node_id="n2",
        mode="agentic",
    )

    updated_node = next(node for node in outcome.graph.nodes if node.id == "n2")
    assert updated_node.status == "formal_verified"
    assert updated_node.formal_artifact is not None
    assert updated_node.formal_artifact.lean_theorem_name == "transfer_nonneg"


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


def test_formalize_candidate_node_repairs_faithfulness_drift_once(tmp_path: Path) -> None:
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
    backend = MockBackend(
        queued_payloads=[
            {
                "lean_theorem_name": "too_abstract",
                "lean_statement": (
                    "theorem too_abstract {ι : Type*} (lhs rhs total : ι → ℝ) : "
                    "∀ t, total t = lhs t + rhs t"
                ),
                "lean_code": (
                    "import Mathlib\n\n"
                    "theorem too_abstract {ι : Type*} (lhs rhs total : ι → ℝ) : "
                    "∀ t, total t = lhs t + rhs t := by\n"
                    "  intro t\n"
                    "  sorry\n"
                ),
            },
            {
                "lean_theorem_name": "sum_nonneg",
                "lean_statement": "theorem sum_nonneg (a b : ℝ) (ha : 0 ≤ a) (hb : 0 ≤ b) : 0 ≤ a + b",
                "lean_code": (
                    "import Mathlib\n\n"
                    "theorem sum_nonneg (a b : ℝ) (ha : 0 ≤ a) (hb : 0 ≤ b) : 0 ≤ a + b := by\n"
                    "  nlinarith\n"
                ),
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
    assert len(updated_node.formal_artifact.attempt_history) == 2
    assert "faithfulness guard" in backend.requests[1].prompt.lower()
    assert "theorem too_abstract" in backend.requests[1].prompt


def test_formalize_candidate_node_uses_at_most_three_repair_retries(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)
    verifier_results = iter(
        [
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="unknown identifier"),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="unknown identifier"),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="unknown identifier"),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="unknown identifier"),
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
                "lean_theorem_name": "sum_nonneg_attempt_2",
                "lean_statement": "0 <= a + b",
                "lean_code": "import Mathlib\n\ntheorem sum_nonneg_attempt_2 : 0 <= a + b := by\n  simp",
            },
            {
                "lean_theorem_name": "sum_nonneg_attempt_3",
                "lean_statement": "0 <= a + b",
                "lean_code": "import Mathlib\n\ntheorem sum_nonneg_attempt_3 : 0 <= a + b := by\n  simp",
            },
            {
                "lean_theorem_name": "sum_nonneg_attempt_4",
                "lean_statement": "0 <= a + b",
                "lean_code": "import Mathlib\n\ntheorem sum_nonneg_attempt_4 : 0 <= a + b := by\n  simp",
            },
            {
                "lean_theorem_name": "sum_nonneg_attempt_5",
                "lean_statement": "0 <= a + b",
                "lean_code": "import Mathlib\n\ntheorem sum_nonneg_attempt_5 : 0 <= a + b := by\n  simp",
            },
        ]
    )

    outcome = formalize_candidate_node(
        backend=backend,
        verifier=verifier,
        graph=build_graph(),
        node_id="n2",
        max_attempts=5,
    )

    updated_node = next(node for node in outcome.graph.nodes if node.id == "n2")
    assert updated_node.status == "formal_failed"
    assert len(updated_node.formal_artifact.attempt_history) == 4
    assert len(backend.requests) == 4


def test_formalize_candidate_node_records_backend_failure_and_emits_update(tmp_path: Path) -> None:
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

    class FailingBackend:
        def run_structured(self, request):
            raise BackendInvocationError("Codex timed out")

    verifier = LeanVerifier(workspace=workspace, command_runner=fake_run)
    updates = []

    outcome = formalize_candidate_node(
        backend=FailingBackend(),
        verifier=verifier,
        graph=build_graph(),
        node_id="n2",
        max_attempts=2,
        on_update=updates.append,
    )

    updated_node = next(node for node in outcome.graph.nodes if node.id == "n2")
    assert updated_node.status == "formal_failed"
    assert updated_node.formal_artifact is not None
    assert updated_node.formal_artifact.verification.command == "backend_request"
    assert "Codex timed out" in updated_node.formal_artifact.verification.stderr
    assert len(updates) == 1
