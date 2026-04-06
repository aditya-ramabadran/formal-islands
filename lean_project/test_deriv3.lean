import Mathlib
open Real
open Filter Topology

noncomputable def bernoulliKL (p q : ℝ) : ℝ :=
  p * log (p / q) + (1 - p) * log ((1 - p) / (1 - q))

lemma hasDerivAt_log_div (q : ℝ) (hq0 : 0 < q) (p : ℝ) (hp0 : 0 < p) :
  HasDerivAt (fun x => log (x / q)) p⁻¹ p := by
  have : (fun x => log (x / q)) =ᶠ[𝓝 p] fun x => log x - log q := by
    apply eventually_of_mem (Ioi_mem_nhds hp0)
    intro x hx
    exact log_div (ne_of_gt hx) (ne_of_gt hq0)
  refine HasDerivAt.congr_of_eventuallyEq ?_ this
  have h1 : HasDerivAt (fun x => log x) p⁻¹ p := hasDerivAt_log (ne_of_gt hp0)
  exact h1.sub_const (log q)

lemma hasDerivAt_p_log_div (q : ℝ) (hq0 : 0 < q) (p : ℝ) (hp0 : 0 < p) :
  HasDerivAt (fun x => x * log (x / q)) (log (p / q) + 1) p := by
  have h1 : HasDerivAt (fun x => log (x / q)) p⁻¹ p := hasDerivAt_log_div q hq0 p hp0
  have h2 := HasDerivAt.mul (hasDerivAt_id p) h1
  have h3 : p * p⁻¹ = 1 := mul_inv_cancel₀ (ne_of_gt hp0)
  have h4 : log (p / q) * 1 + p * p⁻¹ = log (p / q) + 1 := by rw [mul_one, h3]
  exact h4 ▸ h2
