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

    assert command[:3] == ["claude", "-p", "--output-format"]
    assert "--json-schema" in command
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "1234"
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == "medium"
    assert response.payload == {"nodes": []}


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
    assert command[:4] == ["codex", "exec", "--skip-git-repo-check", "--sandbox"]
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
