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
- directed dependency/support edges

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

- a verified supporting child certifies a narrower concrete local core inside an informal parent step

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
- applies deterministic cleanup / refinement / calibration afterward

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

The report now supports:

- clickable SVG graph
- improved graph sizing
- proper text wrapping
- inline code rendering in math-ish text
- automatic dark mode
- better honesty for certified local core nodes

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
- `formalize-all-candidates` runs candidates sequentially through the updated graph
- `run-benchmark` is the one-command smoke path

## 7. Backend Layer

The backend layer lives in:

- [backends/base.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/backends/base.py)
- [backends/codex_cli.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/backends/codex_cli.py)
- [backends/claude_code.py](/Users/adihaya/GitHub/formal-islands/src/formal_islands/backends/claude_code.py)
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

At the time of writing:

- it has been wired and tested locally at the adapter level
- but not yet exercised in the full benchmark suite in this conversation

So it should be treated as:

- implementation-ready
- benchmark-untested

### 7.3 Backend logs

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

- `<node>_worker.lean`
- `<node>_worker_plan.md`
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
- support edge is added
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

The old refined-local-claim extraction was purely deterministic and span-based.
That was good at finding the right neighborhood, but too brittle at choosing the actual subclaim.

The current direction is hybrid:

- use deterministic heuristics to decide that a candidate node is too broad
- seed a backend refinement request with the broad node, its parent, a small coverage sketch, and a few high-scoring span hints
- ask the backend for 1 to 3 narrower concrete local claims
- rank the proposals deterministically and keep the best valid one
- fall back to the original span/window extractor if no proposal is usable

This keeps refinement anchored in the original proof while reducing the chance that a clipped fragment becomes the final refined node.

Two refinements make that hybrid path more robust:

- if the backend certifies a narrow local claim, the deterministic loop can later promote a broader parent reached by a `uses` edge, so a successful core can seed a second pass upward
- the proposal ranker penalizes point-evaluation fragments such as `F_q(q) = 0` when they look like isolated snapshots rather than reusable theorems, which helps broader calculus claims outrank tiny bookkeeping facts

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

There has been discussion about adding an optional LLM-based second-opinion classifier for borderline cases, but that has not been implemented yet.

## 11. Prompt Strategy for the Agentic Worker

The agentic prompt has changed a lot.

Current important prompt intentions:

- preserve ambient setting
- preserve key symbols / quantities
- avoid over-abstraction
- start from the most literal whole-node theorem shape
- only fall back to a concrete sublemma if needed
- document fallback in the plan file
- use local scouting before committing
- use one designated main theorem plus helper lemmas if needed
- include a lightweight coverage sketch so the worker can see the node's internal proof components

This “one main theorem plus helpers” requirement was added after Run 11 exposed confusion where the file contained two theorems:

- one helper
- one more substantial result

and the report/recovery path had initially picked the wrong one.

Current behavior now is:

- the worker must return the intended main theorem name
- the file may contain helper lemmas
- artifact extraction/recovery tries to align to the intended main theorem, not just the first declaration in the file
- after a verified concrete sublemma, the loop makes one bounded coverage-expansion attempt from the verified file rather than treating the first successful core as terminal
- if a certified refined local claim points to a broader parent through a `uses` edge, the parent may be promoted into the candidate set on a later dynamic pass

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
  - honest `formal_sublemma_for` graph structure
  - one-main-theorem plus helper-lemmas discipline

It is now one of the cleanest benchmarks for:

- good planning
- good candidate choice
- useful but narrower certified local core
- honest reporting

### Run 12

There is currently an input file:

- [run12_ito_taylor_expansion.json](/Users/adihaya/GitHub/formal-islands/examples/manual-testing/run12_ito_taylor_expansion.json)

As of this handoff note, this conversation has not yet established a comparable full artifact history for Run 12, so a future session should inspect its state directly rather than assuming it has already been characterized.

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

## 15. Current Weaknesses / Bottlenecks

The main remaining bottlenecks are:

### 15.1 Worker still drifts too far on harder whole-node targets

Especially on:

- functional analysis flavored local nodes
- whole-node local estimates that invite abstraction

### 15.2 Coverage classification is still heuristic

It is much better than before, but it is still heuristic.

This is good enough for a prototype, but borderline cases remain possible.

### 15.3 Whole-node local formalization is still near the frontier

The system is best when:

- there is one obvious local island
- the theorem is concrete
- the local theorem can be made Lean-natural without huge infrastructure

It is weaker when:

- the local node wants many layers of infrastructure
- the natural Lean theorem shape is still fairly broad

### 15.4 Backend parity is newer for Claude

Claude backend implementation is now largely ready, but benchmark experience is still mostly Codex-based.

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

### Formalize all candidates sequentially

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
3. consider an optional LLM-assisted second-opinion coverage classifier for borderline `full_node` vs `concrete_sublemma` cases
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
