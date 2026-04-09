"""HTML/text rendering helpers for static reports."""

from __future__ import annotations

import re
from html import escape
from pathlib import Path


def display_verification_command(verification: object) -> str:
    """Render verification commands using repo-relative paths when possible."""

    command = getattr(verification, "command", "")
    artifact_path = getattr(verification, "artifact_path", None)
    if not isinstance(command, str) or not command:
        return ""
    if not isinstance(artifact_path, str) or not artifact_path:
        return command
    repo_relative = repo_relative_artifact_path(artifact_path)
    if repo_relative and artifact_path in command:
        return command.replace(artifact_path, repo_relative)
    return command


def repo_relative_artifact_path(artifact_path: str) -> str | None:
    """Map an absolute artifact path into a repo-relative path when possible."""

    path = Path(artifact_path)
    parts = path.parts
    if "lean_project" not in parts:
        return None
    anchor_index = parts.index("lean_project")
    return Path(*parts[anchor_index:]).as_posix()


def sanitize_report_payload(value: object) -> object:
    """Recursively scrub public report payloads before serialization."""

    if isinstance(value, dict):
        sanitized = {key: sanitize_report_payload(inner_value) for key, inner_value in value.items()}
        command = sanitized.get("command")
        artifact_path = sanitized.get("artifact_path")
        if isinstance(command, str) and isinstance(artifact_path, str):
            repo_relative = repo_relative_artifact_path(artifact_path)
            if repo_relative:
                sanitized["artifact_path"] = repo_relative
            if repo_relative and artifact_path in command:
                sanitized["command"] = command.replace(artifact_path, repo_relative)
        return sanitized
    if isinstance(value, list):
        return [sanitize_report_payload(item) for item in value]
    return value


def render_math_text(text: str) -> str:
    compacted = compact_report_text(text)
    return f'<div class="math-text">{render_inline_code_html(compacted)}</div>'


def render_faithfulness_label(*, result_kind: str | None, classification: str) -> str:
    if result_kind is None:
        return classification
    labels = {
        "full_match": "full match",
        "faithful_core": "faithful core",
        "downstream_consequence": "downstream consequence",
        "dimensional_analogue": "dimensional analogue",
        "helper_shard": "helper shard",
    }
    return labels.get(result_kind, result_kind.replace("_", " "))


def render_inline_code_html(text: str) -> str:
    """Render backticks plus simple emphasis markers into inline HTML."""

    parts = re.split(r"(`[^`]+`)", text)
    rendered: list[str] = []
    for part in parts:
        if len(part) >= 2 and part.startswith("`") and part.endswith("`"):
            rendered.append(f'<code class="inline-code">{escape(part[1:-1])}</code>')
        else:
            segments = re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", part)
            for seg in segments:
                if seg.startswith("**") and seg.endswith("**") and len(seg) >= 5:
                    rendered.append(f"<em>{escape(seg[2:-2])}</em>")
                elif seg.startswith("*") and seg.endswith("*") and len(seg) >= 3:
                    rendered.append(f"<em>{escape(seg[1:-1])}</em>")
                else:
                    rendered.append(escape(seg))
    return "".join(rendered)


def compact_report_text(text: str) -> str:
    stripped = text.strip()
    stripped = re.sub(r"\n{3,}", "\n\n", stripped)
    stripped = re.sub(r"[ \t]+\n", "\n", stripped)
    stripped = re.sub(r"\n{2,}(\\\[)", r"\n\1", stripped)
    stripped = re.sub(r"(\\\])\n{2,}", r"\1\n", stripped)
    return stripped


def render_lean_code_block(code: str) -> str:
    return f'<pre><code class="language-lean lean-code">{highlight_lean_html(code)}</code></pre>'


def highlight_lean_html(code: str) -> str:
    lines = []
    for raw_line in code.splitlines():
        comment_index = raw_line.find("--")
        if comment_index != -1:
            code_part = raw_line[:comment_index]
            comment_part = raw_line[comment_index:]
        else:
            code_part = raw_line
            comment_part = ""
        highlighted = highlight_lean_code_part(code_part)
        if comment_part:
            highlighted += f'<span class="tok-comment">{escape(comment_part)}</span>'
        lines.append(highlighted)
    return "\n".join(lines)


def highlight_lean_code_part(text: str) -> str:
    keywords = {
        "import",
        "open",
        "namespace",
        "section",
        "end",
        "variable",
        "variables",
        "theorem",
        "lemma",
        "example",
        "def",
        "axiom",
        "where",
        "structure",
        "class",
        "instance",
        "inductive",
        "deriving",
        "match",
        "with",
        "let",
        "in",
        "if",
        "then",
        "else",
        "fun",
        "forall",
    }
    tactics = {
        "by",
        "intro",
        "intros",
        "rintro",
        "apply",
        "exact",
        "show",
        "have",
        "simpa",
        "simp",
        "rw",
        "calc",
        "constructor",
        "cases",
        "refine",
        "obtain",
        "aesop",
        "omega",
        "ring",
        "linarith",
        "norm_num",
    }
    builtin_types = {"ℝ", "ℕ", "ℤ", "Prop", "Type", "Type*", "Bool"}
    token_pattern = re.compile(
        r"(\"(?:[^\"\\\\]|\\\\.)*\")|(\b[A-Za-z_][A-Za-z0-9_']*\b)|(ℝ|ℕ|ℤ|Prop|Type\*?|Bool)|(\d+(?:\.\d+)?)"
    )

    parts: list[str] = []
    last_index = 0
    for match in token_pattern.finditer(text):
        start, end = match.span()
        if start > last_index:
            parts.append(escape(text[last_index:start]))
        string_token, word_token, type_token, number_token = match.groups()
        if string_token is not None:
            parts.append(f'<span class="tok-string">{escape(string_token)}</span>')
        elif type_token is not None or (word_token is not None and word_token in builtin_types):
            token = type_token or word_token
            parts.append(f'<span class="tok-type">{escape(token)}</span>')
        elif word_token is not None:
            if word_token in keywords:
                parts.append(f'<span class="tok-keyword">{escape(word_token)}</span>')
            elif word_token in tactics:
                parts.append(f'<span class="tok-tactic">{escape(word_token)}</span>')
            else:
                parts.append(escape(word_token))
        elif number_token is not None:
            parts.append(f'<span class="tok-number">{escape(number_token)}</span>')
        last_index = end
    if last_index < len(text):
        parts.append(escape(text[last_index:]))
    return "".join(parts)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    return slug or "item"
