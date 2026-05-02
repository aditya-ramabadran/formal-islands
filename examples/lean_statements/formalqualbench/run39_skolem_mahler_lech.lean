/-
Fixed root Lean statement for Formal Islands runs.
Source: math-inc/FormalQualBench
Original file: FormalQualBench/SkolemMahlerLechTheorem/Main.lean
Commit: efaa113c6a00a79e92842ce541b407d7695d7699
URL: https://github.com/math-inc/FormalQualBench/tree/main/FormalQualBench
-/

import Mathlib.Algebra.LinearRecurrence
import Mathlib.Data.Finset.Basic

namespace SkolemMahlerLechTheorem

/-- The arithmetic progression `{a + d * n | n : ℕ}`. -/
def arithProg (a d : ℕ) : Set ℕ :=
  Set.range fun n : ℕ => a + d * n

/-- Skolem-Mahler-Lech theorem: the zero set of a linear recurrence sequence over a characteristic
zero field is a finite union of arithmetic progressions plus a finite exceptional set. -/
theorem MainTheorem (K : Type*) [Field K] [CharZero K] (E : LinearRecurrence K) (u : ℕ → K)
    (hu : E.IsSolution u) :
    ∃ s : Finset ℕ, ∃ t : Finset (ℕ × ℕ),
      {n : ℕ | u n = 0} = (s : Set ℕ) ∪ ⋃ p ∈ (t : Set (ℕ × ℕ)), arithProg p.1 p.2 := by
  sorry

end SkolemMahlerLechTheorem
