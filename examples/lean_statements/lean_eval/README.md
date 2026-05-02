Lean Eval fixed root statements.

These files were copied from the Lean Eval leaderboard benchmark snapshot so
overlapping `examples/manual-testing` inputs can be rerun with
`--fixed-root-lean-statement-file` and `--fixed-root-source lean-eval`.

Source repository:
https://github.com/leanprover/lean-eval-leaderboard

Leaderboard commit:
`6615b604d5d122658e3de1e2e3fc6b31ce0d09f0`

Benchmark snapshot commit:
`f47420d212bf2d9cada6b44d3fbc69e7b9db41c3`

Current entries:

- `run44_oppenheim_inequality.lean`
- `run45_perron_frobenius_positive_eigenvector.lean`
- `run46_rouche_log_counting_zero_eq.lean`
- `run47_entrywise_exponential_psd.lean`
- `run48_complementary_polynomial_unit_circle.lean`

Example command:

```bash
formal-islands run \
  --input examples/manual-testing/run44_oppenheim_inequality.json \
  --backends codex/aristotle \
  --fixed-root-lean-statement-file examples/lean_statements/lean_eval/run44_oppenheim_inequality.lean \
  --fixed-root-source lean-eval
```
