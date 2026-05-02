Lean statement files for fixed-root runs.

Use this folder for external or hand-written Lean root specifications that
should be supplied with `--fixed-root-lean-statement-file`. Keep the informal
theorem/proof JSON in `examples/manual-testing/` or `examples/featured/`, and
put the exact Lean root declaration here.

Example:

```lean
import Mathlib

theorem exact_root (n : Nat) : n = n := by
```

Then run:

```bash
formal-islands run \
  --input examples/manual-testing/my_example.json \
  --backends codex/aristotle \
  --fixed-root-lean-statement-file examples/lean_statements/my_example.lean \
  --fixed-root-source lean-eval
```

The fixed-root source is a provenance label such as `lean-eval`,
`formalqualbench`, or `manual`. It is recorded in the graph/report metadata and
does not change prompt behavior.
