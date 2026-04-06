"""Structured-output schemas for graph extraction and candidate selection."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ExtractionSchemaModel(BaseModel):
    """Shared schema defaults for extraction-stage payloads."""

    model_config = ConfigDict(extra="forbid")


class ExtractedNode(ExtractionSchemaModel):
    """Node shape expected from the graph-extraction LLM pass."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    informal_statement: str = Field(min_length=1)
    informal_proof_text: str = Field(min_length=1)
    display_label: str | None = None


class ExtractedEdge(ExtractionSchemaModel):
    """Dependency edge shape expected from the graph-extraction pass."""

    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    label: str | None = None
    explanation: str | None = None


class ExtractedProofGraph(ExtractionSchemaModel):
    """Top-level extraction payload."""

    theorem_title: str = Field(min_length=1)
    theorem_statement: str = Field(min_length=1)
    root_node_id: str = Field(min_length=1)
    nodes: list[ExtractedNode] = Field(min_length=1)
    edges: list[ExtractedEdge]


class CandidateSelection(ExtractionSchemaModel):
    """Single candidate-formalization decision."""

    node_id: str = Field(min_length=1)
    priority: int = Field(ge=1, le=3)
    rationale: str = Field(min_length=1)

    @field_validator("priority", mode="before")
    @classmethod
    def normalize_priority(cls, value: object) -> int:
        if isinstance(value, str):
            normalized = value.strip().lower()
            named_priorities = {"high": 1, "medium": 2, "low": 3}
            if normalized in named_priorities:
                return named_priorities[normalized]
            if normalized.isdigit():
                return int(normalized)
        return value  # type: ignore[return-value]


class CandidateSelectionResult(ExtractionSchemaModel):
    """Top-level candidate-selection payload."""

    candidates: list[CandidateSelection] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_node_ids(self) -> CandidateSelectionResult:
        node_ids = [candidate.node_id for candidate in self.candidates]
        duplicates = {node_id for node_id in node_ids if node_ids.count(node_id) > 1}
        if duplicates:
            duplicate_list = ", ".join(sorted(duplicates))
            raise ValueError(f"duplicate candidate node ids: {duplicate_list}")
        return self


class RefinedLocalClaimProposal(ExtractionSchemaModel):
    """A backend-proposed narrower local claim extracted from a broad node."""

    title: str = Field(min_length=1)
    display_label: str | None = None
    informal_statement: str = Field(min_length=1)
    informal_proof_text: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class RefinedLocalClaimResult(ExtractionSchemaModel):
    """Top-level payload for a hybrid refined-local-claim proposal pass."""

    proposals: list[RefinedLocalClaimProposal] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_titles(self) -> RefinedLocalClaimResult:
        titles = [proposal.title for proposal in self.proposals]
        duplicates = {title for title in titles if titles.count(title) > 1}
        if duplicates:
            duplicate_list = ", ".join(sorted(duplicates))
            raise ValueError(f"duplicate refined claim proposal titles: {duplicate_list}")
        return self


class PlannedProofGraph(ExtractedProofGraph):
    """Merged theorem-level planning payload: graph plus ranked formalization candidates."""

    candidates: list[CandidateSelection] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_candidate_node_ids(self) -> PlannedProofGraph:
        node_ids = [candidate.node_id for candidate in self.candidates]
        duplicates = {node_id for node_id in node_ids if node_ids.count(node_id) > 1}
        if duplicates:
            duplicate_list = ", ".join(sorted(duplicates))
            raise ValueError(f"duplicate candidate node ids: {duplicate_list}")
        return self
