/-
Fixed root Lean statement for Formal Islands runs.
Source: math-inc/FormalQualBench
Original file: FormalQualBench/JordanCycleTheorem/Main.lean
Commit: efaa113c6a00a79e92842ce541b407d7695d7699
URL: https://github.com/math-inc/FormalQualBench/tree/main/FormalQualBench
-/

import Mathlib.GroupTheory.GroupAction.Jordan

namespace JordanCycleTheorem

open MulAction

/-- Jordan's theorem (cycle version): if `G ≤ Equiv.Perm α` is preprimitive and contains a cycle
whose support has prime cardinality `p` with `p + 3 ≤ Nat.card α`, then
`alternatingGroup α ≤ G`.

Note: this statement already appears in mathlib as a `proof_wanted` declaration
`alternatingGroup_le_of_isPreprimitive_of_isCycle_mem`. -/
theorem MainTheorem {α : Type*} [Fintype α] [DecidableEq α] {G : Subgroup (Equiv.Perm α)}
    (hG : IsPreprimitive G α) {p : ℕ} (hp : p.Prime) (hp' : p + 3 ≤ Nat.card α)
    {g : Equiv.Perm α} (hgc : g.IsCycle) (hgp : g.support.card = p) (hg : g ∈ G) :
    alternatingGroup α ≤ G := by
  sorry

end JordanCycleTheorem
