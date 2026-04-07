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

**`lean_project/` root:**
- Delete `lean_project/test_deriv.lean`, `test_deriv2.lean`, `test_deriv3.lean` — scratch files
- Delete `lean_project/test_taylor.lean`, `test_bernoulli.lean` — scratch files
- These should probably never have been committed; add a glob to `.gitignore` to prevent future commits of `lean_project/test_*.lean`

**`docs/` (internal-only docs to remove or move out of public sight):**
- `docs/design-note.md` — internal architecture notes; either move to `INTERNALS.md` at root (clearly marked as developer-only) or delete
- `docs/run_history.md` — internal benchmark log; too raw for public consumption; move to `INTERNALS.md` or a private branch
- `docs/opengauss-backend-plan.md` — unimplemented integration plan; keep but move to `INTERNALS.md` or a `dev/` folder outside the main docs tree
- `docs/publishing.md` (this file) — also internal

**`artifacts/`:**
- Confirm `artifacts/` is in `.gitignore` — it should never be committed (large, noisy, internal)
- The featured reports will be published separately under `site/reports/` (see Phase 4)

**`lean_project/FormalIslands/Generated/`:**
- Confirm this is gitignored — worker scratch files should not be tracked

---

## 2. Featured Benchmarks Curation

Create `examples/featured/` as the public-facing benchmark input folder.
Select the strongest runs and copy their input JSON files there with clean descriptive names.

**Candidate featured benchmarks** (based on run history):

| Input file | Best run artifact | What verified |
|---|---|---|
| `run4_heat_uniqueness.json` | `run4-heat-uniqueness-gemini-aristotle-4` or `-5` | `energy_dissipation` (full match) + `*__formal_core` |
| `run11_two_point_log_sobolev.json` | `run11-two-point-log-sobolev-claude-aristotle-4` | `key_lemma` (full match) + `convexity__formal_core` |
| `run14_vandermonde_convolution.json` | `run14-vandermonde-convolution-claude-aristotle` | `comb_proof__formal_core` |
| `run15_matrix_determinant_lemma.json` | `run15-matrix-determinant-lemma-claude-aristotle-2` | `special_case` + `root` (two verified nodes) |
| `run10_first_dirichlet_eigenfunction.json` | `run10-first-dirichlet-eigenfunction-gemini-aristotle-6` | two cores across both branches |
| `run7_harmonic_minimizer.json` | `run7-harmonic-minimizer-rerun-claude-aristotle-5` | `dirichlet_pythagorean__formal_core` |

NOTE: **Still keep all the benchmarks in examples/manual-testing, don't remove or edit those files, only copy from there**

**Rename convention for featured examples:**
Use clean descriptive names, not run numbers, for example like
- `heat_uniqueness.json`
- `two_point_log_sobolev.json`
- `vandermonde_convolution.json`
- `matrix_determinant_lemma.json`
- `dirichlet_eigenfunction.json`
- `harmonic_minimizer.json`

---

## 3. CLI Rename + UX Overhaul

### The "smoke" name problem

The CLI is currently called `formal-islands-smoke`. "Smoke" is a developer-in-joke holdover from when this was a scratch smoke-test runner for the pipeline — it means nothing to an end user and makes the tool look unfinished. It should be renamed.

**Rename `formal-islands-smoke` → `formal-islands`** as the main user-facing entry point.
The old name can be kept as an alias or removed entirely once the rename is stable.

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

Every flag is full-path and all of them are required. This is 300+ characters for something that should be a 50-character command.

### Better defaults

Most of these flags have obvious sensible defaults that should never need to be typed:

- **`--workspace`**: Default to `lean_project` relative to the repo root (auto-discovered). Should almost never need to be specified explicitly.
- **`--output-dir`**: Auto-derive from the input filename + backends + a short timestamp slug. E.g. input `heat_uniqueness.json` + backends `gemini/aristotle` → `artifacts/heat-uniqueness-gemini-aristotle-<date>`. User only specifies this if they want an explicit name.
- **`--input`**: When given just a filename (no path), search `examples/featured/` first, then `examples/manual-testing/`. Full path still accepted.

### Backend shorthand

`--planning-backend=gemini --formalization-backend=aristotle` is the most common pattern. Consider:
```
--backends gemini/aristotle
```
as a single shorthand that splits on `/`. `--backends aristotle` means both backends are the same. The long-form flags still work for the cases where they differ.

### Ideal invocation after the overhaul

```bash
formal-islands run heat_uniqueness --backends gemini/aristotle --attempts 4
```

Or with a full path:
```bash
formal-islands run examples/featured/heat_uniqueness.json --backends gemini/aristotle
```

The command is now `run` instead of `run-benchmark` (shorter, unambiguous). The input is just a filename. The backends are one flag. The workspace and output dir are inferred. The only thing you still specify explicitly is the input and the backends.

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
5. Writes a JSON input file to the output dir as `input.json`
6. Immediately calls the `run` pipeline with the generated input

This removes the JSON format requirement entirely for the common case.

**Implementation notes:**
- `new` and `run` both live in the renamed `cli.py` (or `smoke.py` renamed)
- `new` uses `input()` with clear prompts; no new dependencies
- After collecting input, calls the existing `run_benchmark` logic directly (not a subprocess)
- The generated `input.json` is saved alongside the artifacts so the run is reproducible

### One-liner stdin mode (for scripting)

For CI / scripting use:
```bash
formal-islands run --stdin
```
where stdin provides the input as a simple delimited format:
```
TITLE: Heat equation uniqueness
STATEMENT: ...
PROOF: ...
```
This is machine-friendly without requiring a pre-created file.

---

## 4. GitHub Pages Site + Report Gallery

### Strategy

GitHub Pages can serve from:
- `docs/` folder on `main` branch (simple, no extra branch needed)
- A dedicated `gh-pages` branch

Because we already use `docs/` for markdown documentation, use a **`site/` folder** and configure GitHub Pages to serve from `site/` on `main`. (GitHub Pages supports custom source folder via repo settings or `_config.yml`.)

Alternatively: rename the current `docs/` folder to `dev/` (since its contents are developer-facing, not user-facing), and use `docs/` as the GitHub Pages source — this is the conventional path and requires no custom Pages config.

**Recommended: rename `docs/` → `dev/`, use `docs/` as Pages root.**

### `docs/` structure for Pages

```
docs/
  index.html          ← main landing page (NOT README-based; custom HTML)
  reports/
    heat_uniqueness.html
    two_point_log_sobolev.html
    vandermonde_convolution.html
    matrix_determinant_lemma.html
    dirichlet_eigenfunction.html
    harmonic_minimizer.html
  assets/
    style.css         ← shared styles if needed
    screenshot_*.png  ← any screenshots used on index.html
```

### Why custom `index.html` instead of README-based Pages

- GitHub's README-based Pages renders the README as plain Markdown with no custom styling
- We want to link to the live `report.html` files directly — a custom page lets us do this with proper framing (titles, descriptions, visual thumbnails or previews)
- The `report.html` files themselves are already self-contained and self-styled; no extra work needed to display them

### `index.html` contents

The landing page should include:
- Project name + one-paragraph description of what Formal Islands does
- What a "formal island" is (one sentence)
- Link to the GitHub repo
- A gallery section: one card per featured benchmark with:
  - Theorem name / statement preview
  - Short description of what was verified
  - Link to `reports/<name>.html`
- Setup / quick start section (condensed from README)
- Link to full README on GitHub

### Report HTML files

Copy the best run's `04_report.html` into `docs/reports/` with the clean descriptive name.
These files are already fully self-contained (all CSS/JS inline or via CDN), so no assets need to travel with them.

The reports link to CDN-hosted MathJax, so they display correctly on GitHub Pages without any build step.

---

## 5. README Polish

The current README is accurate but written for active developers. A public-facing README needs:

**Add:**
- One-line tagline above the first section (e.g. "Formal Islands turns informal math proofs into honest partial Lean certificates.")
- Screenshot / image of an example report (placeholder for now; fill in once site is live)
- A "Try it" section at the top with the `new` interactive command
- Brief note on what "formal islands" means conceptually, before the technical description
- Shields/badges (build status, license, maybe Lean version)

**Remove / move to `dev/`:**
- Internal details about backend design (covered in design-note.md)
- Run history references
- References to `ARISTOTLE_API_KEY` can stay but should be clearly marked as optional

**Restructure:**
```
## What Is This?         ← new, one paragraph
## Quick Start           ← currently buried; bring up, use `new` command
## Featured Examples     ← link to the GitHub Pages gallery
## How It Works          ← condensed version of current pipeline description
## Setup                 ← current setup section, lightly trimmed
## Backends              ← keep, trim Aristotle API key prominence
## Development           ← keep, move to bottom
```

---

## 6. Report HTML Visual Fixes

Several rendering issues in the current HTML reports need to be fixed before reports are published on the site. These apply to `generator.py` and should be done before regenerating the featured report files in Phase 4.

### `**bold**` markdown in informal proof text

The informal proof text often contains markdown-style step labels like `**Symmetry and base value.**`, `**First derivative.**`, etc. These currently render as literal `**` characters on screen.

**Decision: render `**text**` as `<em>` (italic)**, and also handle `*text*` as `<em>` for consistency.

Rationale:
- Plain bold (`<strong>`) would visually collide with the existing section headers ("Informal statement:", "Coverage:", "Lean theorem name:") which are already `<strong>` in paragraph context
- Italic is the conventional typographic treatment for named proof steps in mathematical writing
- The visual hierarchy becomes clear: `<h3>` node title → `<strong>` structural section labels → `<em>` named proof steps within prose
- No changes needed to the existing section header markup

Implementation: add a markdown-inline pass in `_render_inline_code_html` (alongside the existing backtick → `<code>` pass) that converts `**...**` and `*...*` patterns to `<em>`. Order of operations: backtick spans first (they may contain `*`), then bold/italic.

### Already fixed (don't redo)
- MathJax flicker in SVG node text: fixed via `skipHtmlTags: ['svg']`
- Transparent node fill causing edge bleed-through: fixed by making fills fully opaque
- Backtick → `<code>` inline rendering in coverage notes and checklist text: fixed

---

## 7. License and Metadata

- Add a `LICENSE` file if one doesn't exist (MIT or Apache-2.0 recommended for an open research tool)
- Add a `CITATION.md` or `CITATION.cff` if citation is desired
- Review `pyproject.toml` / `setup.py` for correct metadata (name, description, author)
- Add a `.github/workflows/` CI file that runs `pytest -q` on pushes (optional but professional)

---

## 7. `.gitignore` Audit

Ensure the following are gitignored:
- `artifacts/` — all benchmark output
- `lean_project/.lake/` — Mathlib build cache (already large, should never be committed)
- `lean_project/FormalIslands/Generated/` — worker scratch files
- `lean_project/test_*.lean` — scratch Lean files
- `**/__pycache__/`, `.venv/`, `*.egg-info/` — Python build artifacts
- `.env`, `auth.json` — credentials

---

## Implementation Order

The phases below are ordered by dependency and risk. Each phase can be reviewed independently.

**Phase 1 — Cleanup** (low risk, do first)
- Remove stale files from `examples/`, `lean_project/`
- Audit and update `.gitignore`
- Rename `docs/` → `dev/` (or keep as-is and use `site/`)

**Phase 2 — Featured benchmarks**
- Create `examples/featured/` with renamed input JSONs
- Pick the definitive best run artifact for each featured benchmark
- Update README to reference featured examples

**Phase 3 — Report visual fixes**
- Render `**...**` / `*...*` as `<em>` in `_render_inline_code_html`
- Any other polish to the HTML generator that affects published reports

**Phase 4 — CLI rename + UX overhaul**
- Rename `formal-islands-smoke` → `formal-islands` (keep old name as alias if needed)
- Rename `smoke.py` → `cli.py` (or similar)
- Add smart defaults: workspace auto-discovery, output-dir auto-derivation, `--backends` shorthand
- Rename `run-benchmark` → `run`
- Implement `formal-islands new` interactive entry point
- Update README quick-start to use the new commands

**Phase 5 — Site + gallery**
- Regenerate `04_report.html` for each featured benchmark using the latest generator (picks up all visual fixes from Phase 3)
NOTE: Command needs to be something like "./.venv/bin/formal-islands-smoke report \
  --graph=/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run13-pinsker-via-bernoulli-core-gemini-aristotle-4/03_formalized_graph.json \
  --output-dir=/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run13-pinsker-via-bernoulli-core-gemini-aristotle-4 \
  --planning-backend=gemini" for example (need to specify planning-backend now)
- Create `docs/reports/` and copy in the featured HTML files
- Write `docs/index.html` landing page
NOTE: Site needs to look good, be clear and not too complicated, and clearly explain the project and what the issues it's tackling are / what the need for it is. The gallery for benchmarks etc can have placeholder images that I can fill in using screenshots of the report pages. 
- Configure GitHub Pages to serve from `docs/`

**Phase 6 — README + docs polish**
- Polish README with the new structure, using the new `formal-islands` command name
NOTE: Make README much more user-facing, also include information about the backends and how to install them or authenticate if needed (e.g. codex, gemini, claude code, aristotle), say the preferred combination in my experience is using codex/gemini/claude code as the planning_backend and aristotle as the formalization_backend. Also make sure to talk about the various flags and parameters for the command, default values, especially of stuff like timeouts. 
- Add screenshots / preview images once the Pages site is live
- Write `dev/INTERNALS.md` as a single home for design notes, run history, and integration plans

**Phase 7 — Metadata + CI**
- Add `LICENSE`
- Add `CITATION.cff` if desired
- Add basic CI workflow

---

## Open Questions

1. **Should `examples/manual-testing/` stay in the repo?**
   It's useful for developers rerunning benchmarks but adds clutter for readers. Options:
   - Keep it as-is (just a folder people can ignore)
   - Move it to `dev/benchmarks/` for cleanliness
   - Keep only `examples/featured/` and remove `manual-testing/` entirely from the public repo
NOTE: I think it should be kept, perhaps renamed at the end from "manual-testing" to something else OR moved to dev/benchmarks, but keeping all those files is important.

2. **Which exact run to use per featured benchmark?**
   For some benchmarks (e.g. run4) there are multiple strong runs. Pick the most recent clean one, or the one with the most verified nodes that are faithfully classified. See the candidate table in Phase 2.

3. **Should the reports on the Pages site be regenerated fresh each time (CI) or manually curated?**
   Manual curation is simpler for now. CI re-generation would require committing the Lean workspace or verifying without it, which is complex.
NOTE: Yes, manual curation, no CI.

4. **Domain name for the Pages site?**
   `<username>.github.io/formal-islands` is the default. A custom domain can be added later via `CNAME` file.
