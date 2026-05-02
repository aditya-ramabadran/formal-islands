"""Helpers for optional fixed Lean root specifications."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from formal_islands.models import FixedRootLeanSpec, FormalArtifact, ProofGraph


def build_fixed_root_lean_spec(
    lean_statement: str,
    *,
    source: str | None = None,
) -> FixedRootLeanSpec:
    """Create the graph-level fixed-root specification metadata."""

    text = lean_statement.strip()
    if not text:
        raise ValueError("fixed root Lean statement cannot be empty")
    return FixedRootLeanSpec(
        lean_statement=text,
        statement_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        source=source,
        theorem_name=extract_decl_name(text),
    )


def read_fixed_root_lean_statement_file(path: str | Path) -> str:
    """Read a fixed root Lean statement file."""

    return Path(path).expanduser().read_text(encoding="utf-8").strip()


def fixed_root_spec_prompt_block(graph: ProofGraph, node_id: str) -> str | None:
    """Return the prompt block for a graph's fixed root spec and target node."""

    spec = graph.fixed_root_lean_spec
    if spec is None:
        return None

    source_line = f"Source: {spec.source or 'manual'}"
    hash_line = f"SHA256: {spec.statement_hash}"
    if node_id == graph.root_node_id:
        mode = "HARD ROOT TARGET"
        instructions = (
            "The current target node is the root. The designated main theorem for this "
            "root attempt must prove the exact Lean statement below if the attempt is "
            "reported as root/full-node verified. You may add helper lemmas and imports, "
            "but do not change the theorem name, binders, hypotheses, or conclusion. "
            "If the exact root cannot be proved, prefer an honest failed attempt or a "
            "clearly labeled support core over a misleading theorem with a different shape."
        )
    else:
        mode = "ROOT CONTEXT ONLY"
        instructions = (
            "The statement below is the eventual root specification. The current target "
            "node is not the root, so do not try to prove this statement here and do not "
            "treat it as an assumption. Use it only to keep notation, ambient types, APIs, "
            "and local island choices compatible with the final root target."
        )

    return "\n".join(
        [
            f"Fixed root Lean specification ({mode}):",
            source_line,
            hash_line,
            instructions,
            "```lean",
            spec.lean_statement,
            "```",
        ]
    )


def root_fixed_spec_applies(graph: ProofGraph, node_id: str) -> bool:
    """Whether fixed-spec exactness should be enforced on this node."""

    return graph.fixed_root_lean_spec is not None and node_id == graph.root_node_id


def fixed_spec_exact_header_matches(artifact: FormalArtifact, spec: FixedRootLeanSpec) -> bool:
    """Check whether an artifact's theorem header matches the fixed spec header."""

    expected_name = spec.theorem_name
    if expected_name is not None and artifact.lean_theorem_name.split(".")[-1] != expected_name:
        return False

    expected_header = extract_decl_header(spec.lean_statement, preferred_name=expected_name)
    actual_header = extract_decl_header(
        artifact.lean_code,
        preferred_name=artifact.lean_theorem_name,
    )
    if expected_header is None or actual_header is None:
        return False
    return _normalize_header(expected_header) == _normalize_header(actual_header)


def fixed_spec_mismatch_message(spec: FixedRootLeanSpec, artifact: FormalArtifact) -> str:
    """Human-readable rejection message for a fixed-root mismatch."""

    expected = extract_decl_header(spec.lean_statement, preferred_name=spec.theorem_name)
    actual = extract_decl_header(artifact.lean_code, preferred_name=artifact.lean_theorem_name)
    return "\n".join(
        [
            "Fixed root Lean specification mismatch.",
            "The root node was run with an exact fixed Lean statement, so a root/full-node "
            "claim must preserve that theorem header.",
            f"Expected theorem name: {spec.theorem_name or '(could not extract)'}",
            f"Returned theorem name: {artifact.lean_theorem_name}",
            f"Expected header: {expected or '(could not extract)'}",
            f"Returned header: {actual or '(could not extract)'}",
        ]
    )


def extract_decl_name(lean_text: str) -> str | None:
    """Extract the first theorem/lemma name from Lean text."""

    match = _DECL_RE.search(lean_text)
    return match.group(2) if match else None


def extract_decl_header(
    lean_text: str,
    *,
    preferred_name: str | None = None,
) -> str | None:
    """Extract a theorem/lemma header without its proof body."""

    declarations = _extract_decl_headers(lean_text)
    if not declarations:
        return None
    if preferred_name is not None:
        short_name = preferred_name.split(".")[-1]
        for name, header in declarations:
            if name == preferred_name or name == short_name:
                return header
    return declarations[0][1]


_DECL_RE = re.compile(r"(?m)^\s*(theorem|lemma)\s+([A-Za-z0-9_'.]+)\b")


def _extract_decl_headers(lean_text: str) -> list[tuple[str, str]]:
    matches = list(_DECL_RE.finditer(lean_text))
    declarations: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        declaration_end = matches[index + 1].start() if index + 1 < len(matches) else len(lean_text)
        chunk = lean_text[match.end() : declaration_end]
        statement_end = _find_statement_body_delimiter(chunk)
        if statement_end is None:
            statement_end = len(chunk)
        header = (lean_text[match.start() : match.end()] + chunk[:statement_end]).strip()
        declarations.append((match.group(2), re.sub(r"\s+\n", "\n", header)))
    return declarations


def _find_statement_body_delimiter(chunk: str) -> int | None:
    paren_depth = 0
    brace_depth = 0
    bracket_depth = 0
    block_comment_depth = 0
    in_line_comment = False
    in_string = False
    escape_next = False
    first_top_level_colon_eq: int | None = None

    i = 0
    while i < len(chunk):
        ch = chunk[i]
        nxt = chunk[i + 1] if i + 1 < len(chunk) else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if block_comment_depth > 0:
            if ch == "/" and nxt == "-":
                block_comment_depth += 1
                i += 2
                continue
            if ch == "-" and nxt == "/":
                block_comment_depth -= 1
                i += 2
                continue
            i += 1
            continue
        if in_string:
            if escape_next:
                escape_next = False
            elif ch == "\\":
                escape_next = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "-":
            block_comment_depth += 1
            i += 2
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch == "(":
            paren_depth += 1
        elif ch == ")" and paren_depth:
            paren_depth -= 1
        elif ch == "{":
            brace_depth += 1
        elif ch == "}" and brace_depth:
            brace_depth -= 1
        elif ch == "[":
            bracket_depth += 1
        elif ch == "]" and bracket_depth:
            bracket_depth -= 1

        if paren_depth == 0 and brace_depth == 0 and bracket_depth == 0:
            if ch == ":" and nxt == "=":
                if first_top_level_colon_eq is None:
                    first_top_level_colon_eq = i
                j = i + 2
                while j < len(chunk) and chunk[j].isspace():
                    j += 1
                if chunk.startswith("by", j):
                    return i
            if chunk.startswith("where", i) and _word_boundary(chunk, i, i + 5):
                return i
        i += 1

    return first_top_level_colon_eq


def _word_boundary(text: str, start: int, end: int) -> bool:
    before_ok = start == 0 or not (text[start - 1].isalnum() or text[start - 1] == "_")
    after_ok = end == len(text) or not (text[end].isalnum() or text[end] == "_")
    return before_ok and after_ok


def _normalize_header(header: str) -> str:
    return re.sub(r"\s+", " ", header).strip()
