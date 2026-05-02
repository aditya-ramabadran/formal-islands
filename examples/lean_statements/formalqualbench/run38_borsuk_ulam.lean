/-
Fixed root Lean statement for Formal Islands runs.
Source: math-inc/FormalQualBench
Original file: FormalQualBench/BorsukUlamTheorem/Main.lean
Commit: efaa113c6a00a79e92842ce541b407d7695d7699
URL: https://github.com/math-inc/FormalQualBench/tree/main/FormalQualBench
-/

import Mathlib

namespace BorsukUlamTheorem

noncomputable section

/-- The unit sphere `S^n` as a subtype of `ℝ^{n+1}`. -/
abbrev UnitSphere (n : ℕ) : Type :=
  {x : EuclideanSpace ℝ (Fin (n + 1)) // ‖x‖ = 1}

/-- The antipodal map `x ↦ -x` on the unit sphere. -/
def antipode {n : ℕ} : UnitSphere n → UnitSphere n :=
  fun x => ⟨-x.1, by simpa [norm_neg] using x.2⟩

/-- Borsuk–Ulam theorem (statement): any continuous map `S^n → ℝ^n` identifies a pair of antipodal
points. -/
theorem MainTheorem (n : ℕ) :
    ∀ f : UnitSphere n → EuclideanSpace ℝ (Fin n),
      Continuous f → ∃ x : UnitSphere n, f x = f (antipode x) := by
  sorry

end

end BorsukUlamTheorem

