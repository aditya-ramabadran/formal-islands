/-
Fixed root Lean statement for Formal Islands runs.
Source: leanprover/lean-eval-leaderboard
Original file: benchmark-snapshot/BenchmarkProblems/Catalog.lean
Problem namespace: ProblemOppenheimInequality
Leaderboard commit: 6615b604d5d122658e3de1e2e3fc6b31ce0d09f0
Benchmark snapshot commit: f47420d212bf2d9cada6b44d3fbc69e7b9db41c3
URL: https://github.com/leanprover/lean-eval-leaderboard
-/

import Mathlib

namespace ProblemOppenheimInequality

open scoped MatrixOrder Matrix

-- ANCHOR: oppenheim_inequality__oppenheim_inequality
theorem oppenheim_inequality {n : Type*} [Fintype n] [DecidableEq n]
    {A B : Matrix n n ℝ} (hA : A.PosSemidef) (hB : B.PosSemidef) :
    A.det * ∏ i, B i i ≤ (A ⊙ B).det := by
  sorry
-- ANCHOR_END: oppenheim_inequality__oppenheim_inequality

end ProblemOppenheimInequality
