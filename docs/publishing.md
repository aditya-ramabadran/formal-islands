# Publishing Plan

This document tracks everything needed to make the repo presentable and publicly usable on GitHub.
Nothing here is implemented yet — this is the ordered plan.

---

## 1. Repo Cleanup

### Remove stale / internal files

**`examples/` root:**
- Delete `examples/nonnegative_sum_graph.json` — internal test artifact, not a real benchmark
- Delete `examples/nonnegative_sum_input.json` — same
- Delete `examples/toy_implication_input.md` — old format, superseded by JSON inputs
- Do **not** touch `examples/manual-testing/` — keep all files there; only copy from it, never edit or delete

**`lean_project/` root:**
- Delete `lean_project/test_deriv.lean`, `test_deriv2.lean`, `test_deriv3.lean` — scratch files
- Delete `lean_project/test_taylor.lean`, `test_bernoulli.lean` — scratch files
- Add `lean_project/test_*.lean` to `.gitignore` to prevent future commits of scratch files

**`dev/` (internal docs — renamed from `docs/`):**
- `dev/design-note.md` — internal architecture notes, stays in `dev/`
- `dev/run_history.md` — internal benchmark log, stays in `dev/`
- `dev/opengauss-backend-plan.md` — unimplemented integration plan, stays in `dev/`
- `dev/publishing.md` (this file) — stays in `dev/`
- These are developer-facing only; the renamed `docs/` folder becomes the GitHub Pages root (see Phase 5)

**`artifacts/`:**
- Confirm `artifacts/` is in `.gitignore` — it should never be committed (large, noisy, internal)
- The featured reports will be published under `docs/reports/` (see Phase 5)

**`lean_project/FormalIslands/Generated/`:**
- Confirm this is gitignored — worker scratch files should not be tracked

---

## 2. Featured Benchmarks Curation

Create `examples/featured/` as the public-facing benchmark input folder.
Copy (never move or edit) the input JSON files from `examples/manual-testing/` with clean descriptive names.

**Do not remove or edit `examples/manual-testing/`** — all existing files stay there.

### The four flagship benchmarks

These are the definitive public-facing examples. They were selected for a combination of mathematical substance, result clarity, and graph cleanliness.

| Clean name | Source input | Best run artifact | What verified | Why it's a flagship |
|---|---|---|---|---|
| `two_point_log_sobolev.json` | `run11_two_point_log_sobolev.json` | `run11-two-point-log-sobolev-claude-aristotle-3` | `key_lemma` (full match) + `convexity__formal_core` | Clean graph, two verified nodes, both have nontrivial Lean code |
| `heat_uniqueness.json` | `run4_heat_uniqueness.json` | `run4-heat-uniqueness-gemini-aristotle-4` | `energy_dissipation` (full match) + `main_theorem__formal_core` | Clean graph, natural local island, one node is highly nontrivial |
| `matrix_determinant_lemma.json` | `run15_matrix_determinant_lemma.json` | `run15-matrix-determinant-lemma-claude-aristotle-2` | `special_case` + `root` (2 of 2 nodes) | Less deep mathematically but shows end-to-end formal closure on a benchmark with good Mathlib support |
| `hoeffding_lemma.json` | `run16_hoeffding_lemma.json` | `run16-hoeffding-lemma-gemini-aristotle` | `convexity_expectation` + `log_mgf_bound` | Two central local islands verified; together they cover almost all the proof substance; root staying informal is not a real weakness |

### Benchmarks kept in `examples/manual-testing/` but not featured

All other benchmark inputs remain in `examples/manual-testing/` for development use. They are not highlighted on the public site but are accessible to anyone exploring the repo. The folder may be renamed from `manual-testing` to something cleaner (e.g. `benchmarks`) in a later cleanup pass.

---

## 3. CLI Rename + UX Overhaul

### The "smoke" name problem

The CLI is currently called `formal-islands-smoke`. "Smoke" is a developer-in-joke holdover from when this was a scratch smoke-test runner for the pipeline — it means nothing to an end user and makes the tool look unfinished.

**Rename `formal-islands-smoke` → `formal-islands`** as the main user-facing entry point.
The old name can be kept as an alias during transition, then removed.

### The verbosity problem

The typical invocation today:
```bash
./.venv/bin/formal-islands-smoke run-benchmark \
  --planning-backend=gemini \
  --formalization-backend=aristotle \
  --input=/Users/adihaya/GitHub/formal-islands/examples/manual-testing/run4_heat_uniqueness.json \
  --output-dir=/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run4-heat-uniqueness-gemini-aristotle-5 \
  --workspace=/Users/adihaya/GitHub/formal-islands/lean_project \
  --max-attempts=4
```

Every flag requires a full path and all of them are required. This is 300+ characters for something that should be a 50-character command.

### Better defaults

- **`--workspace`**: Default to `lean_project` relative to the repo root (auto-discovered via nearest `lakefile.toml`). Should almost never need to be specified explicitly.
- **`--output-dir`**: Auto-derive from input filename + backends + short timestamp slug. E.g. `heat_uniqueness.json` + `gemini/aristotle` → `artifacts/heat-uniqueness-gemini-aristotle-<MMDD-HHMM>`. Only specify explicitly if you want a custom name.
- **`--input`**: When given just a filename (no path), search `examples/featured/` first, then `examples/manual-testing/`. Full path still accepted.

### Backend shorthand

`--planning-backend=gemini --formalization-backend=aristotle` is the most common pattern. Add:
```
--backends gemini/aristotle
```
as a shorthand that splits on `/`. `--backends aristotle` means both backends are the same. The long-form flags still work.

### Ideal invocation after the overhaul

```bash
formal-islands run heat_uniqueness --backends gemini/aristotle --attempts 4
```

Or with an explicit path:
```bash
formal-islands run examples/featured/heat_uniqueness.json --backends gemini/aristotle
```

The command is `run` instead of `run-benchmark`. The input is just a filename. The backends are one flag. Workspace and output dir are inferred. The only things you specify are the input and the backends.

### Interactive entry point — `formal-islands new`

For users who don't have a JSON file at all:

```bash
formal-islands new
```

Behavior:
1. Prompts for theorem title (single line)
2. Prompts for theorem statement (multiline, terminate with blank line)
3. Prompts for informal proof text (multiline, same terminator)
4. Asks which backends to use (default: whatever is configured / available)
5. Writes a JSON input file as `input.json` in the output dir
6. Immediately calls the `run` pipeline with the generated input

This removes the JSON format requirement entirely for the common case.

**Implementation notes:**
- `new` and `run` both live in the renamed `cli.py` (currently `smoke.py`)
- `new` uses `input()` with clear prompts; no new dependencies
- After collecting input, calls the existing `run_benchmark` logic directly (not a subprocess)
- The generated `input.json` is saved alongside the artifacts so the run is fully reproducible

### One-liner stdin mode (for scripting / CI)

```bash
formal-islands run --stdin
```
Reads title, statement, and proof from stdin in a simple delimited format:
```
TITLE: Heat equation uniqueness
STATEMENT: ...
PROOF: ...
```

---

## 4. Report HTML Visual Fixes

These apply to `generator.py` and must be done before regenerating the featured report files for the site.

### `**bold**` markdown in informal proof text

Informal proof text often contains markdown-style step labels like `**Symmetry and base value.**`, `**First derivative.**`, etc. These currently render as literal `**` characters.

**Decision: render `**text**` as `<em>` (italic)**, and handle `*text*` as `<em>` as well.

Rationale:
- Plain `<strong>` (bold) would visually collide with the existing section headers ("Informal statement:", "Coverage:", "Lean theorem name:") which are already rendered as bold in paragraph context
- Italic is the conventional typographic treatment for named proof steps in mathematical writing
- The visual hierarchy becomes clean: `<h3>` node title → `<strong>` structural section labels → `<em>` named proof steps within prose
- No changes needed to the existing section header markup

Implementation: add a markdown-inline pass in `_render_inline_code_html` (alongside the existing backtick → `<code>` pass). Process backtick spans first (they may contain `*`), then `**...**` → `<em>`, then `*...*` → `<em>`.

### Already fixed (do not redo)
- MathJax flicker in SVG node text: fixed via `skipHtmlTags: ['svg']`
- Transparent node fill causing edge bleed-through: fixed by making informal/candidate node fills fully opaque
- Backtick → `<code>` inline rendering in coverage notes, checklist text, and rationale: fixed

---

## 5. GitHub Pages Site + Report Gallery

### Folder strategy

Rename the current `docs/` folder to `dev/` — its contents are all developer-facing (design notes, run history, this file). Then use `docs/` as the GitHub Pages root, which is the conventional GitHub Pages path and requires no special configuration.

### `docs/` structure

```
docs/
  index.html              ← main landing page (custom HTML, not README-based)
  reports/
    two_point_log_sobolev.html
    heat_uniqueness.html
    matrix_determinant_lemma.html
    hoeffding_lemma.html
  assets/
    style.css             ← shared landing page styles (if needed)
    screenshot_*.png      ← placeholder images for the gallery cards
```

### Why custom `index.html` instead of README-based Pages

GitHub's README-based Pages renders plain Markdown with no custom styling. A custom `index.html` lets us:
- Link directly to the live report HTML files
- Show a proper gallery with per-benchmark cards
- Present the project clearly to someone who has never heard of Lean or formal verification

### `index.html` design

The landing page must be clear, focused, and not overly technical. It should explain the need for the project (the gap between informal mathematical proofs and fully formal verification) before describing what it does. Contents:

- Project name + one-line tagline
- Short explanation of the problem being solved and why it matters
- What a "formal island" is (one concrete sentence)
- Gallery section: one card per flagship benchmark, with:
  - Theorem name and short informal statement
  - Brief description of what was formally verified
  - Placeholder screenshot (to be filled in with actual screenshots of the report pages)
  - Link to `reports/<name>.html`
- Condensed quick-start section
- Link to the GitHub repo and full README

The gallery placeholder images are screenshots taken from the actual report HTML pages. These are filled in manually; no automation needed.

### Generating the featured report HTML files

Before copying reports to `docs/reports/`, regenerate each one from its artifact using the latest generator (to pick up all visual fixes from Phase 4). The report command currently requires `--planning-backend` explicitly:

```bash
./.venv/bin/formal-islands-smoke report \
  --graph=artifacts/manual-testing/<run-dir>/03_formalized_graph.json \
  --output-dir=artifacts/manual-testing/<run-dir> \
  --planning-backend=<backend>
```

Specific commands for the four flagship runs:

```bash
# Two-point log-Sobolev
./.venv/bin/formal-islands-smoke report \
  --graph=artifacts/manual-testing/run11-two-point-log-sobolev-claude-aristotle-3/03_formalized_graph.json \
  --output-dir=artifacts/manual-testing/run11-two-point-log-sobolev-claude-aristotle-3 \
  --planning-backend=claude

# Heat uniqueness
./.venv/bin/formal-islands-smoke report \
  --graph=artifacts/manual-testing/run4-heat-uniqueness-gemini-aristotle-4/03_formalized_graph.json \
  --output-dir=artifacts/manual-testing/run4-heat-uniqueness-gemini-aristotle-4 \
  --planning-backend=gemini

# Matrix determinant lemma
./.venv/bin/formal-islands-smoke report \
  --graph=artifacts/manual-testing/run15-matrix-determinant-lemma-claude-aristotle-2/03_formalized_graph.json \
  --output-dir=artifacts/manual-testing/run15-matrix-determinant-lemma-claude-aristotle-2 \
  --planning-backend=claude

# Hoeffding's lemma
./.venv/bin/formal-islands-smoke report \
  --graph=artifacts/manual-testing/run16-hoeffding-lemma-gemini-aristotle/03_formalized_graph.json \
  --output-dir=artifacts/manual-testing/run16-hoeffding-lemma-gemini-aristotle \
  --planning-backend=gemini
```

Then copy the resulting `04_report.html` from each artifact directory into `docs/reports/` with the clean name.

---

## 6. README Polish

The current README is written for active developers. The public-facing version needs to be understood by someone who only knows informal mathematics and has heard vaguely of Lean.

**Add:**
- One-line tagline at the top
- A short "What Is This?" section explaining the problem (gap between informal proofs and full formalization, and why partial certification is still useful)
- A "Try it" section near the top using the new `formal-islands new` interactive command
- A "Featured Examples" section linking to the GitHub Pages gallery
- Brief conceptual explanation of what a "formal island" means

**Backend documentation:**
The README should document all supported backends with setup instructions for each:
- **Codex** (`--planning-backend codex` or `--backends codex`): install via npm, authenticate with OpenAI key
- **Claude Code** (`--planning-backend claude`): install via npm, authenticate via `claude auth login` or `ANTHROPIC_API_KEY`
- **Gemini** (`--planning-backend gemini`): install via npm, authenticate with Google API key
- **Aristotle** (`--formalization-backend aristotle`): install via pip, requires `ARISTOTLE_API_KEY` env var
- Preferred combination based on experience: use Gemini or Claude as `--planning-backend`, Aristotle as `--formalization-backend`

**Document all flags and defaults**, especially:
- `--max-attempts` (default: 2; recommend 4 for stronger results)
- `--formalization-timeout-seconds` (no default for Aristotle; add one if not present)
- `--backends` shorthand (once implemented)
- `--workspace` (default: auto-discovered `lean_project/`)
- `--output-dir` (default: auto-derived once implemented)

**Remove / move to `dev/`:**
- Internal pipeline architecture details
- Run history references
- Anything that reads like a developer journal rather than user documentation

**Restructure:**
```
## What Is This?         ← new, one paragraph explaining the problem and the approach
## Quick Start           ← bring to top; use `formal-islands new`
## Featured Examples     ← links to GitHub Pages gallery
## How It Works          ← condensed pipeline description, non-technical
## Setup                 ← install steps
## Backends              ← per-backend setup + preferred combination
## All CLI Flags         ← full flag reference with defaults
## Development           ← move to bottom
```

---

## 7. `.gitignore` Audit

Ensure the following are gitignored:
- `artifacts/` — all benchmark output
- `lean_project/.lake/` — Mathlib build cache (large, should never be committed)
- `lean_project/FormalIslands/Generated/` — worker scratch files
- `lean_project/test_*.lean` — scratch Lean files
- `**/__pycache__/`, `.venv/`, `*.egg-info/` — Python build artifacts
- `.env`, `auth.json` — credentials

---

## 8. License and Metadata

- Add a `LICENSE` file (MIT or Apache-2.0 recommended for an open research tool)
- Add a `CITATION.cff` if citation is desired
- Review `pyproject.toml` for correct metadata (name, description, author, version)
- Add a `.github/workflows/` CI file that runs `pytest -q` on pushes (optional but professional)

---

## Implementation Order

Each phase is independently reviewable and can be done in sequence.

**Phase 1 — Cleanup**
- Remove stale files from `examples/` root and `lean_project/`
- Audit and update `.gitignore`
- Rename `docs/` → `dev/`

**Phase 2 — Featured benchmarks**
- Create `examples/featured/` with the four flagship input JSONs (copied, not moved)
- Confirm the exact artifact directory for each flagship run

**Phase 3 — Report visual fixes**
- Render `**...**` / `*...*` as `<em>` in `_render_inline_code_html`
- Any other generator polish that affects published reports

**Phase 4 — CLI rename + UX overhaul**
- Rename `formal-islands-smoke` → `formal-islands`; rename `smoke.py` → `cli.py`
- Add smart defaults: workspace auto-discovery, output-dir auto-derivation
- Add `--backends` shorthand
- Rename `run-benchmark` → `run`
- Implement `formal-islands new` interactive entry point
- Update README quick-start to use the new commands

**Phase 5 — Site + gallery**
- Regenerate the four flagship `04_report.html` files using the commands above (picks up Phase 3 fixes)
- Copy them into `docs/reports/` with clean names
- Write `docs/index.html` landing page with gallery cards and placeholder screenshots
- Take screenshots of each report and fill in the gallery card images
- Configure GitHub Pages to serve from `docs/`

**Phase 6 — README + docs polish**
- Rewrite README with the new structure and `formal-islands` command name
- Document all backends with setup instructions and preferred combination
- Document all flags with defaults
- Optionally add `dev/INTERNALS.md` as a short index/table-of-contents pointing to the existing dev files — `design-note.md`, `run_history.md`, `opengauss-backend-plan.md` all stay as-is; don't delete or merge them

**Phase 7 — Metadata + CI**
- Add `LICENSE`
- Add `CITATION.cff` if desired
- Add basic CI workflow (`pytest -q` on push)

---

## Resolved Questions

1. **Should `examples/manual-testing/` stay in the repo?**
   Yes — keep all files there. The folder may be renamed from `manual-testing` to `benchmarks` or similar in a later pass, but nothing is removed.

2. **Which exact run to use per flagship benchmark?**
   Resolved — see the flagship table in Phase 2.

3. **CI vs manual curation for published reports?**
   Manual. No CI re-generation. Reports are committed as static files to `docs/reports/`.

4. **Domain name for the Pages site?**
   Default `<username>.github.io/formal-islands` for now. Custom domain can be added later via `CNAME`.
