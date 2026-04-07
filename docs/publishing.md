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

**Benchmarks to keep in `examples/manual-testing/` but not feature:**
- `run9_weak_poisson_lax_milgram.json` — good stress test but Sobolev spaces not in Mathlib; not a showcase
- `run3_full_glassey.json`, `run8_semilinear_heat_blowup.json` — partial successes, too mixed for public highlight

**Rename convention for featured examples:**
Use clean descriptive names, not run numbers:
- `heat_uniqueness.json`
- `two_point_log_sobolev.json`
- `vandermonde_convolution.json`
- `matrix_determinant_lemma.json`
- `dirichlet_eigenfunction.json`
- `harmonic_minimizer.json`

---

## 3. CLI UX Improvement — Interactive Entry Point

The current flow requires creating a JSON file manually before running anything. This is a barrier for new users.

**New command: `formal-islands-smoke new`** (or `formal-islands-smoke run-interactive`)

Behavior:
1. Prompts for theorem title (single line)
2. Prompts for theorem statement (multiline, end with blank line or `---`)
3. Prompts for raw proof text (multiline, same terminator)
4. Optionally prompts for output directory (default: auto-derived from title slug)
5. Writes a JSON input file to a temp or named location
6. Immediately calls `run-benchmark` with the generated JSON

This removes the friction of the JSON format entirely for the common case.

**Implementation notes:**
- Lives in `smoke.py` as a new `new` subcommand
- Uses `input()` calls with clear prompts; no external dependency
- After collecting input, calls the existing `run_benchmark` logic directly (no subprocess)
- The generated JSON is saved to the output dir as `input.json` alongside the run artifacts, so the user can inspect or rerun it

**Alternative / complement: stdin one-liner mode**
For scripting / CI usage, also support:
```bash
formal-islands-smoke run-benchmark --stdin
```
where the tool reads title, statement, and proof from stdin in a simple `---`-delimited format rather than requiring a pre-created JSON file.

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

## 6. License and Metadata

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

**Phase 3 — CLI `new` command**
- Implement `formal-islands-smoke new` interactive entry point
- Update README quick-start to use it

**Phase 4 — Site + gallery**
- Regenerate `04_report.html` for each featured benchmark using the latest generator (picks up all visual fixes)
- Create `docs/reports/` and copy in the featured HTML files
- Write `docs/index.html` landing page
- Configure GitHub Pages to serve from `docs/`

**Phase 5 — README + docs polish**
- Polish README with the new structure
- Add screenshots / preview images once the Pages site is live
- Write `dev/INTERNALS.md` as a single home for design notes, run history, and integration plans

**Phase 6 — Metadata + CI**
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

2. **Which exact run to use per featured benchmark?**
   For some benchmarks (e.g. run4) there are multiple strong runs. Pick the most recent clean one, or the one with the most verified nodes that are faithfully classified. See the candidate table in Phase 2.

3. **Should the reports on the Pages site be regenerated fresh each time (CI) or manually curated?**
   Manual curation is simpler for now. CI re-generation would require committing the Lean workspace or verifying without it, which is complex.

4. **Domain name for the Pages site?**
   `<username>.github.io/formal-islands` is the default. A custom domain can be added later via `CNAME` file.
