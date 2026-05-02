/-
Fixed root Lean statement for Formal Islands runs.
Source: math-inc/FormalQualBench
Original file: FormalQualBench/ColorfulCaratheodoryTheorem/Main.lean
Commit: efaa113c6a00a79e92842ce541b407d7695d7699
URL: https://github.com/math-inc/FormalQualBench/tree/main/FormalQualBench
-/

import Mathlib

namespace ColorfulCaratheodoryTheorem

noncomputable section

/-- The colorful Carathéodory theorem (statement): if each of `d+1` sets of points in `ℝ^d`
contains the origin in its convex hull, then one can pick one point from each set so that the
origin lies in the convex hull of the chosen points. -/
theorem MainTheorem (d : ℕ)
    (C : Fin (d + 1) → Set (EuclideanSpace ℝ (Fin d)))
    (hC : ∀ i, (0 : EuclideanSpace ℝ (Fin d)) ∈ convexHull ℝ (C i)) :
    ∃ p : Fin (d + 1) → EuclideanSpace ℝ (Fin d),
      (∀ i, p i ∈ C i) ∧ (0 : EuclideanSpace ℝ (Fin d)) ∈ convexHull ℝ (Set.range p) := by
  sorry

end

end ColorfulCaratheodoryTheorem

