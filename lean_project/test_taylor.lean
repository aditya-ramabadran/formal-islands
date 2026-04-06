import Mathlib.Analysis.Calculus.Taylor
import Mathlib.Analysis.Calculus.ContDiff.Basic

open Set

lemma one_step_taylor (f : ℝ → ℝ) (hf : ContDiff ℝ 2 f) (x x₀ : ℝ) :
  ∃ x' ∈ uIcc x₀ x,
    f x - f x₀ = deriv f x₀ * (x - x₀) + 1 / 2 * iteratedDeriv 2 f x₀ * (x - x₀) ^ 2 +
      1 / 2 * (iteratedDeriv 2 f x' - iteratedDeriv 2 f x₀) * (x - x₀) ^ 2 := by
  by_cases h : x₀ = x
  · subst h
    use x₀
    simp
  · have h_cont : ContDiffOn ℝ 2 f (uIcc x₀ x) := hf.contDiffOn
    obtain ⟨x', hx', heq⟩ := taylor_mean_remainder_lagrange_iteratedDeriv h h_cont
    use x'
    constructor
    · exact Ioo_subset_Icc_self hx'
    · rw [taylor_within_apply] at heq
      simp only [Finset.sum_range_succ, Nat.factorial_zero, Nat.cast_one, inv_one,
        pow_zero, iteratedDerivWithin_zero, smul_eq_mul, mul_one, Nat.factorial_succ,
        Nat.cast_mul, Nat.cast_add, pow_one, Finset.range_zero] at heq
      have hd1 : iteratedDerivWithin 1 f (uIcc x₀ x) x₀ = deriv f x₀ := by
        rw [iteratedDerivWithin_one]
        exact (hf.differentiable (by norm_num)).differentiableAt.derivWithin (uniqueDiffOn_Icc (by grind) x₀ (by grind))
      rw [hd1] at heq
      have eq2 : f x - (f x₀ + deriv f x₀ * (x - x₀)) = 1 / 2 * iteratedDeriv 2 f x' * (x - x₀) ^ 2 := by
        calc f x - (f x₀ + deriv f x₀ * (x - x₀)) = f x - (∑ x_1 ∈ ∅, ((x_1)! : ℝ)⁻¹ * (x - x₀) ^ x_1 * iteratedDerivWithin x_1 f (uIcc x₀ x) x₀ + 1 * f x₀ + (0 + 1 : ℝ)⁻¹ * (x - x₀) * deriv f x₀) := by
               congr 1
               simp
               ring
             _ = iteratedDeriv (1 + 1) f x' * (x - x₀) ^ (1 + 1) / ((1 + 1) * (0 + 1 : ℝ)) := heq
             _ = 1 / 2 * iteratedDeriv 2 f x' * (x - x₀) ^ 2 := by ring
      linarith
