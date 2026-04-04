# Design note

This first pass stays intentionally narrow.

## Deliberate deferrals

- The Codex backend uses the documented `codex exec --output-schema ... --output-last-message ...` flow as the structured-output path. That is the cleanest documented automation surface we found. If future Codex releases expose a more stable machine-oriented contract, the adapter should switch to it without changing the rest of the pipeline.
- The Claude backend is modeled after the narrow subprocess pattern in OpenProver's `openprover/llm/claude.py`, but does not copy OpenProver's broader agent stack, streaming behavior, or MCP tooling.
- The graph model keeps only nodes and dependency edges. Formal components, collapsible UI groupings, and AND/OR proof-search semantics are explicitly out of scope for this prototype.
- Single-node formalization is bounded and local. Multi-node dependency-aware Lean synthesis, semantic equivalence checking between informal and formal claims, and broad theorem-proving loops are deferred.
- The committed Lean workspace includes a concrete toolchain pin plus a lightweight Mathlib project skeleton. Contributors are still expected to run `lake update` and `lake exe cache get` locally after cloning.
- Candidate selection and formalization are not yet live Mathlib-aware in a programmatic sense. The prototype currently relies on LLM judgment plus local Lean verification against the pinned workspace. A future version could make planning more aware of the local Mathlib environment, but that is intentionally deferred here.
