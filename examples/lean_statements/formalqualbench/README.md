FormalQualBench fixed root statements.

These files were copied from `math-inc/FormalQualBench` so overlapping
`examples/manual-testing` benchmarks can be rerun with
`--fixed-root-lean-statement-file` and `--fixed-root-source formalqualbench`.

Source repository:
https://github.com/math-inc/FormalQualBench

Copied commit:
`efaa113c6a00a79e92842ce541b407d7695d7699`

Current overlaps:

- `run21_colorful_caratheodory.lean`
- `run22_jordan_derangement.lean`
- `run23_de_bruijn_erdos.lean`
- `run24_banach_stone.lean`
- `run25_gleason_kahane_zelazko.lean`
- `run26_runge_theorem.lean`
- `run28_dense_linear_order_quantifier_elimination.lean`
- `run30_burnside_prime_degree.lean`
- `run31_schauder_fixed_point.lean`
- `run34_pontryagin_duality_lca.lean`
- `run35_von_neumann_double_commutant.lean`
- `run38_borsuk_ulam.lean`
- `run39_skolem_mahler_lech.lean`
- `run42_jordan_cycle_theorem.lean`

Example command:

```bash
formal-islands run \
  --input examples/manual-testing/run24_banach_stone.json \
  --backends codex/aristotle \
  --fixed-root-lean-statement-file examples/lean_statements/formalqualbench/run24_banach_stone.lean \
  --fixed-root-source formalqualbench
```
