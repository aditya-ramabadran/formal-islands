# Formal Islands

**Certified local cores inside natural-language mathematical proofs.**

## What Is This?

Current AI-for-math systems face a real bottleneck from two directions at once.

Strong informal systems — large language models doing natural-language mathematical reasoning — can handle broad and flexible proof search, but they can also drift semantically, prove the wrong thing, or produce arguments whose correctness is hard for humans to audit at scale. As these systems get better, the proofs they produce will get longer and harder to check, not easier.

Fully formal systems like Lean give much stronger guarantees, but they are bottlenecked by the limits of Lean and Mathlib. In infrastructure-heavy domains — PDEs, geometric measure theory, spectral theory — you often cannot even *state* the intended theorem faithfully, let alone prove it. This caps what fully formal AI theorem-proving systems can prove today.

Formal Islands is built around a near-term compromise: let an informal system handle the broad reasoning and proof search, then extract the smaller local claims that really are within current formal coverage and certify those in Lean. The result is a **mixed artifact**: most of the proof remains informal, but important **formal islands** inside it are genuinely machine-checked.

A **formal island** is a proof node independently verified in Lean — certified in isolation, without requiring the rest of the proof to be formalized first.

This is more useful than it might sound:

- **It makes human review tractable.** Instead of auditing one long informal proof monolithically, a reviewer can focus on the uncertified gaps while knowing that key lemmas are already verified. The generated report makes this explicit: each uncertified claim gets a concrete review checklist.
- **It gives an honest answer to "what exactly was checked?"** The report distinguishes full-node verification, narrower certified supporting cores, and informal steps — with no overclaiming.
- **It showcases a natural path for combining different systems.** Broad informal reasoning from language models; AlphaProof-style search or other formal tools on the formalizable subproblems. The architecture is modular by design.
- **It can only get better over time.** As Mathlib expands, the same pipeline can certify larger and larger portions of proofs, eventually approaching full formalization where that becomes realistic.

Faithfulness is treated as a first-class concern. Even when the formalization backend proves something that compiles in Lean, there is no guarantee the formal statement actually matches the intended informal theorem. The pipeline uses a series of heuristic checks and LLM-assisted semantic review to guard against drift — but the ultimate check is always left to a human, and the report makes the remaining burden explicit.

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
3. **Formalize** — an agentic backend worker attempts to write and verify a Lean theorem for each candidate node
4. **Report** — the pipeline produces an HTML report with full verification logs, a review checklist, and a "remaining proof burden" summary for each informal node with already-verified children

The planner and formalizer can be the same backend or different ones. In practice, using a strong reasoning model for planning and Aristotle for formalization gives the best results.

After any verification, the result is semantically reviewed: the system checks whether the formal statement actually matches the intended informal claim, and classifies it as a full-node match, a faithful supporting core, or a narrower result. This matters because a proof that compiles in Lean is not automatically a proof of the right thing.

When all direct children of an informal parent node are verified, the parent can be promoted into the candidate set for a follow-up assembly attempt — so successful local cores can bootstrap further verification automatically.

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

Aristotle can only be used as a formalization backend, not for planning. It tends to produce the best Lean results.

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

For running individual stages manually:

```bash
formal-islands plan --input <file> --output-dir <dir> --planning-backend claude
formal-islands formalize-one --graph <file> --output-dir <dir> --formalization-backend aristotle
formal-islands report --graph <file> --output-dir <dir> --planning-backend claude
```

The `report` command accepts `--planning-backend` optionally; if supplied, it synthesizes a "remaining proof burden" paragraph for any informal node that has verified children.

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
