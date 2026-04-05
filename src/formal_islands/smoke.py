"""Small smoke-test CLI for the current prototype pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from formal_islands.backends import BackendError, CodexCLIBackend
from formal_islands.examples import TOY_RAW_PROOF, TOY_THEOREM_STATEMENT
from formal_islands.extraction import (
    extract_proof_graph,
    plan_proof_graph,
    select_formalization_candidates,
)
from formal_islands.formalization import LeanVerifier, LeanWorkspace, formalize_candidate_node
from formal_islands.models import FormalArtifact, ProofGraph, VerificationResult
from formal_islands.report import export_report_bundle, render_html_report
from formal_islands.review import derive_review_obligations


DEFAULT_EXAMPLE_INPUT = {
    "theorem_title": "Nonnegative sum",
    "theorem_statement": TOY_THEOREM_STATEMENT,
    "raw_proof_text": TOY_RAW_PROOF,
}

DEFAULT_BACKEND_TIMEOUT_SECONDS = 180.0
FORMALIZATION_BACKEND_TIMEOUT_SECONDS = 420.0


def main(argv: list[str] | None = None) -> int:
    """Run the smoke-test CLI."""

    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser."""

    parser = argparse.ArgumentParser(prog="formal-islands-smoke")
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
        default=4,
        help="Maximum number of bounded formalization attempts. The current prototype uses at most three repair retries after the initial attempt.",
    )
    formalize_parser.add_argument(
        "--formalization-mode",
        choices=["agentic", "structured", "auto"],
        default="agentic",
        help="Formalization execution mode. Default: agentic Codex worker, with structured fallback available.",
    )
    formalize_parser.set_defaults(func=cmd_formalize_one)

    report_parser = subparsers.add_parser("report")
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
        default=4,
        help="Maximum number of bounded formalization attempts. The current prototype uses at most three repair retries after the initial attempt.",
    )
    run_parser.add_argument(
        "--formalization-mode",
        choices=["agentic", "structured", "auto"],
        default="agentic",
        help="Formalization execution mode. Default: agentic Codex worker, with structured fallback available.",
    )
    run_parser.set_defaults(func=cmd_run_example)

    return parser


def add_backend_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        choices=["codex"],
        default="codex",
        help="Structured backend to use.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Optional Codex model override passed through to `codex exec`.",
    )


def add_input_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--input",
        default="examples/nonnegative_sum_input.json",
        help="JSON file with theorem_title, theorem_statement, and raw_proof_text.",
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


def cmd_extract(args: argparse.Namespace) -> int:
    input_payload = load_input_payload(Path(args.input))
    output_dir = ensure_output_dir(Path(args.output_dir))
    backend = build_backend(
        args.backend,
        args.model,
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
    print(path)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    input_payload = load_input_payload(Path(args.input))
    output_dir = ensure_output_dir(Path(args.output_dir))
    backend = build_backend(
        args.backend,
        args.model,
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
    write_graph(artifacts.candidate_graph, candidate_path)
    print(extracted_path)
    print(candidate_path)
    return 0


def cmd_select_candidates(args: argparse.Namespace) -> int:
    output_dir = ensure_output_dir(Path(args.output_dir))
    backend = build_backend(
        args.backend,
        args.model,
        output_dir / "_backend_logs",
        timeout_seconds=DEFAULT_BACKEND_TIMEOUT_SECONDS,
    )
    graph = load_graph(Path(args.graph))
    updated_graph = select_formalization_candidates(backend=backend, graph=graph)
    path = output_dir / "02_candidate_graph.json"
    write_graph(updated_graph, path)
    print(path)
    return 0


def cmd_formalize_one(args: argparse.Namespace) -> int:
    output_dir = ensure_output_dir(Path(args.output_dir))
    backend = build_backend(
        args.backend,
        args.model,
        output_dir / "_backend_logs",
        timeout_seconds=FORMALIZATION_BACKEND_TIMEOUT_SECONDS,
    )
    graph = load_graph(Path(args.graph))
    node_id = select_candidate_node_id(graph, requested_node_id=args.node_id)
    verifier = LeanVerifier(workspace=LeanWorkspace(root=Path(args.workspace)))
    graph_path = output_dir / "03_formalized_graph.json"
    summary_path = output_dir / "03_formalization_summary.json"

    def write_progress(outcome) -> None:
        write_graph(outcome.graph, graph_path)
        _write_formalization_summary(summary_path, outcome)

    try:
        outcome = formalize_candidate_node(
            backend=backend,
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

    write_progress(outcome)
    print(graph_path)
    print(summary_path)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    output_dir = ensure_output_dir(Path(args.output_dir))
    graph = load_graph(Path(args.graph))
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
    cmd_plan(
        argparse.Namespace(
            backend=args.backend,
            model=args.model,
            input=args.input,
            output_dir=str(output_dir),
        )
    )
    candidate_graph_path = output_dir / "02_candidate_graph.json"
    cmd_formalize_one(
        argparse.Namespace(
            backend=args.backend,
            model=args.model,
            graph=str(candidate_graph_path),
            output_dir=str(output_dir),
            workspace=args.workspace,
            node_id="auto",
            max_attempts=args.max_attempts,
            formalization_mode=args.formalization_mode,
        )
    )
    formalized_graph_path = output_dir / "03_formalized_graph.json"
    cmd_report(
        argparse.Namespace(
            graph=str(formalized_graph_path),
            output_dir=str(output_dir),
        )
    )
    return 0


def build_backend(
    name: str,
    model: str | None,
    log_dir: Path | None = None,
    timeout_seconds: float = DEFAULT_BACKEND_TIMEOUT_SECONDS,
) -> CodexCLIBackend:
    """Create a backend instance for the smoke CLI."""

    if name != "codex":
        raise ValueError(f"unsupported backend: {name}")
    return CodexCLIBackend(model=model, log_dir=log_dir, timeout_seconds=timeout_seconds)


def _write_formalization_summary(path: Path, outcome) -> None:
    path.write_text(
        json.dumps(
            {
                "node_id": outcome.node_id,
                "status": outcome.artifact.verification.status,
                "artifact_path": outcome.artifact.verification.artifact_path,
                "attempt_count": outcome.artifact.verification.attempt_count,
                "stderr": outcome.artifact.verification.stderr,
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


def load_input_payload(path: Path) -> dict[str, Any]:
    """Load theorem input JSON, falling back to the default example if absent."""

    if not path.exists():
        if path == Path("examples/nonnegative_sum_input.json"):
            return DEFAULT_EXAMPLE_INPUT.copy()
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
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
