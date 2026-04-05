# Formal Islands

Formal Islands is a prototype for turning a natural-language theorem proof into a **small proof graph with local Lean-certified islands**.

The goal is not end-to-end autoformalization. The goal is to produce an honest mixed artifact:
- a readable informal proof graph
- one or more local Lean-verified nodes when possible
- a report that shows exactly what was certified and what still needs human review

This is especially useful for analysis, PDE, and variational arguments where:
- the global proof is too large or infrastructure-heavy to formalize end to end
- but some local steps are concrete enough to formalize today

## What The System Does

Given:
- a theorem title
- a theorem statement
- a raw informal proof

the current pipeline:
1. plans a small theorem-level proof graph
2. marks candidate nodes for formalization
3. picks one candidate and runs an agentic Codex worker on a Lean scratch file
4. verifies the resulting Lean file locally
5. produces an HTML report and JSON artifacts

The system can distinguish between:
- full-node formalization
- a narrower but still concrete certified local core
- bad abstraction drift, which is rejected

## Repository Layout

- `/Users/adihaya/GitHub/formal-islands/src/formal_islands`
  Main Python package.
- `/Users/adihaya/GitHub/formal-islands/examples/manual-testing`
  Benchmark inputs.
- `/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing`
  Saved benchmark outputs.
- `/Users/adihaya/GitHub/formal-islands/lean_project`
  Local Lean/Mathlib workspace used for verification.
- `/Users/adihaya/GitHub/formal-islands/lean_project/FormalIslands/Generated`
  Generated worker files and scratch artifacts.

## Setup

Create the virtualenv and install the package:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e '.[dev]'
```

Set up the Lean workspace:

```bash
cd /Users/adihaya/GitHub/formal-islands/lean_project
lake update
lake exe cache get
lake build
cd /Users/adihaya/GitHub/formal-islands
```

Install and authenticate Codex CLI separately:

```bash
codex --version
codex
```

You should either:
- sign in interactively through Codex
- or configure an API key for your environment

Quick auth check:

```bash
test -f "${CODEX_HOME:-$HOME/.codex}/auth.json" && echo AUTH_OK || echo AUTH_MISSING
```

## Input Format

Each input is a JSON file with:

```json
{
  "theorem_title": "Example theorem",
  "theorem_statement": "Full theorem statement...",
  "raw_proof_text": "Raw informal proof..."
}
```

Example files live in:
- `/Users/adihaya/GitHub/formal-islands/examples/manual-testing`

## Fastest Way To Run A Benchmark

The easiest way to run a new benchmark is the new one-command wrapper:

```bash
./.venv/bin/formal-islands-smoke run-benchmark \
  --backend codex \
  --input /Users/adihaya/GitHub/formal-islands/examples/manual-testing/run11_two_point_log_sobolev.json
```

By default, this writes outputs to:

```bash
/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run11-two-point-log-sobolev
```

You can override the output directory explicitly:

```bash
./.venv/bin/formal-islands-smoke run-benchmark \
  --backend codex \
  --input /Users/adihaya/GitHub/formal-islands/examples/manual-testing/run11_two_point_log_sobolev.json \
  --output-dir /Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/my-custom-run
```

Useful options:

```bash
--workspace lean_project
--node-id auto
--formalization-mode agentic
--max-attempts 4
--model gpt-5.4
```

## Stage Commands

If you want to run the stages manually, the CLI also supports:

```bash
./.venv/bin/formal-islands-smoke plan ...
./.venv/bin/formal-islands-smoke formalize-one ...
./.venv/bin/formal-islands-smoke report ...
```

There is also an older convenience command:

```bash
./.venv/bin/formal-islands-smoke run-example ...
```

but `run-benchmark` is the cleaner default for manual theorem/proof JSON inputs.

## Output Files

A normal benchmark run writes:

- `01_extracted_graph.json`
  The planned informal graph before formalization writeback.
- `02_candidate_graph.json`
  The graph with candidate nodes marked.
- `03_formalized_graph.json`
  The graph after a formalization attempt.
- `03_formalization_summary.json`
  Short summary of the chosen node’s formalization outcome.
- `04_report_bundle.json`
  JSON bundle used by the report.
- `04_report.html`
  Human-readable HTML report.
- `_backend_logs/*.json`
  Logged backend requests/responses, timings, and raw CLI output.

Supported backends:
- `codex`
  Uses the local `codex` CLI for structured planning and one-shot agentic formalization.
- `claude`
  Uses the local Claude Code CLI for structured planning and one-shot agentic formalization.
- `gemini`
  Uses the local Gemini CLI for structured planning and one-shot agentic formalization.

Agentic formalization also writes into:

- `/Users/adihaya/GitHub/formal-islands/lean_project/FormalIslands/Generated`

including:
- `<node>_worker.lean`
- `<node>_worker_plan.md`

## Reports

The HTML report includes:
- theorem and graph summary
- clickable node graph
- review checklist
- node-by-node informal statements and proofs
- Lean code and verification logs when available

The report supports:
- MathJax-rendered math
- syntax-highlighted Lean code
- automatic light/dark mode

## Current Formalization Behavior

The current default formalization mode is:
- `agentic`

That means Codex gets one bounded full-auto run to:
- inspect the local Lean workspace
- write a plan markdown file
- edit a single Lean scratch file
- run `lake env lean`
- revise the same file

The system then classifies the result as:
- full-node success
- concrete supporting sublemma
- or failure

If the agentic run times out but leaves a usable Lean file behind, the pipeline will try to salvage and locally verify it.

## Development

Run tests with:

```bash
./.venv/bin/python -m pytest -q
```

Focused smoke/report tests:

```bash
./.venv/bin/python -m pytest tests/test_smoke.py -q
./.venv/bin/python -m pytest tests/test_review_and_report.py -q
./.venv/bin/python -m pytest tests/test_lean_formalization.py -q
```

## Notes

- This repository is still a prototype.
- The graph and report are meant to be honest artifacts, not claims of full formalization.
- Candidate nodes are only formalization opportunities; only nodes with actual Lean artifacts are visually treated as formal in the report graph.
