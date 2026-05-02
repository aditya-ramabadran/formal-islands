/-
Fixed root Lean statement for Formal Islands runs.
Source: math-inc/FormalQualBench
Original file: FormalQualBench/BurnsidePrimeDegreeTheorem/Main.lean
Commit: efaa113c6a00a79e92842ce541b407d7695d7699
URL: https://github.com/math-inc/FormalQualBench/tree/main/FormalQualBench
-/

import Mathlib.GroupTheory.Perm.Basic
import Mathlib.GroupTheory.Perm.Sign
import Mathlib.GroupTheory.Solvable
import Mathlib.GroupTheory.GroupAction.Defs
import Mathlib.GroupTheory.GroupAction.Transitive
import Mathlib.GroupTheory.GroupAction.MultipleTransitivity
import Mathlib.Data.Nat.Prime.Basic

namespace BurnsidePrimeDegreeTheorem

open MulAction

/--
**Burnside's theorem on transitive permutation groups of prime degree (statement)**.

A transitive permutation group of prime degree is either 2-transitive or has a normal regular
subgroup.
-/
theorem MainTheorem
    {α : Type*} [Fintype α]
    {G : Subgroup (Equiv.Perm α)}
    (htrans : IsPretransitive G α)
    (hp : (Fintype.card α).Prime) :
    IsMultiplyPretransitive G α 2 ∨
      ∃ N : Subgroup G, N.Normal ∧ IsPretransitive N α ∧
        ∀ a : α, MulAction.stabilizer N a = ⊥ := by
  sorry

end BurnsidePrimeDegreeTheorem
