# Formal Islands Repository Guide

This document is the deep handoff note for a new Codex or Claude Code session.
It explains:

- what the repository is trying to do
- how the current architecture works
- where the important code lives
- what has been tried already
- what changed over time
- what the manual benchmark suite has shown
- where the current bottlenecks are
- how to work safely in the repo without rediscovering all the same context

It is intentionally more detailed than the README. The README is for normal usage.
This file is for agentic onboarding and continuation work.

## 1. Project Goal

Formal Islands is a prototype for mixed informal/formal mathematical proof review.

The guiding idea is:

- most interesting analysis/PDE/variational proofs are too large and too infrastructure-heavy to formalize end to end right now
- but many such proofs contain small, concrete, inferentially meaningful local steps that are good candidates for Lean formalization
- those local formal steps can reduce real human review burden if they are surfaced honestly

So the system tries to produce:

- a small, readable informal proof graph
- a small set of high-yield local formalization candidates
- one or more Lean-certified local “formal islands”
- an explicit HTML report that separates:
  - the human informal backbone
  - the Lean-certified local core
  - the remaining review obligations

This is not trying to be a full theorem prover.
It is trying to produce honest mixed artifacts with clean boundaries.

## 2. Core Design Principles

The repository has converged on a few strong design preferences.

### 2.1 Explicit artifacts over hidden state

We do not want one giant opaque persistent agent thread that silently mutates state.

Instead the system prefers:

- explicit JSON graph artifacts
- explicit Lean scratch files
- explicit verification logs
- explicit backend logs
- explicit HTML reports

Backend state is disposable; artifact state is the durable truth.

### 2.2 Global planning and local formalization are different jobs

The repository now treats theorem-level planning and local Lean formalization as separate roles.

Planning is:

- theorem-level
- graph-shaping
- candidate-ranking
- review-surface-aware

Formalization is:

- one-node-at-a-time
- local
- compiler-driven
- file-editing-oriented

This split has held up well in practice.

### 2.3 Concrete faithfulness matters more than abstract elegance

The system strongly prefers:

- concrete theorems in the same ambient setting as the node
- same variables / quantities / operators when possible
- local sublemmas that support the node honestly

It strongly dislikes:

- arbitrary `Type*`
- arbitrary measure spaces
- arbitrary Hilbert/inner-product spaces
- unrelated function families
- “mathematically related but semantically too distant” theorems

The current policy is intentionally asymmetric:

- the hard-coded heuristic guard only makes immediate rejections for the most obvious structural mismatches, especially blatant genericity like `Type*`
- borderline cases such as measure-space, inner-product-space, and dimension-downgrade signals are treated as advisory warnings
- the planning backend then makes the final semantic call on whether the theorem is still a faithful core, a smaller honest sublemma, or a real abstraction drift

This is a deliberate calibration choice. We want the local heuristics to be almost never wrong, and we want borderline semantics to be resolved by the planner instead of by fixed marker lists.

### 2.4 Underclaim rather than overclaim

One of the biggest lessons from the benchmark suite is:

- a smaller concrete certified local core is valuable
- but the report must not pretend it certifies the whole parent node

This repo now has explicit support for:

- `full_node`
- `concrete_sublemma`
- `over_abstract`

and uses those categories to change graph structure and report language.

## 3. Repository Map

The main package is:

- [src/formal_islands](/Users/adihaya/GitHub/formal-islands/src/formal_islands)

Important subpackages:

- [backends](/Users/adihaya/GitHub/formal-islands/src/formal_islands/backends)
- [extraction](/Users/adihaya/GitHub/formal-islands/src/formal_islands/extraction)
- [formalization](/Users/adihaya/GitHub/formal-islands/src/formal_islands/formalization)
- [models](/Users/adihaya/GitHub/formal-islands/src/formal_islands/models)
- [review](/Users/adihaya/GitHub/formal-islands/src/formal_islands/review)
- [report](/Users/adihaya/GitHub/formal-islands/src/formal_islands/report)
- [smoke.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/smoke.py)

Other important top-level areas:

- [examples/manual-testing](/Users/adihaya/GitHub/formal-islands/examples/manual-testing)
- [artifacts/manual-testing](/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing)
- [lean_project](/Users/adihaya/GitHub/formal-islands/lean_project)
- [tests](/Users/adihaya/GitHub/formal-islands/tests)

## 4. Data Model

The core graph model lives in:

- [models/proof.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/models/proof.py)

The main objects are:

- `ProofGraph`
- `ProofNode`
- `ProofEdge`
- `FormalArtifact`
- `VerificationResult`
- `FaithfulnessClassification`

The graph is intentionally simple:

- nodes
- directed dependency edges

No AND/OR search semantics, no proof objects, no rich theorem-diff system.

Current important node statuses:

- `informal`
- `candidate_formal`
- `formal_verified`
- `formal_failed`

Important faithfulness classifications on a formal artifact:

- `full_node`
- `concrete_sublemma`
- `over_abstract`

Current important edge label introduced by the newer honesty work:

- `formal_sublemma_for`

That edge means:

- a verified supporting child certifies a narrower concrete local core that the informal parent depends on

## 5. End-to-End Pipeline

The repository now has the following conceptual pipeline.

### 5.1 Theorem-level planning

This is the merged planning stage.

It is implemented primarily in:

- [extraction/pipeline.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/extraction/pipeline.py)
- [extraction/schemas.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/extraction/schemas.py)

The planning stage:

- reads theorem statement + raw proof text
- emits one planned graph plus candidate ranking in one backend call
- applies deterministic cleanup / calibration afterward
- leaves refinement to the later failure-driven formalization fallback path when a source node truly needs a smaller honest subclaim

Important outputs:

- `01_extracted_graph.json`
- `02_candidate_graph.json`

The merged planning stage replaced the earlier strict separation between:

- extraction
- candidate selection

That change was made because extraction and candidate selection were too tightly coupled in practice.

### 5.2 Formalization

The formalization subsystem lives in:

- [formalization/agentic.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/formalization/agentic.py)
- [formalization/lean.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/formalization/lean.py)
- [formalization/loop.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/formalization/loop.py)
- [formalization/pipeline.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/formalization/pipeline.py)
- [formalization/schemas.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/formalization/schemas.py)

There are two formalization modes:

- `structured`
- `agentic`

Current default is:

- `agentic`

The CLI also now splits backend selection into two independent knobs:

- `--planning-backend` for extraction / planning
- `--formalization-backend` for formalization

The legacy `--backend` flag still acts as a shared default when the split flags are omitted.

The agentic mode is the important one now.

It uses one one-shot backend worker run that can:

- write a real Lean scratch file
- write a plan markdown file
- run local Lean commands
- inspect compiler output
- revise the same file

It is still bounded:

- one backend process
- no persistent session memory
- one local file-editing session
- 7 minute timeout by default for formalization calls

Aristotle is a separate formalization-only backend:

- it uses Harmonic's Python SDK instead of the prompt/JSON worker contract
- it is only available for formalization, not planning
- it reads `ARISTOTLE_API_KEY` from the environment
- it defaults to no timeout unless the caller explicitly overrides one
- it logs project submission and completion metadata into the same backend log folder
- it sends a pruned Lean snapshot, not the entire workspace tree

The Aristotle prompt is intentionally plain text rather than a JSON payload of generated Lean code.
It contains:

- the ambient theorem statement as context only
- the target node's informal statement and informal proof text
- a local proof neighborhood split into:
  - verified supporting lemmas already certified in this run
  - verified direct child lemmas of the target node, listed explicitly so the worker can use all of them
  - context-only sibling ingredients in the same proof neighborhood

Verified supporting lemmas are included as text context with their theorem names and Lean statements when available.
They may be relied on as established facts for proof planning, but they are not auto-imported as generated Lean source files in the Aristotle snapshot.
The explicit verified-direct-child block is separate from the generic local neighborhood block because the prompt builders now surface all verified direct children, not just the first one, and the formalizer should be able to use that whole certified child set when assembling a parent theorem.
If Aristotle returns an `ARISTOTLE_SUMMARY_*.md` file, its contents are appended to `_progress.log` as part of the run record, but they are not printed to the terminal.

User-facing formalization is now agentic-only in the CLI. The older structured repair-loop path is retained only as an internal compatibility fallback for legacy callers and tests, not as a supported mode for normal use.

The formalization loop also now does more than just accept or reject a proof:

- the heuristic guard runs first and hard-rejects only the most obvious structural mismatches
- borderline measure-space, inner-product-space, and dimension-downgrade signals are treated as advisory and passed to the planner for the final semantic call
- after verification, the planning backend can classify the Lean theorem against the target node and decide whether the result is already the full match, only a faithful core, a downstream consequence, a dimensional analogue, or just a helper shard
- that same combined semantic review also decides whether bounded coverage expansion is worthwhile
- if all direct children of an informal parent are verified, the loop can now ask the planning backend whether the parent should be promoted into the candidate set as a parent-assembly theorem
- if a failure occurs, repair guidance is built from a cheap heuristic diagnosis plus an optional planning-backend semantic diagnosis
- if a verified concrete sublemma is still a genuine candidate for more coverage, the loop can make one bounded bonus retry on the main proof path

When `formalize-all-candidates` uses Aristotle, the jobs are submitted in parallel batches. Newly promoted parents from a verified concrete sublemma are picked up by the next batch rather than waiting for a fully sequential pass.

This backend is intended for theorem-statement formalization runs where the host pipeline already knows the target node and wants project-level Lean proof completion rather than a chat-style JSON response.

### 5.3 Faithfulness classification

After a formal artifact is produced and verified, the pipeline classifies it as:

- `full_node`
- `concrete_sublemma`
- `over_abstract`

This logic lives in:

- [formalization/pipeline.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/formalization/pipeline.py)

Important point:

- this is currently deterministic and heuristic
- it is not LLM-based

This was strengthened significantly after the benchmark suite exposed two failure modes:

- useful concrete narrowing was being rejected too harshly
- narrower formal cores were being overreported as full-node formalizations

The current faithfulness path now has a two-layer shape:

1. **Heuristic first pass**
   - catches only the most obvious over-abstraction
   - is intentionally conservative and is mostly a cheap warning layer
   - hard-rejects the truly blatant structural mismatches, especially `Type*`-style genericity and unrelated function-family swaps
   - records borderline signals such as measure-space, inner-product-space, and dimension-downgrade hints, but does not try to make the final semantic decision on those
   - still lets the planning backend make the final call on whether a borderline theorem is a faithful core or a real analogue

2. **Planning-backend semantic review**
   - runs only after a verified Lean artifact exists
   - asks whether the theorem is a full match, a faithful core, a downstream consequence, a dimensional analogue, or a helper shard
   - records a coverage score and a short reason
   - is used to decide whether coverage expansion is warranted and whether a later retry is worth attempting

The result of that semantic review is stored in `faithfulness_notes` in a structured text form so the report can show more specific labels than the old generic “concrete sublemma” wording. The heuristic layer can still annotate the artifact with advisory notes, but the planner is the final semantic judge for borderline cases.

### 5.4 Review extraction

Review obligations are derived deterministically in:

- [review/extractor.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/review/extractor.py)

The review stage turns the graph into explicit human obligations like:

- informal proof check
- semantic match check
- boundary/interface check

The boundary logic now understands supporting formal sublemmas more honestly.

### 5.5 Report generation

The static report generator lives in:

- [report/generator.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/report/generator.py)

Outputs:

- `04_report_bundle.json`
- `04_report.html`
- `_progress.log`

The shared progress log is append-only. Re-running a later stage, such as report generation, adds new lines to the existing `_progress.log` rather than truncating it.
When a graph artifact is generated or materially updated, the run log also records a compact node/edge preview so the output directory keeps a readable trace of the graph as it evolves.
Planning-backend semantic assessments and Aristotle summary markdown files are appended there too, so the run log contains the main semantic judgments without spamming the terminal.

The report now supports:

- clickable SVG graph
- improved graph sizing
- proper text wrapping
- inline code rendering in math-ish text
- automatic dark mode
- better honesty for certified local core nodes
- dashed gray refinement edges that still point to proof dependencies, with all arrows standardized as dependency arrows
- node-detail labels that distinguish faithful cores, downstream consequences, dimensional analogues, and similar narrower outcomes

## 6. CLI Entry Point

The user-facing CLI is:

- [smoke.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/smoke.py)

The key commands are:

- `plan`
- `formalize-one`
- `formalize-all-candidates`
- `report`
- `run-benchmark`

Current practical default flow for a benchmark is usually:

```bash
./.venv/bin/formal-islands-smoke run-benchmark \
  --backend codex \
  --input /Users/adihaya/GitHub/formal-islands/examples/manual-testing/run11_two_point_log_sobolev.json
```

Important current behavior:

- `formalize-one` formalizes one chosen candidate node
- `formalize-all-candidates` runs candidates through the updated graph, and with Aristotle it submits each batch in parallel before merging the results back into the shared graph
- `run-benchmark` is the one-command smoke path and, when `--node-id auto`, it now keeps walking the graph in priority order instead of stopping after the first success
- `--formalization-timeout-seconds` controls the agentic backend timeout for the formalization step
- The CLI now exposes agentic formalization only; the old structured repair-loop mode is deprecated and kept only as an internal compatibility path

## 7. Backend Layer

The backend layer lives in:

- [backends/base.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/backends/base.py)
- [backends/codex_cli.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/backends/codex_cli.py)
- [backends/claude_code.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/backends/claude_code.py)
- [backends/gemini_cli.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/backends/gemini_cli.py)
- [backends/mock.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/backends/mock.py)

### 7.1 Codex backend

The Codex backend is the most exercised backend so far.

It supports:

- structured output mode
- one-shot agentic mode
- timeouts
- backend logging
- structured schema validation through CLI flags

Important current behavior:

- planning/backend default timeout: 180s
- formalization/backend default timeout: 420s

### 7.2 Claude backend

The Claude backend now exists as a real parallel backend rather than a stub.

It supports:

- structured output mode
- agentic mode
- timeout handling
- backend logging
- executable discovery including common local install locations
- smoke CLI integration

It has now been exercised in the benchmark suite in this repo alongside Codex and Gemini.

So it should be treated as:

- implementation-ready
- benchmarked

### 7.3 Gemini backend

The Gemini backend also exists as a real parallel backend.

It supports:

- structured output mode
- agentic mode
- stream-json handling
- yolo approval mode for the Lean worker path
- backend logging
- smoke CLI integration

The Gemini adapter is now used both for planning and for agentic formalization runs, with prompt guidance tuned to avoid tiny fallback shards and to keep the worker moving toward a faithful theorem shape.

### 7.4 Backend logs

Each run writes backend logs in the run folder under:

- `_backend_logs/*.json`

These logs are important.
They preserve:

- prompts
- system prompts
- schema
- command
- cwd
- elapsed seconds
- stdout/stderr
- structured payload
- status: started/completed/failed/timeout

This logging became important after full-Glassey runs hung and it was hard to reconstruct exactly what had been sent.

There is also:

- [artifacts/manual-testing/_backend_logs_sublemma_summaries](/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/_backend_logs_sublemma_summaries)

for concrete-sublemma summary calls that were run in place on existing artifacts.

## 8. Lean Workspace

The local Lean workspace is:

- [lean_project](/Users/adihaya/GitHub/formal-islands/lean_project)

Generated worker/scratch files live in:

- [lean_project/FormalIslands/Generated](/Users/adihaya/GitHub/formal-islands/lean_project/FormalIslands/Generated)

Important file patterns:

- `<node>_worker_<timestamp>_<suffix>.lean`
- `<node>_worker_<timestamp>_<suffix>_plan.md`
- `<node>_attempt_<k>.lean`

The verifier logic lives in:

- [formalization/lean.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/formalization/lean.py)

The main verification command is:

- `lake env lean <scratch-file>`

The pipeline now also has salvage/recovery logic:

- if an agentic backend run times out or fails after leaving a usable scratch file
- the system can attempt to recover a `FormalArtifact` from that file
- locally verify it
- and still write back `03_*` artifacts

This was added because some runs produced useful Lean files but failed to complete the backend structured handoff.

### 8.1 External Mathlib search helper

The worker search bottleneck has been pushed out of Lean itself.

The repository now has a dedicated retrieval layer in:

- [mathlib_search.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/mathlib_search.py)

It supports:

- Loogle exact-shape search
- LeanSearch natural-language search
- a local `formal-islands-search` CLI helper for highly targeted follow-up search outside Lean

Example direct use:

```bash
./.venv/bin/formal-islands-search --query "Real.log, Real.sqrt" --provider loogle
```

The important policy is:

- the formalization prompts mention the helper, but the host pipeline does not precompute and inject search bundles by default
- if more search is truly needed, keep it to at most 2 targeted helper queries
- prefer one exact Loogle-shaped query and one LeanSearch natural-language query

The formalization prompts also carry an explicit proof-neighborhood split:

- verified supporting lemmas may be relied on as established facts
- context-only sibling ingredients are only orientation and should not be assumed
- every graph arrow is a dependency edge; refinement-style edges are just narrower dependencies

Aristotle-specific upload pruning:

- include the Lean project skeleton and the active scratch file
- exclude `.lake` build artifacts
- exclude the `FormalIslands/Generated` backlog except for the active scratch file
- exclude `test_*.lean` files at the workspace root

## 8.2 Split planning and formalization backends

The runtime now lets planning and formalization use different backends.

That matters because:

- planning wants extraction-quality graph and candidate ranking
- formalization wants a proof worker optimized for Lean file generation and repair

The new CLI shape is:

- `--planning-backend` for planning / graph extraction
- `--formalization-backend` for formalization

This makes combinations like `claude` for planning and `aristotle` for formalization possible without changing the planning stage.

The planning backend is also still used when a verified result is only a concrete supporting sublemma, so the prose summary for that certified local core can be written by the planning model while Aristotle focuses on Lean proof production.

## 9. Evolution of the Architecture

This section matters because many current code paths are the result of benchmark-driven iteration.

### 9.1 Early state

Originally the pipeline was more strictly staged:

1. extraction
2. deterministic simplification
3. candidate selection
4. deterministic refinement
5. structured formalization of one node
6. Lean verification
7. structured repair loop
8. report

This worked, but several problems emerged:

- extraction could overcompress away good formal islands
- candidate selection only saw the post-compression graph
- structured formalization was awkward for compiler-driven revision
- the repair loop was clumsy compared to editing a real file

### 9.2 Merged planning stage

The repository moved to a merged theorem-level planning stage:

- one backend planning call
- explicit graph + candidate ranking
- then deterministic cleanup/refinement/calibration

This improved graph quality and candidate quality substantially.

### 9.3 Agentic formalization worker

Formalization moved from:

- repeated structured JSON theorem generation

to:

- a one-shot agentic file-editing worker

This was a major change.

It allowed:

- real scratch file editing
- real Lean reruns
- local API scouting and planning
- better recovery from compiler issues

The current search strategy changed after repeated benchmark runs showed that broad grep-based scouting was too slow and too noisy. The worker is now told about the local `formal-islands-search` helper and a strict retrieval budget, but the host pipeline does not precompute or inject search hints by default. The prompt still tells it to commit to a theorem shape sooner.

### 9.4 Planning markdown file

The worker used to “start coding immediately.”

That was too brittle.

The worker now begins with a small markdown plan file that records:

- target theorem
- ambient setting
- key symbols to preserve
- abstractions to avoid
- theorem shape
- proof route
- likely Mathlib lemmas/APIs

If it changes direction substantially, it appends a new labeled section.

### 9.5 Stronger output-shape discipline

The worker had a tendency to fail on dumb surface-syntax issues.

So explicit prompt constraints were added:

- prefer ASCII identifiers
- avoid Unicode binder names like `λ₁`
- prefer `lambda1`
- keep theorem signatures syntactically conservative
- prefer boring Lean syntax over fancy notation

### 9.6 Honest supporting sublemmas

One of the most important architectural changes:

- useful concrete local cores should not be rejected
- but they also should not be reported as full-node success

This led to the current `concrete_sublemma` support:

- parent stays informal
- verified supporting child is inserted
- dependency edge is added
- review/report language becomes honest

### 9.7 Bounded coverage expansion attempt

Another later change:

- after a verified `concrete_sublemma`
- the system may try exactly one bounded follow-up expansion attempt

The idea is:

- do not treat the first verified local core as always terminal
- sometimes it is a foothold toward fuller node coverage

Current behavior:

- one extra bounded attempt
- if it reaches `full_node`, great
- otherwise keep the original verified core

Important nuance:

- if the bounded attempt starts from a recovered agentic scratch file, the recovered artifact can still be expanded as long as it verifies as a concrete sublemma

### 9.8 Hybrid refined local claims

The old refined-local-claim extraction used to run eagerly during planning.
That was too speculative: it could surface tiny bookkeeping facts before the main node had even been tried.

The current direction is fallback-only:

- try the best whole node first
- only if that node fails in a meaningful way should the loop consider one smaller honest subclaim
- the refinement request is anchored to the source node being refined, not to a neighboring sibling that merely shares vocabulary
- the backend may propose 1 to 3 narrower concrete local claims
- rank the proposals deterministically and keep the best valid one
- fall back to the original span/window extractor if no proposal is usable

This keeps refinement tied to a real failure mode and reduces the chance that a clipped fragment becomes a first-class candidate.

Two refinements make that path more robust:

- if the backend certifies a narrow local claim, the deterministic loop can later promote the broader parent node once all of its direct children are verified, so a successful core can seed a second pass upward
- the proposal ranker and deterministic span fallback penalize point-evaluation fragments and substitution-only facts when they look like isolated snapshots rather than reusable theorems

## 10. How the Current Faithfulness Classifier Works

The classifier is deterministic and heuristic.

It does not use an LLM.

It currently does three-way classification:

- `over_abstract`
- `concrete_sublemma`
- `full_node`

The logic lives in:

- [formalization/pipeline.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/formalization/pipeline.py)

High-level behavior:

1. reject clear abstraction drift
2. compute a rough coverage score
3. detect narrower-but-concrete sublemmas
4. otherwise accept full-node

Important drift signals:

- arbitrary `Type*`
- arbitrary measure spaces
- arbitrary inner-product/normed spaces
- multiple fresh unrelated function families

Important concrete-sublemma signals:

- fresh placeholder variables / functions not present in node text
- loss of concrete markers from a concrete node
- undercoverage relative to the complexity of the parent node
- omitted named ingredients
- low coverage for a broad multi-step node

Important current lesson:

- the classifier is useful and testable
- but still heuristic
- it is better as a deterministic floor than as a perfect semantic judge

Borderline cases now go through the combined semantic review described above; the old idea of a separate optional second-opinion classifier has effectively been absorbed into that planner-led check.

## 11. Prompt Strategy for the Agentic Worker

The agentic prompt has changed a lot.

Current important prompt intentions:

- preserve ambient setting
- preserve key symbols / quantities
- avoid over-abstraction
- start from the most literal whole-node theorem shape
- only fall back to a concrete sublemma after the main node has genuinely failed
- document fallback in the plan file
- the local `formal-islands-search` helper is available if it truly needs extra retrieval
- if more search is truly needed, do at most 2 additional targeted searches with `formal-islands-search`
- prefer one exact Loogle-shaped query and one LeanSearch natural-language query
- explicitly use the workspace's real Mathlib location under `.lake/packages/mathlib/Mathlib`
- use one designated main theorem plus helper lemmas if needed
- include a lightweight coverage sketch so the worker can see the node's internal proof components
- if the current source node is too broad after a failed attempt, carve out a smaller subclaim from that source node rather than from a neighboring sibling
- do not produce trivial substitution lemmas or bare point evaluations as refined claims

This “one main theorem plus helpers” requirement was added after Run 11 exposed confusion where the file contained two theorems:

- one helper
- one more substantial result

and the report/recovery path had initially picked the wrong one.

Current behavior now is:

- the worker must return the intended main theorem name
- the file may contain helper lemmas
- artifact extraction/recovery tries to align to the intended main theorem, not just the first declaration in the file
- after a verified concrete sublemma, the loop makes one bounded coverage-expansion attempt from the verified file rather than treating the first successful core as terminal
- when all direct children of an informal parent are verified, that parent may be promoted into the candidate set on a later dynamic pass
- timestamped worker and plan filenames avoid collisions when the same node is revisited

## 12. Manual Benchmark Suite

This section records the broad benchmark history and the main lessons.

The inputs live in:

- [examples/manual-testing](/Users/adihaya/GitHub/formal-islands/examples/manual-testing)

The saved artifacts live in:

- [artifacts/manual-testing](/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing)

### Run 1: weighted L2 toy

Status:

- early sanity/smoke test

Purpose:

- prove the pipeline works at all
- simple local estimate style example

Lesson:

- useful as a small harness
- not representative of the project’s true target use case

### Run 2: reduced Glassey

Files:

- [run2_reduced_glassey.json](/Users/adihaya/GitHub/formal-islands/examples/manual-testing/run2_reduced_glassey.json)
- [run2-reduced-glassey](/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run2-reduced-glassey)

Status:

- one of the strongest benchmarks in the suite

Purpose:

- PDE-flavored theorem with meaningful local formal island
- still informal globally

Main lesson:

- this remains one of the best “north star” benchmarks
- import guidance and better formalization behavior materially helped here

This benchmark is still arguably the best overall demonstration artifact.

### Run 3: full Glassey

Files:

- [run3_full_glassey.json](/Users/adihaya/GitHub/formal-islands/examples/manual-testing/run3_full_glassey.json)
- [run3-full-glassey](/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run3-full-glassey)

Status:

- much harder

Main lessons:

- backend latency/hanging became a real problem
- structured logging was necessary
- the bottleneck shifted from Lean import problems to backend generation difficulty

This benchmark was important for operational robustness:

- backend timeout logging
- better debugging artifacts

### Run 4: heat uniqueness

Files:

- [run4_heat_uniqueness.json](/Users/adihaya/GitHub/formal-islands/examples/manual-testing/run4_heat_uniqueness.json)
- [run4-heat-uniqueness](/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run4-heat-uniqueness)

Status:

- clear success

Purpose:

- small PDE proof with an obvious energy identity / monotonicity island

Main lessons:

- graph quality improved significantly under merged planning
- very good benchmark for “small, clean, real” behavior
- still one of the best sanity-check benchmarks

### Run 5: weak maximum principle via negative part

Files:

- [run5_negative_part_maximum_principle.json](/Users/adihaya/GitHub/formal-islands/examples/manual-testing/run5_negative_part_maximum_principle.json)
- [run5-negative-part-maximum-principle](/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run5-negative-part-maximum-principle)

Status:

- partial success

Purpose:

- test whether the worker would target the right local core instead of trivial positivity facts

Main lessons:

- planning/candidate choice were strong
- the worker still tended to drift to more abstract measure-space style theorems
- this run helped motivate stronger anti-abstraction pressure

### Run 6: weak solution equals unique minimizer of Dirichlet energy

Files:

- [run6_dirichlet_minimizer.json](/Users/adihaya/GitHub/formal-islands/examples/manual-testing/run6_dirichlet_minimizer.json)
- [run6-dirichlet-minimizer](/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run6-dirichlet-minimizer)

Status:

- good benchmark, partial success

Purpose:

- test whether the system could preserve meaningful variational local cores

Main lessons:

- graph was excellent
- candidate ranking was good
- worker still generalized too far in some versions
- this benchmark helped motivate the stronger concrete/local-core classification work

### Run 7: harmonic minimizer

Files:

- [run7_harmonic_minimizer.json](/Users/adihaya/GitHub/formal-islands/examples/manual-testing/run7_harmonic_minimizer.json)
- [run7-harmonic-minimizer](/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run7-harmonic-minimizer)

Status:

- very useful benchmark

Purpose:

- test a strong energy decomposition local core inside a broader variational theorem

Main lessons:

- initially exposed the “useful concrete narrowing gets rejected” problem
- later became a good example of honest supporting-sublemma behavior

### Run 8: semilinear heat blow-up

Files:

- [run8_semilinear_heat_blowup.json](/Users/adihaya/GitHub/formal-islands/examples/manual-testing/run8_semilinear_heat_blowup.json)
- [run8-semilinear-heat-blowup](/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run8-semilinear-heat-blowup)

Status:

- one of the strongest post-Glassey benchmarks

Purpose:

- stronger PDE benchmark with multiple possible local energy-method islands

Main lessons:

- showed that the system could verify a meaningful concrete local core
- also exposed the overclaiming problem when the Lean theorem covered only the back half of a larger node
- drove the “supporting child node” design

### Run 9: weak Poisson via Lax–Milgram

Files:

- [run9_weak_poisson_lax_milgram.json](/Users/adihaya/GitHub/formal-islands/examples/manual-testing/run9_weak_poisson_lax_milgram.json)
- [run9-weak-poisson-lax-milgram](/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run9-weak-poisson-lax-milgram)

Status:

- informative but weaker outcome

Purpose:

- test whole-node local estimate formalization pressure

Main lessons:

- planning preserved the right local estimate nodes
- the worker still wanted to drift toward abstract Hilbert/Lp-style packaging
- this benchmark remains useful as a stress test for “literal whole-node first” discipline

### Run 10: first Dirichlet eigenfunction

Files:

- [run10_first_dirichlet_eigenfunction.json](/Users/adihaya/GitHub/formal-islands/examples/manual-testing/run10_first_dirichlet_eigenfunction.json)
- [run10-first-dirichlet-eigenfunction](/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run10-first-dirichlet-eigenfunction)

Status:

- stronger than Run 9, but still partial

Purpose:

- test whole local variational/eigenvalue node formalization

Main lessons:

- output-shape discipline mattered
- Unicode/notation mistakes can derail otherwise good attempts
- the worker could get close to a faithful whole node but still stop at a narrower certified core

### Run 11: two-point logarithmic Sobolev inequality

Files:

- [run11_two_point_log_sobolev.json](/Users/adihaya/GitHub/formal-islands/examples/manual-testing/run11_two_point_log_sobolev.json)
- [run11-two-point-log-sobolev](/Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run11-two-point-log-sobolev)

Status:

- strong benchmark
- important architecture benchmark

Purpose:

- global theorem still nontrivial
- local convexity/scalar calculus island is extremely natural

Main lessons:

- candidate ranking was good
- initial full-node classification was too generous
- this benchmark directly motivated:
  - stricter full-node vs supporting-core classification
  - honest dependency-graph structure with `formal_sublemma_for` labels where useful
  - one-main-theorem plus helper-lemmas discipline

It is now one of the cleanest benchmarks for:

- good planning
- good candidate choice
- useful but narrower certified local core
- honest reporting

### Run 12

There is currently an input file:

- [run12_ito_taylor_expansion.json](/Users/adihaya/GitHub/formal-islands/examples/manual-testing/run12_ito_taylor_expansion.json)

Run 12 is now characterized in the repository history:

- the extracted graph isolates the deterministic Taylor island and a secondary stochastic/convergence candidate
- Gemini's deterministic-node attempt found the right Taylor neighborhood but still timed out before producing a finished theorem
- the stochastic node search drifted too broadly into infrastructure-heavy probability/measure theory

The main lesson is still current:

- the benchmark choice is good
- the worker still needs better theorem-shape commitment and better search discipline

## 13. UI / Report Lessons

The report generator changed a lot due to benchmark inspection.

Important fixes already made:

- graph height bug that hid bottom nodes
- rightward page overflow due to long math/code content
- inline code rendering inside node text
- dark mode support
- dark-mode fixes for graph background, checklist text, and Lean code visibility
- graph styling now treats `candidate_formal` as still visually informal unless the node actually carries a Lean artifact

Important takeaway:

- the HTML report is part of the core product, not an afterthought
- many architecture issues only became obvious by reading the report artifacts

## 14. Current Strengths

At the current repository state, these things are working reasonably well:

- merged theorem-level planning
- compact readable graphs
- candidate ranking usually picks the right node first
- one-shot agentic worker is much better than the old structured loop for many tasks
- backend logging/debuggability
- honest support-node representation for certified local cores
- report usability is much better than earlier
- Claude and Gemini backends are real, usable parallel options
- external Mathlib retrieval is now built in before formalization starts

## 15. Current Weaknesses / Bottlenecks

The main remaining bottlenecks are:

### 15.1 Worker still drifts too far on harder whole-node targets

Especially on:

- functional analysis flavored local nodes
- whole-node local estimates that invite abstraction

### 15.2 Coverage classification uses a heuristic floor plus planner review

It is much better than before, but it is still heuristic.

This is good enough for a prototype, but borderline cases are now intentionally handed to the planning backend rather than being decided solely by the heuristic layer.

### 15.3 Whole-node local formalization is still near the frontier

The system is best when:

- there is one obvious local island
- the theorem is concrete
- the local theorem can be made Lean-natural without huge infrastructure

It is weaker when:

- the local node wants many layers of infrastructure
- the natural Lean theorem shape is still fairly broad

### 15.4 Backend parity is newer for Claude

Claude backend implementation is now largely ready, Gemini backend support is also in place, and Aristotle is available as an optional formalization-only backend. The remaining question is not backend availability, but how well each worker commits to a good theorem shape once it has enough local context and, if needed, a couple of tightly targeted search results from the helper.

## 16. Practical Commands for Future Sessions

### Run a benchmark in one command

```bash
./.venv/bin/formal-islands-smoke run-benchmark \
  --backend codex \
  --input /Users/adihaya/GitHub/formal-islands/examples/manual-testing/run11_two_point_log_sobolev.json
```

For Claude:

```bash
./.venv/bin/formal-islands-smoke run-benchmark \
  --backend claude \
  --input /Users/adihaya/GitHub/formal-islands/examples/manual-testing/run11_two_point_log_sobolev.json
```

For split planning/formalization, including Aristotle:

```bash
./.venv/bin/formal-islands-smoke run-benchmark \
  --planning-backend claude \
  --formalization-backend aristotle \
  --input /Users/adihaya/GitHub/formal-islands/examples/manual-testing/run11_two_point_log_sobolev.json
```

### Plan only

```bash
./.venv/bin/formal-islands-smoke plan \
  --backend codex \
  --input /Users/adihaya/GitHub/formal-islands/examples/manual-testing/run11_two_point_log_sobolev.json \
  --output-dir /Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run11-two-point-log-sobolev
```

### Formalize one node

```bash
./.venv/bin/formal-islands-smoke formalize-one \
  --backend codex \
  --graph /Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run11-two-point-log-sobolev/02_candidate_graph.json \
  --output-dir /Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run11-two-point-log-sobolev \
  --workspace /Users/adihaya/GitHub/formal-islands/lean_project \
  --node-id n3 \
  --formalization-mode agentic
```

### Formalize all candidates

```bash
./.venv/bin/formal-islands-smoke formalize-all-candidates \
  --backend codex \
  --graph /Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run11-two-point-log-sobolev/02_candidate_graph.json \
  --output-dir /Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run11-two-point-log-sobolev \
  --workspace /Users/adihaya/GitHub/formal-islands/lean_project \
  --formalization-mode agentic
```

### Report only

```bash
./.venv/bin/formal-islands-smoke report \
  --graph /Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run11-two-point-log-sobolev/03_formalized_graph.json \
  --output-dir /Users/adihaya/GitHub/formal-islands/artifacts/manual-testing/run11-two-point-log-sobolev
```

### Useful test slices

```bash
./.venv/bin/python -m pytest tests/test_backends.py tests/test_smoke.py tests/test_lean_formalization.py -q
```

```bash
./.venv/bin/python -m pytest tests/test_extraction_pipeline.py tests/test_formalization_pipeline.py tests/test_review_and_report.py -q
```

## 17. Suggested Priorities for a Future Session

If a fresh agent session starts from this repo state, the best likely next tasks are:

1. benchmark the Claude backend on real runs
2. tighten whole-node-first worker behavior on harder local estimate nodes
3. keep the planning backend as the final semantic judge for borderline faithfulness / coverage calls
4. continue improving benchmark quality rather than adding speculative infrastructure

## 18. Things Explicitly Not Wanted Right Now

The repo has repeatedly rejected these directions for now:

- giant opaque persistent-thread theorem prover
- theorem-drift / semantic-audit heavy machinery
- live Mathlib-aware planning/search infrastructure
- AND/OR proof-search semantics
- heavy formal components / large UI redesign
- overengineered semantic scoring systems without tests

The current philosophy is:

- simple artifacts
- local honesty
- bounded agentic work
- benchmark-driven iteration

## 19. Final Mental Model

The best short mental model for the current repository is:

- one theorem-level planner creates a small, review-friendly graph
- one local agentic worker tries to formalize a meaningful node
- Lean verifies what it can
- the classifier decides whether that certification is:
  - full-node
  - narrower supporting core
  - or too abstract
- the graph/report then surface that boundary honestly

That is what Formal Islands currently is.

## 20. Current Operational Semantics in Detail

This section is the most important one if you want to understand what the system actually does today when a run succeeds, partially succeeds, or fails for Lean-engineering reasons.

The main files to read alongside this section are:

- [smoke.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/smoke.py)
- [formalization/loop.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/formalization/loop.py)
- [formalization/pipeline.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/formalization/pipeline.py)
- [formalization/lean.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/formalization/lean.py)
- [formalization/aristotle.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/formalization/aristotle.py)
- [backends/aristotle.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/backends/aristotle.py)
- [progress.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/progress.py)

### 20.1 Command-level orchestration

The CLI entry points in [smoke.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/smoke.py) are not symmetric.

- `cmd_plan(...)`
  - builds the initial theorem graph and candidate ranking
  - writes `01_extracted_graph.json` and `02_candidate_graph.json`
  - does not catch planning-backend failures
  - so if Claude/Codex/Gemini run out of usage or otherwise fail during planning, the command normally aborts
- `cmd_formalize_one(...)`
  - formalizes exactly one chosen candidate
  - catches backend failures and writes failure artifacts instead of silently dying
  - still writes `03_formalized_graph.json` and `03_formalization_summary.json`
- `cmd_formalize_all_candidates(...)`
  - walks candidate nodes in priority order
  - for Aristotle, submits jobs in parallel batches and then merges the results back into the shared graph
  - is the main “try multiple nodes” formalization path
- `cmd_run_benchmark(...)`
  - runs planning, formalization, and reporting end-to-end
  - if planning fails, the run usually fails immediately
  - if a single node fails in formalization, the run may still produce a graph and report with failure artifacts

The overall orchestration is intentionally explicit:

- planning creates graph artifacts
- formalization mutates candidate nodes into verified or failed nodes
- report generation reads the graph artifact and review obligations

### 20.2 What happens when the planning backend fails

The planning backend is used for:

- theorem-level graph planning
- candidate ranking
- local proof-neighborhood context
- combined semantic review of verified formal artifacts
- retry diagnosis
- coverage-expansion gating

There are two very different failure regimes:

1. **Planning stage failure**
   - `cmd_plan(...)` does not swallow backend errors
   - `cmd_run_benchmark(...)` also does not swallow backend errors around the planning stage
   - so a Claude token/usage failure, CLI timeout, or backend invocation error during planning is usually fatal for the run
2. **Formalization-stage advisory failure**
   - helper calls inside the formalization loop catch `BackendError`
   - if the planning backend fails during those calls, the code falls back to heuristics or skips the advisory action
   - the run often continues, just with less semantic guidance

The key advisory functions live in [formalization/loop.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/formalization/loop.py):

- `_apply_combined_verification_assessment(...)`
  - asks the planning backend to compare a verified Lean theorem against the target node
  - if the call fails, the code returns the existing artifact and `None`
- `_request_planning_repair_assessment(...)`
  - asks for a semantic repair diagnosis after a failure
  - if the call fails, the code falls back to a heuristic repair diagnosis
- `_maybe_upgrade_concrete_sublemma_to_full_node(...)`
  - checks whether a verified concrete sublemma already matches the target
  - if the check fails, coverage expansion is skipped and the run continues

So a planning-backend quota failure is often fatal when it happens before formalization starts, but often merely degrades quality once the formalization loop is already running.

### 20.3 What happens during formalization

The formalization loop is implemented in:

- `_formalize_candidate_node_structured(...)`
- `_formalize_candidate_node_agentic(...)`
- `_formalize_candidate_node_aristotle(...)`

The common structure is:

1. build a target-specific prompt
2. ask the backend for a Lean artifact
3. verify the artifact locally
4. classify the result semantically
5. decide whether to retry, expand coverage, or stop

This is intentionally not a fully autonomous search procedure. The system has a bounded retry budget and a bounded expansion budget.

The current default formalization mode exposed to users is `agentic`.
The old structured repair-loop mode is no longer user-facing, although some compatibility code remains internally.

### 20.4 Retry behavior after a failed attempt

The current system does **not** react to all failures in the same way.

The retry diagnosis is computed from:

- the compiler stderr/stdout
- the planning backend, if available
- the previous verification result
- the existing faithfulness notes, if they are relevant

The classifier lives in:

- `classify_heuristic_repair_assessment(...)` in [formalization/pipeline.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/formalization/pipeline.py)
- `_classify_retry_failure(...)` in [formalization/loop.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/formalization/loop.py)

The important repair categories are:

- `setting_fix`
  - the theorem drifted into the wrong mathematical universe
  - examples: finite-dimensional proxy instead of the function-space claim, measure-space proxy instead of a concrete variational step
  - response: lock the ambient setting and do not retry by broadening or changing universes
- `theorem_shape_fix`
  - the theorem proved a different claim than the node asked for
  - examples: downstream consequence, assumed hypothesis instead of a proof obligation, abstract proxy theorem
  - response: lock the theorem shape to the node
- `lean_packaging_fix`
  - the theorem is probably fine mathematically, but Lean packaging is broken
  - examples: unknown identifier, unknown namespace, missing typeclass, syntax/token error
  - response: keep the theorem fixed and repair imports / names / syntax
- `proof_strategy_fix`
  - the theorem shape is okay, but the proof approach is brittle
  - examples: rewrite loops, `simp` failures, `linarith` failures, malformed tactic structure
  - response: keep the theorem fixed and change proof strategy
- `try_smaller_sublemma`
  - the full theorem is too hard, but a smaller honest local core may be worth trying
  - response: allow a fallback refinement only if the smaller claim is still meaningful
- `try_larger_core`
  - used for the bonus-retry path
  - response: expand a verified concrete core upward toward the parent theorem without changing the mathematical universe

The current behavior after a failed attempt is:

1. summarize the compiler feedback into a short human-readable line
2. classify the failure
3. build retry feedback that says whether the theorem should stay locked, the setting should stay locked, or only packaging/proof strategy should change
4. retry the same theorem if there is budget left
5. only after the retry budget is exhausted does the system consider a fallback refinement, and only for specific failure categories

That means a good target that fails for Lean-engineering reasons is not automatically replaced by a smaller theorem. The system first tries to fix the same theorem.

Borderline faithfulness cases are handled slightly differently:

- the heuristic layer may emit an advisory signal for things like measure-space, inner-product-space, or dimension-downgrade hints
- those signals do not automatically force `OVER_ABSTRACT`
- instead, the combined semantic review can confirm that the theorem is still a `faithful_core` or a concrete local core in the same setting
- only the truly blatant structural mismatches remain hard rejects at the heuristic layer

### 20.5 What happens when a good target fails on Lean-engineering reasons

This is the subtle case you specifically asked about.

Suppose the planning backend and faithfulness guard both think the theorem is basically right, but Lean compilation or file handling fails.

The current behavior is:

- the system retries the same theorem up to the attempt limit
- it adds a short compiler summary to `_progress.log`
- it asks for a retry diagnosis
- it rebuilds the retry prompt based on that diagnosis
- if the diagnosis says the theorem is still a good faithful core, the theorem shape stays locked
- if the failure is `lean_packaging_fix`, the fix focuses on imports, names, namespaces, and syntax
- if the failure is `proof_strategy_fix`, the fix focuses on the proof script
- if the failure is `setting_fix` or `theorem_shape_fix`, the retry prompt is much stricter about preserving the same theorem universe and logical shape
- if the artifact only has a borderline heuristic warning, the planner gets the final say before the system treats it as over-abstract

What the current system does **not** do is equally important:

- it does not eagerly convert a Lean packaging error into a smaller fallback theorem
- it does not resurrect the old eager “refine everything” behavior
- it does not treat a good target as permission to drift into an easier but less faithful theorem

If the theorem is still unverified at the end of the retry budget, the system usually stops. Only some failure categories can trigger a fallback refinement.

### 20.6 Formalization outcomes and semantic review

After a Lean file verifies, the system classifies the result as one of:

- `full_node`
- `concrete_sublemma`
- `over_abstract`

The current post-verification semantic review is more informative than that raw three-way classification.

The planning backend can additionally judge a verified theorem as:

- `full_match`
- `faithful_core`
- `downstream_consequence`
- `dimensional_analogue`
- `helper_shard`

This semantic review is recorded in `faithfulness_notes` as a compact string like:

- `[full_match] ...`
- `[faithful_core] ...`
- `[downstream_consequence] ...`

That review is then used to decide:

- whether coverage expansion should happen
- whether a later retry is worth attempting
- whether the verified theorem should be upgraded to `full_node`

Important subtlety:

- `full_match` means the verified theorem is judged to be the full target
- `faithful_core` means the theorem is already close enough in theorem shape that expansion is not worthwhile, but it may still be a narrower core
- `certifies_main_burden` is a separate flag that asks whether the theorem covers the hardest inferential step of the node
- if the heuristic layer emitted only a borderline warning, the planner can explicitly override that warning in favor of `faithful_core` or `full_match`

Those are deliberately not identical.

### 20.7 Coverage expansion and bonus retries

Coverage expansion is separate from ordinary retrying.

It lives in:

- `_attempt_structured_coverage_expansion(...)`
- `_attempt_agentic_coverage_expansion(...)`
- `_attempt_aristotle_coverage_expansion(...)`

Current behavior:

- only verified concrete sublemmas are eligible
- if the planning backend says the theorem is a `full_match`, the artifact is upgraded to `full_node`
- if the planning backend says the theorem is a `faithful_core`, coverage expansion is skipped
- if the planning backend says `expansion_warranted` is false, coverage expansion is skipped
- otherwise one bounded expansion attempt may run

The bonus retry path is even narrower:

- it only fires when the verified result is still a concrete sublemma
- the planning backend must say the result is worth retrying later
- the result must still be on the main proof path
- the node must be a high-priority candidate

This is the system’s current answer to “should we keep pushing this theorem upward?”

### 20.8 Refinement fallback

Refined local claims are now fallback-only.

That means:

- the system first tries the best whole node
- only after a meaningful failure does it consider a smaller local claim
- the source node being refined is explicit
- trivial substitution facts, point evaluations, and bookkeeping fragments are heavily penalized
- a refined claim is only created if it still carries meaningful inferential load

The implementation sits in:

- [extraction/pipeline.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/extraction/pipeline.py)
- `_maybe_refine_failed_node(...)`

That is a deliberate change from the older eager refinement behavior. The current design prefers honesty and proof relevance over opportunistic graph growth.

### 20.8.1 What borderline faithfulness warnings mean now

When the local heuristic produces a borderline warning rather than a hard reject:

- the artifact is still allowed to proceed to the combined semantic review
- the planner sees the heuristic warning as advisory context
- the planner may still label the artifact `faithful_core` or `full_match` if the theorem is actually in the right setting
- the run only treats the theorem as truly over-abstract if the heuristic hard-rejects it or the planner confirms that it is genuinely a proxy theorem

This is the current answer to the “run4 heat-method energy identity” style of case: we prefer planner-confirmed borderline handling over a brittle hard-coded rejection.

### 20.8.2 Parent assembly promotion after children are verified

The formalization loop now has a late promotion stage that is distinct from refinement and coverage expansion.

The new rule is:

- scan informal parent nodes whose direct children are all already `formal_verified`
- ask the planning backend whether the parent is now cheap enough to formalize as a parent-assembly theorem
- if the planner says yes, promote the parent to `candidate_formal`
- cache that planner decision for the current child snapshot so the same eligible parent is not re-asked repeatedly
- let the normal dynamic discovery loop pick up the newly promoted parent on a later pass

This is intentionally not the same as coverage expansion:

- coverage expansion grows a verified theorem upward when the theorem itself is only a concrete sublemma
- parent assembly promotion starts from an informal parent that became feasible because its children were already certified

The older follow-up hook has been folded into the same post-verification philosophy. The important new behavior is the planner-gated scan over informal parents whose children have all been discharged.

### 20.8.3 Report-stage remaining-proof-burden synthesis

After formalization is finished and the final graph is being prepared for reporting, the report stage can synthesize a short "Remaining proof burden" paragraph for any node that is still unverified but has at least one verified direct child.

This is a report-only annotation step:

- it does not change planning
- it does not change formalization candidate selection
- it does not change the graph during formalization itself

The report-stage synthesis:

- asks the planning backend to read the parent's informal proof together with all verified direct child lemmas
- asks for a concise delta description: what remains to be checked once those verified child results are assumed
- stores the resulting text on the node as report metadata
- renders it under the node's informal proof in the HTML report
- titles the section as `Remaining proof burden (assuming results of [child ids])`, with the verified child ids linked to their node cards

This differs from the parent-assembly promotion step:

- parent-assembly promotion decides whether an informal parent should now become a formalization candidate
- remaining-proof-burden synthesis decides how to explain the residual proof burden in the final report, even if the parent is never promoted

Because this is a final-report artifact, it runs after all formalization is complete and can be invoked from the report stage on the finished graph.

### 20.9 Aristotle-specific behavior

Aristotle is formalization-only.

It does not participate in planning. It does:

- project submission through the Python SDK
- project status polling
- result download
- Lean file recovery from the returned archive
- local Lean verification

The relevant files are:

- [backends/aristotle.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/backends/aristotle.py)
- [formalization/aristotle.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/formalization/aristotle.py)

Important details:

- `ARISTOTLE_API_KEY` must be set
- the backend has no default timeout unless the caller explicitly sets one
- the returned project snapshot is pruned to the relevant Lean workspace pieces
- Aristotle summaries, if produced, are appended to `_progress.log`
- Aristotle completion logs now include the project status, such as `COMPLETE` or `COMPLETE_WITH_ERRORS`

When Aristotle fails to produce a usable result:

- the code attempts to recover the Lean file from the returned archive
- if recovery works, it may still verify the result locally
- if recovery fails, the run ends with a formalization failure artifact

### 20.10 Logging and artifacts

The run directory now contains several different kinds of records:

- `01_extracted_graph.json`
- `02_candidate_graph.json`
- `03_formalized_graph.json`
- `03_formalization_summary.json`
- `04_report_bundle.json`
- `04_report.html`
- `_progress.log`
- `_backend_logs/*.json`

The most useful runtime trace is `_progress.log`.

It is append-only and now records:

- stage starts and completions
- graph previews when the graph is generated or materially updated
- node attempts
- compiler summaries
- retry diagnoses
- local Lean verification starts and completions
- coverage-expansion attempts
- Aristotle project completion status
- combined semantic review results
- report-stage remaining-proof-burden syntheses
- Aristotle summary markdown blocks

This is why the current progress logs are much more useful than they were early in the project. They let you reconstruct:

- what node was attempted
- what kind of failure happened
- whether the system retried the same theorem
- whether a smaller fallback core was considered
- whether a verified core was promoted or expanded

### 20.11 A compact “what should I expect?” guide

If a run is healthy:

- planning produces a small graph
- candidate ranking looks reasonable
- the formalizer stays in the same mathematical setting
- Lean verification succeeds
- the planning backend often labels the result as `full_match` or `faithful_core`

If a good target fails for packaging reasons:

- the system retries the same theorem
- the retry prompt focuses on Lean packaging / syntax / imports
- it usually does **not** invent a new theorem

If a good target fails for proof-strategy reasons:

- the system retries the same theorem
- it may change proof strategy
- if a smaller honest core is explicitly justified, it may refine afterward

If the formalizer drifts into the wrong mathematical universe:

- the hard heuristic guard should catch the obvious cases immediately
- the planning review should settle the borderline cases
- the retry prompt should lock the theorem shape or setting
- the system should not “salvage” by switching to a different universe

If a verified theorem is narrower but honest:

- the graph records it as a supporting core
- the report says it is a narrower core, not the whole node
- the system may make one bounded expansion attempt if the planning review says that is worthwhile

If a parent node still remains informal but has verified children:

- the report stage may add a short "Remaining proof burden" paragraph
- the title names the verified child ids and links them to their node cards
- the paragraph is meant to help a human reviewer see exactly what the certified children have discharged and what is still left to check

That is the behavior the code currently implements.
