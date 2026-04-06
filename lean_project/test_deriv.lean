import Mathlib

open Real

noncomputable def F (q p : ℝ) := p * log (p / q) + (1 - p) * log ((1 - p) / (1 - q)) - 2 * (p - q)^2

lemma hasDerivAt_F (q p : ℝ) (hq0 : 0 < q) (hq1 : q < 1) (hp0 : 0 < p) (hp1 : p < 1) :
  HasDerivAt (F q) (log (p / q) - log ((1 - p) / (1 - q)) - 4 * (p - q)) p := by
  unfold F
  apply HasDerivAt.sub
  · apply HasDerivAt.add
    · apply HasDerivAt.mul
      · exact hasDerivAt_id p
      · apply HasDerivAt.log
        · apply HasDerivAt.div_const (hasDerivAt_id p) q
        · positivity
    · apply HasDerivAt.mul
      · exact (hasDerivAt_id p).const_sub 1
      · apply HasDerivAt.log
        · apply HasDerivAt.div_const ((hasDerivAt_id p).const_sub 1) (1 - q)
        · positivity
  · apply HasDerivAt.mul_const
    · exact HasDerivAt.pow 2 ((hasDerivAt_id p).sub_const q)
  sorry
