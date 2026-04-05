"""Core graph and review models for the Formal Islands prototype."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NodeStatus(StrEnum):
    """Execution state for a proof node."""

    INFORMAL = "informal"
    CANDIDATE_FORMAL = "candidate_formal"
    FORMAL_VERIFIED = "formal_verified"
    FORMAL_FAILED = "formal_failed"


class VerificationStatus(StrEnum):
    """Status for a local Lean verification attempt."""

    NOT_ATTEMPTED = "not_attempted"
    VERIFIED = "verified"
    FAILED = "failed"


class FaithfulnessClassification(StrEnum):
    """How closely a verified Lean theorem matches the target informal node."""

    FULL_NODE = "full_node"
    CONCRETE_SUBLEMMA = "concrete_sublemma"
    OVER_ABSTRACT = "over_abstract"


class ReviewObligationKind(StrEnum):
    """Deterministic review-check categories."""

    INFORMAL_PROOF_CHECK = "informal_proof_check"
    FORMAL_SEMANTIC_MATCH_CHECK = "formal_semantic_match_check"
    BOUNDARY_INTERFACE_CHECK = "boundary_interface_check"


class StrictModel(BaseModel):
    """Project-wide strict model defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)


class VerificationResult(StrictModel):
    """Captured result from a Lean verification command."""

    status: VerificationStatus = VerificationStatus.NOT_ATTEMPTED
    command: str = ""
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    elapsed_seconds: float = Field(default=0.0, ge=0.0)
    attempt_count: int = Field(default=0, ge=0)
    artifact_path: str | None = None


class FormalArtifact(StrictModel):
    """Formal theorem material attached to a node."""

    lean_theorem_name: str = Field(min_length=1)
    lean_statement: str = Field(min_length=1)
    lean_code: str = Field(min_length=1)
    faithfulness_classification: FaithfulnessClassification = FaithfulnessClassification.FULL_NODE
    faithfulness_notes: str | None = None
    verification: VerificationResult = Field(default_factory=VerificationResult)
    attempt_history: list[VerificationResult] = Field(default_factory=list)


class ProofNode(StrictModel):
    """A single claim in the extracted proof graph."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    informal_statement: str = Field(min_length=1)
    informal_proof_text: str = Field(min_length=1)
    status: NodeStatus = NodeStatus.INFORMAL
    display_label: str | None = None
    formalization_priority: int | None = Field(default=None, ge=1, le=3)
    formalization_rationale: str | None = None
    formal_artifact: FormalArtifact | None = None

    @model_validator(mode="after")
    def validate_state(self) -> ProofNode:
        """Enforce node-state invariants without guessing future features."""

        if self.status in {
            NodeStatus.FORMAL_VERIFIED,
            NodeStatus.FORMAL_FAILED,
        } and self.formal_artifact is None:
            raise ValueError("formal_artifact is required for formal node states")

        has_candidate_metadata = (
            self.formalization_priority is not None or self.formalization_rationale is not None
        )
        if has_candidate_metadata and (
            self.formalization_priority is None or self.formalization_rationale is None
        ):
            raise ValueError(
                "formalization_priority and formalization_rationale must be set together"
            )

        return self


class ProofEdge(StrictModel):
    """A dependency edge from parent claim to child claim."""

    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    label: str | None = None
    explanation: str | None = None


class ProofGraph(StrictModel):
    """Validated proof graph with a theorem root and dependency edges."""

    theorem_title: str = Field(min_length=1)
    theorem_statement: str = Field(min_length=1)
    root_node_id: str = Field(min_length=1)
    nodes: list[ProofNode] = Field(min_length=1)
    edges: list[ProofEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self) -> ProofGraph:
        """Reject malformed graphs early and deterministically."""

        node_ids = [node.id for node in self.nodes]
        duplicate_ids = {node_id for node_id in node_ids if node_ids.count(node_id) > 1}
        if duplicate_ids:
            duplicates = ", ".join(sorted(duplicate_ids))
            raise ValueError(f"duplicate node ids: {duplicates}")

        node_id_set = set(node_ids)
        if self.root_node_id not in node_id_set:
            raise ValueError("root_node_id must reference a node in nodes")

        for edge in self.edges:
            if edge.source_id not in node_id_set:
                raise ValueError(f"edge source_id '{edge.source_id}' does not reference a node")
            if edge.target_id not in node_id_set:
                raise ValueError(f"edge target_id '{edge.target_id}' does not reference a node")

        return self


class ReviewObligation(StrictModel):
    """A human review task derived from the graph."""

    id: str = Field(min_length=1)
    kind: ReviewObligationKind
    text: str = Field(min_length=1)
    node_ids: list[str] = Field(min_length=1)
