import Mathlib
open Real
open Filter Topology

lemma hasDerivAt_log_div (q : ℝ) (hq0 : 0 < q) (p : ℝ) (hp0 : 0 < p) :
  HasDerivAt (fun x => log (x / q)) p⁻¹ p := by
  have : (fun x => log (x / q)) =ᶠ[𝓝 p] fun x => log x - log q := by
    apply eventually_of_mem (Ioi_mem_nhds hp0)
    intro x hx
    exact log_div (ne_of_gt hx) (ne_of_gt hq0)
  refine HasDerivAt.congr_of_eventuallyEq ?_ this.symm
  have h1 : HasDerivAt (fun x => log x) p⁻¹ p := hasDerivAt_log (ne_of_gt hp0)
  have h2 : HasDerivAt (fun x => log x - log q) (p⁻¹ - 0) p := HasDerivAt.sub_const h1 (log q)
  simpa using h2
