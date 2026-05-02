/-
Fixed root Lean statement for Formal Islands runs.
Source: leanprover/lean-eval-leaderboard
Original file: benchmark-snapshot/BenchmarkProblems/Catalog.lean
Problem namespace: ProblemExistsComplementaryPolynomialOnUnitCircle
Leaderboard commit: 6615b604d5d122658e3de1e2e3fc6b31ce0d09f0
Benchmark snapshot commit: f47420d212bf2d9cada6b44d3fbc69e7b9db41c3
URL: https://github.com/leanprover/lean-eval-leaderboard
-/

import Mathlib

namespace ProblemExistsComplementaryPolynomialOnUnitCircle

open Polynomial

-- ANCHOR: exists_complementary_polynomial_on_unit_circle__exists_complementary_polynomial_on_unit_circle
theorem exists_complementary_polynomial_on_unit_circle (P : ℂ[X])
    (hP : ∀ z : Circle, ‖P.eval (z : ℂ)‖ ≤ 1) :
    ∃ Q : ℂ[X],
      Q.natDegree = P.natDegree ∧
        ∀ z : Circle, ‖P.eval (z : ℂ)‖ ^ 2 + ‖Q.eval (z : ℂ)‖ ^ 2 = 1 := by
  sorry
-- ANCHOR_END: exists_complementary_polynomial_on_unit_circle__exists_complementary_polynomial_on_unit_circle

end ProblemExistsComplementaryPolynomialOnUnitCircle
