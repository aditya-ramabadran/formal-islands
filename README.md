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
3. runs one or more candidate formalization attempts with an agentic backend worker
4. verifies the resulting Lean files locally
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
  Timestamped worker files, plan files, and scratch artifacts.

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
--formalization-timeout-seconds 900
--model gpt-5.4
```

Formalization mode is agentic-only in the CLI. The older structured repair-loop mode is deprecated and no longer exposed as a user-facing option.

You can also split planning and formalization backends:

```bash
./.venv/bin/formal-islands-smoke run-benchmark \
  --planning-backend claude \
  --formalization-backend aristotle \
  --input /Users/adihaya/GitHub/formal-islands/examples/manual-testing/run11_two_point_log_sobolev.json \
  --output-dir /Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run11-two-point-log-sobolev
```

When `--node-id auto` is used, `run-benchmark` formalizes candidate nodes in priority order and can continue to later candidates after an early success. Use `formalize-one` only if you want exactly one node attempt.

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
- `_progress.log`
  Shared run progress log in the output directory. It is append-only, so rerunning later stages like report generation will add to the existing file instead of replacing it. When the graph is generated or materially updated, the log also gets a compact node/edge preview. Planning-backend semantic assessments and Aristotle summary markdown files are appended there too.
- `_backend_logs/*.json`
  Logged backend requests/responses, timings, and raw CLI output.

Supported backends:
- `codex`
  Uses the local `codex` CLI for structured planning and one-shot agentic formalization.
- `claude`
  Uses the local Claude Code CLI for structured planning and one-shot agentic formalization.
- `gemini`
  Uses the local Gemini CLI for structured planning and one-shot agentic formalization.
- `aristotle`
  Uses Harmonic's Aristotle Python SDK for formalization only. Set `ARISTOTLE_API_KEY` in your environment before using it. Planning still uses the usual local CLI backends. Aristotle runs without a default timeout unless you explicitly pass one.

External Mathlib search helper:

```bash
./.venv/bin/formal-islands-search --query "Real.log, Real.sqrt" --provider loogle
```

Use this for highly targeted theorem-shape lookups outside Lean.
The formalization prompts mention this helper so the agentic worker can use it if needed, but the main pipeline no longer precomputes and injects search bundles by default. The worker is still told to keep any self-directed follow-up search to at most two highly targeted queries.

Agentic formalization also writes into:

- `/Users/adihaya/GitHub/formal-islands/lean_project/FormalIslands/Generated`

including:
- timestamped worker files like `<node>_worker_<timestamp>_<suffix>.lean`
- timestamped plan files like `<node>_worker_<timestamp>_<suffix>_plan.md`

The smoke CLI accepts split backend flags:

- `--planning-backend` for extraction / planning
- `--formalization-backend` for the formalization worker

If you omit them, the legacy `--backend` flag still acts as the shared default for both stages.

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
- dashed gray provenance / refinement edges for nearby nodes that are not proof dependencies
- result labels that distinguish faithful cores, downstream consequences, dimensional analogues, and other narrower outcomes

## Current Formalization Behavior

The current default formalization mode is:
- `agentic`

That means the selected agentic backend gets one bounded full-auto run to:
- inspect the local Lean workspace
- write a plan markdown file
- edit a single Lean scratch file
- run `lake env lean`
- revise the same file

The prompt now also includes a lightweight coverage sketch for the target node, so the worker can see which local subclaims the node is really made of.

The formalization prompt also splits nearby nodes into:
- verified supporting lemmas already certified in this run, which may be relied on as established facts
- context-only sibling ingredients, which are only there for orientation and should not be assumed

After a formalization verifies, the pipeline asks the planning backend for a combined semantic review of the Lean theorem against the target node. That review can classify the result as a full match, a faithful core, a downstream consequence, a dimensional analogue, or a helper shard, and it controls whether coverage expansion should run.

The agentic prompt also explicitly reminds the worker where Mathlib lives in this workspace:
- `.lake/packages/mathlib/Mathlib`
- not `lean_project/mathlib/Mathlib`

The worker is told to rely on the local `formal-islands-search` helper only if it truly needs extra retrieval, and to commit to a theorem shape sooner rather than wandering through broad library scouting.

The refined-local-claim path is now fallback-driven rather than eager:
- the loop first tries the best whole node
- only after a meaningful failure does it consider a smaller subclaim from that same source node
- trivial substitution-only claims and bare point evaluations are filtered out

Aristotle submissions use a pruned Lean snapshot rather than the entire workspace tree:
- the committed Lean project skeleton
- the active scratch file
- no `.lake` build tree
- no `Generated` backlog from earlier runs
- no `test_*.lean` scratch files

The Aristotle prompt uses the same local-proof split in plain text. Verified supporting lemmas are listed with their theorem names and Lean statements when available; the generated Lean code for those lemmas is not auto-imported into the Aristotle snapshot.

The Aristotle prompt itself is plain text, not a bundle of generated Lean source files. It includes:
- the ambient theorem statement as context only
- the target node's informal statement and informal proof text
- a local proof neighborhood split into:
  - verified supporting lemmas already certified in this run
  - context-only sibling ingredients in the same proof neighborhood

Verified supporting lemmas are given as text summaries with their Lean theorem name and Lean statement when available. They are treated as established facts for proof planning, but their generated Lean code is not auto-imported into the Aristotle snapshot.
If Aristotle returns an `ARISTOTLE_SUMMARY_*.md` file, its contents are appended to `_progress.log` so the run log keeps the backend's own plain-text summary without echoing it to the terminal.

When `formalize-all-candidates` uses Aristotle, jobs are submitted in parallel batches so multiple candidate nodes can be worked on at once. Newly promoted parents are then picked up by the next batch.

Every benchmark run also writes a shared progress log to `_progress.log` inside the corresponding output directory.

The system then classifies the result as:
- full-node success
- concrete supporting sublemma
- or failure

The current verification path is a little richer than that summary:
- the heuristic faithfulness guard runs first and rejects obvious abstraction drift, including dimension downgrades
- the planning backend may then refine the result kind and coverage estimate after verification
- repair retries combine fast Lean-specific heuristics with an optional planning-backend diagnosis
- if the result is only a concrete supporting sublemma, the pipeline may run one bounded coverage-expansion attempt from the verified file
- if the result is still a concrete sublemma but the planning backend says it is worth another try, a single bonus retry may be attempted on the main proof path

If the agentic run times out but leaves a usable Lean file behind, the pipeline will try to salvage and locally verify it.

When a verified result is only a concrete supporting sublemma, the pipeline makes one additional bounded coverage-expansion attempt from the verified Lean file. That follow-up is intentionally narrow: it tries to grow the same local claim upward toward the parent node rather than restarting from scratch.

Two more details matter in the current run loop:
- if a recovered agentic artifact verifies as a concrete sublemma, it still gets the same bounded expansion attempt
- when a refined local claim is certified, the source node reached through a `uses` edge can be promoted into the candidate set in a later dynamic pass, so a successful narrow core can feed a follow-up formalization target
- when the graph is run in auto mode, `run-benchmark` now keeps discovering newly promoted candidates instead of stopping after the first success
- when the result is only a concrete supporting sublemma, the planning backend can still be used to write the short informal statement/proof summary for that certified local core

Generated worker filenames are timestamped to avoid conflicts:
- `<node>_worker_<timestamp>_<suffix>.lean`
- `<node>_worker_<timestamp>_<suffix>_plan.md`

The refined-local-claim ranking also penalizes point-evaluation fragments like `F_q(q) = 0` when they look like isolated snapshot facts rather than a reusable local theorem. That keeps the system from over-valuing tiny algebraic shards over broader claims with real inferential load.

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
