import Mathlib.Data.Real.Basic
import Mathlib.Tactic.Linarith
import Mathlib.Tactic.Positivity
import Mathlib.Tactic.Ring

lemma bernoulli_ineq_second_deriv_bound (p : ℝ) (hp : 0 < p) (hp1 : p < 1) :
  1 / (p * (1 - p)) - 4 ≥ 0 := by
  have h1 : 0 < p * (1 - p) := by nlinarith
  have h2 : 1 - 4 * p * (1 - p) = (1 - 2 * p)^2 := by ring
  have h3 : 0 ≤ 1 - 4 * p * (1 - p) := by
    rw [h2]
    positivity
  have h4 : p * (1 - p) ≤ 1 / 4 := by linarith
  have h5 : 4 ≤ 1 / (p * (1 - p)) := by
    exact (le_one_div (by positivity) h1).mpr h4
  linarith
