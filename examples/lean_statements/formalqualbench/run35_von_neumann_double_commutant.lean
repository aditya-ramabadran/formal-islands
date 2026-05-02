/-
Fixed root Lean statement for Formal Islands runs.
Source: math-inc/FormalQualBench
Original file: FormalQualBench/VonNeumannDoubleCommutantTheorem/Main.lean
Commit: efaa113c6a00a79e92842ce541b407d7695d7699
URL: https://github.com/math-inc/FormalQualBench/tree/main/FormalQualBench
-/

import Mathlib.Analysis.LocallyConvex.WeakOperatorTopology
import Mathlib.Analysis.VonNeumannAlgebra.Basic

namespace VonNeumannDoubleCommutantTheorem

open scoped Topology

variable {H : Type*} [NormedAddCommGroup H] [InnerProductSpace ℂ H] [CompleteSpace H]

/-- **von Neumann double commutant theorem (statement)**:
for a unital *-subalgebra `S ⊆ B(H)`, being closed in the weak operator topology is equivalent to
being equal to its bicommutant. -/
theorem MainTheorem (S : StarSubalgebra ℂ (H →L[ℂ] H)) :
    IsClosed ((ContinuousLinearMap.toWOT (RingHom.id ℂ) H H) '' (S : Set (H →L[ℂ] H))) ↔
      Set.centralizer (Set.centralizer (S : Set (H →L[ℂ] H))) = (S : Set (H →L[ℂ] H)) := by
  sorry

end VonNeumannDoubleCommutantTheorem

