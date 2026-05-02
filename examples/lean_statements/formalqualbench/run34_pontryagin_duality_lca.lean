/-
Fixed root Lean statement for Formal Islands runs.
Source: math-inc/FormalQualBench
Original file: FormalQualBench/PontryaginDuality/Main.lean
Commit: efaa113c6a00a79e92842ce541b407d7695d7699
URL: https://github.com/math-inc/FormalQualBench/tree/main/FormalQualBench
-/

import Mathlib.Topology.Algebra.PontryaginDual

namespace PontryaginDuality

open scoped Topology

/-- A topological-group isomorphism `e : A ≃ₜ* PontryaginDual (PontryaginDual A)` realizes
Pontryagin biduality if it agrees pointwise with the canonical evaluation map
`a ↦ (fun χ ↦ χ a)`. -/
def IsEvaluationIso (A : Type*) [CommGroup A] [TopologicalSpace A] [IsTopologicalGroup A]
    (e : A ≃ₜ* PontryaginDual (PontryaginDual A)) : Prop :=
  ∀ a (χ : PontryaginDual A), e a χ = χ a

/-- **Pontryagin duality (canonical formulation)**: every locally compact Hausdorff abelian
topological group is canonically topologically isomorphic to its double Pontryagin dual, via the
evaluation map `a ↦ (χ ↦ χ a)`. -/
theorem MainTheorem (A : Type*) [CommGroup A] [TopologicalSpace A]
    [IsTopologicalGroup A]
    [LocallyCompactSpace A] [T2Space A] :
    ∃ e : A ≃ₜ* PontryaginDual (PontryaginDual A), IsEvaluationIso A e := by
  sorry

end PontryaginDuality
