# OpenGauss Backend — Integration Plan

This document records the design exploration for adding OpenGauss as an optional
formalization-only backend. **The original stdin-pipe plan was wrong — see below.**

---

## What OpenGauss Is

OpenGauss (`gauss` CLI) is a project-scoped Lean workflow orchestrator from Math, Inc.
It wraps lean4-skills workflows including `/autoformalize`, `/prove`, `/draft`, `/formalize`.

It is **not** a new inference engine. What it actually does:
1. Discovers the active Lean project (via `.gauss/project.yaml`)
2. Stages lean4-skills plugin assets and a per-project `lean-lsp.mcp.json` config
3. Writes a startup context file with project info + workflow instruction
4. Spawns Claude Code as a subprocess with the staged MCP and plugin, telling it to run `/lean4:autoformalize <statement>` as its first action
5. Tracks the child process via stream-json output parsing

**OpenGauss is fundamentally a staging + subprocess launcher for Claude Code + lean4-skills.** The actual Lean work happens inside a regular Claude Code session.

---

## Why The Original Plan Was Wrong

The previous plan proposed:
```bash
echo "/autoformalize ..." | gauss chat --yolo
```
This **does not work** because:
- `gauss chat` uses **prompt_toolkit** for its TUI — it does not read from stdin as a pipe
- Stdin is fully disconnected from the command dispatch loop; prompt_toolkit has its own key binding / input queue
- The `-q` / single-query flag does exist (`gauss chat -q "/autoformalize ..."`) but `/autoformalize` spawns a background daemon thread — it would die when the parent gauss process exits, before Claude Code can do any work

The correct insight: we should **bypass the Gauss REPL entirely** and call the same Claude Code subprocess that Gauss would spawn, by importing Gauss's staging logic directly.

---

## What Gauss Actually Produces (Code-Level)

### The child Claude Code argv (from `_build_claude_runtime`):
```
claude
  --model <CLAUDE_MODEL>
  --dangerously-skip-permissions
  "You are in a Gauss-managed Lean workflow session.
   Read the startup context at <startup_context_path> first.
   Then run this command inside the active project as your first workflow action:
   /lean4:autoformalize <user_statement>"
```

### The child env includes:
- `HOME=<backend_home>` — where the lean-lsp MCP is baked into `.claude.json`
- `LEAN4_PLUGIN_ROOT`, `LEAN4_SCRIPTS`, `LEAN4_REFS` — pointing to lean4-skills plugin
- `GAUSS_YOLO_MODE=1`
- Auth credentials forwarded from the parent

### The lean-lsp MCP config (`lean-lsp.mcp.json`):
```json
{
  "mcpServers": {
    "lean-lsp": {
      "type": "stdio",
      "command": "<uvx_path>",
      "args": ["--from", "lean-lsp-mcp", "lean-lsp-mcp"],
      "env": { "LEAN_PROJECT_PATH": "<lean_root>" }
    }
  }
}
```
This is generated per project with the correct `LEAN_PROJECT_PATH`.

### Global assets already staged on this machine:
- `~/.gauss/autoformalize/assets/lean4-skills/` — the lean4-skills plugin (already present)
- `~/.gauss/autoformalize/claude-code/managed/` — per-session staging area

---

## Correct Integration Approach

### Key insight
We don't need to drive the Gauss REPL. We call `resolve_autoformalize_request()` from
Python to stage the per-project MCP config and get the exact Claude Code argv/env, then
run Claude Code ourselves using our own subprocess management loop.

### Prerequisites (one-time setup)
1. `lean_project` must have `.gauss/project.yaml` — run once:
   ```bash
   cd lean_project && /Users/adihaya/GitHub/OpenGauss/venv/bin/gauss project init
   ```
   This creates `.gauss/project.yaml` with `lean_root: "."` and the project manifest.

2. `rg` (ripgrep) must be in PATH — required by `resolve_autoformalize_request`.

### Runtime flow (per formalization job)

```python
import sys
sys.path.insert(0, "/Users/adihaya/GitHub/OpenGauss")

from gauss_cli.autoformalize import resolve_autoformalize_request

# 1. Stage per-project assets and get the child Claude Code argv/env
plan = resolve_autoformalize_request(
    f"/autoformalize {informal_statement}",
    config=None,                      # reads ~/.gauss/config.yaml
    active_cwd=str(lean_project_path),
)
# plan.handoff_request.argv  → [claude, --model, ..., --dangerously-skip-permissions, "<prompt>"]
# plan.handoff_request.env   → full env with MCP baked in (HOME points to backend_home)
# plan.handoff_request.cwd   → lean_project_path

# 2. Add --output-format stream-json if not present (enables structured progress)
argv = list(plan.handoff_request.argv)
if "--output-format" not in argv:
    argv[1:1] = ["-p", "--output-format", "stream-json"]

# 3. Run Claude Code as a subprocess (mirrors swarm_manager._run_claude_code_background)
proc = subprocess.Popen(
    argv,
    cwd=plan.handoff_request.cwd,
    env=plan.handoff_request.env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    stdin=subprocess.DEVNULL,
    text=True,
)

# 4. Stream parse output, poll with timeout
lean_status = None
for line in proc.stdout:
    event = parse_stream_event(line)   # watch for lean_status, result, errors
    if timed_out: proc.terminate(); break

proc.wait()

# 5. Scan for output Lean file
# Claude Code, given a target scratch_file path in the prompt, writes there directly.
# Or scan Generated/ for newest .lean file since job_start_time.
result_lean = find_newest_lean_file(
    lean_project_path / "FormalIslands" / "Generated",
    since=job_start_time,
)
```

### Output file collection
The lean4:autoformalize skill writes to the Lean project. Two approaches:
- **Preferred**: include the target scratch file path in the user_instruction passed to Gauss:
  `f"/autoformalize {statement}\n\nWrite the result to: {scratch_file_path}"`
- **Fallback**: scan `lean_project/FormalIslands/Generated/` for files modified after job start

### Parallelism
Unlike Aristotle (which takes a pruned snapshot), OpenGauss child sessions write into
the live Lean project. For parallel node formalization, each job should target a unique
scratch file path to avoid clobbering. The `--worktree` flag exists in Gauss but we
skip it since managing worktrees adds complexity; using distinct file paths suffices.

---

## `_run_claude_code_background` equivalent (minimal impl)

We do not need to import Gauss's `swarm_manager`. We can write a slim equivalent:

```python
def _run_opengauss_job(argv, cwd, env, timeout_seconds) -> tuple[str, str]:
    """Run the staged Claude Code subprocess, return (lean_status, result_text)."""
    import subprocess, json, time

    proc = subprocess.Popen(
        argv, cwd=cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, text=True,
    )
    lean_status = "starting"
    result_text = ""
    deadline = time.time() + (timeout_seconds or 1e9)

    for line in proc.stdout:
        line = line.rstrip("\n")
        if not line:
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        etype = event.get("type", "")
        if etype == "tool_result":
            content = event.get("content", "")
            if isinstance(content, str):
                if "sorry" in content.lower():
                    lean_status = "has_sorry"
                elif "no errors" in content.lower() or "goals accomplished" in content.lower():
                    lean_status = "verified"
        elif etype == "result":
            result_text = event.get("result", "")[:500]
            if event.get("subtype") == "error_max_turns":
                lean_status = "max_turns"
        if time.time() > deadline:
            proc.terminate()
            lean_status = "timeout"
            break

    proc.wait()
    return lean_status, result_text
```

---

## How This Compares to Existing Backends

| Aspect | Aristotle | OpenGauss |
|---|---|---|
| Interface | Python SDK (`aristotlelib`) | Python import → staged Claude Code subprocess |
| Invocation | `client.submit(...)` | `resolve_autoformalize_request()` → Popen |
| Auth | `ARISTOTLE_API_KEY` env var | Claude Code local login or `ANTHROPIC_API_KEY` |
| Output location | SDK returns tarball | Target scratch file path or Generated/ scan |
| Timeout control | SDK param | Our own poll loop with deadline |
| Parallel isolation | Pruned snapshot per job | Distinct scratch file paths per job |
| Lean project req | Just the skeleton | `.gauss/project.yaml` + project init |
| Retry on faithfulness reject | Re-submit with feedback | New `resolve_autoformalize_request` call |
| Underlying model | Harmonic's backend | Claude (configurable in `~/.gauss/config.yaml`) |
| lean4-skills MCP | Not used | Included — gives Lean LSP access |

---

## New File: `src/formal_islands/formalization/opengauss.py`

Class: `OpenGaussBackend` with:
- `opengauss_repo_path: Path` — path to OpenGauss checkout (for sys.path insert)
- `lean_project_path: Path` — the registered Lean project root
- `timeout_seconds: float | None = None`
- `log_dir: Path | None = None`

Main method: `submit(node_id, informal_statement, scratch_file_path) -> OpenGaussRun`

Internal steps:
1. Call `resolve_autoformalize_request()` with the statement + scratch file target
2. Patch argv to add `--output-format stream-json -p`
3. Run subprocess with our poll loop
4. Return `OpenGaussRun(lean_status, lean_file_path, elapsed_seconds)`

This fits the same adapter role as `AristotleBackend` in `formalize/loop.py`.

---

## CLI Integration

- Add `"opengauss"` to `resolve_backend_name()` in `smoke.py`
- `build_backend(formalization=True)` instantiates `OpenGaussBackend`
- `--formalization-backend opengauss` activates it; planning backend unaffected
- New optional flags: `--opengauss-repo-path` (default: env `OPENGAUSS_REPO_PATH`)

---

## One-Time Setup Checklist

```bash
# 1. Initialize lean_project as a Gauss project (creates .gauss/project.yaml)
cd /path/to/formal-islands/lean_project
/Users/adihaya/GitHub/OpenGauss/venv/bin/gauss project init

# 2. Verify lean4-skills assets are staged (already done on this machine)
ls ~/.gauss/autoformalize/assets/lean4-skills/

# 3. Set OPENGAUSS_REPO_PATH (or pass --opengauss-repo-path)
export OPENGAUSS_REPO_PATH=/Users/adihaya/GitHub/OpenGauss

# 4. Run a test formalization
./.venv/bin/formal-islands-smoke run-benchmark \
  --planning-backend claude \
  --formalization-backend opengauss \
  --input examples/manual-testing/run11_two_point_log_sobolev.json \
  --output-dir artifacts/manual-testing/run11-opengauss-test
```

---

## Open Questions (Still To Resolve)

1. **Does lean4:autoformalize write to a named output file**, or does it always choose its own?
   Read `~/.gauss/autoformalize/assets/lean4-skills/plugins/lean4/` skills to confirm.
   If not, the Generated/ scan fallback is required.

2. **Does `resolve_autoformalize_request` need `rg` in PATH for every call**, or only for
   the initial asset staging? Could be skipped if assets are already warm.

3. **Can we skip project.yaml discovery** by calling `_build_claude_runtime` directly
   with a synthetic `GaussProject` object? This would remove the `project init` prerequisite.

4. **Does the lean-lsp MCP start cleanly for a fresh `lean_project`** that has never been
   used with Gauss before? The MCP (`lean-lsp-mcp`) needs `LEAN_PROJECT_PATH` to be a
   valid Lean 4 project with a `lakefile.lean` or `lakefile.toml`.

---

## Implementation Phases

**Phase 1 (minimal — just get it working):**
- `OpenGaussBackend` class with `resolve_autoformalize_request()` import
- Stream-json poll loop with timeout
- Generated/ scan for output file
- Feed into `LeanVerifier.verify_existing_file()`
- `--formalization-backend opengauss` CLI flag + `OPENGAUSS_REPO_PATH` env var

**Phase 2 (nice to have):**
- Bypass `project.yaml` requirement by synthesizing a `GaussProject` directly
- Faithfulness feedback loop (new call with feedback string appended to statement)
- Configurable underlying model via `--opengauss-model` (sets `~/.gauss/config.yaml` override)
- Progress streaming to `_progress.log` by tailing the stream-json events live

**Not planned:**
- `--worktree` isolation (distinct scratch file paths are sufficient)
- `/swarm` polling (we own the subprocess directly; no need for Gauss's task tracker)
- Interactive mode (we always run in `-p --output-format stream-json` background mode)
