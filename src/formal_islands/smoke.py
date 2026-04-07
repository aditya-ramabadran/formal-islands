"""Formal Islands CLI."""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any

from formal_islands.backends import (
    BackendError,
    AristotleBackend,
    ClaudeCodeBackend,
    CodexCLIBackend,
    GeminiCLIBackend,
    StructuredBackend,
)
from formal_islands.examples import TOY_RAW_PROOF, TOY_THEOREM_STATEMENT
from formal_islands.extraction import (
    extract_proof_graph,
    plan_proof_graph,
    select_formalization_candidates,
)
from formal_islands.formalization import (
    LeanVerifier,
    LeanWorkspace,
    formalize_candidate_node,
    formalize_candidate_nodes,
)
from formal_islands.formalization.loop import (
    DEFAULT_FORMALIZATION_ATTEMPTS,
)
from formal_islands.models import (
    FormalArtifact,
    ProofGraph,
    VerificationResult,
    canonical_dependency_direction_warnings,
)
from formal_islands.report import export_report_bundle, render_html_report
from formal_islands.report.annotation import synthesize_remaining_proof_burdens
from formal_islands.review import derive_review_obligations
from formal_islands.progress import (
    append_graph_summary_to_progress_log,
    progress,
    use_progress_log,
)


DEFAULT_EXAMPLE_INPUT = {
    "theorem_title": "Nonnegative sum",
    "theorem_statement": TOY_THEOREM_STATEMENT,
    "raw_proof_text": TOY_RAW_PROOF,
}

DEFAULT_BACKEND_TIMEOUT_SECONDS = 360.0
GEMINI_BACKEND_TIMEOUT_SECONDS = 360.0
FORMALIZATION_BACKEND_TIMEOUT_SECONDS = 420.0
PROGRESS_LOG_FILENAME = "_progress.log"


def _log_dependency_direction_warnings(graph: ProofGraph, *, context: str) -> None:
    for warning in canonical_dependency_direction_warnings(graph):
        progress(f"{context}: canonical direction warning: {warning}")


def main(argv: list[str] | None = None) -> int:
    """Run the Formal Islands CLI."""

    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser."""

    parser = argparse.ArgumentParser(prog="formal-islands")
    subparsers = parser.add_subparsers(dest="command")

    extract_parser = subparsers.add_parser("extract")
    add_backend_args(extract_parser)
    add_input_args(extract_parser)
    add_output_dir_arg(extract_parser)
    extract_parser.set_defaults(func=cmd_extract)

    plan_parser = subparsers.add_parser("plan")
    add_backend_args(plan_parser)
    add_input_args(plan_parser)
    add_output_dir_arg(plan_parser)
    plan_parser.set_defaults(func=cmd_plan)

    select_parser = subparsers.add_parser("select-candidates")
    add_backend_args(select_parser)
    add_graph_input_arg(select_parser)
    add_output_dir_arg(select_parser)
    select_parser.set_defaults(func=cmd_select_candidates)

    formalize_parser = subparsers.add_parser("formalize-one")
    add_backend_args(formalize_parser)
    add_graph_input_arg(formalize_parser)
    add_output_dir_arg(formalize_parser)
    formalize_parser.add_argument(
        "--workspace",
        default="lean_project",
        help="Path to the local Lean workspace.",
    )
    formalize_parser.add_argument(
        "--node-id",
        default="auto",
        help="Candidate node id to formalize. Default: auto-pick highest-priority candidate.",
    )
    formalize_parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_FORMALIZATION_ATTEMPTS,
        help=(
            "Maximum number of bounded formalization attempts. Default: "
            f"{DEFAULT_FORMALIZATION_ATTEMPTS}."
        ),
    )
    formalize_parser.add_argument(
        "--formalization-mode",
        choices=["agentic"],
        default="agentic",
        help="Formalization execution mode. Agentic is the only supported option.",
    )
    add_formalization_timeout_arg(formalize_parser)
    formalize_parser.set_defaults(func=cmd_formalize_one)

    formalize_all_parser = subparsers.add_parser("formalize-all-candidates")
    add_backend_args(formalize_all_parser)
    add_graph_input_arg(formalize_all_parser)
    add_output_dir_arg(formalize_all_parser)
    formalize_all_parser.add_argument(
        "--workspace",
        default="lean_project",
        help="Path to the local Lean workspace.",
    )
    formalize_all_parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_FORMALIZATION_ATTEMPTS,
        help=(
            "Maximum number of bounded formalization attempts per node. Default: "
            f"{DEFAULT_FORMALIZATION_ATTEMPTS}."
        ),
    )
    formalize_all_parser.add_argument(
        "--formalization-mode",
        choices=["agentic"],
        default="agentic",
        help="Formalization execution mode. Agentic is the only supported option.",
    )
    add_formalization_timeout_arg(formalize_all_parser)
    formalize_all_parser.set_defaults(func=cmd_formalize_all_candidates)

    report_parser = subparsers.add_parser("report")
    add_backend_args(report_parser)
    add_graph_input_arg(report_parser)
    add_output_dir_arg(report_parser)
    report_parser.set_defaults(func=cmd_report)

    run_parser = subparsers.add_parser("run-example")
    add_backend_args(run_parser)
    add_input_args(run_parser)
    add_output_dir_arg(run_parser)
    run_parser.add_argument(
        "--workspace",
        default="lean_project",
        help="Path to the local Lean workspace.",
    )
    run_parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_FORMALIZATION_ATTEMPTS,
        help=(
            "Maximum number of bounded formalization attempts. Default: "
            f"{DEFAULT_FORMALIZATION_ATTEMPTS}."
        ),
    )
    run_parser.add_argument(
        "--formalization-mode",
        choices=["agentic"],
        default="agentic",
        help="Formalization execution mode. Agentic is the only supported option.",
    )
    add_formalization_timeout_arg(run_parser)
    run_parser.set_defaults(func=cmd_run_example)

    for cmd_name in ("run-benchmark", "run"):
        bp = subparsers.add_parser(
            cmd_name,
            help="Plan and formalize a theorem end-to-end." if cmd_name == "run" else argparse.SUPPRESS,
        )
        add_backend_args(bp)
        add_input_args(bp)
        bp.add_argument(
            "--output-dir",
            default=None,
            help=(
                "Directory where outputs should be written. "
                "Default: auto-derived from input filename, backends, and timestamp."
            ),
        )
        bp.add_argument(
            "--workspace",
            default=None,
            help=(
                "Path to the local Lean workspace. "
                "Default: auto-discovered lean_project/ relative to the repo root."
            ),
        )
        bp.add_argument(
            "--node-id",
            default="auto",
            help=(
                "Candidate node id to formalize. Default 'auto': formalize all candidates in priority order. "
                "Pass a specific node id to formalize only that one node."
            ),
        )
        bp.add_argument(
            "--max-attempts",
            type=int,
            default=DEFAULT_FORMALIZATION_ATTEMPTS,
            help=(
                f"Maximum number of bounded formalization attempts per node. Default: {DEFAULT_FORMALIZATION_ATTEMPTS}."
            ),
        )
        bp.add_argument(
            "--formalization-mode",
            choices=["agentic"],
            default="agentic",
            help="Formalization execution mode. Agentic is the only supported option.",
        )
        add_formalization_timeout_arg(bp)
        bp.set_defaults(func=cmd_run_benchmark)

    new_parser = subparsers.add_parser("new", help="Interactively enter a theorem and run the pipeline.")
    add_backend_args(new_parser)
    new_parser.add_argument(
        "--workspace",
        default=None,
        help="Path to the local Lean workspace. Default: auto-discovered.",
    )
    new_parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_FORMALIZATION_ATTEMPTS,
        help=f"Maximum number of formalization attempts. Default: {DEFAULT_FORMALIZATION_ATTEMPTS}.",
    )
    add_formalization_timeout_arg(new_parser)
    new_parser.set_defaults(func=cmd_new)

    return parser


def add_backend_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backends",
        default=None,
        metavar="PLANNING/FORMALIZATION",
        help=(
            "Shorthand for both backends as 'planning/formalization' (e.g. gemini/aristotle). "
            "A single name (e.g. claude) uses that backend for both stages."
        ),
    )
    parser.add_argument(
        "--backend",
        choices=["codex", "claude", "gemini"],
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--model",
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--planning-backend",
        choices=["codex", "claude", "gemini"],
        default=None,
        help="Backend to use for planning / extraction stages.",
    )
    parser.add_argument(
        "--planning-model",
        default=None,
        help="Optional model override for the planning backend.",
    )
    parser.add_argument(
        "--formalization-backend",
        choices=["codex", "claude", "gemini", "aristotle"],
        default=None,
        help="Backend to use for formalization stages. Aristotle is supported here only.",
    )
    parser.add_argument(
        "--formalization-model",
        default=None,
        help="Optional model override for the formalization backend when supported.",
    )


def add_input_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--input",
        default="examples/nonnegative_sum_input.json",
        help=(
            "JSON file with theorem_title, theorem_statement, and raw_proof_text. "
            "Bare filenames (without path separators) are searched in examples/featured/ "
            "and examples/manual-testing/ automatically."
        ),
    )


def add_graph_input_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--graph",
        required=True,
        help="Path to a saved proof graph JSON file.",
    )


def add_output_dir_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where stage outputs should be written.",
    )


def add_formalization_timeout_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--formalization-timeout-seconds",
        type=float,
        default=None,
        help=(
            "Optional timeout override for the formalization backend worker in seconds. "
            "Leave unset to use the backend default (Aristotle defaults to no timeout)."
        ),
    )


def _cleanup_archive_artifacts(output_dir: Path) -> list[Path]:
    """Remove tar.gz artifacts that are no longer needed after a run completes."""

    removed: list[Path] = []
    if not output_dir.exists():
        return removed

    for archive_path in sorted(output_dir.rglob("*.tar.gz")):
        try:
            archive_path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            continue
        removed.append(archive_path)

    if removed:
        progress(f"cleaned up {len(removed)} archive artifact(s) under {output_dir}")
    return removed


def cmd_extract(args: argparse.Namespace) -> int:
    input_payload = load_input_payload(Path(args.input))
    output_dir = ensure_output_dir(Path(args.output_dir))
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME):
        progress("extract stage starting")
        backend = build_backend(
            resolve_backend_name(args, formalization=False),
            resolve_backend_model(args, formalization=False),
            output_dir / "_backend_logs",
            timeout_seconds=DEFAULT_BACKEND_TIMEOUT_SECONDS,
        )
        graph = extract_proof_graph(
            backend=backend,
            theorem_statement=input_payload["theorem_statement"],
            raw_proof_text=input_payload["raw_proof_text"],
            theorem_title_hint=input_payload["theorem_title"],
        )
        path = output_dir / "01_extracted_graph.json"
        write_graph(graph, path)
        append_graph_summary_to_progress_log(graph, label="01_extracted_graph.json")
        print(path)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    input_payload = load_input_payload(Path(args.input))
    output_dir = ensure_output_dir(Path(args.output_dir))
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME):
        progress("plan stage starting")
        backend = build_backend(
            resolve_backend_name(args, formalization=False),
            resolve_backend_model(args, formalization=False),
            output_dir / "_backend_logs",
            timeout_seconds=DEFAULT_BACKEND_TIMEOUT_SECONDS,
        )
        artifacts = plan_proof_graph(
            backend=backend,
            theorem_statement=input_payload["theorem_statement"],
            raw_proof_text=input_payload["raw_proof_text"],
            theorem_title_hint=input_payload["theorem_title"],
        )
        extracted_path = output_dir / "01_extracted_graph.json"
        candidate_path = output_dir / "02_candidate_graph.json"
        write_graph(artifacts.extracted_graph, extracted_path)
        append_graph_summary_to_progress_log(artifacts.extracted_graph, label="01_extracted_graph.json")
        write_graph(artifacts.candidate_graph, candidate_path)
        append_graph_summary_to_progress_log(
            artifacts.candidate_graph,
            label="02_candidate_graph.json",
            previous_graph=artifacts.extracted_graph,
        )
        print(extracted_path)
        print(candidate_path)
    return 0


def cmd_select_candidates(args: argparse.Namespace) -> int:
    output_dir = ensure_output_dir(Path(args.output_dir))
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME):
        progress("candidate selection stage starting")
        backend = build_backend(
            resolve_backend_name(args, formalization=False),
            resolve_backend_model(args, formalization=False),
            output_dir / "_backend_logs",
            timeout_seconds=DEFAULT_BACKEND_TIMEOUT_SECONDS,
        )
        graph = load_graph(Path(args.graph))
        _log_dependency_direction_warnings(graph, context="candidate-selection stage")
        updated_graph = select_formalization_candidates(backend=backend, graph=graph)
        path = output_dir / "02_candidate_graph.json"
        write_graph(updated_graph, path)
        append_graph_summary_to_progress_log(
            updated_graph,
            label="02_candidate_graph.json",
            previous_graph=graph,
        )
        print(path)
    return 0


def cmd_formalize_one(args: argparse.Namespace) -> int:
    output_dir = ensure_output_dir(Path(args.output_dir))
    cleanup_archives = getattr(args, "cleanup_archives", True)
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME):
        progress("formalization stage starting")
        planning_backend_name = resolve_backend_name(args, formalization=False)
        planning_backend = build_backend(
            planning_backend_name,
            resolve_backend_model(args, formalization=False),
            output_dir / "_backend_logs",
            timeout_seconds=DEFAULT_BACKEND_TIMEOUT_SECONDS,
        )
        formalization_backend_name = resolve_backend_name(args, formalization=True)
        backend = build_backend(
            formalization_backend_name,
            resolve_backend_model(args, formalization=True),
            output_dir / "_backend_logs",
            timeout_seconds=resolve_formalization_timeout(args, formalization_backend_name),
            formalization=True,
        )
        graph = load_graph(Path(args.graph))
        _log_dependency_direction_warnings(graph, context="formalization stage")
        node_id = select_candidate_node_id(graph, requested_node_id=args.node_id)
        verifier = LeanVerifier(workspace=LeanWorkspace(root=Path(args.workspace)))
        graph_path = output_dir / "03_formalized_graph.json"
        summary_path = output_dir / "03_formalization_summary.json"
        latest_graph = graph

        def write_progress(outcome) -> None:
            nonlocal latest_graph
            write_graph(outcome.graph, graph_path)
            append_graph_summary_to_progress_log(
                outcome.graph,
                label=f"03_formalized_graph.json ({outcome.node_id})",
                previous_graph=latest_graph,
            )
            latest_graph = outcome.graph
            _write_formalization_summary(summary_path, outcome)

        try:
            outcome = formalize_candidate_node(
                backend=backend,
                planning_backend=planning_backend,
                verifier=verifier,
                graph=graph,
                node_id=node_id,
                max_attempts=args.max_attempts,
                on_update=write_progress,
                mode=args.formalization_mode,
            )
        except BackendError as exc:
            failure_outcome = _backend_failure_outcome(graph=graph, node_id=node_id, error=exc)
            write_progress(failure_outcome)
            print(graph_path)
            print(summary_path)
            return 0
        else:
            write_progress(outcome)
            print(graph_path)
            print(summary_path)
        finally:
            if cleanup_archives:
                _cleanup_archive_artifacts(output_dir)
    return 0


def cmd_formalize_all_candidates(args: argparse.Namespace) -> int:
    output_dir = ensure_output_dir(Path(args.output_dir))
    cleanup_archives = getattr(args, "cleanup_archives", True)
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME):
        try:
            progress("formalization stage starting")
            planning_backend_name = resolve_backend_name(args, formalization=False)
            planning_backend = build_backend(
                planning_backend_name,
                resolve_backend_model(args, formalization=False),
                output_dir / "_backend_logs",
                timeout_seconds=DEFAULT_BACKEND_TIMEOUT_SECONDS,
            )
            formalization_backend_name = resolve_backend_name(args, formalization=True)
            backend = build_backend(
                formalization_backend_name,
                resolve_backend_model(args, formalization=True),
                output_dir / "_backend_logs",
                timeout_seconds=resolve_formalization_timeout(args, formalization_backend_name),
                formalization=True,
            )
            graph = load_graph(Path(args.graph))
            _log_dependency_direction_warnings(graph, context="formalization stage")
            verifier = LeanVerifier(workspace=LeanWorkspace(root=Path(args.workspace)))
            graph_path = output_dir / "03_formalized_graph.json"
            summary_path = output_dir / "03_formalization_summaries.json"
            latest_graph = graph

            summaries: list[dict[str, Any]] = []

            def write_progress(outcome) -> None:
                nonlocal latest_graph
                write_graph(outcome.graph, graph_path)
                append_graph_summary_to_progress_log(
                    outcome.graph,
                    label=f"03_formalized_graph.json ({outcome.node_id})",
                    previous_graph=latest_graph,
                )
                latest_graph = outcome.graph

            outcomes = formalize_candidate_nodes(
                backend=backend,
                planning_backend=planning_backend,
                verifier=verifier,
                graph=graph,
                max_attempts=args.max_attempts,
                on_update=write_progress,
                mode=args.formalization_mode,
            )

            write_graph(outcomes.graph, graph_path)
            append_graph_summary_to_progress_log(
                outcomes.graph,
                label="03_formalized_graph.json",
                previous_graph=latest_graph,
            )
            for outcome in outcomes.outcomes:
                summaries.append(
                    {
                        "node_id": outcome.node_id,
                        "status": outcome.artifact.verification.status,
                        "artifact_path": outcome.artifact.verification.artifact_path,
                        "attempt_count": outcome.artifact.verification.attempt_count,
                        "stderr": outcome.artifact.verification.stderr,
                        "faithfulness_classification": outcome.artifact.faithfulness_classification,
                        "lean_theorem_name": outcome.artifact.lean_theorem_name,
                    }
                )
            summary_path.write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
            print(graph_path)
            print(summary_path)
        finally:
            if cleanup_archives:
                _cleanup_archive_artifacts(output_dir)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    output_dir = ensure_output_dir(Path(args.output_dir))
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME):
        progress("report stage starting")
        graph = load_graph(Path(args.graph))
        _log_dependency_direction_warnings(graph, context="report stage")
        planning_backend_name = getattr(args, "planning_backend", None)
        legacy_backend_name = getattr(args, "backend", None)
        planning_backend = None
        if planning_backend_name is not None or legacy_backend_name is not None:
            planning_backend = build_backend(
                planning_backend_name or legacy_backend_name,
                getattr(args, "planning_model", None)
                if planning_backend_name is not None
                else getattr(args, "model", None),
                output_dir / "_backend_logs",
                timeout_seconds=DEFAULT_BACKEND_TIMEOUT_SECONDS,
            )
        if planning_backend is not None:
            progress("report stage: synthesizing remaining proof burdens")
            graph = synthesize_remaining_proof_burdens(
                graph=graph,
                planning_backend=planning_backend,
            )
        obligations = derive_review_obligations(graph)
        bundle = export_report_bundle(graph, obligations)
        html = render_html_report(graph, obligations)

        bundle_path = output_dir / "04_report_bundle.json"
        html_path = output_dir / "04_report.html"
        bundle_path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
        html_path.write_text(html, encoding="utf-8")
        print(bundle_path)
        print(html_path)
    return 0


def cmd_run_example(args: argparse.Namespace) -> int:
    output_dir = ensure_output_dir(Path(args.output_dir))
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME):
        try:
            progress("running example end-to-end")
            formalization_backend_name = resolve_backend_name(args, formalization=True)
            formalization_timeout = resolve_formalization_timeout(args, formalization_backend_name)
            progress("example planning stage starting")
            cmd_plan(
                argparse.Namespace(
                    backend=getattr(args, "backend", None),
                    model=getattr(args, "model", None),
                    planning_backend=getattr(args, "planning_backend", None),
                    planning_model=getattr(args, "planning_model", None),
                    formalization_backend=getattr(args, "formalization_backend", None),
                    formalization_model=getattr(args, "formalization_model", None),
                    input=args.input,
                    output_dir=str(output_dir),
                )
            )
            candidate_graph_path = output_dir / "02_candidate_graph.json"
            progress("example formalization stage starting")
            cmd_formalize_one(
                argparse.Namespace(
                    backend=getattr(args, "backend", None),
                    model=getattr(args, "model", None),
                    planning_backend=getattr(args, "planning_backend", None),
                    planning_model=getattr(args, "planning_model", None),
                    formalization_backend=getattr(args, "formalization_backend", None),
                    formalization_model=getattr(args, "formalization_model", None),
                    graph=str(candidate_graph_path),
                    output_dir=str(output_dir),
                    workspace=args.workspace,
                    node_id="auto",
                    max_attempts=args.max_attempts,
                    formalization_mode=args.formalization_mode,
                    formalization_timeout_seconds=formalization_timeout,
                    cleanup_archives=False,
                )
            )
            formalized_graph_path = output_dir / "03_formalized_graph.json"
            progress("example report stage starting")
            cmd_report(
                argparse.Namespace(
                    graph=str(formalized_graph_path),
                    output_dir=str(output_dir),
                    backend=getattr(args, "backend", None),
                    model=getattr(args, "model", None),
                    planning_backend=getattr(args, "planning_backend", None),
                    planning_model=getattr(args, "planning_model", None),
                    formalization_backend=getattr(args, "formalization_backend", None),
                    formalization_model=getattr(args, "formalization_model", None),
                )
            )
        finally:
            _cleanup_archive_artifacts(output_dir)
    return 0


def cmd_run_benchmark(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    output_dir = ensure_output_dir(
        Path(args.output_dir)
        if args.output_dir is not None
        else default_output_dir_for_input(input_path, args)
    )
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME):
        try:
            progress("running benchmark end-to-end")
            progress("benchmark planning stage starting")
            cmd_plan(
                argparse.Namespace(
                    backends=getattr(args, "backends", None),
                    backend=getattr(args, "backend", None),
                    model=getattr(args, "model", None),
                    planning_backend=getattr(args, "planning_backend", None),
                    planning_model=getattr(args, "planning_model", None),
                    formalization_backend=getattr(args, "formalization_backend", None),
                    formalization_model=getattr(args, "formalization_model", None),
                    input=str(input_path),
                    output_dir=str(output_dir),
                )
            )
            candidate_graph_path = output_dir / "02_candidate_graph.json"
            formalization_backend_name = resolve_backend_name(args, formalization=True)
            formalization_timeout = resolve_formalization_timeout(args, formalization_backend_name)
            common = argparse.Namespace(
                backends=getattr(args, "backends", None),
                backend=getattr(args, "backend", None),
                model=getattr(args, "model", None),
                planning_backend=getattr(args, "planning_backend", None),
                planning_model=getattr(args, "planning_model", None),
                formalization_backend=getattr(args, "formalization_backend", None),
                formalization_model=getattr(args, "formalization_model", None),
                graph=str(candidate_graph_path),
                output_dir=str(output_dir),
                workspace=workspace,
                max_attempts=args.max_attempts,
                formalization_mode=args.formalization_mode,
                formalization_timeout_seconds=formalization_timeout,
                cleanup_archives=False,
            )
            progress("benchmark formalization stage starting")
            if args.node_id == "auto":
                cmd_formalize_all_candidates(common)
            else:
                cmd_formalize_one(
                    argparse.Namespace(**vars(common), node_id=args.node_id)
                )
            formalized_graph_path = output_dir / "03_formalized_graph.json"
            progress("benchmark report stage starting")
            cmd_report(
                argparse.Namespace(
                    graph=str(formalized_graph_path),
                    output_dir=str(output_dir),
                    backends=getattr(args, "backends", None),
                    backend=getattr(args, "backend", None),
                    model=getattr(args, "model", None),
                    planning_backend=getattr(args, "planning_backend", None),
                    planning_model=getattr(args, "planning_model", None),
                    formalization_backend=getattr(args, "formalization_backend", None),
                    formalization_model=getattr(args, "formalization_model", None),
                )
            )
        finally:
            _cleanup_archive_artifacts(output_dir)
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    """Interactively prompt for a theorem and run the full pipeline."""

    print("=== Formal Islands — New Theorem ===")
    print()
    try:
        theorem_title = input("Theorem title: ").strip()
    except EOFError:
        print("error: interactive input not available", file=sys.stderr)
        return 1
    if not theorem_title:
        print("error: theorem title is required", file=sys.stderr)
        return 1

    print("Theorem statement (empty line to finish):")
    statement_lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            break
        statement_lines.append(line)
    theorem_statement = "\n".join(statement_lines).strip()
    if not theorem_statement:
        print("error: theorem statement is required", file=sys.stderr)
        return 1

    print("Proof sketch (empty line to finish):")
    proof_lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            break
        proof_lines.append(line)
    raw_proof_text = "\n".join(proof_lines).strip()
    if not raw_proof_text:
        print("error: proof sketch is required", file=sys.stderr)
        return 1

    slug = re.sub(r"[^a-z0-9]+", "_", theorem_title.lower()).strip("_")[:40]
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path("artifacts/manual-testing") / f"{slug}-{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    input_path = run_dir / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "theorem_title": theorem_title,
                "theorem_statement": theorem_statement,
                "raw_proof_text": raw_proof_text,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    progress(f"wrote input to {input_path}")

    run_args = argparse.Namespace(
        backends=getattr(args, "backends", None),
        backend=getattr(args, "backend", None),
        model=getattr(args, "model", None),
        planning_backend=getattr(args, "planning_backend", None),
        planning_model=getattr(args, "planning_model", None),
        formalization_backend=getattr(args, "formalization_backend", None),
        formalization_model=getattr(args, "formalization_model", None),
        input=str(input_path),
        output_dir=str(run_dir),
        workspace=getattr(args, "workspace", None),
        node_id="auto",
        max_attempts=args.max_attempts,
        formalization_mode="agentic",
        formalization_timeout_seconds=getattr(args, "formalization_timeout_seconds", None),
    )
    return cmd_run_benchmark(run_args)


def build_backend(
    name: str,
    model: str | None,
    log_dir: Path | None = None,
    timeout_seconds: float | None = None,
    formalization: bool = False,
) -> StructuredBackend | AristotleBackend:
    """Create a backend instance for the smoke CLI."""

    if name == "codex":
        timeout_seconds = DEFAULT_BACKEND_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        return CodexCLIBackend(model=model, log_dir=log_dir, timeout_seconds=timeout_seconds)
    if name == "claude":
        timeout_seconds = DEFAULT_BACKEND_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        return ClaudeCodeBackend(model=model, log_dir=log_dir, timeout_seconds=timeout_seconds)
    if name == "gemini":
        timeout_seconds = GEMINI_BACKEND_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        return GeminiCLIBackend(model=model, log_dir=log_dir, timeout_seconds=timeout_seconds)
    if name == "aristotle":
        if not formalization:
            raise ValueError("aristotle is only supported for formalization backends")
        timeout_seconds = None if timeout_seconds is None else timeout_seconds
        return AristotleBackend(log_dir=log_dir, timeout_seconds=timeout_seconds)
    raise ValueError(f"unsupported backend: {name}")


def resolve_backend_name(args: argparse.Namespace, *, formalization: bool) -> str:
    backends_shorthand = getattr(args, "backends", None)
    if backends_shorthand is not None:
        parts = backends_shorthand.split("/", 1)
        if len(parts) == 2:
            return parts[1].strip() if formalization else parts[0].strip()
        return parts[0].strip()

    explicit_name = getattr(
        args,
        "formalization_backend" if formalization else "planning_backend",
        None,
    )
    if explicit_name is not None:
        return explicit_name

    legacy_name = getattr(args, "backend", None)
    if legacy_name is not None:
        return legacy_name

    return "codex"


def resolve_backend_model(args: argparse.Namespace, *, formalization: bool) -> str | None:
    explicit_model = getattr(
        args,
        "formalization_model" if formalization else "planning_model",
        None,
    )
    if explicit_model is not None:
        return explicit_model

    return getattr(args, "model", None)


def resolve_formalization_timeout(args: argparse.Namespace, backend_name: str) -> float | None:
    explicit_timeout = getattr(args, "formalization_timeout_seconds", None)
    if explicit_timeout is not None:
        return explicit_timeout
    if backend_name == "aristotle":
        return None
    return FORMALIZATION_BACKEND_TIMEOUT_SECONDS


def _write_formalization_summary(path: Path, outcome) -> None:
    path.write_text(
        json.dumps(
            {
                "node_id": outcome.node_id,
                "status": outcome.artifact.verification.status,
                "artifact_path": outcome.artifact.verification.artifact_path,
                "attempt_count": outcome.artifact.verification.attempt_count,
                "stderr": outcome.artifact.verification.stderr,
                "faithfulness_classification": outcome.artifact.faithfulness_classification,
                "lean_theorem_name": outcome.artifact.lean_theorem_name,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _backend_failure_outcome(*, graph: ProofGraph, node_id: str, error: BackendError):
    verification = VerificationResult(
        status="failed",
        command="backend_request",
        exit_code=None,
        stdout="",
        stderr=str(error),
        attempt_count=1,
        artifact_path=None,
    )
    artifact = FormalArtifact(
        lean_theorem_name=f"{node_id}_backend_failure",
        lean_statement="-- backend did not return a Lean statement",
        lean_code="-- backend did not return Lean code",
        verification=verification,
        attempt_history=[verification],
    )
    updated_graph = graph.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"status": "formal_failed", "formal_artifact": artifact})
                if node.id == node_id
                else node
                for node in graph.nodes
            ]
        }
    )
    from formal_islands.formalization.loop import FormalizationOutcome

    return FormalizationOutcome(graph=updated_graph, node_id=node_id, artifact=artifact)


def _resolve_input_path(path: Path) -> Path | None:
    """Resolve an input path, searching featured/ and manual-testing/ for bare filenames."""
    if path.exists():
        return path
    # For bare filenames (no directory separators), search example dirs.
    if path.parent == Path("."):
        name = path.name if path.suffix else path.name + ".json"
        for search_dir in (
            Path("examples/featured"),
            Path("examples/manual-testing"),
        ):
            candidate = search_dir / name
            if candidate.exists():
                return candidate
    return None


def _discover_workspace() -> Path | None:
    """Walk up from cwd looking for a lean_project/ directory with a lakefile."""
    for directory in [Path.cwd(), *Path.cwd().parents]:
        candidate = directory / "lean_project"
        if candidate.is_dir() and (
            (candidate / "lakefile.lean").exists()
            or (candidate / "lakefile.toml").exists()
        ):
            return candidate
    return None


def _resolve_workspace(workspace_arg: str | None) -> str:
    """Resolve the Lean workspace path, auto-discovering if not specified."""
    if workspace_arg is not None:
        return workspace_arg
    discovered = _discover_workspace()
    if discovered is not None:
        return str(discovered)
    return "lean_project"


def load_input_payload(path: Path) -> dict[str, Any]:
    """Load theorem input JSON, falling back to the default example if absent."""

    resolved = _resolve_input_path(path)
    if resolved is None:
        if path == Path("examples/nonnegative_sum_input.json"):
            return DEFAULT_EXAMPLE_INPUT.copy()
        raise FileNotFoundError(path)
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    required_keys = {"theorem_statement", "raw_proof_text"}
    missing = sorted(required_keys - set(payload))
    if missing:
        raise ValueError(f"input payload is missing keys: {', '.join(missing)}")
    if "theorem_title" not in payload:
        payload["theorem_title"] = payload.get("theorem_title_hint", "Untitled theorem")
    return payload


def load_graph(path: Path) -> ProofGraph:
    """Load a graph JSON file into the validated internal model."""

    return ProofGraph.model_validate_json(path.read_text(encoding="utf-8"))


def write_graph(graph: ProofGraph, path: Path) -> None:
    """Write a graph JSON file."""

    path.write_text(json.dumps(graph.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")


def ensure_output_dir(path: Path) -> Path:
    """Create the output directory if needed."""

    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output_dir_for_input(path: Path, args: argparse.Namespace | None = None) -> Path:
    """Derive a sensible artifact directory from an input JSON path."""

    stem = path.stem.replace("_", "-")
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if args is not None:
        planning = resolve_backend_name(args, formalization=False)
        formalization = resolve_backend_name(args, formalization=True)
        return Path("artifacts/manual-testing") / f"{stem}-{planning}-{formalization}-{timestamp}"
    return Path("artifacts/manual-testing") / f"{stem}-{timestamp}"


def select_candidate_node_id(graph: ProofGraph, requested_node_id: str = "auto") -> str:
    """Select a candidate node id deterministically."""

    if requested_node_id != "auto":
        return requested_node_id

    candidates = [node for node in graph.nodes if node.status == "candidate_formal"]
    if not candidates:
        raise ValueError("graph does not contain any candidate_formal nodes")

    chosen = sorted(
        candidates,
        key=lambda node: (
            (node.formalization_priority or 999),
            node.id,
        ),
    )[0]
    return chosen.id


if __name__ == "__main__":
    raise SystemExit(main())
