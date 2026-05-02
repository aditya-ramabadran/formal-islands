/-
Fixed root Lean statement for Formal Islands runs.
Source: leanprover/lean-eval-leaderboard
Original file: benchmark-snapshot/BenchmarkProblems/Catalog.lean
Problem namespace: ProblemPosSemidefMapExp
Leaderboard commit: 6615b604d5d122658e3de1e2e3fc6b31ce0d09f0
Benchmark snapshot commit: f47420d212bf2d9cada6b44d3fbc69e7b9db41c3
URL: https://github.com/leanprover/lean-eval-leaderboard
-/

import Mathlib

namespace ProblemPosSemidefMapExp

open scoped MatrixOrder Matrix

-- ANCHOR: posSemidef_map_exp__posSemidef_map_exp
theorem posSemidef_map_exp {n : Type*} [Fintype n] [DecidableEq n]
    {A : Matrix n n ℝ} (hA : A.PosSemidef) :
    (A.map Real.exp).PosSemidef := by
  sorry
-- ANCHOR_END: posSemidef_map_exp__posSemidef_map_exp

end ProblemPosSemidefMapExp
