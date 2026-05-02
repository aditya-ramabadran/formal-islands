# [Formal Islands](https://aditya-ramabadran.github.io/formal-islands/)

**Partial formal certification for natural-language mathematical proofs.**

Formal Islands turns a theorem statement plus informal proof into a proof graph with Lean-verified nodes and a human-readable report. The output is a mixed artifact that makes the formally checked pieces, the remaining informal gaps, and the review burden explicit. In practice, the graph-guided workflow also acts as a useful formalization orchestration layer: certifying child nodes first can improve later parent-level attempts and sometimes yields cleaner root formalizations than a one-shot direct attempt.

**Research prototype / experimental.** The system is useful, but it is still actively evolving and should be treated as a research artifact rather than a polished production tool.

## What Is This?

Current AI-for-math systems face a real bottleneck from two directions at once.

Strong informal systems (large language models doing natural-language mathematical reasoning) can handle broad and flexible proof search, but they can also drift semantically, prove the wrong thing, or produce arguments whose correctness is hard for humans to audit at scale. As these systems get better, the proofs they produce will get longer and harder to check, not easier.

Fully formal systems like Lean give much stronger guarantees, but they are bottlenecked by the limits of Lean and Mathlib. In infrastructure-heavy domains like PDEs, geometric measure theory, and spectral theory, you often cannot even *state* the intended theorem faithfully, let alone prove it. This caps what fully formal AI theorem-proving systems can prove today.

Formal Islands is built around a near-term compromise: let an informal system handle the broad reasoning and proof search, then extract the smaller local claims that really are within current formal coverage and certify those in Lean. The result is a **mixed artifact**: most of the proof remains informal, but important **formal islands** inside it are genuinely machine-checked.

A **formal island** is a proof node independently verified in Lean, certified in isolation, without requiring the rest of the proof to be formalized first. The benefits of this system:

- **It makes human review tractable.** Instead of auditing one long informal proof monolithically, a reviewer can focus on the uncertified gaps while knowing that key lemmas are already verified. The generated report makes this explicit: each uncertified claim gets a concrete review checklist.
- **It gives an honest answer to "what exactly was checked?"** The report distinguishes full-node verification, narrower certified supporting cores, and informal steps, with no overclaiming.
- **It showcases a natural path for combining different systems.** Broad informal reasoning from language models, and the potential to use formal/Lean-optimized tools on the formalizable subproblems. The architecture is modular by design.
- **It can only get better over time.** As Mathlib expands, the same pipeline can certify larger and larger portions of proofs, eventually approaching full formalization where that becomes realistic.

Even when the formalization backend proves something that compiles in Lean, there is no guarantee the formal statement actually matches the intended informal theorem. The pipeline uses a series of heuristic checks and LLM-assisted semantic review to guard against drift, but the ultimate check is always left to a human, and the report makes the remaining burden explicit.

The pipeline classifies each result as a full-node match, a faithful local core, or a narrower sublemma, and surfaces any remaining semantic gap explicitly in the report.

### What Files Get Produced?

Each run writes a small set of stage artifacts:

- `01_extracted_graph.json`: the extracted theorem graph from the planning stage
- `03_formalized_graph.json`: the graph after formalization attempts and verification results
- `04_report.html`: the human-facing report with the proof graph, logs, and review checklist

Depending on the command, you may also see `02_candidate_graph.json` and `04_report_bundle.json` in the output directory.

Runs can also be resumed later from `03_formalized_graph.json` with the `continue` command: you can seed one or more nodes back into the candidate set, append to the same `_progress.log` / `graph_history.jsonl`, and let the normal auto formalization + promotion logic continue from there.

For evaluation and comparison, the repo also has a `direct-root` diagnostic command. It sends the original theorem statement and proof text directly to Aristotle and asks for one root theorem named `direct_root_aristotle`, without proof-graph decomposition or child-island staging. This is useful as a baseline, but it is intentionally separate from the normal Formal Islands artifact. If an external benchmark supplies a fixed Lean root statement, `direct-root`, `run`, and `continue` can also take `--fixed-root-lean-statement` or `--fixed-root-lean-statement-file`.

### How To Read A Run

The main output is a mixed artifact, not just a success/failure bit.

- **Formal verified node:** the local Lean theorem compiled and passed semantic review as a full-node match.
- **Certified core / faithful core:** the Lean theorem verified an important local burden, but the full parent statement still has a remaining gap.
- **Informal node with remaining proof burden:** the report explains exactly what is still left for a human reviewer or a later continuation pass.

This is why partial-certification runs are first-class results here: a good run may leave the root informal while still discharging the hardest or most review-intensive local steps.

## Featured Examples

Live reports on [the website](https://aditya-ramabadran.github.io/formal-islands/):

| Theorem | Verified |
|---|---|
| [Young's Convolution Inequality](https://aditya-ramabadran.github.io/formal-islands/reports/young_convolution.html) | 3 of 4 nodes formally verified |
| [Banach-Stone Theorem](https://aditya-ramabadran.github.io/formal-islands/reports/banach_stone.html) | 4 of 4 nodes formally verified, including a clean modular root assembled from imported verified children |
| [Heat Equation Uniqueness](https://aditya-ramabadran.github.io/formal-islands/reports/heat_uniqueness.html) | Energy dissipation lemma + uniqueness core |
| [Colorful Carathéodory Theorem](https://aditya-ramabadran.github.io/formal-islands/reports/colorful_caratheodory.html) | Active-vertices lemma + distance-improvement lemma + root theorem |
| [Gleason-Kahane-Zelazko Theorem](https://aditya-ramabadran.github.io/formal-islands/reports/gleason_kahane_zelazko.html) | Square-preservation + polarization certified; analytic branch left explicit |

## Quick Start

```bash
git clone https://github.com/aditya-ramabadran/formal-islands.git
cd formal-islands
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Run on your own theorem interactively:

```bash
formal-islands new --backends claude/aristotle
```

Or run against a featured example:

```bash
formal-islands run banach_stone --backends claude/aristotle --max-attempts 4
```

The workspace path and output directory are inferred automatically.

## Common Commands

Run the full pipeline on a theorem interactively:

```bash
formal-islands new --backends claude/aristotle
```

Run a saved example or input file end to end:

```bash
formal-islands run banach_stone --backends claude/aristotle --max-attempts 4
```

Run a broad exploratory sweep that attempts every extracted node, not just the planner-selected candidates:

```bash
formal-islands run banach_stone \
  --backends claude/aristotle \
  --max-attempts 4 \
  --attempt-all-nodes
```

This mode is useful for benchmark exploration and stress testing, but the default curated workflow is still conservative candidate selection.

Continue an existing run from its saved graph:

```bash
formal-islands continue \
  --output-dir artifacts/manual-testing/run19-young-convolution-inequality-gemini-aristotle \
  --node case_1 \
  --planning-backend gemini \
  --formalization-backend aristotle
```

Regenerate a report from an existing graph:

```bash
formal-islands report \
  --graph artifacts/manual-testing/run19-young-convolution-inequality-gemini-aristotle/03_formalized_graph.json \
  --output-dir artifacts/manual-testing/run19-young-convolution-inequality-gemini-aristotle
```

Run a direct-root baseline attempt without proof-graph decomposition:

```bash
formal-islands direct-root \
  --input examples/manual-testing/run4_heat_uniqueness.json \
  --output-dir artifacts/direct-full-attempts/heat_uniqueness-aristotle \
  --max-attempts 8
```

Run with an exact fixed Lean root statement, such as a FormalQualBench-style specification:

```bash
formal-islands run \
  --input examples/manual-testing/my_example.json \
  --backends codex/aristotle \
  --fixed-root-lean-statement-file examples/lean_statements/my_example_root_spec.lean \
  --fixed-root-source lean-eval
```

In fixed-spec mode, the root node is treated strictly: a root/full-node verification must preserve the supplied theorem header. Non-root child nodes receive the fixed root statement only as context for choosing compatible local definitions, notation, and APIs. `--fixed-root-source` is a provenance label recorded in the graph/report metadata; it does not change backend behavior.

## How It Works

1. **Plan**: a planning backend reads the informal proof and builds a small proof graph (4-8 nodes)
2. **Select**: candidate nodes for formalization are ranked by how concrete and self-contained they are; the planner is discouraged from selecting a parent while leaving an obvious direct blocker child informal, and a small blocker sweep can promote that child when appropriate
3. **Formalize**: an agentic backend worker attempts to write and verify a Lean theorem for each candidate node
4. **Report**: the pipeline produces an HTML report with full verification logs, a review checklist, and a "remaining proof burden" summary for each informal node with already-verified children

The planner and formalizer can be the same backend or different ones. In practice, using a strong reasoning model for planning and Aristotle for formalization gives the best results.

After any verification, the result is semantically reviewed: the system checks whether the formal statement actually matches the intended informal claim, and classifies it as a full-node match, a faithful supporting core, or a narrower result. This matters because a proof that compiles in Lean is not automatically a proof of the right thing.

The faithfulness heuristics are intentionally conservative, but repeated `Type*`-style abstraction rejections can now trigger a second-stage planner review to distinguish true semantic drift from a canonical Lean encoding of the same local claim.

When all direct children of an informal parent node are verified, the parent can be promoted into the candidate set for a follow-up assembly attempt, so successful local cores can bootstrap further verification automatically.

In several benchmarks, this child-first traversal is not just explanatory. It materially improves later parent-level theorem attempts by providing better staging, tighter theorem-family control, and already-certified local ingredients.

In stronger recent runs, that graph structure has become real proof modularity: parent/root artifacts can assemble imported verified child modules directly, rather than copying child code into one long scratch file.

After the initial candidates are exhausted, the pipeline also does a narrow “last blocker” sweep: if one remaining informal endpoint/base-case node is the only obstacle to a promising broader parent/root closure, it can be promoted and tried late in the run.

If you rerun `report` later without a planning backend, previously generated `remaining_proof_burden` text is preserved and reused from the saved run artifacts.

For exploratory evaluation, `--attempt-all-nodes` can override the conservative candidate selector and mark every extracted informal node as `candidate_formal`. This is deliberately opt-in because it can spend substantial backend budget on nodes that the planner would normally avoid, but it is helpful when checking whether a stronger formalization backend can close more of an older benchmark than the original candidate policy attempted.

## Setup

### Python package

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

### Lean workspace

```bash
cd lean_project
lake update
lake exe cache get
lake build
cd ..
```

This takes a while the first time (Mathlib build cache download). After that, verification runs are fast.

## Backends

The pipeline uses two separate backends: one for **planning** (proof graph construction, semantic faithfulness review) and one for **formalization** (Lean code generation and repair). These can be the same or different.

**Planning backends** build the proof graph, select candidates, and do semantic checks. Any of claude, gemini, or codex can be used.

**Formalization backends** are given a Lean workspace and act as autonomous agentic workers: they can read files, edit a scratch Lean file, run `lake env lean`, and iterate. Claude Code, Gemini, and Codex all work this way. Aristotle (Harmonic's API) is also supported as a formalization backend and tends to give the best Lean results.

Aristotle is Harmonic's Lean-specialized formalization API, used here as a project-based backend for formalization tasks.

**Preferred combination:** `--backends claude/aristotle` or `--backends gemini/aristotle`. A strong reasoning model for planning, Aristotle for formalization.

### Claude Code (`--planning-backend claude` / `--formalization-backend claude`)

```bash
npm install -g @anthropic-ai/claude-code
claude                          # follow the interactive auth flow
# or: export ANTHROPIC_API_KEY=...
```

### Gemini (`--planning-backend gemini` / `--formalization-backend gemini`)

```bash
npm install -g @google/gemini-cli
gemini                          # follow the interactive auth flow
# or: export GEMINI_API_KEY=...
```

### Codex (`--planning-backend codex` / `--formalization-backend codex`)

```bash
npm install -g @openai/codex
codex                           # follow the interactive auth flow
# or: export OPENAI_API_KEY=...
```

### Aristotle (`--formalization-backend aristotle`)

Installed automatically via pip as part of `aristotlelib`. Requires an API key:

```bash
export ARISTOTLE_API_KEY=...
```

[Aristotle](https://aristotle.harmonic.fun/) is Harmonic's Lean-specialized formalization API, and can only be used as a formalization backend, not for planning. It tends to produce the best Lean results.

## Full CLI Reference

### `formal-islands run`

Run the full pipeline (plan → formalize → report) on an input file.

```
formal-islands run <input> [options]
```

`<input>` can be:
- A bare filename like `banach_stone`, searched in `examples/featured/` then `examples/manual-testing/` automatically
- A path to any JSON file with `theorem_title`, `theorem_statement`, and `raw_proof_text`

| Flag | Default | Description |
|---|---|---|
| `--backends PLANNING/FORMALIZATION` | (none) | Shorthand, e.g. `gemini/aristotle` or `claude` for both |
| `--planning-backend` | `codex` | Backend for planning/extraction stages |
| `--formalization-backend` | `codex` | Backend for formalization; `aristotle` recommended |
| `--max-attempts N` | `4` | Formalization attempts per node |
| `--output-dir PATH` | auto | Auto-derived from input name + backends + timestamp |
| `--workspace PATH` | auto | Auto-discovered `lean_project/` from repo root |
| `--node-id ID` | `auto` | Formalize only this node; default formalizes all candidates |
| `--formalization-timeout-seconds N` | none (Aristotle) | Timeout for the formalization worker |
| `--attempt-all-nodes` | off | Exploratory mode: after planning, mark every informal node as `candidate_formal` |
| `--fixed-root-lean-statement TEXT` | (none) | Optional exact Lean root statement; hard target for root attempts, context only for children |
| `--fixed-root-lean-statement-file PATH` | (none) | Read the optional exact Lean root statement from a file |

### `formal-islands new`

Interactive entry point. No input file needed.

```
formal-islands new --backends claude/aristotle
```

Prompts for theorem title, statement, and proof sketch, then runs the full pipeline.

### Stage commands

For running individual stages manually:

```bash
formal-islands plan --input <file> --output-dir <dir> --planning-backend claude
formal-islands formalize-one --graph <file> --output-dir <dir> --formalization-backend aristotle
formal-islands report --graph <file> --output-dir <dir> --planning-backend claude
```

The `report` command accepts `--planning-backend` optionally; if supplied, it synthesizes a "remaining proof burden" paragraph for any informal node that has verified children.

### `formal-islands direct-root`

Run an Aristotle-only direct full/root formalization diagnostic on an input theorem/proof JSON, bypassing proof-graph extraction and candidate selection.

```bash
formal-islands direct-root \
  --input examples/manual-testing/run19_young_convolution_inequality.json \
  --output-dir artifacts/direct-full-attempts/young_convolution-aristotle \
  --max-attempts 8
```

This writes:

- `direct_root_prompt.txt`: the exact standalone Aristotle prompt
- `direct_root_summary.json`: attempt history, Aristotle project metadata, theorem-name check, optional fixed-spec header check, and local Lean verification result
- extracted Aristotle result files under the output directory, plus the copied generated Lean file in the Lean workspace

The command is meant for diagnostics and paper baselines: it answers "can Aristotle close the root directly under this prompt and budget?" It does not produce a proof graph, semantic-island report, or remaining-burden review packet.

For benchmark tasks with an externally supplied Lean statement, add one of:

- `--fixed-root-lean-statement "theorem exact_root ... := by"`
- `--fixed-root-lean-statement-file examples/lean_statements/root_statement.lean`

When a fixed root statement is supplied, direct-root uses its theorem name when one can be extracted and rejects a compiling returned file if the main theorem header does not match the fixed statement.

### `formal-islands continue`

Resume a finished run from its existing `03_formalized_graph.json`, reintroducing specific node ids as fresh candidates and then continuing the normal auto formalization loop from there.

```bash
formal-islands continue \
  --output-dir artifacts/manual-testing/run19-young-convolution-inequality-gemini-aristotle \
  --node case_1 \
  --instructions "Keep the endpoint case in the original measure-theoretic setting." \
  --planning-backend gemini \
  --formalization-backend aristotle
```

This appends to the same `_progress.log` and `graph_history.jsonl`, rewrites `03_formalized_graph.json` and `03_formalization_summaries.json`, and regenerates the report.

Continuation can also carry targeted human guidance into the next formalization prompt:

- `--instructions "..."` appends inline advice, such as a preferred indexing convention or proof route.
- `--instructions-file path/to/hints.txt` appends a longer hint file.
- `--lean-statement "theorem ..."` appends a preferred Lean theorem statement or theorem-shape hint.

These hints are stored in the continued node's formalization rationale, so they remain visible in the graph, progress history, prompts, and report.

Continuation can also attach or replace a graph-level fixed root statement:

```bash
formal-islands continue \
  --output-dir artifacts/manual-testing/existing-run \
  --node root \
  --fixed-root-lean-statement-file examples/lean_statements/root_spec.lean \
  --fixed-root-source formalqualbench \
  --planning-backend codex \
  --formalization-backend aristotle
```

This is different from `--lean-statement`: `--lean-statement` is a soft continuation hint for the continued node, while `--fixed-root-lean-statement-file` is a run-level root specification. If the continued node is the root, the exact theorem header is enforced. If the continued node is a child, the fixed root statement is provided only as compatibility context.

## Input Format

Each input is a JSON file with:

```json
{
  "theorem_title": "Heat equation uniqueness",
  "theorem_statement": "Full theorem statement...",
  "raw_proof_text": "Informal proof..."
}
```

Featured examples are in `examples/featured/`. Additional benchmarks are in `examples/manual-testing/`.

## Development

```bash
.venv/bin/python -m pytest -q
```

Developer notes, architecture docs, and internal benchmark history are in `dev/`.
