"""Helpers for user-steered continuation attempts."""

from __future__ import annotations


CONTINUATION_INSTRUCTIONS_HEADER = "Additional user instructions for this continuation attempt:"


def format_continuation_rationale(instructions: str | None) -> str:
    """Return the formalization rationale used for a continuation request."""

    if instructions is None or not instructions.strip():
        return "User continuation request."
    return (
        "User continuation request.\n\n"
        f"{CONTINUATION_INSTRUCTIONS_HEADER}\n"
        f"{instructions.strip()}"
    )


def extract_continuation_instructions(
    formalization_rationale: str | None,
) -> str | None:
    """Extract continuation-specific user instructions from a node rationale."""

    if (
        not formalization_rationale
        or CONTINUATION_INSTRUCTIONS_HEADER not in formalization_rationale
    ):
        return None
    _, instructions = formalization_rationale.split(CONTINUATION_INSTRUCTIONS_HEADER, 1)
    instructions = instructions.strip()
    return instructions or None
