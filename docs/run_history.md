# Run History

This file records benchmark runs in a compact, review-friendly way.

For each benchmark section:
- each run is timestamped
- each run names the artifact folder
- each run notes what the planner chose, how the formalizer behaved, and whether the final artifact is actually useful
- multiple runs of the same benchmark stay together so comparisons are easy

## run3-full-glassey
- `2026-04-06 09:11 PT` — `artifacts/manual-testing/run3-full-glassey-aristotle`
  - Earlier attempt with no separate narrative summary in the draft history.
  - Keep only as the earlier baseline for comparison.

- `2026-04-06 09:21-10:33 PT` — `artifacts/manual-testing/run3-full-glassey-aristotle-2`
  - Benchmark quality: still a good benchmark in structure, since the graph isolates the two real local ingredients: the virial step and the weighted \(L^2\) inequality.
  - Planner: chose a sensible virial / weighted-inequality island and preserved the right branching proof shape.
  - Formalizer: stayed in the right broad theorem family, but only certified a narrower virial-energy algebra core rather than the main PDE computation; the weighted inequality remained unformalized.
  - Verdict: useful partial result and honest artifact, but still not a convincing benchmark win. It shows the system can find a meaningful shard, not yet the best island in the proof.

- `2026-04-06 15:59 PT` — `artifacts/manual-testing/run3-full-glassey-claude-aristotle-3`
  - Benchmark quality: still a good benchmark in structure, since it isolates the two real local ingredients: the virial step and the weighted \(L^2\) inequality.
  - Planner: again preserved the right branching proof shape, with virial and weighted inequality feeding the blow-up conclusion.
  - Formalizer: verified only a narrow virial helper shard, namely the final algebraic bookkeeping identity matching the IBP output to \(16E\). This is honest and correctly classified, but it is still far from the main analytic burden of the virial node.
  - Weighted inequality attempt: moved toward a more concrete 1D real-valued special case, but still only as a dimensional analogue, and then failed Lean verification on proof-engineering / import issues.
  - Comparison to earlier run: broadly similar to `run3-full-glassey-aristotle-2`. The run is honest and diagnostically useful, but not materially closer to a convincing formalization of the benchmark’s best islands.
  - Verdict: useful partial artifact, but still not a showcase result. Run3 remains better as a stress test than as a public-facing success

## run4-heat-uniqueness

- `2026-04-06 09:21-10:59 PT` — `artifacts/manual-testing/run4-heat-uniqueness-aristotle-2`
  - Benchmark quality: one of the best small benchmarks in the suite, because the proof has a clean energy-method spine and an obvious local island.
  - Planner: found a genuinely good energy-method island and kept the graph compact and readable.
  - Formalizer: produced honest partials instead of overclaiming full-node success. The main good output was a solid energy-dissipation core; the run also included a tiny bookkeeping refinement and a broader uniqueness scaffold.
  - What went well: this was materially better than older overpromoted versions, and the report now tells the truth much more clearly.
  - What still went wrong: refinement was still too eager, since it surfaced a very small bookkeeping claim; provenance / dependency semantics in the graph were still a bit awkward; and the run still did not compose the verified local pieces into one stronger parent-level story.
  - Verdict: one of the more useful runs so far, and plausibly a public-facing benchmark if cleaned up slightly more.

- `2026-04-06 15:26 PT` — `artifacts/manual-testing/run4-heat-uniqueness-gemini-aristotle-3`
  - Benchmark quality: still one of the best small benchmarks in the suite, because the proof has a clean energy-method spine and a single obvious local island.
  - Planner: again kept the graph extremely clean, with `energy_dissipation` as the central local step feeding the uniqueness theorem.
  - Formalizer: attempted a theorem that was actually quite close to the intended local node. It stayed in the 1D interval setting, kept the concrete objects \(w, w_x, w_t, w_{xx}\), used the heat equation and the boundary conditions, and proved the energy-derivative / dissipation formula up to an explicit Leibniz-rule hypothesis.
  - What went wrong: the faithfulness guard appears to have rejected this too aggressively. Unlike the bad abstraction failures in some other benchmarks, this theorem did not really switch to a different theorem family; it looks much more like a reasonable faithful core with one analytic step factored into a hypothesis.
  - Comparison to earlier run: worse in outcome than `run4-heat-uniqueness-aristotle-2`, which produced honest useful partials. This latest run ended with no verified artifact, even though the attempted theorem arguably looked more faithful than the final `formal_failed` label suggests.
  - Verdict: likely a bad run classification rather than a bad benchmark. This result suggests the current faithfulness guard may now be too blunt for heat-uniqueness-style local cores, and run4 should remain in the suite as a strong candidate public-facing benchmark once that calibration issue is fixed

## run5-negative-part-maximum-principle
- `2026-04-06 09:11 PT` — `artifacts/manual-testing/run5-negative-part-maximum-principle-aristotle`
  - Benchmark quality: good benchmark, because the gradient identity for \(u_-\) is a natural high-value local island.
  - Planner: less faithful than the later run in where the formalizer landed, but still extracted a useful proof structure.
  - Formalizer: failed on the best target, but salvaged two meaningful supporting results: a zero-gradient consequence and a root-side supporting core showing that vanishing negative-part energy forces \(u \ge 0\).
  - Verdict: useful partial success. Not the ideal island, but it still produced honest and mathematically relevant certified output.

- `2026-04-06 10:21-11:10 PT` — `artifacts/manual-testing/run5-negative-part-maximum-principle-aristotle-2`
  - Benchmark quality: still good, and in some sense even better exercised here because the run aimed more directly at the true local island.
  - Planner: improved materially by aiming at the real `grad_identity` node.
  - Formalizer: reached the right theorem family for `grad_identity`, but then failed on Lean-engineering issues (naming collisions, brittle helper lemmas, local proof execution), and the root drifted to an abstract energy-backbone theorem that was rejected.
  - Comparison to earlier run: more faithful in target selection, but less productive in final certified output.
  - Verdict: likely a real regression in practical output quality, but a productive one diagnostically: the system now aims better but salvages less well. This suggests a narrow post-failure fallback for “good target / bad Lean execution” cases may be worth adding later.

- `2026-04-06 14:03 PT` — `artifacts/manual-testing/run5-negative-part-maximum-principle-claude-aristotle-3`
  - Benchmark quality: still a good benchmark, since the graph isolates one genuine high-value local island, the gradient identity for the negative part, feeding the root theorem.
  - Planner: kept the proof graph clean and focused, with the right local proof structure.
  - Formalizer: regressed badly. `gradient_identity` drifted into a finite-dimensional analogue with arbitrary vector fields \(f,g\), and `root` drifted even farther into a 1D concavity maximum principle on an interval, which is only a distant analogue of the target proof.
  - Guard behavior: correct. The gradient-identity attempt was honestly rejected as over-abstract / dimensional-analogue drift. The root attempt also failed and never got close to the actual negative-part / IBP argument.
  - Comparison to earlier run5s: worse than both prior runs. The first run at least salvaged useful supporting cores, and the second run reached the right theorem family for `grad_identity` before dying on Lean-engineering issues. This run missed the right theorem family entirely and produced no certified artifact.
  - Verdict: bad run on a good benchmark. This is a real regression and suggests the retry / fallback policy still allows overly large theorem-family jumps instead of staying anchored to the chosen local node.

## run7-harmonic-minimizer

- `2026-04-06 10:21-11:10 PT` — `artifacts/manual-testing/run7-harmonic-minimizer-aristotle-2`
  - Benchmark quality: still a good benchmark in principle, because the Pythagorean energy decomposition is exactly the kind of local island Formal Islands should certify.
  - Planner: isolated the right energy-decomposition island and kept the proof graph compact.
  - Formalizer: drifted to a finite-dimensional gradient-field analogue and stayed too abstract even after retry. The guard correctly rejected the result.
  - What went wrong: the retry policy repaired inside the wrong theorem family instead of forcing a return to the concrete variational setting with \(u,v,w=v-u,H_0^1(\Omega)\) and weak harmonicity.
  - Verdict: good benchmark, bad run. This is mainly a failure of post-rejection redirection, not of extraction.

- `2026-04-06 14:03 PT` — `artifacts/manual-testing/run7-harmonic-minimizer-claude-aristotle-3`
  - Benchmark quality: still a good benchmark in principle, because the Pythagorean energy decomposition is exactly the kind of local island Formal Islands should ideally certify.
  - Planner: again isolated the right `energy_decomp` island and kept the proof graph compact and faithful.
  - Formalizer: still failed on the main local island. `energy_decomp` drifted into an abstract gradient-field / measure-theoretic theorem and was correctly rejected by the faithfulness guard.
  - What improved versus the previous run: unlike the earlier run, this one did salvage a verified supporting core at the root level. The certified `root__formal_core` proves the energy inequality from the orthogonality hypothesis and nonnegativity of the remainder term, and it is now honestly classified as a narrower `faithful_core` rather than overclaimed as full-node success.
  - Comparison to earlier run: better than `run7-harmonic-minimizer-aristotle-2`, because it leaves behind a genuinely useful certified artifact instead of only an abstract rejected attempt. But it still misses the highest-value local island.
  - Verdict: improved partial success on a good benchmark, but still not a showcase result. The main remaining issue is that the worker continues to prefer the easier orthogonality-backed inequality over the more faithful energy-decomposition theorem

## run8-semilinear-heat-blowup

- TODO

## run9-weak-poisson-lax-milgram

- `2026-04-06 10:21-10:50 PT` — `artifacts/manual-testing/run9-weak-poisson-lax-milgram-aristotle-2`
  - Benchmark quality: mathematically good graph, but also a somewhat dangerous benchmark for the current system because its natural local nodes are very easy to re-express abstractly.
  - Planner: picked the correct local nodes, namely coercivity and continuity.
  - Formalizer: immediately escaped into abstract proxy theorems (inner-product / \(L^2\) / measure-space packaging), which the faithfulness guard rejected.
  - What went wrong: this was not mainly a Lean execution failure; it was a theorem-shape control failure. The worker never really stayed on the concrete Sobolev / gradient-form statements.
  - Verdict: useful as a stress test, but currently weak as a showcase benchmark. It confirms that the worker still needs much stronger resistance to abstraction drift on functional-analytic nodes.

- `2026-04-06 14:03 PT` — `artifacts/manual-testing/run9-weak-poisson-lax-milgram-claude-aristotle-3`
  - Benchmark quality: still mathematically good as a stress test, since the graph cleanly isolates boundedness of \(a\), coercivity of \(a\), and continuity of \(\ell\) as the real Lax–Milgram inputs.
  - Planner: again produced the right local proof structure and a clean branching graph.
  - Formalizer: repeated the same abstraction drift as earlier runs. `boundedness_a` was turned into a theorem on an abstract real inner-product space `H` standing in for \(H_0^1(\Omega)\), and `continuity_ell` into an abstract \(L^2\)/measure-space proxy theorem with a free \(H^1\)-norm placeholder. Formalizer said in its notes "Since Sobolev spaces (H₀¹(Ω)) are not yet available in Mathlib, the formalization declares a single concrete type H (not a universe-polymorphic Type*) equipped with a real inner-product-space structure, standing for H₀¹(Ω)."
  - Important nuance: the problem was not merely use of `Type*`; the main issue was replacing the concrete Sobolev / gradient / domain setting of the node with a proxy Hilbert-space or measure-space theorem. That is mathematically related, but too far from the target node for this repo’s faithfulness standard.
  - Guard behavior: correct. Both attempts were honestly rejected as over-abstract rather than being misreported as useful wins.
  - Comparison to earlier run9: essentially confirms the earlier diagnosis rather than improving it. The run is honest, but not substantively better.
  - Verdict: useful benchmark diagnostically, but still weak as a showcase result. The main remaining issue is theorem-shape control during formalization, not planning quality. This benchmark may remain better as a stress test than as a flagship public example unless the node target is narrowed to a more concrete local estimate that resists abstraction. In my opinion we should ditch this benchmark, Sobolev spaces aren't in Mathlib and there's nothing we can do to make it more faithful given that. 

## run10-first-dirichlet-eigenfunction

- `2026-04-06 04:31 PT` — `artifacts/manual-testing/run10-first-dirichlet-eigenfunction-aristotle`
  - Benchmark quality: still a good benchmark in principle, since it separates the deep attainment/direct-method burden from the much smaller algebraic multiplier-identification step.
  - Planner: produced a sensible graph with two meaningful proof burdens: attainment and weak eigenvalue / multiplier identification.
  - Formalizer: achieved one real partial success. It verified `attainment__formal_core`, a nontrivial finite-dimensional Rayleigh-quotient attainment theorem on `EuclideanSpace ℝ (Fin d)`, honestly recorded as a narrower supporting core rather than full-node success.
  - Additional near-hit: the refined multiplier-identification node had a plausible theorem shape for the algebraic step \(\mu = \lambda_1\), but failed Lean verification due to a low-level Unicode `λ₁` token error.
  - Verdict: meaningful partial success. Not a true win on the intended infinite-dimensional benchmark, but it did produce a genuinely nontrivial certified local core and also showed that the smaller algebraic island is promising.

- `2026-04-06 15:35 PT` — `artifacts/manual-testing/run10-first-dirichlet-eigenfunction-gemini-aristotle-3`
  - Benchmark quality: still good, for the same reason — the benchmark contains one deep functional-analytic node and one very attractive small algebraic island.
  - Planner: again produced a reasonable proof graph and identified the right local algebraic step `multiplier_is_lambda` as a top target.
  - Formalizer: regressed in practical output. `existence_of_minimizer` drifted into an over-abstract surrogate theorem that packaged the hard PDE/compactness content into a giant hypothesis and was correctly rejected by the faithfulness guard. `multiplier_is_lambda` was mathematically promising and close to the intended local island, but again failed Lean verification on a Unicode `λ` token issue.
  - Comparison to previous run: worse overall, because it produced no verified artifact at all. The previous run at least verified a real supporting core and nearly got the algebraic multiplier step as well.
  - Verdict: regression in output quality, but not a total conceptual miss. The benchmark still looks good, and the multiplier-identification node still looks like the most promising formal island. The main remaining problem here seems more like syntax / packaging hygiene and salvage after near-miss concrete attempts than theorem-selection failure.

## run11-two-point-log-sobolev

- `2026-04-05 22:20-23:50 PT` — `artifacts/manual-testing/run11-two-point-log-sobolev-aristotle`
  - Benchmark quality: one of the strongest benchmarks in the suite. The global theorem is meaningful, but it reduces to a clean scalar proof with obvious local islands (convexity, critical point, normalized scalar inequality).
  - Planner: produced a very good proof graph. The main theorem reduces to the scalar inequality \(G(u)\ge 0\), which in turn depends on convexity of \(G\) and the critical-point fact \(G'(1)=0\).
  - Formalizer: had a genuinely strong run. It verified:
    - the refined local claim `scalar_ineq_refined_local_claim` proving \(G'(1)=0\),
    - a concrete convexity core `G_convex__formal_core` proving the nonnegativity of the explicit \(G''\)-expression on \((0,2)\),
    - and a verified scalar-inequality core `scalar_ineq__formal_core` establishing \(G(u)\ge 0\) on \([0,2]\).
  - What went well: unlike many PDE-heavy runs, the formalizer stayed in the correct theorem family and proved subresults that carry real inferential weight in the original proof, not just distant analogues or bookkeeping tails.
  - What still looks imperfect: the graph semantics around the refined critical-point node are a bit awkward, and the `full_node` classification on that refined claim is too strong for what is really a narrow local subclaim. But these are reporting/semantics issues, not mathematical failures.
  - Verdict: one of the best runs so far, and a strong candidate for a public-facing showcase benchmark. It demonstrates the repo’s core idea well: extracting a meaningful proof graph and certifying multiple central local islands 

- `2026-04-06 16:02 PT` — `artifacts/manual-testing/run11-two-point-log-sobolev-claude-aristotle3`
  - Benchmark quality: one of the strongest benchmarks in the suite. The global theorem is meaningful, but the proof reduces to a clean scalar argument with obvious local islands.
  - Planner: again produced an excellent proof graph. The root theorem reduces to the scalar two-point inequality, which in turn reduces to the core one-dimensional inequality \(G(u)\ge 0\).
  - Formalizer: had a genuinely strong run. It directly verified:
    - `scalar_ineq` as a full match for the complete scalar two-point log-Sobolev inequality, and
    - `core_1d` as a full match for the core one-dimensional inequality \(G(u)\ge 0\) on \([0,2]\).
  - What went well: unlike many PDE-heavy runs, the formalizer stayed exactly in the intended theorem family and proved local results that carry major inferential weight in the original proof. The artifact is also cleaner than the earlier run11, since the main verified nodes are now the two natural theorem-level reductions rather than a mix of refined critical-point / convexity helper nodes.
  - Comparison to previous run: at least as strong mathematically, and arguably better as a showcase artifact because the graph is simpler and the verified nodes line up more directly with the human proof structure.
  - Verdict: one of the best runs so far, and a very strong candidate for a public-facing showcase benchmark. It demonstrates the repo’s core idea clearly and convincingly

