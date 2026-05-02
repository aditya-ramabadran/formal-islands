/-
Fixed root Lean statement for Formal Islands runs.
Source: leanprover/lean-eval-leaderboard
Original file: benchmark-snapshot/BenchmarkProblems/Catalog.lean
Problem namespace: ProblemRoucheLogCountingZeroEq
Leaderboard commit: 6615b604d5d122658e3de1e2e3fc6b31ce0d09f0
Benchmark snapshot commit: f47420d212bf2d9cada6b44d3fbc69e7b9db41c3
URL: https://github.com/leanprover/lean-eval-leaderboard
-/

import Mathlib

namespace ProblemRoucheLogCountingZeroEq

open ValueDistribution

-- ANCHOR: rouche_logCounting_zero_eq__rouche_logCounting_zero_eq
theorem rouche_logCounting_zero_eq {f g : ℂ → ℂ} {R : ℝ}
    (hR : 1 ≤ R)
    (hf : Meromorphic f)
    (hg : AnalyticOn ℂ g Set.univ)
    (hbound : ∀ z : ℂ, ‖z‖ = R → ‖g z‖ < ‖f z‖) :
    logCounting (f + g) 0 R = logCounting f 0 R := by
  sorry
-- ANCHOR_END: rouche_logCounting_zero_eq__rouche_logCounting_zero_eq

end ProblemRoucheLogCountingZeroEq
