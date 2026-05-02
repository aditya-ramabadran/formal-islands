/-
Fixed root Lean statement for Formal Islands runs.
Source: leanprover/lean-eval-leaderboard
Original file: benchmark-snapshot/BenchmarkProblems/Catalog.lean
Problem namespace: ProblemIrreducibleNonnegativeMatrixHasPositiveEigenvectorAtSpectralRadius
Leaderboard commit: 6615b604d5d122658e3de1e2e3fc6b31ce0d09f0
Benchmark snapshot commit: f47420d212bf2d9cada6b44d3fbc69e7b9db41c3
URL: https://github.com/leanprover/lean-eval-leaderboard
-/

import Mathlib

namespace ProblemIrreducibleNonnegativeMatrixHasPositiveEigenvectorAtSpectralRadius

open scoped NNReal

-- ANCHOR: irreducible_nonnegative_matrix_has_positive_eigenvector_at_spectralRadius__irreducible_nonnegative_matrix_has_positive_eigenvector_at_spectralRadius
theorem irreducible_nonnegative_matrix_has_positive_eigenvector_at_spectralRadius {n : Type*} [Fintype n] [DecidableEq n]
    (A : Matrix n n ℝ)
    (hA : A.IsIrreducible) :
    ∃ v : n → ℝ,
      Module.End.HasEigenvector (Matrix.toLin' A) (spectralRadius ℝ A).toReal v ∧
      (∀ i, 0 < v i) := by
  sorry
-- ANCHOR_END: irreducible_nonnegative_matrix_has_positive_eigenvector_at_spectralRadius__irreducible_nonnegative_matrix_has_positive_eigenvector_at_spectralRadius

end ProblemIrreducibleNonnegativeMatrixHasPositiveEigenvectorAtSpectralRadius
