# OpenGauss Backend — Integration Plan

This document records the design exploration for adding OpenGauss as an optional
formalization-only backend. It is intentionally planning-only: nothing here has been
implemented yet.

---

## What OpenGauss Is

OpenGauss (`gauss` CLI) is a project-scoped Lean workflow orchestrator from Math, Inc.
It wraps lean4-skills workflows including `/prove`, `/draft`, `/formalize`, and
`/autoformalize`. It is **not** a new inference engine — it manages backend child sessions,
project state, and swarm lifecycle on top of existing models (defaults to `claude-code`).

Relevant commands:
- `/autoformalize <statement>` — autonomous synthesis, fire-and-forget, spawns a managed child session
- `/formalize <statement>` — interactive, needs human in the loop (not useful for our pipeline)
- `/swarm` — list running/completed agents
- `/swarm attach <task-id>` / `/swarm cancel <task-id>` — lifecycle management

---

## What `gauss --help` Revealed

```
usage: gauss [-h] [--version] [--resume SESSION] [--continue [SESSION_NAME]]
             [--worktree] [--yolo] [--pass-session-id]
             {chat,model,gateway,setup,...}
```

Key findings:
- **No `--command` or headless flag exists.** The workflow commands (`/autoformalize`,
  `/swarm`, etc.) are only accessible inside the interactive `gauss chat` REPL.
- `--yolo` — bypasses all approval prompts, needed for non-interactive use.
- `--worktree` — runs in an isolated git worktree, useful for parallel node jobs.
- `--resume SESSION` — reattach to a previous session by ID, useful for polling.

There is no one-shot invocation mode like `claude -p "..."` or `codex run`.

---

## Integration Approach

Since there is no headless flag, the only practical approach is to drive `gauss chat`
as a subprocess with stdin piping:

```
echo "/project use <lean_project_path>\n/autoformalize <statement>\n" \
  | gauss chat --yolo
```

Steps:
1. Spawn `gauss chat --yolo` with stdin as a pipe.
2. Send `/project use <lean_project_path>` + newline.
3. Send `/autoformalize <informal_statement>` + newline.
4. Read stdout until a task/session ID appears in the output.
5. Separately re-invoke `gauss --resume <session_id>`, send `/swarm`, parse status.
6. Poll step 5 on a timer until status is done/failed.
7. On done: scan `lean_project/FormalIslands/Generated/` for new/modified `.lean` files since task start.
8. Copy result into our scratch worker file; feed into `LeanVerifier.verify_existing_file()`.
9. On timeout: attempt salvage from whatever partial `.lean` exists (same as agentic salvage).
10. On done/cancel: send `/swarm cancel <task-id>` to clean up.

### Parallism via `--worktree`
The `--worktree` flag creates an isolated git worktree per session. This aligns
naturally with parallel node formalization in `formalize_candidate_nodes`. Each parallel
Aristotle/OpenGauss job gets its own worktree, avoiding scratch-file collisions.

---

## How This Compares to Existing Backends

| Aspect | Aristotle | OpenGauss |
|---|---|---|
| Interface | Python SDK (`aristotlelib`) | Stateful CLI session (stdin pipe) |
| Invocation | `client.submit(...)` | Pipe into `gauss chat --yolo` |
| Status | SDK callback / poll via `project.refresh()` | `/swarm` command output |
| Project setup | None needed | `/project init` required once per project |
| Auth | `ARISTOTLE_API_KEY` env var | `ANTHROPIC_API_KEY` or Claude login |
| Timeout control | We control via `timeout_seconds` | External — need our own poll wrapper |
| Output location | Known (tarball from SDK) | Need to scan for modified `.lean` files |
| Parallel isolation | Snapshot temp dir per job | `--worktree` flag |
| Retry on faithfulness reject | Supported (re-submit with feedback) | Possible (new session with feedback) |

---

## New File: `src/formal_islands/backends/opengauss.py`

Class: `OpenGaussBackend` with at minimum:
- `gauss_executable: str = "gauss"` — path to gauss binary
- `lean_project_path: Path` — the registered Lean project root
- `polling_interval_seconds: float = 30.0`
- `timeout_seconds: float | None = None`
- `log_dir: Path | None = None`

Main method: `submit_autoformalize(statement, scratch_file_path, task_name) -> OpenGaussRun`

This does NOT implement `AgenticStructuredBackend` directly (OpenGauss is not a
general-purpose LLM backend); it fits the same role as `AristotleBackend` — a
formalization-only adapter invoked from `formalize/loop.py`.

---

## CLI Integration

- Add `"opengauss"` to `resolve_backend_name()` in `smoke.py`
- `build_backend(formalization=True)` instantiates `OpenGaussBackend`
- `--formalization-backend opengauss` activates it; planning backend unaffected
- New optional flags: `--opengauss-project-path`, `--opengauss-executable`

---

## Open Questions (Must Resolve Before Implementing)

1. **Does `gauss chat` process commands and exit cleanly when stdin is a pipe?**
   Test manually: `echo "/autoformalize the naturals are infinite" | gauss chat --yolo`
   Some REPLs hang waiting for more stdin; if so, this approach needs a PTY wrapper.

2. **Does `/autoformalize` accept a target file path** as an argument, or does it always
   choose its own output location? If we can't direct the output, we need to scan for
   the newest `.lean` file written after the job started.

3. **What does `/swarm` output look like when a job is running vs. done?**
   We need the exact status strings to parse reliably.

4. **Does the project need to be pre-initialized** (`/project init`) before
   `/autoformalize` works, or can both run in the same session?

5. **Does `--worktree` isolate the project state** well enough that two parallel
   `gauss chat` sessions don't conflict on the same `.gauss/project.yaml`?

---

## Recommended Next Step

Before writing any backend code: clone OpenGauss locally (already installed), then test:

```bash
# Test 1: does gauss chat accept piped stdin cleanly?
echo -e "/project use $(pwd)/lean_project\n/autoformalize 1 + 1 = 2\n" | gauss chat --yolo

# Test 2: what does /swarm output look like?
gauss chat --yolo
# then type: /swarm

# Test 3: can we get a session ID from the stdout of an autoformalize?
```

If test 1 exits cleanly and produces a parseable session/task ID in stdout, the
two-phase implementation becomes straightforward. If it hangs, a PTY wrapper
(e.g., `pexpect`) would be needed instead of a plain subprocess pipe.

---

## Implementation Phases

**Phase 1 (minimal — just get it working):**
- `OpenGaussBackend` with stdin-pipe approach
- Poll loop with `/swarm` output parsing
- Scan for result `.lean` file
- Feed into `LeanVerifier.verify_existing_file()`
- `--formalization-backend opengauss` CLI flag

**Phase 2 (nice to have):**
- `--worktree` support for parallel node formalization
- Faithfulness feedback loop (re-submit new session with feedback on first attempt)
- Configurable `gauss.autoformalize.backend` passthrough for underlying model choice
- Progress streaming from the child session (if gauss exposes it)
