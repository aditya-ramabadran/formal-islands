from __future__ import annotations

import re
import subprocess
from pathlib import Path
from threading import RLock

import pytest

from formal_islands.backends import BackendInvocationError, MockBackend
from formal_islands.formalization.agentic import (
    agentic_worker_plan_path,
    build_agentic_formalization_request,
    recover_agentic_artifact_from_scratch_file,
)
from formal_islands.formalization.aristotle import build_aristotle_formalization_prompt
from formal_islands.formalization.aristotle import _append_aristotle_summary_files
from formal_islands.formalization.lean import LeanVerifier, LeanWorkspace
from formal_islands.formalization.loop import (
    _attempt_agentic_coverage_expansion,
    _build_agentic_faithfulness_feedback,
    _build_aristotle_faithfulness_feedback,
    _promote_informal_parents_with_verified_children,
    _summarize_compiler_feedback,
    formalize_candidate_node,
    ParentPromotionCache,
    RepairAssessment,
    RepairCategory,
)
from formal_islands.models import FormalArtifact, ProofEdge, ProofGraph, ProofNode, VerificationResult
from formal_islands.progress import use_progress_log


def extract_agentic_paths(prompt: str) -> tuple[Path, Path]:
    scratch_match = re.search(r"Scratch file to create and edit: ([^\n]+)", prompt)
    plan_match = re.search(r"Plan markdown file to create and maintain: ([^\n]+)", prompt)
    assert scratch_match is not None
    assert plan_match is not None
    return Path(scratch_match.group(1)), Path(plan_match.group(1))


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


def build_two_child_graph() -> ProofGraph:
    return ProofGraph(
        theorem_title="Toy theorem",
        theorem_statement="If a and b are nonnegative, then a + b is nonnegative.",
        root_node_id="n0",
        nodes=[
            ProofNode(
                id="n0",
                title="Main claim",
                informal_statement="a + b is nonnegative.",
                informal_proof_text="It follows from the arithmetic lemmas n1 and n2.",
            ),
            ProofNode(
                id="n1",
                title="Arithmetic lemma 1",
                informal_statement="0 <= a.",
                informal_proof_text="This is a local technical fact.",
                status="formal_verified",
                formal_artifact=FormalArtifact(
                    lean_theorem_name="a_nonneg",
                    lean_statement="theorem a_nonneg : 0 ≤ a",
                    lean_code="theorem a_nonneg : 0 ≤ a := by\n  sorry",
                ),
            ),
            ProofNode(
                id="n2",
                title="Arithmetic lemma 2",
                informal_statement="0 <= b.",
                informal_proof_text="This is another local technical fact.",
                status="formal_verified",
                formal_artifact=FormalArtifact(
                    lean_theorem_name="b_nonneg",
                    lean_statement="theorem b_nonneg : 0 ≤ b",
                    lean_code="theorem b_nonneg : 0 ≤ b := by\n  sorry",
                ),
            ),
        ],
        edges=[ProofEdge(source_id="n0", target_id="n1"), ProofEdge(source_id="n0", target_id="n2")],
    )


def build_two_child_candidate_graph() -> ProofGraph:
    graph = build_two_child_graph()
    updated_nodes = [
        node.model_copy(
            update={
                "status": "candidate_formal",
                "formalization_priority": 1,
                "formalization_rationale": "Parent assembly target.",
            }
        )
        if node.id == "n0"
        else node
        for node in graph.nodes
    ]
    return graph.model_copy(update={"nodes": updated_nodes})


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

    assert scratch_path.name.startswith("node_1_attempt_2_")
    assert scratch_path.name.endswith(".lean")
    assert scratch_path.read_text(encoding="utf-8") == "theorem t : True := by trivial"


def test_lean_workspace_prepares_agentic_worker_file(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)

    scratch_path = workspace.prepare_worker_file("node/1")

    assert scratch_path.name.startswith("node_1_worker_")
    assert scratch_path.name.endswith(".lean")
    assert "agentic formalization worker" in scratch_path.read_text(encoding="utf-8")


def test_lean_verifier_defaults_to_240_second_timeout(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)
    verifier = LeanVerifier(workspace=workspace)

    assert verifier.timeout_seconds == 240.0


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
    assert "target theorem shape" in request.prompt.lower()
    assert "likely mathlib lemmas" in request.prompt.lower()
    assert "grep" in request.prompt.lower()
    assert "appending a new labeled section" in request.prompt.lower()
    assert "default to the most literal whole-node theorem shape" in request.prompt.lower()
    assert "only fall back to a narrower concrete sublemma" in request.prompt.lower()
    assert "do not jump immediately to a more abstract or indirect theorem" in request.prompt.lower()
    assert "one designated main theorem" in request.prompt.lower()
    assert "helper lemmas" in request.prompt.lower()
    assert "must correspond to that single main theorem" in request.prompt.lower()
    assert "coverage sketch" in request.prompt.lower()
    assert "local proof neighborhood" in request.prompt.lower()
    assert "verified supporting lemmas already certified in this run" in request.prompt.lower()
    assert "context-only sibling ingredients" in request.prompt.lower()
    assert "dependency note" in request.prompt.lower()
    assert "reserved keyword" in request.prompt.lower()
    assert "component of the sketch" in request.prompt.lower()
    assert "formal-islands-search" in request.prompt.lower()


def test_build_agentic_formalization_request_includes_all_verified_direct_children(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)
    scratch_path = workspace.prepare_worker_file("n0")

    request = build_agentic_formalization_request(
        graph=build_two_child_candidate_graph(),
        node_id="n0",
        workspace_root=workspace.root,
        scratch_file_path=scratch_path,
    )

    lowered = request.prompt.lower()
    assert "verified direct child lemmas" in lowered
    assert "a_nonneg" in request.prompt
    assert "b_nonneg" in request.prompt


def test_build_aristotle_formalization_prompt_marks_ambient_theorem_as_context_only(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)
    scratch_path = workspace.prepare_worker_file("n2")
    graph = build_graph()
    node = next(node for node in graph.nodes if node.id == "n2")

    prompt = build_aristotle_formalization_prompt(
        graph=graph,
        node=node,
        desired_theorem_name="n2_aristotle",
        relative_scratch_path=scratch_path.relative_to(workspace.root),
    )

    assert "ambient theorem statement (context only" in prompt.lower()
    assert "primary formalization target" in prompt.lower()
    assert "do not try to prove the ambient theorem statement itself" in prompt.lower()
    assert "informal statement:" in prompt.lower()
    assert "informal proof text:" in prompt.lower()
    assert "local proof neighborhood" in prompt.lower()
    assert "verified supporting lemmas already certified in this run" in prompt.lower()
    assert "context-only sibling ingredients" in prompt.lower()
    assert "dependency note" in prompt.lower()
    assert "do not convert a difficult intermediate identity" in prompt.lower()
    assert "do not make a major shrink" in prompt.lower()
    assert "reserved keyword" in prompt.lower()
    assert "genuinely nontrivial" in prompt.lower()
    assert "fail rather than returning a trivial or over-shrunk theorem" in prompt.lower()
    assert "reserved keyword" in prompt.lower()
    assert "lambda1" in prompt.lower()


def test_build_aristotle_formalization_prompt_includes_all_verified_direct_children(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)
    scratch_path = workspace.prepare_worker_file("n0")
    graph = build_two_child_candidate_graph()
    node = next(node for node in graph.nodes if node.id == "n0")

    prompt = build_aristotle_formalization_prompt(
        graph=graph,
        node=node,
        desired_theorem_name="n0_aristotle",
        relative_scratch_path=scratch_path.relative_to(workspace.root),
    )

    lowered = prompt.lower()
    assert "verified direct child lemmas" in lowered
    assert "a_nonneg" in prompt
    assert "b_nonneg" in prompt


def test_parent_promotion_assessment_is_cached_and_promotes_parent(tmp_path: Path) -> None:
    graph = build_two_child_graph()
    planning_backend = MockBackend(
        queued_payloads=[
            {
                "promote_parent": True,
                "recommended_priority": 2,
                "reason": "The remaining work is just parent assembly.",
            }
        ]
    )
    cache = ParentPromotionCache(decisions={}, lock=RLock())
    progress_log = tmp_path / "_progress.log"

    with use_progress_log(progress_log):
        updated_graph = _promote_informal_parents_with_verified_children(
            graph=graph,
            planning_backend=planning_backend,
            parent_promotion_cache=cache,
        )
    root = next(node for node in updated_graph.nodes if node.id == "n0")
    assert root.status == "candidate_formal"
    assert root.formalization_priority == 2
    assert root.formalization_rationale is not None
    assert "parent assembly" in root.formalization_rationale.lower()
    assert len(planning_backend.requests) == 1
    log_text = progress_log.read_text(encoding="utf-8")
    assert "parent promotion assessment -> promote=True" in log_text
    assert "priority=2" in log_text


def test_parent_promotion_assessment_cache_reuses_negative_decision(tmp_path: Path) -> None:
    graph = build_two_child_graph()
    planning_backend = MockBackend(
        queued_payloads=[
            {
                "promote_parent": False,
                "recommended_priority": None,
                "reason": "The parent still carries the main burden.",
            }
        ]
    )
    cache = ParentPromotionCache(decisions={}, lock=RLock())
    progress_log = tmp_path / "_progress.log"

    with use_progress_log(progress_log):
        first_graph = _promote_informal_parents_with_verified_children(
            graph=graph,
            planning_backend=planning_backend,
            parent_promotion_cache=cache,
        )
        second_graph = _promote_informal_parents_with_verified_children(
            graph=first_graph,
            planning_backend=planning_backend,
            parent_promotion_cache=cache,
        )

    root = next(node for node in second_graph.nodes if node.id == "n0")
    assert root.status == "informal"
    assert len(planning_backend.requests) == 1
    log_text = progress_log.read_text(encoding="utf-8")
    assert "cache hit" in log_text.lower()
    assert "promote=False" in log_text


def test_faithfulness_feedback_locks_theorem_family_for_setting_fix() -> None:
    verification = FormalArtifact(
        lean_theorem_name="narrow_energy_split",
        lean_statement="theorem narrow_energy_split : True",
        lean_code="theorem narrow_energy_split : True := by trivial",
    ).verification.model_copy(
        update={
            "command": "faithfulness_guard",
            "stderr": "Formalization drifted too far from the target node. One-dimensional interval proxy.",
            "stdout": "",
        }
    )
    repair_assessment = RepairAssessment(
        category=RepairCategory.SETTING_FIX,
        note="The theorem moved to a lower-dimensional proxy model.",
    )

    agentic_prompt = _build_agentic_faithfulness_feedback(
        previous_result=verification,
        repair_assessment=repair_assessment,
    )
    aristotle_prompt = _build_aristotle_faithfulness_feedback(
        previous_result=verification,
        repair_assessment=repair_assessment,
    )

    for prompt in (agentic_prompt, aristotle_prompt):
        lowered = prompt.lower()
        assert "same ambient universe" in lowered
        assert "lower-dimensional, proxy, or analogue theorem family" in lowered
        assert "smaller but still concrete local sublemma" not in lowered


def test_faithfulness_feedback_mentions_ascii_binder_names_for_packaging_fix() -> None:
    verification = FormalArtifact(
        lean_theorem_name="binder_issue",
        lean_statement="theorem binder_issue : True",
        lean_code="theorem binder_issue : True := by trivial",
    ).verification.model_copy(
        update={
            "command": "lake env lean",
            "stderr": "unexpected token 'λ'; expected '_' or identifier",
            "stdout": "",
        }
    )
    repair_assessment = RepairAssessment(
        category=RepairCategory.LEAN_PACKAGING_FIX,
        note="Unicode binder names are not valid in Lean theorem headers.",
    )

    agentic_prompt = _build_agentic_faithfulness_feedback(
        previous_result=verification,
        repair_assessment=repair_assessment,
    )
    aristotle_prompt = _build_aristotle_faithfulness_feedback(
        previous_result=verification,
        repair_assessment=repair_assessment,
    )

    for prompt in (agentic_prompt, aristotle_prompt):
        lowered = prompt.lower()
        assert "lean-safe binder names" in lowered
        assert "unicode" in lowered
        assert "lambda1" in lowered


def test_append_aristotle_summary_files_writes_to_active_progress_log(tmp_path: Path) -> None:
    extracted_root = tmp_path / "aristotle-extracted"
    extracted_root.mkdir()
    summary_path = extracted_root / "ARISTOTLE_SUMMARY_example.md"
    summary_path.write_text("# Summary\nUseful details.\n", encoding="utf-8")
    progress_log = tmp_path / "_progress.log"

    with use_progress_log(progress_log):
        _append_aristotle_summary_files(extracted_root)

    log_text = progress_log.read_text(encoding="utf-8")
    assert "-------" in log_text
    assert "Aristotle summary file: ARISTOTLE_SUMMARY_example.md" in log_text
    assert "# Summary" in log_text
    assert "Useful details." in log_text
    assert log_text.count("-------") >= 2


def test_lean_verifier_logs_local_verification_to_progress_file(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)
    progress_log = tmp_path / "_progress.log"

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
    with use_progress_log(progress_log):
        result = verifier.verify_code(
            lean_code="theorem t : True := by trivial",
            node_id="n2",
            attempt_number=1,
        )

    assert result.status == "verified"
    log_text = progress_log.read_text(encoding="utf-8")
    assert "running local Lean verification for node n2 (attempt 1)" in log_text


def test_compiler_feedback_summary_prefers_error_like_lines() -> None:
    verification = VerificationResult(
        status="failed",
        command="lake env lean",
        exit_code=1,
        stdout="",
        stderr=(
            "error: type mismatch\n"
            "expected\n"
            "  Nat\n"
            "found\n"
            "  Int\n"
        ),
    )

    summary = _summarize_compiler_feedback(verification)

    assert summary.startswith("error: type mismatch")


def test_recover_agentic_artifact_prefers_expected_main_theorem(tmp_path: Path) -> None:
    workspace = create_workspace(tmp_path)
    scratch_path = workspace.prepare_worker_file("n3")
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
        assert args[1:3] == ["env", "lean"]
        assert cwd == tmp_path.resolve()
        assert args[3].startswith(str((tmp_path / "FormalIslands" / "Generated").resolve()))
        assert "_attempt_1_" in args[3]
        assert args[3].endswith(".lean")
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
        assert args[3].startswith(str((tmp_path / "lean_project" / "FormalIslands" / "Generated").resolve()))
        assert "_attempt_1_" in args[3]
        assert args[3].endswith(".lean")
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
    worker_file = workspace.prepare_worker_file("n2")

    class FakeAgenticBackend:
        timeout_seconds = 420.0

        def __init__(self) -> None:
            self.summary_calls = 0

        def run_agentic_structured(self, request, *, timeout_seconds=None):
            worker_file, plan_file = extract_agentic_paths(request.prompt)
            self.worker_file = worker_file
            self.plan_file = plan_file
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
    worker_file = workspace.prepare_worker_file("n2")

    class FakeAgenticBackend:
        timeout_seconds = 420.0

        def __init__(self) -> None:
            self.summary_calls = 0

        def run_agentic_structured(self, request, *, timeout_seconds=None):
            worker_file, plan_file = extract_agentic_paths(request.prompt)
            self.worker_file = worker_file
            self.plan_file = plan_file
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
        edge for edge in outcome.graph.edges if edge.source_id == "n2" and edge.target_id == child.id
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


def test_agentic_coverage_expansion_is_skipped_when_planning_backend_says_theorem_already_matches(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    scratch_path = workspace.prepare_worker_file("n2")
    scratch_path.write_text(
        "theorem sum_nonneg (a b : ℝ) : 0 ≤ a + b := by\n  nlinarith\n",
        encoding="utf-8",
    )

    graph = build_graph()
    artifact = FormalArtifact(
        lean_theorem_name="sum_nonneg",
        lean_statement="theorem sum_nonneg (a b : ℝ) : 0 ≤ a + b",
        lean_code=scratch_path.read_text(encoding="utf-8"),
        verification=VerificationResult(
            status="verified",
            command="lake env lean",
            exit_code=0,
            stdout="",
            stderr="",
            attempt_count=1,
            artifact_path=str(scratch_path),
        ),
        faithfulness_classification="concrete_sublemma",
    )

    class NeverCalledAgenticBackend:
        timeout_seconds = 420.0

        def run_agentic_structured(self, request, *, timeout_seconds=None):
            raise AssertionError("coverage expansion should have been skipped")

    planning_backend = MockBackend(
        queued_payloads=[
            {
                "result_kind": "full_match",
                "certifies_main_burden": True,
                "coverage_score": 10,
                "expansion_warranted": False,
                "worth_retrying_later": False,
                "reason": "The verified theorem already states the target inequality on [0, 2].",
            }
        ]
    )
    verifier = LeanVerifier(workspace=workspace, command_runner=lambda *args, **kwargs: None)

    progress_log = tmp_path / "_progress.log"
    with use_progress_log(progress_log):
        upgraded = _attempt_agentic_coverage_expansion(
            backend=NeverCalledAgenticBackend(),
            planning_backend=planning_backend,
            verifier=verifier,
            graph=graph,
            node_id="n2",
            artifact=artifact,
            scratch_path=scratch_path,
            attempt_history=[],
        )

    assert upgraded is not None
    assert upgraded.faithfulness_classification == "full_node"
    assert planning_backend.requests
    log_text = progress_log.read_text(encoding="utf-8")
    assert "formalization assessment result_kind=full_match" in log_text
    assert "The verified theorem already states the target inequality on [0, 2]." in log_text


def test_agentic_coverage_expansion_is_skipped_when_planning_backend_says_faithful_core(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    scratch_path = workspace.prepare_worker_file("n2")
    scratch_path.write_text(
        "theorem sum_nonneg (a b : ℝ) : 0 ≤ a + b := by\n  nlinarith\n",
        encoding="utf-8",
    )

    graph = build_graph()
    artifact = FormalArtifact(
        lean_theorem_name="sum_nonneg",
        lean_statement="theorem sum_nonneg (a b : ℝ) : 0 ≤ a + b",
        lean_code=scratch_path.read_text(encoding="utf-8"),
        verification=VerificationResult(
            status="verified",
            command="lake env lean",
            exit_code=0,
            stdout="",
            stderr="",
            attempt_count=1,
            artifact_path=str(scratch_path),
        ),
        faithfulness_classification="concrete_sublemma",
    )

    class NeverCalledAgenticBackend:
        timeout_seconds = 420.0

        def run_agentic_structured(self, request, *, timeout_seconds=None):
            raise AssertionError("coverage expansion should have been skipped")

    planning_backend = MockBackend(
        queued_payloads=[
            {
                "result_kind": "faithful_core",
                "certifies_main_burden": False,
                "coverage_score": 8,
                "expansion_warranted": True,
                "worth_retrying_later": False,
                "reason": "The theorem already has the right setting and proof path, so the shape should stay fixed.",
            }
        ]
    )
    verifier = LeanVerifier(workspace=workspace, command_runner=lambda *args, **kwargs: None)

    progress_log = tmp_path / "_progress.log"
    with use_progress_log(progress_log):
        upgraded = _attempt_agentic_coverage_expansion(
            backend=NeverCalledAgenticBackend(),
            planning_backend=planning_backend,
            verifier=verifier,
            graph=graph,
            node_id="n2",
            artifact=artifact,
            scratch_path=scratch_path,
            attempt_history=[],
        )

    assert upgraded is not None
    assert upgraded.faithfulness_classification == "concrete_sublemma"
    assert upgraded.faithfulness_notes is not None
    assert "faithful_core" in upgraded.faithfulness_notes
    assert planning_backend.requests
    log_text = progress_log.read_text(encoding="utf-8")
    assert "formalization assessment result_kind=faithful_core" in log_text
    assert "skipping coverage expansion" in log_text


def test_formalize_candidate_node_agentic_retries_once_after_faithfulness_failure(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    worker_file = workspace.prepare_worker_file("n2")

    class FakeAgenticBackend:
        timeout_seconds = 420.0

        def __init__(self) -> None:
            self.calls = 0
            self.requests = []

        def run_agentic_structured(self, request, *, timeout_seconds=None):
            worker_file, plan_file = extract_agentic_paths(request.prompt)
            self.worker_file = worker_file
            self.plan_file = plan_file
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
    assert "formal-islands-search" in backend.requests[0].prompt.lower()
    assert "at most 2 additional highly targeted searches" in backend.requests[0].prompt.lower()
    assert "appending a new labeled section" in backend.requests[1].prompt.lower()
    assert "the theorem family is locked" in backend.requests[1].prompt.lower()
    assert "do not switch theorem family to a simpler proxy universe" in backend.requests[1].prompt.lower()
    assert backend.worker_file.read_text(encoding="utf-8").startswith("import Mathlib.Data.Real.Basic")
    assert len(updates) == 2


def test_formalize_candidate_node_agentic_uses_at_most_one_faithfulness_retry(
    tmp_path: Path,
) -> None:
    workspace = create_workspace(tmp_path)
    worker_file = workspace.prepare_worker_file("n2")

    class FakeAgenticBackend:
        timeout_seconds = 420.0

        def __init__(self) -> None:
            self.calls = 0

        def run_agentic_structured(self, request, *, timeout_seconds=None):
            worker_file, plan_file = extract_agentic_paths(request.prompt)
            self.worker_file = worker_file
            self.plan_file = plan_file
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
    worker_file = workspace.prepare_worker_file("n2")

    class FakeAgenticBackend:
        timeout_seconds = 420.0

        def run_agentic_structured(self, request, *, timeout_seconds=None):
            worker_file, plan_file = extract_agentic_paths(request.prompt)
            self.worker_file = worker_file
            self.plan_file = plan_file
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
    worker_file = workspace.prepare_worker_file("n2")

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
            worker_file, plan_file = extract_agentic_paths(request.prompt)
            self.worker_file = worker_file
            self.plan_file = plan_file
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
    assert "lock the theorem shape" in backend.requests[1].prompt.lower()


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
