from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from formal_islands.backends import (
    BackendInvocationError,
    BackendOutputError,
    BackendUnavailableError,
    ClaudeCodeBackend,
    CodexCLIBackend,
    MockBackend,
    StructuredBackendRequest,
)


def test_mock_backend_returns_queued_payload() -> None:
    backend = MockBackend(queued_payloads=[{"ok": True}])
    request = StructuredBackendRequest(prompt="Hello", json_schema={"type": "object"})

    response = backend.run_structured(request)

    assert response.payload == {"ok": True}
    assert backend.requests == [request]


def test_mock_backend_raises_when_queue_is_empty() -> None:
    backend = MockBackend(queued_payloads=[])

    with pytest.raises(BackendInvocationError):
        backend.run_structured(StructuredBackendRequest(prompt="x", json_schema={"type": "object"}))


def test_claude_backend_parses_structured_output() -> None:
    backend = ClaudeCodeBackend(model="sonnet", max_output_tokens=1234, effort="medium")
    request = StructuredBackendRequest(
        prompt="Extract the graph.",
        system_prompt="Return JSON only.",
        json_schema={"type": "object"},
    )

    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps({"structured_output": {"nodes": []}}),
        stderr="",
    )

    with patch("shutil.which", return_value="/usr/bin/claude"), patch(
        "subprocess.run", return_value=completed
    ) as run_mock:
        response = backend.run_structured(request)

    command = run_mock.call_args.args[0]
    env = run_mock.call_args.kwargs["env"]

    assert Path(command[0]).name == "claude"
    assert command[1:3] == ["-p", "--output-format"]
    assert "--json-schema" in command
    assert "--tools" in command
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "1234"
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == "medium"
    assert response.payload == {"nodes": []}


def test_claude_backend_writes_debug_log_when_log_dir_is_configured(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    backend = ClaudeCodeBackend(model="sonnet", log_dir=log_dir, timeout_seconds=120.0)
    request = StructuredBackendRequest(
        prompt="Return an empty candidate list.",
        system_prompt="Return JSON only.",
        json_schema={"type": "object", "properties": {"candidates": {"type": "array"}}},
        cwd=tmp_path,
        task_name="select_candidates",
    )

    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps({"structured_output": {"candidates": []}}),
        stderr="",
    )

    with patch("shutil.which", return_value="/usr/bin/claude"), patch(
        "subprocess.run", return_value=completed
    ):
        backend.run_structured(request)

    logs = sorted(log_dir.glob("select_candidates_*.json"))
    assert logs
    payload = json.loads(logs[0].read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["payload"] == {"candidates": []}
    assert payload["agentic"] is False
    assert isinstance(payload["elapsed_seconds"], float)


def test_claude_backend_run_agentic_structured_uses_tool_mode(tmp_path: Path) -> None:
    backend = ClaudeCodeBackend(model="sonnet")
    request = StructuredBackendRequest(
        prompt="Edit the scratch file and return JSON.",
        system_prompt="Return JSON only.",
        json_schema={
            "type": "object",
            "properties": {
                "lean_theorem_name": {"type": "string"},
                "lean_statement": {"type": "string"},
                "final_file_path": {"type": "string"},
                "plan_file_path": {"type": "string"},
            },
        },
        cwd=tmp_path,
        task_name="formalize_node_agentic",
    )

    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(
            {
                "structured_output": {
                    "lean_theorem_name": "t",
                    "lean_statement": "True",
                    "final_file_path": "/tmp/t.lean",
                    "plan_file_path": "/tmp/t_plan.md",
                }
            }
        ),
        stderr="",
    )

    with patch("shutil.which", return_value="/usr/bin/claude"), patch(
        "subprocess.run", return_value=completed
    ) as run_mock:
        backend.run_agentic_structured(request, timeout_seconds=420.0)

    command = run_mock.call_args.args[0]
    assert "--permission-mode" in command
    assert "bypassPermissions" in command
    assert "--dangerously-skip-permissions" in command
    assert "--tools" in command
    assert "default" in command
    assert "--add-dir" not in command
    assert "--setting-sources" in command


def test_claude_backend_uses_common_fallback_executable_locations(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    local_bin = fake_home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    executable = local_bin / "claude"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    backend = ClaudeCodeBackend()
    request = StructuredBackendRequest(prompt="x", json_schema={"type": "object"})
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps({"structured_output": {"ok": True}}),
        stderr="",
    )

    with patch("shutil.which", return_value=None), patch.dict(
        "os.environ", {"HOME": str(fake_home)}, clear=False
    ), patch("pathlib.Path.home", return_value=fake_home), patch(
        "subprocess.run", return_value=completed
    ) as run_mock:
        response = backend.run_structured(request)

    command = run_mock.call_args.args[0]
    assert command[0] == str(executable)
    assert response.payload == {"ok": True}


def test_claude_backend_rejects_invalid_json_output() -> None:
    backend = ClaudeCodeBackend()
    request = StructuredBackendRequest(prompt="x", json_schema={"type": "object"})

    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="not json",
        stderr="",
    )

    with patch("shutil.which", return_value="/usr/bin/claude"), patch(
        "subprocess.run", return_value=completed
    ):
        with pytest.raises(BackendOutputError):
            backend.run_structured(request)


def test_codex_backend_requires_executable() -> None:
    backend = CodexCLIBackend()

    with patch("shutil.which", return_value=None):
        with pytest.raises(BackendUnavailableError):
            backend.run_structured(
                StructuredBackendRequest(prompt="x", json_schema={"type": "object"})
            )


def test_codex_backend_requires_auth_when_no_api_key(tmp_path: Path) -> None:
    backend = CodexCLIBackend()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()

    with patch("shutil.which", return_value="/usr/bin/codex"), patch.dict(
        "os.environ", {"CODEX_HOME": str(codex_home)}, clear=False
    ):
        with pytest.raises(BackendUnavailableError):
            backend.run_structured(
                StructuredBackendRequest(prompt="x", json_schema={"type": "object"})
            )


def test_codex_backend_writes_schema_and_reads_structured_output(tmp_path: Path) -> None:
    backend = CodexCLIBackend(model="gpt-5.4")
    request = StructuredBackendRequest(
        prompt="Select candidate nodes.",
        system_prompt="Return only the requested JSON object.",
        json_schema={"type": "object", "properties": {"candidates": {"type": "array"}}},
        cwd=tmp_path,
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        schema_path = Path(command[command.index("--output-schema") + 1])
        output_path = Path(command[command.index("--output-last-message") + 1])

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema["type"] == "object"

        output_path.write_text('{"candidates": []}', encoding="utf-8")
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="stdout",
            stderr="",
        )

    auth_home = tmp_path / "codex-home"
    auth_home.mkdir()
    (auth_home / "auth.json").write_text("{}", encoding="utf-8")

    with patch("shutil.which", return_value="/usr/bin/codex"), patch.dict(
        "os.environ", {"CODEX_HOME": str(auth_home)}, clear=False
    ), patch("subprocess.run", side_effect=fake_run) as run_mock:
        response = backend.run_structured(request)

    command = run_mock.call_args.args[0]
    assert command[:3] == ["codex", "exec", "--skip-git-repo-check"]
    assert "--sandbox" in command
    assert "--ephemeral" in command
    assert response.payload == {"candidates": []}


def test_codex_backend_normalizes_optional_properties_for_schema_file(tmp_path: Path) -> None:
    backend = CodexCLIBackend()
    request = StructuredBackendRequest(
        prompt="Return data.",
        json_schema={
            "type": "object",
            "properties": {
                "outer": {
                    "type": "object",
                    "properties": {
                        "optional_value": {
                            "anyOf": [{"type": "string"}, {"type": "null"}]
                        }
                    },
                }
            },
        },
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        schema_path = Path(command[command.index("--output-schema") + 1])
        output_path = Path(command[command.index("--output-last-message") + 1])
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        assert schema["required"] == ["outer"]
        assert schema["properties"]["outer"]["required"] == ["optional_value"]

        output_path.write_text('{"outer": {"optional_value": null}}', encoding="utf-8")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    auth_home = tmp_path / "codex-home"
    auth_home.mkdir()
    (auth_home / "auth.json").write_text("{}", encoding="utf-8")

    with patch("shutil.which", return_value="/usr/bin/codex"), patch.dict(
        "os.environ", {"CODEX_HOME": str(auth_home)}, clear=False
    ), patch("subprocess.run", side_effect=fake_run):
        response = backend.run_structured(request)

    assert response.payload == {"outer": {"optional_value": None}}


def test_codex_backend_rejects_missing_output_file(tmp_path: Path) -> None:
    backend = CodexCLIBackend()
    auth_home = tmp_path / "codex-home"
    auth_home.mkdir()
    (auth_home / "auth.json").write_text("{}", encoding="utf-8")

    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch("shutil.which", return_value="/usr/bin/codex"), patch.dict(
        "os.environ", {"CODEX_HOME": str(auth_home)}, clear=False
    ), patch("subprocess.run", return_value=completed):
        with pytest.raises(BackendOutputError):
            backend.run_structured(
                StructuredBackendRequest(prompt="x", json_schema={"type": "object"})
            )


def test_codex_backend_logs_missing_output_file_as_failed(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    backend = CodexCLIBackend(log_dir=log_dir)
    auth_home = tmp_path / "codex-home"
    auth_home.mkdir()
    (auth_home / "auth.json").write_text("{}", encoding="utf-8")

    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="stdout", stderr="")

    with patch("shutil.which", return_value="/usr/bin/codex"), patch.dict(
        "os.environ", {"CODEX_HOME": str(auth_home)}, clear=False
    ), patch("subprocess.run", return_value=completed):
        with pytest.raises(BackendOutputError):
            backend.run_structured(
                StructuredBackendRequest(prompt="x", json_schema={"type": "object"}, task_name="missing_output")
            )

    logs = sorted(log_dir.glob("missing_output_*.json"))
    assert logs
    payload = json.loads(logs[0].read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert "did not write the structured output file" in payload["error"]


def test_codex_backend_raises_clean_error_on_timeout(tmp_path: Path) -> None:
    backend = CodexCLIBackend(timeout_seconds=12.0)
    auth_home = tmp_path / "codex-home"
    auth_home.mkdir()
    (auth_home / "auth.json").write_text("{}", encoding="utf-8")

    with patch("shutil.which", return_value="/usr/bin/codex"), patch.dict(
        "os.environ", {"CODEX_HOME": str(auth_home)}, clear=False
    ), patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["codex"], timeout=12.0),
    ):
        with pytest.raises(BackendInvocationError, match="timed out"):
            backend.run_structured(
                StructuredBackendRequest(prompt="x", json_schema={"type": "object"})
            )


def test_codex_backend_writes_debug_log_when_log_dir_is_configured(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    backend = CodexCLIBackend(model="gpt-5.4", log_dir=log_dir)
    request = StructuredBackendRequest(
        prompt="Return an empty candidate list.",
        system_prompt="Return JSON only.",
        json_schema={"type": "object", "properties": {"candidates": {"type": "array"}}},
        cwd=tmp_path,
        task_name="select_candidates",
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"candidates": []}', encoding="utf-8")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="stdout", stderr="")

    auth_home = tmp_path / "codex-home"
    auth_home.mkdir()
    (auth_home / "auth.json").write_text("{}", encoding="utf-8")

    with patch("shutil.which", return_value="/usr/bin/codex"), patch.dict(
        "os.environ", {"CODEX_HOME": str(auth_home)}, clear=False
    ), patch("subprocess.run", side_effect=fake_run):
        backend.run_structured(request)

    logs = sorted(log_dir.glob("select_candidates_*.json"))
    assert logs
    payload = json.loads(logs[0].read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["prompt"] == "Return an empty candidate list."
    assert payload["payload"] == {"candidates": []}
    assert isinstance(payload["elapsed_seconds"], float)
    assert payload["elapsed_seconds"] >= 0.0


def test_codex_backend_run_agentic_structured_uses_full_auto(tmp_path: Path) -> None:
    backend = CodexCLIBackend(model="gpt-5.4")
    request = StructuredBackendRequest(
        prompt="Edit the scratch file and return JSON.",
        system_prompt="Return JSON only.",
        json_schema={
            "type": "object",
            "properties": {
                "lean_theorem_name": {"type": "string"},
                "lean_statement": {"type": "string"},
                "final_file_path": {"type": "string"},
                "plan_file_path": {"type": "string"},
            },
        },
        cwd=tmp_path,
        task_name="formalize_node_agentic",
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "lean_theorem_name": "t",
                    "lean_statement": "True",
                    "final_file_path": "/tmp/t.lean",
                    "plan_file_path": "/tmp/t_plan.md",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    auth_home = tmp_path / "codex-home"
    auth_home.mkdir()
    (auth_home / "auth.json").write_text("{}", encoding="utf-8")

    with patch("shutil.which", return_value="/usr/bin/codex"), patch.dict(
        "os.environ", {"CODEX_HOME": str(auth_home)}, clear=False
    ), patch("subprocess.run", side_effect=fake_run) as run_mock:
        backend.run_agentic_structured(request, timeout_seconds=420.0)

    command = run_mock.call_args.args[0]
    assert "--full-auto" in command
    assert "--sandbox" not in command
