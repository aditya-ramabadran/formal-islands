/-
Fixed root Lean statement for Formal Islands runs.
Source: math-inc/FormalQualBench
Original file: FormalQualBench/GleasonKahaneZelazkoTheorem/Main.lean
Commit: efaa113c6a00a79e92842ce541b407d7695d7699
URL: https://github.com/math-inc/FormalQualBench/tree/main/FormalQualBench
-/

import Mathlib

namespace GleasonKahaneZelazkoTheorem

/-- Gleason-Kahane-Zelazko theorem for complex Banach algebras: a normalized complex-linear
functional on a complex Banach algebra that does not vanish on invertible elements is an algebra
homomorphism. -/
theorem MainTheorem (A : Type*) [NormedRing A] [NormedAlgebra ℂ A]
    [CompleteSpace A] :
    ∀ φ : A →ₗ[ℂ] ℂ, φ 1 = 1 →
      (∀ a : A, IsUnit a → φ a ≠ 0) →
      ∃ ψ : A →ₐ[ℂ] ℂ, ψ.toLinearMap = φ := by
  sorry

end GleasonKahaneZelazkoTheorem
