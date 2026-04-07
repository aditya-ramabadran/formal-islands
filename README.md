# Formal Islands

**Certified local cores inside natural-language mathematical proofs.**

Most proofs are written informally. Full formalization in Lean is powerful but expensive — often months of infrastructure work for a single theorem. Formal Islands targets the middle ground: given a theorem and an informal proof, the pipeline finds the steps that are concrete enough to certify *now*, verifies them in Lean, and produces an honest report that shows exactly what was certified and what still needs human review.

A **formal island** is a proof node that has been independently verified in Lean — certified in isolation, without requiring the surrounding proof to be formalized first.

## Featured Examples

Live reports on [GitHub Pages](https://aditya-ramabadran.github.io/formal-islands/):

| Theorem | Verified |
|---|---|
| [Two-Point Log-Sobolev Inequality](https://aditya-ramabadran.github.io/formal-islands/reports/two_point_log_sobolev.html) | Scalar inequality + G(u) ≥ 0 core lemma |
| [Heat Equation Uniqueness](https://aditya-ramabadran.github.io/formal-islands/reports/heat_uniqueness.html) | Energy dissipation lemma + uniqueness core |
| [Matrix Determinant Lemma](https://aditya-ramabadran.github.io/formal-islands/reports/matrix_determinant_lemma.html) | Both nodes — full formal closure |
| [Hoeffding's Lemma](https://aditya-ramabadran.github.io/formal-islands/reports/hoeffding_lemma.html) | Convexity bound + log-MGF bound |

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
formal-islands run two_point_log_sobolev --backends claude/aristotle --max-attempts 4
```

The workspace path and output directory are inferred automatically.

## How It Works

1. **Plan** — a planning backend reads the informal proof and builds a small proof graph (4–8 nodes)
2. **Select** — candidate nodes for formalization are ranked by how concrete and self-contained they are
3. **Formalize** — an agentic backend worker attempts to write and verify a Lean theorem for each candidate
4. **Report** — the pipeline produces an HTML report and JSON artifacts with full verification logs

The report shows exactly what was certified (with Lean code), what failed, and what was never attempted. It includes a review checklist summarizing the gap between the informal proof and the verified fragments.

When all direct children of an informal parent node have been verified, the parent can be promoted into the candidate set for a follow-up assembly attempt — so a successful local core can bootstrap further verification automatically.

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

The pipeline uses two separate backends: one for **planning** (proof graph construction, semantic review) and one for **formalization** (Lean code generation and repair). These can be the same or different.

**Preferred combination:** `--backends claude/aristotle` or `--backends gemini/aristotle`. Using a strong planning backend alongside Aristotle for formalization gives the best results in practice.

### Claude Code (`--planning-backend claude`)

```bash
npm install -g @anthropic-ai/claude-code
claude                          # follow the interactive auth flow
# or: export ANTHROPIC_API_KEY=...
```

### Gemini (`--planning-backend gemini`)

```bash
npm install -g @google/gemini-cli
gemini                          # follow the interactive auth flow
# or: export GEMINI_API_KEY=...
```

### Codex (`--planning-backend codex`)

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

Aristotle is the only supported formalization backend. Planning backends (claude, gemini, codex) do not support formalization.

## All CLI Flags

### `formal-islands run`

Run the full pipeline (plan → formalize → report) on an input file.

```
formal-islands run <input> [options]
```

`<input>` can be:
- A bare filename like `two_point_log_sobolev` — searched in `examples/featured/` then `examples/manual-testing/` automatically
- A path to any JSON file with `theorem_title`, `theorem_statement`, and `raw_proof_text`

| Flag | Default | Description |
|---|---|---|
| `--backends PLANNING/FORMALIZATION` | — | Shorthand, e.g. `gemini/aristotle` or `claude` for both |
| `--planning-backend` | `codex` | Backend for planning/extraction stages |
| `--formalization-backend` | `codex` | Backend for formalization; `aristotle` recommended |
| `--max-attempts N` | `2` | Formalization attempts per node; `4` gives stronger results |
| `--output-dir PATH` | auto | Auto-derived from input name + backends + timestamp |
| `--workspace PATH` | auto | Auto-discovered `lean_project/` from repo root |
| `--node-id ID` | `auto` | Formalize only this node; default formalizes all candidates |
| `--formalization-timeout-seconds N` | none (Aristotle) | Timeout for the formalization worker |

### `formal-islands new`

Interactive entry point — no input file needed.

```
formal-islands new --backends claude/aristotle
```

Prompts for theorem title, statement, and proof sketch, then runs the full pipeline.

### Stage commands

For running individual stages:

```bash
formal-islands plan --input <file> --output-dir <dir> --planning-backend claude
formal-islands formalize-one --graph <file> --output-dir <dir> --formalization-backend aristotle
formal-islands report --graph <file> --output-dir <dir> --planning-backend claude
```

The `report` command accepts `--planning-backend` optionally; if supplied, it synthesizes a "remaining proof burden" paragraph for any informal node that has verified children.

## Development

```bash
.venv/bin/python -m pytest -q
```

Focused test subsets:

```bash
.venv/bin/pytest tests/test_smoke.py -q
.venv/bin/pytest tests/test_review_and_report.py -q
```

Developer notes and internal architecture docs are in `dev/`.
