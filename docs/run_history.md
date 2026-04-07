# Run History

This file records benchmark runs in a compact, review-friendly way.

For each benchmark section:
- each run is timestamped
- each run names the artifact folder
- each run notes what the planner chose, how the formalizer behaved, and whether the final artifact is actually useful
- multiple runs of the same benchmark stay together so comparisons are easy

Overview of the best benchmark runs:
1. run11-two-point-log-sobolev-claude-aristotle-3: clean graph, two verified nodes, both have nontrivial Lean code 
2. run4-heat-uniqueness-gemini-aristotle-4: clean graph, natural local island, it has two total verified nodes, one doesn't do much but the other is pretty nontrivial 
3. run15-matrix-determinant-lemma-claude-aristotle-2: less impressive mathematically since the theorem itself isn't that hard, and its able to prove the entire thing formally (2/2 nodes), but shows the system can go end-to-end on a benchmark with good mathlib support
4. run16-hoeffding-lemma-gemini-aristotle: two central local islands verified, together they cover almost all the substance of the proof, root staying informal not serious weakness
5. run17-gershgorin-circle-gemini-aristotle: root-side theorem still classified as faithful core rather than full-node theorem but seems good
6. run3-full-glassey-aristotle-2: clean graph, 2 certified local cores verified in Lean, one is fairly trivial but one is pretty nontrivial

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

- `2026-04-06 17:47 PT` — `artifacts/manual-testing/run4-heat-uniqueness-gemini-aristotle-4`
  - Benchmark quality: still one of the best small benchmarks in the suite, because the proof has a clean energy-method spine and a single obvious local island.
  - Planner: again kept the graph extremely clean, with `energy_dissipation` as the central local step feeding the uniqueness theorem.
  - Formalizer: this time had a genuinely strong run. It verified:
    - `energy_dissipation` as a full match for the intended local energy-dissipation lemma, and
    - `main_theorem__formal_core` as a strong faithful core for the uniqueness conclusion from nonincreasing energy and matching initial/boundary data.
  - What improved: this directly validates the recent faithfulness-guard change. The kind of concrete interval-calculus core that was previously being rejected too aggressively is now accepted and verified.
  - Comparison to earlier runs:
    - better than `run4-heat-uniqueness-aristotle-2`, because the artifact is cleaner and no longer cluttered by a tiny bookkeeping refinement;
    - much better than `run4-heat-uniqueness-gemini-aristotle-3`, where a very similar local theorem was rejected too harshly and nothing was verified.
  - Verdict: one of the strongest runs in the repo so far, and a very strong public-facing showcase benchmark. It demonstrates the intended Formal Islands workflow clearly and also provides direct evidence that the recent faithfulness-policy change was a good one.

- `2026-04-06 20:23 PT` — `artifacts/manual-testing/run4-heat-uniqueness-gemini-aristotle-5`
  - Benchmark quality: still one of the best small benchmarks in the suite, because the proof has a clean energy-method spine and a single obvious local island.
  - Planner: again kept the graph compact and well structured, with `energy_dissipation` as the central local step feeding the uniqueness theorem.
  - Formalizer: had another genuinely strong run. It verified:
    - `energy_dissipation` as a full match for the intended local energy-dissipation lemma, and
    - `heat_uniqueness__formal_core` as a strong concrete supporting theorem for the final uniqueness deduction from nonincreasing energy and matching initial data.
  - What went well: the formalizer again stayed in the correct theorem family and produced the same clean two-part proof spine as the previous successful run4: one verified analytic PDE estimate and one verified energy-method conclusion.
  - Comparison to earlier runs:
    - much better than `run4-heat-uniqueness-gemini-aristotle-3`, where a very similar local theorem was rejected too harshly and nothing was verified;
    - broadly on par with `run4-heat-uniqueness-gemini-aristotle-4`, but valuable because it shows that the strong run4 result is stable and repeatable rather than a one-off.
  - **Operational note:** **this run used `max_attempts=4` rather than the default `2`**, though in practice the verified nodes compiled on their first successful attempts and did not need the extra retry budget.
  - Verdict: still one of the strongest runs in the repo, and one of the best public-facing showcase benchmarks. This rerun mainly strengthens confidence that run4 is now a reliable example of the intended Formal Islands workflow.

- `2026-04-07 11:49 PT` — `artifacts/manual-testing/run4-heat-uniqueness-gemini-aristotle-6`
  - Benchmark quality: still one of the best small benchmarks in the suite, because the proof has a clean energy-method spine and a single obvious local island.
  - Planner / pipeline behavior: this run gives evidence that the two new pipeline features are now working.
    - The root `uniqueness_heat_equation` was **promoted after child verification** and is now marked `candidate_formal` with priority 2.
    - The final report bundle now includes non-null `remaining_proof_burden` text for still-informal parent nodes, including both `uniqueness_heat_equation` and `energy_dissipation`.
  - Formalizer: again produced a strong two-part artifact. It verified:
    - `energy_dissipation__formal_core`, capturing the main integration-by-parts / nonpositive-energy-derivative calculation, and
    - `uniqueness_heat_equation__formal_core`, capturing the real-analysis uniqueness deduction from zero initial energy and nonincreasing energy.
  - What improved: beyond the mathematical content, the reporting is now better. The artifact explicitly records what remains informal at each parent node, which makes the mixed informal/formal proof boundary much clearer.
  - What still looks incomplete: although the root was promoted, the final verified result still appears as a supporting `__formal_core` node rather than as a direct formalization attached to the parent itself. So parent promotion is clearly influencing the graph metadata, but it is not yet obviously producing a direct parent-level formal artifact.
  - Comparison to earlier runs:
    - mathematically still in the same strong family as `run4-heat-uniqueness-gemini-aristotle-4` and `-5`;
    - better as a reporting artifact, because the new remaining-burden text makes the residual informal obligations explicit.
  - **Operational note:** **this run used `max_attempts=8` rather than the default `2`**.
  - Verdict: still one of the strongest runs in the repo, and now also a better human-facing artifact because the parent-promotion metadata and remaining-proof-burden reporting are starting to work. The next improvement would be to make promoted parents more visibly attempted as parent-level theorems, rather than only yielding supporting `__formal_core` children.
  
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

- `2026-04-06 22:25 PT` — `artifacts/manual-testing/run5-negative-part-maximum-principle-claude-aristotle-4`
  - Benchmark quality: still a good benchmark, because the gradient identity for \(u_-\) is a natural high-value local island inside the weak maximum principle proof.
  - Planner: kept the proof graph clean and focused, with the right intended local structure.
  - Formalizer: partially recovered after the weaker recent reruns. It verified:
    - `grad_identity__formal_core`, and
    - `root__formal_core`.
  - What went well: unlike `run5-negative-part-maximum-principle-claude-aristotle-3`, this run did not drift into obviously unrelated analogues and did leave behind two honest verified supporting artifacts.
  - What still went wrong:
    - `grad_identity__formal_core` is only the algebraic shell of the target node: it bakes in the negative-part gradient as an `if`-expression built from an arbitrary `grad_u`, rather than deriving the actual chain-rule/Sobolev identity for \(u_-\).
    - `root__formal_core` verifies only the final logical skeleton of the maximum-principle argument, while packaging the PDE/IBP/gradient-identity chain and the Poincaré step as hypotheses.
  - Comparison to earlier runs:
    - better than `run5-negative-part-maximum-principle-claude-aristotle-3`, which missed the theorem family entirely and produced no verified artifact;
    - better than `run5-negative-part-maximum-principle-aristotle-2` in practical output, since this run at least verifies supporting theorems;
    - more mixed relative to the very first `run5-negative-part-maximum-principle-aristotle`, which also salvaged useful supporting results.
  - **Operational note:** **this run used `max_attempts=8` rather than the default `2`**. The extra retry budget appears to have helped recover verified supporting artifacts, even though it still did not reach the true analytic local island.
  - Verdict: partial recovery on a good benchmark, but still not a showcase result. The benchmark should remain in the suite, but the verified results are still structural supporting shells rather than certifications of the main analytic burden.

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

- `2026-04-06 17:47 PT` — `artifacts/manual-testing/run7-harmonic-minimizer-rerun-claude-aristotle-4`
  - Benchmark quality: still a good benchmark in principle, because the cross-term vanishing step and the resulting Pythagorean energy decomposition are exactly the kind of local islands Formal Islands should aim to certify.
  - Planner: produced a sensible graph, separating the cross-term lemma from the overall Dirichlet-energy minimization conclusion.
  - Formalizer: improved materially. It verified `cross_term_vanishes__formal_core`, a genuinely meaningful faithful core that instantiates weak harmonicity with the specific test gradient \(\nabla v - \nabla u\) and concludes the cross term vanishes.
  - What went well: this is a better and more central certified result than in the earlier run7 attempts. It is clearly on the right proof path and carries real inferential weight in the original argument.
  - What still went wrong: the root node `harmonic_min_dirichlet` still drifted too far into an abstract theorem about gradient fields, measures, and an abstract set `H01_grads`, and was correctly rejected by the faithfulness guard.
  - Comparison to earlier runs:
    - better than `run7-harmonic-minimizer-aristotle-2`, which only produced rejected abstractions;
    - better than `run7-harmonic-minimizer-claude-aristotle-3`, because the verified result here is more central and more faithful than the earlier root-side salvage.
  - Verdict: improved partial success on a good benchmark. Not yet a showcase result, because the main minimization theorem still fails, but this rerun shows real progress and suggests the benchmark should remain in the suite.

- `2026-04-06 20:22 PT` — `artifacts/manual-testing/run7-harmonic-minimizer-rerun-claude-aristotle-5`
  - Benchmark quality: still a good benchmark in principle, because the Pythagorean energy decomposition is exactly the kind of local island Formal Islands should aim to certify.
  - Planner: produced a sensible graph centered on the `dirichlet_pythagorean` identity feeding the energy-minimization theorem.
  - Formalizer: improved materially and produced the best run7 artifact so far.
    - `dirichlet_pythagorean__formal_core` verified successfully and formalizes the central integral identity
      \(\int_\Omega |\nabla u+\nabla w|^2 = \int_\Omega |\nabla u|^2 + \int_\Omega |\nabla w|^2\)
      under the vanishing cross-term hypothesis.
    - `harmonic_min_dirichlet` still failed to verify. The attempted theorem stayed in the gradient-field setting and was not immediately rejected after the first attempt, but the Lean proof failed on proof-engineering issues and finally on a missing object-file/import problem.
  - What went well: the certified island is stronger and more central than in the earlier run7s. It captures most of the algebraic/integration content of the target local node and is clearly on the right proof path.
  - What still went wrong: the hardest functional-analytic step is still missing — namely deriving the cross-term vanishing from actual weak harmonicity and \(w \in H_0^1(\Omega)\) — and the root minimization theorem remains unverified.
  - Comparison to earlier runs:
    - better than `run7-harmonic-minimizer-aristotle-2`, which only produced rejected abstractions;
    - better than `run7-harmonic-minimizer-claude-aristotle-3`, whose salvage was on the easier root-side inequality;
    - better than `run7-harmonic-minimizer-rerun-claude-aristotle-4`, because the verified result here is closer to the real central local island.
  - **Operational note:** **this run used `max_attempts=4` rather than the default `2`**. The extra retry budget appears to have mattered here, since the verified `dirichlet_pythagorean__formal_core` only succeeded on attempt 3.
  - Verdict: best run7 so far, and a meaningful partial success on a good benchmark. Still not a showcase result, because the main weak-harmonicity-to-minimization burden remains unverified, but clearly a real step forward.

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

- `2026-04-06 17:47 PT` — `artifacts/manual-testing/run10-first-dirichlet-eigenfunction-gemini-aristotle-4`
  - Benchmark quality: still a good benchmark in principle, since it separates the deep attainment/direct-method burden from the much smaller algebraic multiplier-identification step.
  - Planner: again produced a sensible graph with the two real proof burdens: minimizer existence and multiplier identification.
  - Formalizer: did not achieve a verified artifact. `minimizer_existence` was allowed past the softened faithfulness gate, but it still relied on an abstract Hilbert-space surrogate `H01` and a large bundled compactness/lower-semicontinuity hypothesis, then failed Lean verification with syntax issues and unsolved goals. `multiplier_identification` again targeted the right algebraic idea, but wrapped it in an arbitrary `Type*` / abstract bilinear-form theorem and was hard-rejected by the faithfulness guard.
  - Comparison to earlier runs:
    - worse than `run10-first-dirichlet-eigenfunction-aristotle`, which at least verified a nontrivial finite-dimensional attainment core and nearly got the multiplier step;
    - not clearly better than `run10-first-dirichlet-eigenfunction-gemini-aristotle-3`, since this rerun still produced no verified artifact and still failed to keep the multiplier-identification node concrete.
  - Verdict: disappointing rerun on a still-good benchmark. The benchmark should remain in the suite, and `multiplier_identification` still looks like the best local island, but this run confirms that the current worker still needs much better theorem-shape discipline on that node and that the direct-method existence node is still too infrastructure-heavy to be a near-term showcase result

- `2026-04-06 20:26 PT` — `artifacts/manual-testing/run10-first-dirichlet-eigenfunction-gemini-aristotle-5`
  - Benchmark quality: still a good benchmark in principle, since it separates the deep direct-method existence burden from the much smaller Euler–Lagrange / multiplier-identification step.
  - Planner: again produced a sensible graph with the two real proof burdens: minimizer existence and Euler–Lagrange derivation.
  - Formalizer: improved somewhat over the last two run10 reruns by recovering a verified artifact.
    - `minimizer_exists` still failed: Aristotle again drifted into an abstract Hilbert-space / embedding / weak-topology formalization with packaged compactness and lower-semicontinuity hypotheses, and the faithfulness guard rejected it on all attempts.
    - `euler_lagrange_derivation__formal_core` verified successfully: it formalizes the algebraic core where one tests the weak equation at `u₁`, uses normalization and energy, identifies `μ = λ₁`, and substitutes back.
  - Comparison to earlier runs:
    - better than `run10-first-dirichlet-eigenfunction-gemini-aristotle-3` and `-4`, because this run at least restores a useful verified artifact rather than ending empty-handed;
    - still worse than `run10-first-dirichlet-eigenfunction-aristotle`, which verified a more substantial attainment-side core and also nearly got the multiplier step.
  - **Operational note:** **this run used `max_attempts=4` rather than the default `2`**. The extra retry budget helped enough to recover a verified smaller shard on the Euler–Lagrange side, but it did not fix the deeper theorem-shape problem on the minimizer-existence node.
  - Verdict: partial recovery on a still-good benchmark. The small Euler–Lagrange / multiplier-identification region remains the most promising formal island here, while the direct-method existence node still looks too infrastructure-heavy and too prone to over-abstract formalization for the current worker.

- `2026-04-06 22:25 PT` — `artifacts/manual-testing/run10-first-dirichlet-eigenfunction-gemini-aristotle-6`
  - Benchmark quality: still a good benchmark in principle, since it separates the deep compactness/direct-method burden from the smaller but important attainment and Euler–Lagrange identification steps.
  - Planner: produced a clean graph with a sensible decomposition into the existence-of-limit branch, the attainment branch, and the Euler–Lagrange branch.
  - Formalizer: this is the strongest run10 artifact so far. It verified:
    - `infimum_attained__formal_core`, a faithful local core for the attainment step showing that weak lower semicontinuity plus minimizing-sequence convergence force `dirichletEnergy Ω u1 = lambda1 Ω`, and
    - `euler_lagrange_equation__formal_core`, a faithful local core for the algebraic multiplier-identification step that upgrades the Lagrange multiplier equation to the weak eigenvalue equation with eigenvalue `lambda1`.
  - What went well: unlike the earlier run10s, this run certifies useful supporting cores on **both** main branches of the proof graph rather than only one side or none at all.
  - What still went wrong: the hardest infrastructure-heavy steps remain informal:
    - `existence_admissible_limit` is still not formalized, so the compactness / weak-convergence / strong \(L^2\) convergence step is missing;
    - the Euler–Lagrange core still assumes the existence of a Lagrange multiplier equation rather than deriving it from constrained minimization.
  - Comparison to earlier runs:
    - better than `run10-first-dirichlet-eigenfunction-gemini-aristotle-3`, `-4`, and `-5`, because it now certifies two central local cores rather than one narrow shard or nothing;
    - arguably better as a public-facing artifact than the original `run10-first-dirichlet-eigenfunction-aristotle`, because the verified nodes align more directly with the intended proof structure instead of relying on a finite-dimensional surrogate theorem.
  - **Operational note:** **this run used `max_attempts=8` rather than the default `2`**.
  - Verdict: strong partial success on a good benchmark, and the best run10 artifact so far. Still not a top showcase benchmark, because the deepest functional-analytic steps remain unverified, but now a genuinely convincing example of meaningful formal islands inside a larger informal proof.
 
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

- `2026-04-06 16:02 PT` — `artifacts/manual-testing/run11-two-point-log-sobolev-claude-aristotle-3`
  - Benchmark quality: one of the strongest benchmarks in the suite. The global theorem is meaningful, but the proof reduces to a clean scalar argument with obvious local islands.
  - Planner: again produced an excellent proof graph. The root theorem reduces to the scalar two-point inequality, which in turn reduces to the core one-dimensional inequality \(G(u)\ge 0\).
  - Formalizer: had a genuinely strong run. It directly verified:
    - `scalar_ineq` as a full match for the complete scalar two-point log-Sobolev inequality, and
    - `core_1d` as a full match for the core one-dimensional inequality \(G(u)\ge 0\) on \([0,2]\).
  - What went well: unlike many PDE-heavy runs, the formalizer stayed exactly in the intended theorem family and proved local results that carry major inferential weight in the original proof. The artifact is also cleaner than the earlier run11, since the main verified nodes are now the two natural theorem-level reductions rather than a mix of refined critical-point / convexity helper nodes.
  - Comparison to previous run: at least as strong mathematically, and arguably better as a showcase artifact because the graph is simpler and the verified nodes line up more directly with the human proof structure.
  - Verdict: one of the best runs so far, and a very strong candidate for a public-facing showcase benchmark. It demonstrates the repo’s core idea clearly and convincingly

- `2026-04-06 20:23 PT` — `artifacts/manual-testing/run11-two-point-log-sobolev-claude-aristotle-4`
  - Benchmark quality: still one of the strongest benchmarks in the suite. The global theorem is meaningful, but the proof reduces to a clean scalar argument with obvious local islands.
  - Planner: again produced a strong proof graph, with the root reducing to the one-variable key lemma \(G(u)\ge 0\), and the convexity calculation sitting underneath that lemma.
  - Formalizer: had another genuinely strong run. It verified:
    - `key_lemma` as a full match for the main one-variable inequality \(G(u)\ge 0\) on \([0,2]\), and
    - `convexity__formal_core` as a faithful certified core for the nonnegativity of the explicit second-derivative expression \(G''(u)\).
  - What went well: the formalizer stayed in the correct theorem family and certified the main analytic burden of the proof, not just a distant analogue or a bookkeeping tail.
  - Comparison to previous runs:
    - stronger mathematically than the earliest run11 in some ways, because the key lemma itself is now verified directly;
    - slightly less clean as a showcase artifact than `run11-two-point-log-sobolev-claude-aristotle-3`, since the root scalar inequality node is not itself verified and the graph is a bit more helper-layered again.
  - **Operational note:** **this run used `max_attempts=4` rather than the default `2`**, and the extra retry budget mattered: `key_lemma` verified on attempt 2 and `convexity__formal_core` verified on attempt 3.
  - Verdict: still one of the best runs in the repo, and a strong public-facing showcase benchmark. It also provides good evidence that extra retry budget can pay off on the right scalar-analysis benchmarks.

## run12-ito-taylor-expansion

- `2026-04-06 17:35 PT` — `artifacts/manual-testing/run12-ito-taylor-expansion-gemini-aristotle-3`
  - Benchmark quality: still a good benchmark in principle, because it separates a deterministic Taylor-expansion island from a probabilistic \(L^2\) cross-variation estimate.
  - Planner: preserved the right proof structure, with `taylor_expansion_identity` and `cross_variation_l2_bound` feeding the Itô formula root.
  - Formalizer: had mixed behavior across the two nodes.
    - `taylor_expansion_identity` stayed in the correct deterministic theorem family and targeted the right local identity, but failed Lean verification on calculus / interval-integral execution issues rather than on faithfulness.
    - `cross_variation_l2_bound` again drifted into an abstract `Type*` probability-space theorem and was hard-rejected by the faithfulness guard.
  - What went well: the deterministic Taylor node remains a promising formal island, and this run again shows the system can aim at it directly rather than replacing it with a proxy theorem.
  - What still went wrong: nothing was verified, and the probabilistic node remains too abstraction-prone. The deterministic node is closer, but still fails on proof execution rather than theorem selection.
  - Verdict: useful diagnostic run, but not a showcase result. The benchmark should stay in the suite, with the main improvement priorities being better Lean execution on deterministic analysis and stronger theorem-shape discipline 

- `2026-04-06 22:27 PT` — `artifacts/manual-testing/run12-ito-taylor-expansion-gemini-aristotle-4`
  - Benchmark quality: still a good benchmark in principle, because it separates a deterministic Taylor-expansion island from a stochastic convergence node.
  - Planner: again preserved the right proof structure, with `discrete_taylor_expansion` and `stochastic_convergence` feeding the Itô-formula root.
  - Formalizer: still did not verify anything.
    - `discrete_taylor_expansion` stayed in the correct deterministic theorem family and continued targeting the right Taylor-expansion identity, but failed Lean verification after many repair attempts on calculus/API/algebra issues around Taylor remainder lemmas, interval coercions (`Icc`/`uIcc`/`uIoo`), and telescoping-sum manipulations.
    - `stochastic_convergence` again drifted into an abstract `Type*` probability-space theorem with generic processes and packaged convergence hypotheses, and was hard-rejected by the faithfulness guard on every attempt.
  - What went well: the deterministic Taylor node still looks like the right formal island, and this run again confirms that the system recognizes that rather than replacing it with a totally different theorem.
  - What still went wrong: no artifact was verified, and the extra retry budget did not help enough. The stochastic node remains too abstraction-prone, while the deterministic node remains stuck on Lean execution rather than theorem choice.
  - Comparison to previous run: broadly similar to `run12-ito-taylor-expansion-gemini-aristotle-3`, but now with stronger evidence that the deterministic node is limited by proof execution rather than by faithfulness, since it stayed on-target for many attempts without closing.
  - **Operational note:** **this run used `max_attempts=8` rather than the default `2`**. The larger retry budget did not produce a verified result, which suggests that for this benchmark the current bottleneck is not just insufficient retry count.
  - Verdict: useful diagnostic run, but still not a showcase result. The benchmark should remain in the suite, with the main improvement priorities still being better Lean execution on deterministic analysis and stronger theorem-shape discipline on the stochastic node.

## run13-pinsker-via-bernoulli-core

- `2026-04-05 22:04 PT` — `artifacts/manual-testing/run13-pinsker-via-bernoulli-core-claude-aristotle-2`
  - Benchmark quality: good benchmark, because the proof cleanly separates a heavy measure-theoretic reduction step (DPI) from a very natural scalar formal island (the Bernoulli inequality).
  - Planner: produced a sensible graph with the right decomposition: DPI plus Bernoulli scalar inequality feeding the Pinsker root theorem.
  - Formalizer:
    - `dpi` failed in the familiar way, drifting to an abstract generic `Type*` measurable-space theorem and getting hard-rejected by the faithfulness guard.
    - `bernoulli_ineq__formal_core` verified successfully and proved the exact scalar inequality
      \(p\log(p/q) + (1-p)\log((1-p)/(1-q)) \ge 2(p-q)^2\).
  - What went well: the Bernoulli scalar step is exactly the right local island for this benchmark, and Aristotle did certify it.
  - What still looked off: the verified Bernoulli theorem was represented as a narrower supporting core rather than as the full scalar node, even though its statement essentially matched the whole Bernoulli inequality.
  - Verdict: strong partial success on a good benchmark. Not yet a full showcase for the global theorem, but already a convincing example of a meaningful local island being certified inside a larger proof.

- `2026-04-06 15:38 PT` — `artifacts/manual-testing/run13-pinsker-via-bernoulli-core-gemini-aristotle-3`
  - Benchmark quality: still a strong benchmark for the same reason — the Bernoulli scalar inequality is an obvious and valuable formal island inside a larger measure-theoretic argument.
  - Planner: again produced a good graph, with the Bernoulli scalar inequality isolated as the central local target and the Pinsker root depending on it.
  - Formalizer:
    - `pinskers_inequality` failed, again drifting into a very abstract measure-theoretic theorem over arbitrary measurable spaces and getting rejected by the faithfulness guard.
    - `bernoulli_scalar_inequality` verified successfully and is now classified directly as a `full_match` for the scalar node.
  - What improved versus the previous run: the artifact is semantically cleaner. The Bernoulli inequality is now represented as the verified node itself, rather than needing a separate “certified core” child to carry the real theorem.
  - Comparison to previous run: better as a public-facing artifact, even if mathematically similar, because the reporting now lines up more directly with what was actually proved.
  - **Operational note:** **this latest run used `max_attempts=4` rather than the default `2`**.
  - Verdict: one of the better partial-success benchmarks in the suite. The global Pinsker theorem still remains out of reach for the current worker, but the Bernoulli scalar island is now a clean and convincing certified result.

- `2026-04-07 09:21 PT` — `artifacts/manual-testing/run13-pinsker-via-bernoulli-core-gemini-aristotle-4`
  - Benchmark quality: still a good benchmark, because the scalar Bernoulli inequality remains an obvious and valuable formal island inside a larger measure-theoretic proof.
  - Planner: this time chose a narrower, cleaner strategy. It left the root `pinskers_inequality` informal and targeted only the scalar `bernoulli_inequality` node.
  - Formalizer: had a clean successful run on that node. `bernoulli_inequality` verified directly as a **full match** on the first attempt.
  - What went well: the run is semantically clean and honest. The certified theorem is exactly the intended scalar local island, with no extra “formal_core” child bookkeeping and no abstraction drift on the verified node.
  - What is missing relative to earlier run13s: the pipeline did not even attempt the root theorem or an intermediate DPI-style supporting theorem. So this artifact is narrower than the earlier run13s in scope, even though the scalar certification itself is clean.
  - What I think about the root: unlike run16/Hoeffding, the root here is probably **not** a cheap assembly theorem. It still depends on substantial measure-theoretic infrastructure: total variation, measurable set selection, pushforward measures, binary reduction, and KL data processing. So leaving it informal is currently defensible.
  - **Operational note:** **this run used `max_attempts=8` rather than the default `2`**, but in practice the extra retry budget did not matter here because the verified Bernoulli node succeeded on attempt 1.
  - Verdict: good clean partial success. Slightly narrower as an artifact than the earlier run13s, but still a solid public-facing example of Formal Islands certifying the right scalar core inside a larger theorem. Parent-promotion logic should be added for cheap assembly cases, but this benchmark probably should **not** automatically promote the root unless the remaining burden is judged genuinely lightweight.

## run14-vandermonde-convolution

- `2026-04-06 18:57 PT` — `artifacts/manual-testing/run14-vandermonde-convolution-claude-aristotle`
  - Benchmark quality: acceptable as a small sanity-check benchmark, but weak as a flagship Formal Islands example. The theorem is very simple, and the theorem proof given and the graph contain two complete proof paths that both establish the same final identity.
  - Planner: produced a clean graph with two proof branches: a generating-function proof and a combinatorial proof.
  - Formalizer:
    - `genfn_proof` targeted the right statement and was close in spirit, but failed Lean verification on a relatively small proof-translation issue when converting the antidiagonal coefficient sum into the range-sum form.
    - `comb_proof` succeeded immediately, proving the Vandermonde identity by rewriting `Nat.add_choose_eq` and then `sum_antidiagonal_eq_sum_range_succ`.
  - What is slightly off: the verified `comb_proof__formal_core` is labeled as a narrower supporting core, but its statement is really the full conclusion of the combinatorial node. The only missing piece is proof-path provenance: from the statement alone, one cannot tell that the Lean proof followed the intended double-counting route.
  - Faithfulness issue: the main issue here is not an over-harsh faithfulness guard. It is more that the current classification/reporting still has trouble distinguishing “full statement proved, but intended proof route not reflected” from “narrower local supporting theorem.”
  - Verdict: good as a toy benchmark / smoke test, especially for comparing alternative proof routes, but not a strong public-facing Formal Islands showcase. The benchmark is simply too small and proof-path-ambiguous to demonstrate the repo’s main

## run15-matrix-determinant-lemma

- `2026-04-06 18:57 PT` — `artifacts/manual-testing/run15-matrix-determinant-lemma-claude-aristotle`
  - Benchmark quality: good benchmark in principle. The special case `det(I + uv^T) = 1 + v^T u` is exactly the right finite-dimensional algebraic island, and the full matrix determinant lemma is a natural one-step wrapper once that core is established.
  - Planner: produced a sensible two-node graph, with the rank-one special case feeding the full determinant lemma.
  - Formalizer:
    - `special_case` eventually moved to a concrete statement with `(n : ℕ)` and vectors `u v : Fin n → ℝ`, which is the right theorem family for the benchmark.
    - However, the saved Lean file did not compile: the matrix expression was malformed (`det` was being projected off something Lean parsed as a natural number, and `row (Fin 1)` was used with the wrong argument shape).
    - `root` drifted into an unnecessarily generic library-style theorem with `{n : Type*}` and `{α : Type*} [CommRing α]`, and was hard-rejected by the faithfulness guard.
  - What went wrong: this run shows two separate issues:
    1. Aristotle still defaults to over-generalized theorem shapes (`Type*`, arbitrary scalar rings) unless pushed back.
    2. Even when it does concretize to the right statement, it can still get the concrete Mathlib matrix API wrong and fail Lean verification.
  - Faithfulness issue: the guard behaved reasonably on the root node. The real problem is not that Lean needed `Type*`; it is that Aristotle unnecessarily generalized into a library-style theorem rather than staying with the benchmark’s concrete finite-dimensional setting.
  - Reliability issue: any Aristotle-side claim that the proof “compiles cleanly” should not be trusted unless it matches the pipeline’s stored `verification` record. In this run, the authoritative verification result says the special-case file failed Lean compilation and the root was rejected before final verification.
  - Verdict: useful diagnostic run on a good benchmark, but not a success. The benchmark should stay in the suite, with the main next improvements being stronger anti-generalization pressure on theorem statements and better concrete use of Mathlib’s matrix APIs

- `2026-04-06 22:26 PT` — `artifacts/manual-testing/run15-matrix-determinant-lemma-claude-aristotle-2`
  - Benchmark quality: good benchmark in principle. The special case `det(I + uv^T) = 1 + v^T u` is the right finite-dimensional algebraic island, and the full matrix determinant lemma is a natural one-step wrapper once that core is established.
  - Planner: again produced a sensible two-node graph, with the rank-one special case feeding the full determinant lemma.
  - Formalizer: this time had a genuinely strong run. It verified:
    - `special_case` as a **full match** for the rank-one identity `det(I + uv^T) = 1 + v^T u`, and
    - `root` as a **full match** for the full matrix determinant lemma `det(A + uv^T) = det(A)(1 + v^T A⁻¹ u)`.
  - What improved versus the previous run:
    - the system eventually escaped the unnecessary `Type*` / library-style over-generalization that caused hard rejections before;
    - it also repaired the earlier concrete matrix-API mistakes and successfully used the appropriate Mathlib determinant lemmas.
  - Comparison to previous run:
    - much better than `run15-matrix-determinant-lemma-claude-aristotle`, where the special case never compiled and the root theorem was hard-rejected as over-generalized;
    - this new run turns the benchmark from a diagnostic example into a real success artifact.
  - **Operational note:** **this run used `max_attempts=8` rather than the default `2`**. The extra retry budget clearly mattered: `special_case` verified on attempt 4 and `root` verified on attempt 5, after earlier failed attempts due to both over-generalization and Lean engineering issues.
  - Verdict: strong success, and one of the cleaner algebraic showcase benchmarks in the suite. This is a good public-facing example of the Formal Islands workflow on a benchmark with strong Mathlib support.

## run16-hoeffding-lemma

- `2026-04-07 08:34 PT` — `artifacts/manual-testing/run16-hoeffding-lemma-gemini-aristotle`
  - Benchmark quality: strong benchmark in principle. The proof splits naturally into two meaningful local islands: a probabilistic expectation bound via convexity, and a purely real-analytic log-MGF bound.
  - Planner: produced a clean and sensible graph, with `convexity_expectation` and `log_mgf_bound` feeding the Hoeffding-lemma root theorem.
  - Formalizer: had a genuinely strong run. It verified:
    - `convexity_expectation` as a **full match** for the expectation bound \( \mathbb{E}[e^{sX}] \le e^{-pu}(q + p e^u) \), and
    - `log_mgf_bound` as a **full match** for the scalar inequality \( -pu + \log(q + p e^u) \le u^2/8 \).
  - What went well: both verified nodes are central proof burdens, not helper scraps. The run stayed in the correct theorem family, and the final artifact gives a very good mixed informal/formal decomposition of Hoeffding’s lemma.
  - What still remains informal: the root theorem itself was not formally assembled, but this is not a major weakness here because the two verified child nodes already capture almost all of the mathematical substance of the proof.
  - **Operational note:** **this run used `max_attempts=8` rather than the default `2`**, but in practice the extra retry budget was not very important: `log_mgf_bound` verified on attempt 1, and `convexity_expectation` verified on attempt 2 after one initial faithfulness rejection.
  - Verdict: strong partial success, and a good candidate for public-facing use. Not quite as polished as the very best end-to-end-feeling showcase runs, since the root theorem remains informal, but the certified islands are central, substantial, and mathematically convincing.

## run17-gershgorin-circle

- `2026-04-07 08:33 PT` — `artifacts/manual-testing/run17-gershgorin-circle-gemini-aristotle`
  - Benchmark quality: strong benchmark in principle. The proof has a clean, central local island — the eigenvector maximum-component bound — feeding the full Gershgorin conclusion.
  - Planner: produced a sensible graph with the key local inequality `eigenvector_component_bound` supporting the root theorem.
  - Formalizer: had a genuinely strong run. It verified:
    - `eigenvector_component_bound` as a **full match** for the core Gershgorin inequality obtained from the maximal component of an eigenvector, and
    - `gershgorin_theorem__formal_core` as a strong faithful certified core showing that any eigenvalue with a nonzero eigenvector lies in at least one Gershgorin disc.
  - What went well: the verified nodes are central and mathematically meaningful, not helper scraps. In particular, the maximum-component bound is exactly the main inferential step in the classical proof.
  - What still remains slightly informal / downgraded: the theorem-level verified core is represented as a supporting `faithful_core` rather than full-node certification, mainly because it stops at the eigenvalue/existential-disc formulation instead of explicitly packaging the final spectrum-containment statement.
  - **Operational note:** **this run used `max_attempts=8` rather than the default `2`**, but in practice the extra retry budget was not very important: `eigenvector_component_bound` verified on attempt 1, and `gershgorin_theorem__formal_core` verified on attempt 2 after a minor Lean repair.
  - Verdict: strong partial success, and a good candidate for public-facing use. Not quite a full end-to-end theorem verification, but very close mathematically, and an excellent example of Formal Islands certifying the central local burden of a classical theorem.