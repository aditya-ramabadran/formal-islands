# Formal Islands

Formal Islands is a prototype system for turning a natural-language mathematical proof into a **proof graph with formal islands**.

The input is deliberately simple:

* a theorem statement
* an **unstructured** informal proof in natural language

The system uses an LLM to extract structure from that prose, breaking it into nodes and edges that represent claims and dependencies. Some nodes remain informal. Some are selected as candidates for local formalization in Lean. When a node is successfully formalized and verified, the graph records both its informal and formal versions.

The goal is not full end-to-end formalization. The goal is to produce the strongest honest certification artifact currently available under Lean/Mathlib coverage, and to tell a human reviewer exactly what remains to be checked.

## Quickstart

The repository now includes a small smoke-test CLI for the current prototype:

* `formal-islands-smoke extract`
* `formal-islands-smoke select-candidates`
* `formal-islands-smoke formalize-one`
* `formal-islands-smoke report`
* `formal-islands-smoke run-example`

### One-time setup

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e '.[dev]'

cd lean_project
lake update
lake exe cache get
lake build
cd ..
```

Install Codex CLI separately and authenticate it before using the Codex backend:

```bash
codex --version
codex
```

Then choose **Sign in with ChatGPT** in the interactive Codex prompt, or set `CODEX_API_KEY` for non-interactive use.

### Verify Codex auth/config

```bash
test -f "${CODEX_HOME:-$HOME/.codex}/auth.json" && echo "codex auth present"
codex exec --skip-git-repo-check --sandbox read-only --output-schema /tmp/formal-islands-ping-schema.json --output-last-message /tmp/formal-islands-ping-output.json "Return a JSON object with ok=true"
cat /tmp/formal-islands-ping-output.json
```

Create the tiny schema file once before that ping:

```bash
cat > /tmp/formal-islands-ping-schema.json <<'EOF'
{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":false}
EOF
```

Expected output:

* `/tmp/formal-islands-ping-output.json`
* JSON of the form `{"ok": true}`

### End-to-end smoke run on the included smallest example

The smallest included raw-proof example is:

* [`examples/nonnegative_sum_input.json`](examples/nonnegative_sum_input.json)

You can run the whole current pipeline in one command:

```bash
./.venv/bin/formal-islands-smoke run-example \
  --backend codex \
  --input examples/nonnegative_sum_input.json \
  --output-dir artifacts/nonnegative-sum \
  --workspace lean_project \
  --max-attempts 1
```

Or run the stages explicitly:

```bash
./.venv/bin/formal-islands-smoke extract \
  --backend codex \
  --input examples/nonnegative_sum_input.json \
  --output-dir artifacts/nonnegative-sum

./.venv/bin/formal-islands-smoke select-candidates \
  --backend codex \
  --graph artifacts/nonnegative-sum/01_extracted_graph.json \
  --output-dir artifacts/nonnegative-sum

./.venv/bin/formal-islands-smoke formalize-one \
  --backend codex \
  --graph artifacts/nonnegative-sum/02_candidate_graph.json \
  --output-dir artifacts/nonnegative-sum \
  --workspace lean_project \
  --node-id auto \
  --max-attempts 1

./.venv/bin/formal-islands-smoke report \
  --graph artifacts/nonnegative-sum/03_formalized_graph.json \
  --output-dir artifacts/nonnegative-sum
```

Expected stage outputs:

* extraction:

  * `artifacts/nonnegative-sum/01_extracted_graph.json`
* candidate selection:

  * `artifacts/nonnegative-sum/02_candidate_graph.json`
* one formalization attempt:

  * `artifacts/nonnegative-sum/03_formalized_graph.json`
  * `artifacts/nonnegative-sum/03_formalization_summary.json`
  * generated Lean scratch file under `lean_project/FormalIslands/Generated/`
* report generation:

  * `artifacts/nonnegative-sum/04_report_bundle.json`
  * `artifacts/nonnegative-sum/04_report.html`

## Motivation

There is a real near-term gap between two extremes:

* powerful informal proofs from strong language models, which can be broad and useful but are hard to verify
* fully formal proofs, which give stronger guarantees but are bottlenecked by library coverage and formalization cost

The right near-term object is therefore not a binary “formal vs. informal” proof. It is a **mixed proof artifact**:

* formalize the local subclaims that are currently formalizable
* keep the rest informal
* make the trust boundary explicit
* generate a clear checklist of what a human still has to verify

This is especially compelling in areas like PDE. A large PDE proof may rely on infrastructure not yet present in Mathlib, but many local steps inside that proof can still be formalized today: explicit integrals, inequalities, finite-dimensional estimates, concrete computations, standard analysis lemmas, and related technical subclaims.

## Prototype scope

This repository is for **Prototype 1**.

That prototype should be deliberately narrow and reliable:

* input is raw theorem statement + raw informal proof text
* an LLM extracts a structured proof graph
* a second LLM pass selects candidate formal islands
* selected nodes are formalized locally in Lean if possible
* the final output is a graph plus a human review checklist

The first prototype should **not** try to be a general theorem-proving agent, a paper-scale autoformalizer, or a giant multi-agent system.

## Core graph semantics

The prototype core model is intentionally simple:

* **nodes**
* **edges**

Do **not** build formal components into the core model yet.

A future viewer may visually collapse adjacent formal nodes into components, but that is a later UI feature. The first version should get a single working formal island end to end before adding collapsibility complexity.

Do **not** build AND/OR proof-search semantics into the model either.

This prototype is not representing search. It is representing a **single completed proof**. So the intended meaning is:

* an edge means: **the parent node depends on the child node**
* if a node has multiple children, those children are understood **conjunctively by default**
* the parent node’s `informal_proof_text` explains how its child claims are combined

Optional edge labels may be useful for readability in the final report, but they should be treated as display metadata only, not as operational semantics.

## Node model

A node represents a mathematical claim.

Each node should store at least:

* `id`
* `title`
* `informal_statement`
* `informal_proof_text`
* `status`

  * one of: `informal`, `candidate_formal`, `formal_verified`, `formal_failed`
* `formalization_priority`

  * optional integer, e.g. `1` to `3`
* `formalization_rationale`

  * optional string
* `formal_artifact`

  * optional object, present only after a formalization attempt
* optional tags / metadata
* optional `display_label`

  * a freeform short label like `"case 1"` or `"technical estimate"` if useful for display

A node that has been formalized successfully still keeps its informal text. This is essential because Lean certifies the formal theorem, but the human still needs to verify that the formal theorem matches the intended informal claim.

## Edge model

Edges are **dependency edges**.

Each edge should store:

* `source_id`
* `target_id`
* optional `label`
* optional `explanation`

That is all.

There is no required edge-type taxonomy in the prototype. Labels like `"case 1"`, `"uses estimate"`, or `"reduction step"` are allowed, but they are display-only metadata and the code should not branch on them.

## Extraction contract

The user provides unstructured proof text, so the system needs an explicit extraction contract.

The first LLM stage should return a structured graph in this shape:

* `theorem_title`
* `theorem_statement`
* `root_node_id`
* `nodes`
* `edges`

Where each extracted node contains only the **informal graph** fields:

* `id`
* `title`
* `informal_statement`
* `informal_proof_text`
* optional `display_label`

and each edge contains:

* `source_id`
* `target_id`
* optional `label`
* optional `explanation`

At this extraction stage:

* every node starts as `status = informal`
* there is no `formal_artifact`
* there is no candidate/prioritization decision yet

That candidate-selection decision should be a **second stage**, not mixed into the first one.

## Candidate selection contract

The second LLM stage should read the extracted graph and return only candidate-formalization metadata.

Its output should look like:

* `candidates`

where each candidate contains:

* `node_id`
* `priority`

  * small integer, e.g. `1` low, `2` medium, `3` high
* `rationale`

The Python pipeline then updates those nodes from `informal` to `candidate_formal` and stores the priority/rationale.

For the prototype, candidate selection should strongly prefer:

* local technical nodes
* explicit computations
* inequalities
* self-contained analysis lemmas
* leaf-ish or low-dependency claims

## Formal artifact model

After a formalization attempt, a node may gain a `formal_artifact`.

That object should include at least:

* `lean_theorem_name`
* `lean_statement`
* `lean_code`
* `verification`

The `verification` object should include:

* `status`

  * one of: `not_attempted`, `verified`, `failed`
* `command`
* `exit_code`
* `stdout`
* `stderr`
* `elapsed_seconds`
* `attempt_count`

If a formalization fails after the bounded retry loop, the node should become `formal_failed` and still preserve the artifact/log data from the attempt.

## Formalization contract

The formalization LLM stage should also use an explicit contract.

For a single candidate node, the model should return a JSON object containing at least:

* `lean_theorem_name`
* `lean_statement`
* `lean_code`

Optional additional fields like notes are fine, but the prototype should not depend on them.

The `lean_code` should contain a theorem matching `lean_theorem_name`, and it should prove the `lean_statement` it claims to prove. The Python pipeline may store both the extracted `lean_statement` and the full `lean_code` without trying to deeply parse Lean syntax in the prototype.

## Human verification surface

One of the main outputs is a human-readable summary of what still needs to be checked.

At minimum, the system should produce three classes of review obligations:

### 1. Informal proof obligations

For each informal node, the human must check that the informal proof actually establishes the node’s informal statement, assuming its child nodes.

### 2. Formal semantic-match obligations

For each verified formal node, the human must check that the **informal statement** attached to the node matches the **formal Lean statement** that was actually proved.

Lean only guarantees the formal theorem. It does not guarantee that the formal theorem captures the intended informal claim.

### 3. Boundary obligations

Whenever an informal node depends on a formal child, the human must check that the formal child proves exactly what the informal parent is using.

This checklist is a first-class artifact of the project. The point is not only to formalize some subclaims, but to make the remaining review burden explicit and smaller.

## End-to-end prototype pipeline

The intended pipeline is:

1. **Input**

   * theorem statement
   * unstructured informal proof

2. **Structure extraction**

   * call an LLM to convert the proof into a structured graph
   * validate against the graph-extraction schema

3. **Candidate formal-island selection**

   * call an LLM to identify which nodes look formalizable under current Mathlib coverage
   * update node statuses and store priority/rationale

4. **Formalization attempts**

   * for selected nodes, call the formalizer to generate Lean code
   * verify locally with Lean
   * if verification succeeds, attach Lean artifacts to the node
   * if verification fails, preserve the failure state and logs

5. **Review-surface extraction**

   * deterministically derive review obligations from the graph

6. **Report generation**

   * emit graph JSON
   * emit a static HTML report
   * emit a checklist / review view
   * emit Lean artifacts and logs for formalized nodes

## Lean setup

This repo should **commit** a lightweight `lean_project/` skeleton containing:

* `lean-toolchain`
* `lakefile.toml` or `lakefile.lean`
* a minimal source tree for generated modules to live under

This repo should **not** commit:

* `.lake/`
* build outputs
* mathlib cache data
* generated scratch verification files

Those should be gitignored.

After cloning the repo, the expected setup is:

1. install `elan`
2. select stable Lean with `elan default leanprover/lean4:stable`
3. from `lean_project/`, run `lake update`
4. then run `lake exe cache get`
5. optionally run `lake build` once to confirm the workspace is healthy

For contributors who need to regenerate the Lean project from scratch, the relevant mathlib-style project creation pattern is `lake +v4.24.0 new <project_name> math`, followed by `lake update`. For this repository, though, the intended workflow is to **use the committed `lean_project/` skeleton**, not regenerate it each time.

## Lean local verification

The Lean story needs to be explicit.

Formalization should be local and deterministic.

### Verification strategy

The prototype formalizer should work like this:

1. take one candidate node
2. gather local context:

   * theorem statement
   * node informal statement
   * node informal proof text
   * maybe immediate parent/child summaries
   * maybe already-formalized child artifacts if they exist
3. prompt the LLM to generate Lean code
4. write the result to a scratch file inside the local Lean/Mathlib workspace
5. verify it using a local `lake`-based command
6. record success or failure
7. if failure, optionally retry a small bounded number of times using compiler feedback

For the prototype, formalization should stay **bounded and local**.

A good default is:

* formalize one node at a time
* use a small retry loop, e.g. 2 or 3 total attempts max
* pass compiler output back into the prompt on retry
* stop cleanly after the bound and preserve logs

### Lean command execution

The prototype verifier should likely use a local command such as:

* `lake env lean path/to/generated_file.lean`

or an equivalent deterministic verification command inside `lean_project/`.

The Python side should capture:

* exit code
* stdout
* stderr
* elapsed time
* file path of the generated artifact

Those become part of the node’s verification metadata.

## Local model backends

The project should support local CLI-based model backends behind a common interface.

At minimum, the repo should plan for:

* `MockBackend`
* `ClaudeCodeBackend`
* `CodexCLIBackend`

These are all backend implementations of the same higher-level tasks:

* graph extraction
* candidate selection
* node formalization

The core pipeline should not depend on one specific vendor CLI.

## Claude Code backend

Claude Code should be supported behind a narrow backend abstraction.

### Important principle

Claude Code is **not** treated here as a direct API dependency.

Instead, the intended model is:

* the user installs and authenticates Claude Code separately
* the project invokes the local `claude` executable as a subprocess
* prompts are sent through the CLI
* structured output is parsed back into the project’s backend response format

### Why support it

Claude Code is useful for local prototyping because it can provide a convenient local model backend without forcing the first prototype to depend on a separate API integration.

### Why not depend on it exclusively

The project should not assume:

* a specific local CLI version forever
* a specific authentication mode
* a specific Claude-only runtime

So the repository should define a backend interface and support it as one backend among several.

### Implementation note

A good reference for the Claude CLI wrapper approach is OpenProver’s `openprover/llm/claude.py`.

This project’s version should be much narrower:

* one-shot prompt calls
* structured JSON output
* clean subprocess error handling
* tests with mocked subprocess calls

Streaming, tool-calling, and complex session management are future work.

## Codex CLI backend

Codex CLI should also be supported behind the same backend abstraction.

Codex CLI is a local coding agent that can be installed with `npm install -g @openai/codex`, authenticated either with an API key or by signing in with ChatGPT, and run in non-interactive automation mode with `codex exec ...`.

### Important principle

Codex CLI is also **not** treated here as a direct SDK or API dependency.

Instead, the intended model is:

* the user installs and authenticates Codex CLI separately
* the project invokes the local `codex` executable as a subprocess
* the backend uses Codex’s non-interactive mode where appropriate
* prompts/results are normalized into the project’s internal backend response format

### Why support it

Codex CLI is a strong fit for this repository because:

* it is designed for local code-generation and coding-agent workflows
* it has a documented non-interactive mode for automation
* it can be used with either API-key auth or ChatGPT-linked auth
* it is a natural alternative to Claude Code for the same local-backend role

### Why still keep it narrow

The project should not assume more than what is clearly documented.

So the first implementation should:

* isolate all Codex subprocess behavior to one backend module
* detect whether `codex` exists on PATH
* fail gracefully with a helpful error if unavailable
* use mocked subprocess tests
* avoid speculative features unless the official repo/docs make them clear

### AGENTS.md note

Because Codex CLI supports project-level guidance through `AGENTS.md`, this repo may later add an `AGENTS.md` file with Formal Islands-specific backend instructions. That is optional for the first prototype, but the architecture should leave room for it.

## Report / viewer

The first viewer should be simple and useful.

It does not need a heavy frontend stack.

A good first version is a static HTML report that includes:

* theorem
* graph summary
* per-node detail sections
* clear marking of informal vs. verified-formal nodes
* a separate review checklist section
* links or expandable blocks for Lean code and compiler logs on formalized nodes

A future viewer may visually collapse adjacent formal nodes, but that should be postponed until the basic end-to-end prototype works.

## Suggested architecture

A good initial Python architecture is:

* `models/`

  * node / edge / graph / formal artifact / review obligation models

* `backends/`

  * backend protocol
  * mock backend
  * Claude Code subprocess backend
  * Codex CLI subprocess backend

* `extraction/`

  * prompt + schema for graph extraction from raw proof text
  * conversion from backend JSON into validated graph objects
  * candidate-selection prompt + schema

* `formalization/`

  * Lean workspace management
  * Lean verifier
  * bounded retry loop for node formalization
  * candidate-node formalization orchestration

* `review/`

  * deterministic review-obligation extraction

* `report/`

  * static HTML report generation
  * JSON export

* `cli/`

  * entrypoints for:

    * extract graph
    * select candidates
    * attempt formalization
    * generate report

## Testing philosophy

This repository should be built conservatively.

Requirements:

* deterministic modules should have real unit tests
* subprocess behavior should be mocked in tests
* Lean command execution should be abstracted enough to test cleanly
* report generation should have structural or snapshot tests
* unclear design questions should be documented rather than guessed through

The project should prefer:

* small tested increments
* explicit TODOs
* narrow interfaces
* clear artifacts

over:

* speculative end-to-end automation
* giant agent loops
* broad untested abstractions
* fake demos that do not verify anything locally

## Immediate milestones

The first serious build milestones should be:

1. package scaffolding
2. typed graph models with nodes and edges only
3. backend abstraction
4. Claude Code subprocess backend with mocked tests
5. Codex CLI subprocess backend with mocked tests
6. graph-extraction schema and pipeline from raw proof text
7. candidate-selection schema and pipeline
8. committed `lean_project/` skeleton plus local Lean verifier wrapper
9. bounded single-node formalization loop with mocked tests
10. deterministic review-obligation extraction
11. static HTML report generation
12. one or two example end-to-end fixtures

## Non-goals

Formal Islands is not trying to:

* fully formalize research mathematics
* solve arbitrary theorem proving end to end
* eliminate human review
* prove general semantic equivalence between informal math and Lean
* become a giant general-purpose coding-agent framework

It is trying to produce a mixed formal/informal proof artifact with a smaller, clearer, and more honest review burden.
