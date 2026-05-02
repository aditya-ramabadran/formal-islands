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

Root attempts use the first theorem/lemma declaration in the file as a hard
target: the scratch file is seeded with that declaration header as a theorem
skeleton, and a returned root artifact is rejected if the declaration header
changes. Child-node attempts receive the same statement only as compatibility
context.

This guard checks declaration-header equality, not a full external benchmark
comparator. If a benchmark requires exact namespace/module packaging, run that
benchmark's comparator or inspect the packaging separately.
