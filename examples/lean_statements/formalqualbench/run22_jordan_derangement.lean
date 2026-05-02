/-
Fixed root Lean statement for Formal Islands runs.
Source: math-inc/FormalQualBench
Original file: FormalQualBench/JordanDerangementTheorem/Main.lean
Commit: efaa113c6a00a79e92842ce541b407d7695d7699
URL: https://github.com/math-inc/FormalQualBench/tree/main/FormalQualBench
-/

import Mathlib.Combinatorics.Derangements.Basic
import Mathlib.GroupTheory.GroupAction.Jordan

namespace JordanDerangementTheorem

open MulAction

/-- Jordan's derangement theorem: a finite transitive permutation group on a nontrivial set contains
a derangement (equivalently, a fixed-point-free element). -/
theorem MainTheorem {α : Type*} [Finite α] [Nontrivial α]
    {G : Subgroup (Equiv.Perm α)} (hG : IsPretransitive G α) :
    ∃ g : Equiv.Perm α, g ∈ G ∧ g ∈ derangements α := by
  sorry

end JordanDerangementTheorem
