"""Structured-output schemas for single-node formalization."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FormalizationSchemaModel(BaseModel):
    """Shared schema defaults for formalization payloads."""

    model_config = ConfigDict(extra="forbid")


class FormalizationResult(FormalizationSchemaModel):
    """Explicit backend contract for a proposed Lean theorem."""

    lean_theorem_name: str = Field(min_length=1)
    lean_statement: str = Field(min_length=1)
    lean_code: str = Field(min_length=1)
