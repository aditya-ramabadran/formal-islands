"""Formal Islands CLI."""

from __future__ import annotations

import argparse
import datetime
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any
from threading import RLock

from formal_islands.backends import (
    BackendError,
    AristotleBackend,
    ClaudeCodeBackend,
    CodexCLIBackend,
    GeminiCLIBackend,
    StructuredBackend,
)
from formal_islands.continuation import format_continuation_rationale
from formal_islands.direct_root import (
    run_direct_root_aristotle_diagnostic,
    write_direct_root_diagnostic_summary,
)
from formal_islands.fixed_spec import (
    build_fixed_root_lean_spec,
    read_fixed_root_lean_statement_file,
)
from formal_islands.examples import TOY_RAW_PROOF, TOY_THEOREM_STATEMENT
from formal_islands.extraction import (
    extract_proof_graph,
    plan_proof_graph,
    select_formalization_candidates,
)
from formal_islands.formalization import (
    DirectRootProbeConfig,
    LeanVerifier,
    LeanWorkspace,
    formalize_candidate_node,
    formalize_candidate_nodes,
)
from formal_islands.formalization.loop import (
    DEFAULT_FORMALIZATION_ATTEMPTS,
)
from formal_islands.models import (
    FixedRootLeanSpec,
    FormalArtifact,
    ProofGraph,
    VerificationResult,
    canonical_dependency_direction_warnings,
)
from formal_islands.report import export_report_bundle, load_graph_history_entries, render_html_report
from formal_islands.report.annotation import synthesize_remaining_proof_burdens
from formal_islands.review import derive_review_obligations
from formal_islands.progress import (
    append_to_progress_log,
    append_graph_snapshot_to_history_log,
    append_graph_summary_to_progress_log,
    progress,
    use_graph_history_log,
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
GRAPH_HISTORY_LOG_FILENAME = "graph_history.jsonl"


def _log_dependency_direction_warnings(graph: ProofGraph, *, context: str) -> None:
    for warning in canonical_dependency_direction_warnings(graph):
        progress(f"{context}: canonical direction warning: {warning}")


def _log_cli_invocation(
    *,
    command_name: str,
    args: argparse.Namespace,
    effective: dict[str, object] | None = None,
) -> None:
    """Append a stable CLI invocation summary to the active progress log."""

    arg_items = {
        key: value
        for key, value in vars(args).items()
        if key != "func"
    }
    cli_parts = ["formal-islands", command_name]
    for key in sorted(arg_items):
        value = arg_items[key]
        if value is None:
            continue
        flag = f"--{key.replace('_', '-')}"
        if isinstance(value, bool):
            if value:
                cli_parts.append(flag)
            continue
        cli_parts.append(f"{flag}={shlex.quote(str(value))}")

    lines = ["CLI invocation summary:", f"  command: {' '.join(cli_parts)}"]
    if arg_items:
        lines.append("  raw args:")
        for key in sorted(arg_items):
            lines.append(f"    {key} = {arg_items[key]!r}")
    if effective:
        lines.append("  effective settings:")
        for key in sorted(effective):
            lines.append(f"    {key} = {effective[key]!r}")
    append_to_progress_log("\n".join(lines))


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
    select_parser.add_argument(
        "--attempt-all-nodes",
        action="store_true",
        help=(
            "Bypass conservative candidate selection and mark every currently informal "
            "node as candidate_formal. Intended for exploratory benchmark sweeps."
        ),
    )
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

    continue_parser = subparsers.add_parser("continue")
    add_backend_args(continue_parser)
    add_output_dir_arg(continue_parser)
    continue_parser.add_argument(
        "--workspace",
        default=None,
        help="Path to the local Lean workspace. Default: auto-discovered.",
    )
    continue_parser.add_argument(
        "--node",
        "--node-id",
        dest="node_ids",
        action="append",
        required=True,
        help=(
            "Node id to reintroduce as a candidate for continuation. "
            "Repeat the flag to seed multiple nodes."
        ),
    )
    continue_parser.add_argument(
        "--instructions",
        action="append",
        default=None,
        help=(
            "Extra continuation guidance to pass into the next formalization prompt. "
            "Repeat the flag to concatenate multiple instruction blocks."
        ),
    )
    continue_parser.add_argument(
        "--instructions-file",
        action="append",
        default=None,
        help=(
            "Path to a text file containing extra continuation guidance. "
            "Repeat the flag to concatenate multiple files."
        ),
    )
    continue_parser.add_argument(
        "--lean-statement",
        default=None,
        help=(
            "Optional preferred Lean theorem statement or theorem-shape hint for the "
            "continued node attempt."
        ),
    )
    add_fixed_root_spec_args(continue_parser)
    continue_parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_FORMALIZATION_ATTEMPTS,
        help=(
            "Maximum number of bounded formalization attempts per node during continuation. "
            f"Default: {DEFAULT_FORMALIZATION_ATTEMPTS}."
        ),
    )
    continue_parser.add_argument(
        "--formalization-mode",
        choices=["agentic"],
        default="agentic",
        help="Formalization execution mode. Agentic is the only supported option.",
    )
    add_formalization_timeout_arg(continue_parser)
    continue_parser.set_defaults(func=cmd_continue)

    report_parser = subparsers.add_parser("report")
    add_backend_args(report_parser)
    add_graph_input_arg(report_parser)
    add_output_dir_arg(report_parser)
    report_parser.set_defaults(func=cmd_report)

    direct_root_parser = subparsers.add_parser(
        "direct-root",
        help="Run an Aristotle direct-root diagnostic on a theorem/proof JSON input.",
    )
    add_input_args(direct_root_parser)
    direct_root_parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Directory where direct full/root attempt diagnostic outputs should be written. "
            "Default: auto-derived from input filename and timestamp."
        ),
    )
    direct_root_parser.add_argument(
        "--workspace",
        default=None,
        help=(
            "Path to the local Lean workspace. "
            "Default: auto-discovered lean_project/ relative to the repo root."
        ),
    )
    direct_root_parser.add_argument(
        "--formalization-backend",
        choices=["aristotle"],
        default="aristotle",
        help="Formalization backend for this diagnostic. Currently only Aristotle is supported.",
    )
    direct_root_parser.add_argument(
        "--max-attempts",
        "--max_attempts",
        dest="max_attempts",
        type=int,
        default=DEFAULT_FORMALIZATION_ATTEMPTS,
        help=(
            "Maximum number of bounded Aristotle direct-root attempts. Default: "
            f"{DEFAULT_FORMALIZATION_ATTEMPTS}."
        ),
    )
    add_fixed_root_spec_args(direct_root_parser)
    add_formalization_timeout_arg(direct_root_parser)
    direct_root_parser.set_defaults(func=cmd_direct_root)

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
    run_parser.add_argument(
        "--attempt-all-nodes",
        action="store_true",
        help=(
            "After planning, mark every currently informal node as candidate_formal. "
            "This is an exploratory mode for broad benchmark sweeps, not the default "
            "curated artifact workflow."
        ),
    )
    add_fixed_root_spec_args(run_parser)
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
        bp.add_argument(
            "--attempt-all-nodes",
            action="store_true",
            help=(
                "After planning, mark every currently informal node as candidate_formal. "
                "This is an exploratory mode for broad benchmark sweeps, not the default "
                "curated artifact workflow."
            ),
        )
        add_fixed_root_spec_args(bp)
        add_formalization_timeout_arg(bp)
        add_direct_root_probe_args(bp)
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


def add_fixed_root_spec_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--fixed-root-lean-statement",
        "--strict-lean-statement",
        dest="fixed_root_lean_statement",
        default=None,
        help=(
            "Optional exact Lean statement for the root theorem. Root attempts must preserve "
            "this theorem header; child attempts receive it only as compatibility context."
        ),
    )
    parser.add_argument(
        "--fixed-root-lean-statement-file",
        "--strict-lean-statement-file",
        dest="fixed_root_lean_statement_file",
        default=None,
        help=(
            "Path to an optional exact Lean root statement. Root attempts must preserve this "
            "theorem header; child attempts receive it only as compatibility context."
        ),
    )
    parser.add_argument(
        "--fixed-root-source",
        default="manual",
        help="Short provenance label for the fixed Lean root statement.",
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


def add_direct_root_probe_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-direct-root-probe",
        action="store_true",
        help=(
            "Disable the default first-wave direct-root Aristotle probe. By default, "
            "full run/run-benchmark Aristotle runs submit a compact direct-root attempt "
            "in parallel with the first graph formalization wave and short-circuit if it "
            "verifies and passes semantic audit."
        ),
    )
    parser.add_argument(
        "--run-graph-if-direct-root-verifies",
        action="store_true",
        help=(
            "Continue the graph pipeline even if the first-wave direct-root probe verifies "
            "the root and passes semantic audit. Useful when you still want a mixed-islands "
            "artifact for analysis."
        ),
    )


def _build_direct_root_probe_config(
    *,
    args: argparse.Namespace,
    formalization_backend_name: str,
    output_dir: Path,
    fixed_root_lean_spec: FixedRootLeanSpec | None,
) -> DirectRootProbeConfig | None:
    if getattr(args, "no_direct_root_probe", False):
        return None
    if formalization_backend_name != "aristotle":
        return None
    input_payload = getattr(args, "direct_root_probe_input_payload", None)
    if input_payload is None:
        return None
    return DirectRootProbeConfig(
        input_payload=input_payload,
        output_dir=output_dir,
        fixed_root_lean_spec=fixed_root_lean_spec,
        max_attempts=int(getattr(args, "max_attempts", DEFAULT_FORMALIZATION_ATTEMPTS)),
        run_graph_if_direct_root_verifies=bool(
            getattr(args, "run_graph_if_direct_root_verifies", False)
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


def _normalize_requested_node_ids(raw_node_ids: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_node_ids or []:
        node_id = str(raw).strip()
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        normalized.append(node_id)
    if not normalized:
        raise ValueError("at least one node id is required for continuation")
    return normalized


def _collect_continuation_instructions(args: argparse.Namespace) -> str | None:
    parts: list[str] = []
    for raw in getattr(args, "instructions", None) or []:
        text = str(raw).strip()
        if text:
            parts.append(text)

    for raw_path in getattr(args, "instructions_file", None) or []:
        path = Path(str(raw_path)).expanduser()
        text = path.read_text(encoding="utf-8").strip()
        if text:
            parts.append(f"Instructions from {path}:\n{text}")

    lean_statement = getattr(args, "lean_statement", None)
    if lean_statement is not None and str(lean_statement).strip():
        parts.append(
            "Preferred Lean theorem statement or theorem-shape hint:\n"
            f"{str(lean_statement).strip()}"
        )

    if not parts:
        return None
    return "\n\n".join(parts)


def _collect_fixed_root_lean_spec(args: argparse.Namespace) -> FixedRootLeanSpec | None:
    inline_statement = getattr(args, "fixed_root_lean_statement", None)
    statement_file = getattr(args, "fixed_root_lean_statement_file", None)
    has_inline = inline_statement is not None and str(inline_statement).strip()
    has_file = statement_file is not None and str(statement_file).strip()
    if has_inline and has_file:
        raise ValueError(
            "provide either --fixed-root-lean-statement or "
            "--fixed-root-lean-statement-file, not both"
        )
    if not has_inline and not has_file:
        return None
    if has_file:
        statement = read_fixed_root_lean_statement_file(str(statement_file))
        source = str(getattr(args, "fixed_root_source", None) or "manual")
    else:
        statement = str(inline_statement).strip()
        source = str(getattr(args, "fixed_root_source", None) or "manual")
    return build_fixed_root_lean_spec(statement, source=source)


def _attach_fixed_root_lean_spec(
    *,
    graph: ProofGraph,
    fixed_root_lean_spec: FixedRootLeanSpec | None,
) -> ProofGraph:
    if fixed_root_lean_spec is None:
        return graph
    return graph.model_copy(update={"fixed_root_lean_spec": fixed_root_lean_spec})


def _prepare_graph_for_continuation(
    *,
    graph: ProofGraph,
    node_ids: list[str],
    continuation_instructions: str | None = None,
) -> ProofGraph:
    requested = set(node_ids)
    nodes_by_id = {node.id: node for node in graph.nodes}
    missing = [node_id for node_id in node_ids if node_id not in nodes_by_id]
    if missing:
        raise ValueError(f"continuation requested unknown node ids: {', '.join(missing)}")

    rationale = format_continuation_rationale(continuation_instructions)

    updated_nodes = []
    for node in graph.nodes:
        if node.id not in requested:
            updated_nodes.append(node)
            continue
        if node.status == "formal_verified":
            raise ValueError(
                f"node '{node.id}' is already formal_verified; continuation retries for verified nodes are not supported"
            )
        updated_nodes.append(
            node.model_copy(
                update={
                    "status": "candidate_formal",
                    "formalization_priority": node.formalization_priority or 1,
                    "formalization_rationale": rationale,
                    "last_formalization_attempt_count": None,
                    "last_formalization_outcome": None,
                    "last_formalization_failure_kind": None,
                    "last_formalization_note": None,
                    "formal_artifact": None,
                }
            )
        )
    return graph.model_copy(update={"nodes": updated_nodes})


def cmd_extract(args: argparse.Namespace) -> int:
    input_payload = load_input_payload(Path(args.input))
    output_dir = ensure_output_dir(Path(args.output_dir))
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME), use_graph_history_log(
        output_dir / GRAPH_HISTORY_LOG_FILENAME
    ):
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
        _append_graph_logs(graph, label="01_extracted_graph.json", event="extract_stage_output")
        print(path)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    input_payload = load_input_payload(Path(args.input))
    output_dir = ensure_output_dir(Path(args.output_dir))
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME), use_graph_history_log(
        output_dir / GRAPH_HISTORY_LOG_FILENAME
    ):
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
        _append_graph_logs(
            artifacts.extracted_graph,
            label="01_extracted_graph.json",
            event="plan_stage_extracted_graph",
        )
        write_graph(artifacts.candidate_graph, candidate_path)
        _append_graph_logs(
            artifacts.candidate_graph,
            label="02_candidate_graph.json",
            previous_graph=artifacts.extracted_graph,
            event="plan_stage_candidate_graph",
        )
        print(extracted_path)
        print(candidate_path)
    return 0


def cmd_select_candidates(args: argparse.Namespace) -> int:
    output_dir = ensure_output_dir(Path(args.output_dir))
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME), use_graph_history_log(
        output_dir / GRAPH_HISTORY_LOG_FILENAME
    ):
        progress("candidate selection stage starting")
        backend = build_backend(
            resolve_backend_name(args, formalization=False),
            resolve_backend_model(args, formalization=False),
            output_dir / "_backend_logs",
            timeout_seconds=DEFAULT_BACKEND_TIMEOUT_SECONDS,
        )
        graph = load_graph(Path(args.graph))
        _log_dependency_direction_warnings(graph, context="candidate-selection stage")
        if getattr(args, "attempt_all_nodes", False):
            updated_graph = mark_all_informal_nodes_candidate_formal(graph)
            progress(
                "candidate selection stage: --attempt-all-nodes marked every informal "
                "node as candidate_formal"
            )
        else:
            updated_graph = select_formalization_candidates(backend=backend, graph=graph)
        path = output_dir / "02_candidate_graph.json"
        write_graph(updated_graph, path)
        _append_graph_logs(
            updated_graph,
            label="02_candidate_graph.json",
            previous_graph=graph,
            event="candidate_selection_output",
        )
        print(path)
    return 0


def mark_all_informal_nodes_candidate_formal(graph: ProofGraph) -> ProofGraph:
    """Return a graph where every informal node is an exploratory candidate."""

    changed = False
    updated_nodes = []
    for node in graph.nodes:
        if node.status != "informal":
            updated_nodes.append(node)
            continue

        changed = True
        updated_nodes.append(
            node.model_copy(
                update={
                    "status": "candidate_formal",
                    "formalization_priority": node.formalization_priority or 3,
                    "formalization_rationale": (
                        node.formalization_rationale
                        or (
                            "Selected by --attempt-all-nodes exploratory mode. "
                            "This node was not necessarily judged to be a conservative "
                            "high-yield formal island."
                        )
                    ),
                }
            )
        )

    if not changed:
        return graph
    return graph.model_copy(update={"nodes": updated_nodes})


def cmd_formalize_one(args: argparse.Namespace) -> int:
    output_dir = ensure_output_dir(Path(args.output_dir))
    cleanup_archives = getattr(args, "cleanup_archives", True)
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME), use_graph_history_log(
        output_dir / GRAPH_HISTORY_LOG_FILENAME
    ):
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
            _append_graph_logs(
                outcome.graph,
                label=f"03_formalized_graph.json ({outcome.node_id})",
                previous_graph=latest_graph,
                event="formalization_update",
                node_id=outcome.node_id,
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
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME), use_graph_history_log(
        output_dir / GRAPH_HISTORY_LOG_FILENAME
    ):
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
            direct_root_probe = _build_direct_root_probe_config(
                args=args,
                formalization_backend_name=formalization_backend_name,
                output_dir=output_dir,
                fixed_root_lean_spec=getattr(args, "fixed_root_lean_spec", None),
            )
            graph_path = output_dir / "03_formalized_graph.json"
            summary_path = output_dir / "03_formalization_summaries.json"
            latest_graph = graph

            def write_progress(outcome) -> None:
                nonlocal latest_graph
                write_graph(outcome.graph, graph_path)
                _append_graph_logs(
                    outcome.graph,
                    label=f"03_formalized_graph.json ({outcome.node_id})",
                    previous_graph=latest_graph,
                    event="formalization_update",
                    node_id=outcome.node_id,
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
                direct_root_probe=direct_root_probe,
            )

            write_graph(outcomes.graph, graph_path)
            _append_graph_logs(
                outcomes.graph,
                label="03_formalized_graph.json",
                previous_graph=latest_graph,
                event="formalization_stage_output",
            )
            _write_formalization_summaries(summary_path, outcomes.graph, outcomes.outcomes)
            print(graph_path)
            print(summary_path)
        finally:
            if cleanup_archives:
                _cleanup_archive_artifacts(output_dir)
    return 0


def cmd_continue(args: argparse.Namespace) -> int:
    output_dir = ensure_output_dir(Path(args.output_dir))
    cleanup_archives = getattr(args, "cleanup_archives", True)
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    graph_path = output_dir / "03_formalized_graph.json"
    if not graph_path.exists():
        raise FileNotFoundError(
            f"cannot continue run because {graph_path} does not exist"
        )

    requested_node_ids = _normalize_requested_node_ids(getattr(args, "node_ids", []))
    continuation_instructions = _collect_continuation_instructions(args)
    fixed_root_lean_spec = _collect_fixed_root_lean_spec(args)
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME), use_graph_history_log(
        output_dir / GRAPH_HISTORY_LOG_FILENAME
    ):
        try:
            _log_cli_invocation(
                command_name=str(getattr(args, "command", "continue")),
                args=args,
                effective={
                    "output_dir": str(output_dir),
                    "graph_path": str(graph_path),
                    "workspace": str(workspace),
                    "requested_node_ids": requested_node_ids,
                    "planning_backend": resolve_backend_name(args, formalization=False),
                    "planning_model": resolve_backend_model(args, formalization=False),
                    "formalization_backend": resolve_backend_name(args, formalization=True),
                    "formalization_model": resolve_backend_model(args, formalization=True),
                    "formalization_timeout_seconds": resolve_formalization_timeout(
                        args,
                        resolve_backend_name(args, formalization=True),
                    ),
                    "has_continuation_instructions": continuation_instructions is not None,
                    "has_fixed_root_lean_spec": fixed_root_lean_spec is not None,
                    "fixed_root_lean_spec_hash": (
                        fixed_root_lean_spec.statement_hash if fixed_root_lean_spec else None
                    ),
                },
            )
            progress("continuation stage starting")
            progress(
                "user continuation request: attempting node(s) "
                + ", ".join(requested_node_ids)
            )
            if continuation_instructions:
                progress("user continuation request includes extra formalization instructions")
            if fixed_root_lean_spec:
                progress(
                    "user continuation request includes fixed root Lean specification "
                    f"{fixed_root_lean_spec.statement_hash[:12]}"
                )
            graph = load_graph(graph_path)
            graph = _attach_fixed_root_lean_spec(
                graph=graph,
                fixed_root_lean_spec=fixed_root_lean_spec,
            )
            _log_dependency_direction_warnings(graph, context="continuation stage")
            continued_graph = _prepare_graph_for_continuation(
                graph=graph,
                node_ids=requested_node_ids,
                continuation_instructions=continuation_instructions,
            )
            write_graph(continued_graph, graph_path)
            _append_graph_logs(
                continued_graph,
                label="03_formalized_graph.json (continuation request)",
                previous_graph=graph,
                event="continuation_request",
                node_id=requested_node_ids[0] if len(requested_node_ids) == 1 else None,
                metadata={
                    "requested_nodes": requested_node_ids,
                    "has_continuation_instructions": continuation_instructions is not None,
                    "continuation_instructions": continuation_instructions,
                    "has_fixed_root_lean_spec": fixed_root_lean_spec is not None,
                    "fixed_root_lean_spec_hash": (
                        fixed_root_lean_spec.statement_hash if fixed_root_lean_spec else None
                    ),
                    "fixed_root_lean_spec_source": (
                        fixed_root_lean_spec.source if fixed_root_lean_spec else None
                    ),
                },
            )

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
            verifier = LeanVerifier(workspace=LeanWorkspace(root=Path(workspace)))
            summary_path = output_dir / "03_formalization_summaries.json"
            latest_graph = continued_graph
            from formal_islands.formalization.loop import ParentPromotionCache
            shared_parent_promotion_cache = ParentPromotionCache(decisions={}, lock=RLock())

            def write_progress(outcome) -> None:
                nonlocal latest_graph
                write_graph(outcome.graph, graph_path)
                _append_graph_logs(
                    outcome.graph,
                    label=f"03_formalized_graph.json ({outcome.node_id})",
                    previous_graph=latest_graph,
                    event="formalization_update",
                    node_id=outcome.node_id,
                )
                latest_graph = outcome.graph

            outcomes: list[Any] = []
            seeded_outcomes = formalize_candidate_nodes(
                backend=backend,
                planning_backend=planning_backend,
                verifier=verifier,
                graph=continued_graph,
                node_ids=requested_node_ids,
                max_attempts=args.max_attempts,
                on_update=write_progress,
                mode=args.formalization_mode,
                parent_promotion_cache=shared_parent_promotion_cache,
            )
            current_graph = seeded_outcomes.graph
            outcomes.extend(seeded_outcomes.outcomes)
            attempted_in_continuation = {
                outcome.node_id for outcome in seeded_outcomes.outcomes
            }

            auto_outcomes = formalize_candidate_nodes(
                backend=backend,
                planning_backend=planning_backend,
                verifier=verifier,
                graph=current_graph,
                node_ids=None,
                max_attempts=args.max_attempts,
                on_update=write_progress,
                mode=args.formalization_mode,
                parent_promotion_cache=shared_parent_promotion_cache,
                initial_attempted_ids=attempted_in_continuation,
            )
            current_graph = auto_outcomes.graph
            outcomes.extend(auto_outcomes.outcomes)

            write_graph(current_graph, graph_path)
            _append_graph_logs(
                current_graph,
                label="03_formalized_graph.json",
                previous_graph=latest_graph,
                event="formalization_stage_output",
            )
            _write_formalization_summaries(summary_path, current_graph, outcomes)
            progress("continuation report stage starting")
            cmd_report(
                argparse.Namespace(
                    graph=str(graph_path),
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
            print(graph_path)
            print(summary_path)
            print(output_dir / "04_report_bundle.json")
            print(output_dir / "04_report.html")
        finally:
            if cleanup_archives:
                _cleanup_archive_artifacts(output_dir)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    output_dir = ensure_output_dir(Path(args.output_dir))
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME), use_graph_history_log(
        output_dir / GRAPH_HISTORY_LOG_FILENAME
    ):
        progress("report stage starting")
        graph_path = Path(args.graph)
        graph = load_graph(graph_path)
        graph = _hydrate_report_annotations_from_previous_bundle(
            graph=graph,
            output_dir=output_dir,
        )
        original_graph = graph
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
        _persist_report_annotations_to_graph_if_canonical(
            graph=graph,
            graph_path=graph_path,
            output_dir=output_dir,
        )
        _append_graph_logs(
            graph,
            label="04_report_graph",
            previous_graph=original_graph,
            event="report_stage_graph",
        )
        obligations = derive_review_obligations(graph)
        graph_history = load_graph_history_entries(output_dir / GRAPH_HISTORY_LOG_FILENAME)
        bundle = export_report_bundle(graph, obligations, graph_history=graph_history)
        html = render_html_report(graph, obligations, graph_history=graph_history)

        bundle_path = output_dir / "04_report_bundle.json"
        html_path = output_dir / "04_report.html"
        bundle_path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
        html_path.write_text(html, encoding="utf-8")
        print(bundle_path)
        print(html_path)
    return 0


def cmd_direct_root(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    output_dir = ensure_output_dir(
        Path(args.output_dir)
        if args.output_dir is not None
        else default_direct_root_output_dir_for_input(input_path)
    )
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME):
        fixed_root_lean_spec = _collect_fixed_root_lean_spec(args)
        _log_cli_invocation(
            command_name=str(getattr(args, "command", "direct-root")),
            args=args,
            effective={
                "input_path": str(input_path),
                "output_dir": str(output_dir),
                "workspace": str(workspace),
                "formalization_backend": "aristotle",
                "formalization_timeout_seconds": resolve_formalization_timeout(
                    args,
                    "aristotle",
                ),
                "max_attempts": args.max_attempts,
                "has_fixed_root_lean_spec": fixed_root_lean_spec is not None,
                "fixed_root_lean_spec_hash": (
                    fixed_root_lean_spec.statement_hash if fixed_root_lean_spec else None
                ),
            },
        )
        progress("direct-root diagnostic stage starting")
        input_payload = load_input_payload(input_path)
        backend = build_backend(
            "aristotle",
            model=None,
            log_dir=output_dir / "_backend_logs",
            timeout_seconds=resolve_formalization_timeout(args, "aristotle"),
            formalization=True,
        )
        if not isinstance(backend, AristotleBackend):
            raise TypeError("direct-root diagnostic expected an Aristotle backend")
        verifier = LeanVerifier(workspace=LeanWorkspace(root=Path(workspace)))
        diagnostic = run_direct_root_aristotle_diagnostic(
            backend=backend,
            verifier=verifier,
            input_payload=input_payload,
            output_dir=output_dir,
            max_attempts=args.max_attempts,
            fixed_root_lean_spec=fixed_root_lean_spec,
        )
        summary_path = output_dir / "direct_root_summary.json"
        write_direct_root_diagnostic_summary(diagnostic, summary_path)
        progress(
            "direct-root diagnostic finished with "
            f"verified_root={diagnostic.verified_root}"
        )
        print(summary_path)
        print(diagnostic.scratch_path)
    return 0


def _hydrate_report_annotations_from_previous_bundle(
    *,
    graph: ProofGraph,
    output_dir: Path,
) -> ProofGraph:
    bundle_path = output_dir / "04_report_bundle.json"
    if not bundle_path.exists():
        return graph
    try:
        payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return graph
    previous_graph_payload = payload.get("graph")
    if not isinstance(previous_graph_payload, dict):
        return graph
    try:
        previous_graph = ProofGraph.model_validate(previous_graph_payload)
    except Exception:
        return graph

    previous_burdens = {
        node.id: node.remaining_proof_burden
        for node in previous_graph.nodes
        if node.remaining_proof_burden
    }
    if not previous_burdens:
        return graph

    changed = False
    updated_nodes = []
    for node in graph.nodes:
        if node.remaining_proof_burden or node.id not in previous_burdens:
            updated_nodes.append(node)
            continue
        updated_nodes.append(
            node.model_copy(update={"remaining_proof_burden": previous_burdens[node.id]})
        )
        changed = True

    if not changed:
        return graph

    progress(
        "report stage: reused previously synthesized remaining proof burden text from 04_report_bundle.json"
    )
    return graph.model_copy(update={"nodes": updated_nodes})


def _persist_report_annotations_to_graph_if_canonical(
    *,
    graph: ProofGraph,
    graph_path: Path,
    output_dir: Path,
) -> None:
    canonical_graph_path = output_dir / "03_formalized_graph.json"
    try:
        is_canonical = graph_path.resolve() == canonical_graph_path.resolve()
    except FileNotFoundError:
        is_canonical = graph_path == canonical_graph_path
    if not is_canonical:
        return

    try:
        existing_graph = load_graph(graph_path)
    except Exception:
        existing_graph = None
    if existing_graph is not None and existing_graph.model_dump(mode="json") == graph.model_dump(mode="json"):
        return

    write_graph(graph, graph_path)
    progress("report stage: persisted report annotations into 03_formalized_graph.json")


def cmd_run_example(args: argparse.Namespace) -> int:
    output_dir = ensure_output_dir(Path(args.output_dir))
    fixed_root_lean_spec = _collect_fixed_root_lean_spec(args)
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME), use_graph_history_log(
        output_dir / GRAPH_HISTORY_LOG_FILENAME
    ):
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
            if fixed_root_lean_spec is not None:
                original_candidate_graph = load_graph(candidate_graph_path)
                candidate_graph = _attach_fixed_root_lean_spec(
                    graph=original_candidate_graph,
                    fixed_root_lean_spec=fixed_root_lean_spec,
                )
                write_graph(candidate_graph, candidate_graph_path)
                _append_graph_logs(
                    candidate_graph,
                    label="02_candidate_graph.json (fixed root Lean spec)",
                    previous_graph=original_candidate_graph,
                    event="fixed_root_spec_attached",
                    metadata={
                        "fixed_root_lean_spec_hash": fixed_root_lean_spec.statement_hash,
                        "fixed_root_lean_spec_source": fixed_root_lean_spec.source,
                    },
                )
                progress(
                    "example setup: attached fixed root Lean specification "
                    f"{fixed_root_lean_spec.statement_hash[:12]}"
                )
            if getattr(args, "attempt_all_nodes", False):
                original_candidate_graph = load_graph(candidate_graph_path)
                candidate_graph = mark_all_informal_nodes_candidate_formal(
                    original_candidate_graph
                )
                write_graph(candidate_graph, candidate_graph_path)
                _append_graph_logs(
                    candidate_graph,
                    label="02_candidate_graph.json (--attempt-all-nodes)",
                    previous_graph=original_candidate_graph,
                    event="candidate_selection_output",
                )
                progress(
                    "example candidate stage: --attempt-all-nodes marked every informal "
                    "node as candidate_formal"
                )
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
    input_payload = load_input_payload(input_path)
    output_dir = ensure_output_dir(
        Path(args.output_dir)
        if args.output_dir is not None
        else default_output_dir_for_input(input_path, args)
    )
    workspace = _resolve_workspace(getattr(args, "workspace", None))
    fixed_root_lean_spec = _collect_fixed_root_lean_spec(args)
    with use_progress_log(output_dir / PROGRESS_LOG_FILENAME), use_graph_history_log(
        output_dir / GRAPH_HISTORY_LOG_FILENAME
    ):
        try:
            planning_backend_name = resolve_backend_name(args, formalization=False)
            planning_backend_model = resolve_backend_model(args, formalization=False)
            formalization_backend_name = resolve_backend_name(args, formalization=True)
            formalization_backend_model = resolve_backend_model(args, formalization=True)
            formalization_timeout = resolve_formalization_timeout(
                args,
                formalization_backend_name,
            )
            _log_cli_invocation(
                command_name=str(getattr(args, "command", "run-benchmark")),
                args=args,
                effective={
                    "input_path": str(input_path),
                    "output_dir": str(output_dir),
                    "workspace": str(workspace),
                    "planning_backend": planning_backend_name,
                    "planning_model": planning_backend_model,
                    "formalization_backend": formalization_backend_name,
                    "formalization_model": formalization_backend_model,
                    "formalization_timeout_seconds": formalization_timeout,
                    "attempt_all_nodes": bool(getattr(args, "attempt_all_nodes", False)),
                    "direct_root_probe_enabled": (
                        formalization_backend_name == "aristotle"
                        and getattr(args, "node_id", "auto") == "auto"
                        and not bool(getattr(args, "no_direct_root_probe", False))
                    ),
                    "run_graph_if_direct_root_verifies": bool(
                        getattr(args, "run_graph_if_direct_root_verifies", False)
                    ),
                    "has_fixed_root_lean_spec": fixed_root_lean_spec is not None,
                    "fixed_root_lean_spec_hash": (
                        fixed_root_lean_spec.statement_hash if fixed_root_lean_spec else None
                    ),
                },
            )
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
            if fixed_root_lean_spec is not None:
                original_candidate_graph = load_graph(candidate_graph_path)
                candidate_graph = _attach_fixed_root_lean_spec(
                    graph=original_candidate_graph,
                    fixed_root_lean_spec=fixed_root_lean_spec,
                )
                write_graph(candidate_graph, candidate_graph_path)
                _append_graph_logs(
                    candidate_graph,
                    label="02_candidate_graph.json (fixed root Lean spec)",
                    previous_graph=original_candidate_graph,
                    event="fixed_root_spec_attached",
                    metadata={
                        "fixed_root_lean_spec_hash": fixed_root_lean_spec.statement_hash,
                        "fixed_root_lean_spec_source": fixed_root_lean_spec.source,
                    },
                )
                progress(
                    "benchmark setup: attached fixed root Lean specification "
                    f"{fixed_root_lean_spec.statement_hash[:12]}"
                )
            if getattr(args, "attempt_all_nodes", False):
                original_candidate_graph = load_graph(candidate_graph_path)
                candidate_graph = mark_all_informal_nodes_candidate_formal(
                    original_candidate_graph
                )
                write_graph(candidate_graph, candidate_graph_path)
                _append_graph_logs(
                    candidate_graph,
                    label="02_candidate_graph.json (--attempt-all-nodes)",
                    previous_graph=original_candidate_graph,
                    event="candidate_selection_output",
                )
                progress(
                    "benchmark candidate stage: --attempt-all-nodes marked every informal "
                    "node as candidate_formal"
                )
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
                direct_root_probe_input_payload=input_payload,
                fixed_root_lean_spec=fixed_root_lean_spec,
                no_direct_root_probe=getattr(args, "no_direct_root_probe", False),
                run_graph_if_direct_root_verifies=getattr(
                    args, "run_graph_if_direct_root_verifies", False
                ),
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
    outcome_node = next((node for node in outcome.graph.nodes if node.id == outcome.node_id), None)
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
                "node_status_after_attempt": outcome_node.status if outcome_node is not None else None,
                "last_formalization_outcome": (
                    outcome_node.last_formalization_outcome if outcome_node is not None else None
                ),
                "last_formalization_attempt_count": (
                    outcome_node.last_formalization_attempt_count if outcome_node is not None else None
                ),
                "last_formalization_note": (
                    outcome_node.last_formalization_note if outcome_node is not None else None
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_formalization_summaries(path: Path, graph: ProofGraph, outcomes: list[Any]) -> None:
    summaries: list[dict[str, Any]] = []
    for outcome in outcomes:
        outcome_node = next((node for node in graph.nodes if node.id == outcome.node_id), None)
        summaries.append(
            {
                "node_id": outcome.node_id,
                "status": outcome.artifact.verification.status,
                "artifact_path": outcome.artifact.verification.artifact_path,
                "attempt_count": outcome.artifact.verification.attempt_count,
                "stderr": outcome.artifact.verification.stderr,
                "faithfulness_classification": outcome.artifact.faithfulness_classification,
                "lean_theorem_name": outcome.artifact.lean_theorem_name,
                "node_status_after_attempt": outcome_node.status if outcome_node is not None else None,
                "last_formalization_outcome": (
                    outcome_node.last_formalization_outcome if outcome_node is not None else None
                ),
                "last_formalization_attempt_count": (
                    outcome_node.last_formalization_attempt_count if outcome_node is not None else None
                ),
                "last_formalization_note": (
                    outcome_node.last_formalization_note if outcome_node is not None else None
                ),
            }
        )
    path.write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")


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
    _append_optional_input_context(payload)
    return payload


def _append_optional_input_context(payload: dict[str, Any]) -> None:
    """Append optional context fields to the theorem/proof text seen by backends.

    The base input schema is intentionally tiny. For paper-level case studies,
    though, a theorem-like unit often needs surrounding context: cited earlier
    paper lemmas, notation, intended proof role, or suggested internal islands.
    Extra context is appended explicitly as context, not silently converted into
    assumptions.
    """

    sections: list[str] = []
    for key in (
        "additional_context",
        "paper_context",
        "formalization_context",
        "surrounding_context",
    ):
        raw = payload.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            text = raw.strip()
        else:
            text = json.dumps(raw, indent=2, ensure_ascii=False)
        if text:
            title = key.replace("_", " ").title()
            sections.append(f"{title}:\n{text}")

    case_study = payload.get("paper_case_study")
    if isinstance(case_study, dict):
        sections.append("Paper Case Study Metadata:\n" + json.dumps(case_study, indent=2, ensure_ascii=False))

    if not sections:
        return

    context_block = (
        "\n\nAdditional context for this input. This context is for notation, "
        "surrounding paper dependencies, and proof role. It is not part of the "
        "target theorem statement unless the target statement or proof explicitly "
        "cites it as an assumption.\n\n"
        + "\n\n".join(sections)
    )
    payload["theorem_statement"] = str(payload["theorem_statement"]).rstrip() + context_block


def load_graph(path: Path) -> ProofGraph:
    """Load a graph JSON file into the validated internal model."""

    return ProofGraph.model_validate_json(path.read_text(encoding="utf-8"))


def write_graph(graph: ProofGraph, path: Path) -> None:
    """Write a graph JSON file."""

    path.write_text(json.dumps(graph.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")


def _append_graph_logs(
    graph: ProofGraph,
    *,
    label: str,
    previous_graph: ProofGraph | None = None,
    event: str = "graph_snapshot",
    node_id: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    append_graph_summary_to_progress_log(
        graph,
        label=label,
        previous_graph=previous_graph,
    )
    append_graph_snapshot_to_history_log(
        graph,
        label=label,
        previous_graph=previous_graph,
        event=event,
        node_id=node_id,
        metadata=metadata,
    )


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


def default_direct_root_output_dir_for_input(path: Path) -> Path:
    """Derive a direct full/root-attempt artifact directory from an input JSON path."""

    stem = path.stem.replace("_", "-")
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("artifacts/direct-full-attempts") / f"{stem}-aristotle-{timestamp}"


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
