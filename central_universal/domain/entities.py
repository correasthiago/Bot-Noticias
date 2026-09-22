"""Entidades puras do dominio (Secao 6).

Sao dataclasses sem dependencia de FastAPI, SQLite ou FSRS. A camada
persistence sabe serializar/desserializar estas classes; a camada
domain nao sabe que SQLite existe.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from central_universal.domain.enums import (
    CompetencyDimensionState,
    Dimension,
    EvidenceRelation,
    EvidenceType,
    HelpLevel,
    ProductionResult,
    ProviderFunction,
    SessionStatus,
)


@dataclass
class Learner:
    id: str
    display_name: str
    created_at: str


@dataclass
class LearningDomain:
    id: str
    code: str
    name: str


@dataclass
class Competency:
    id: str
    domain_id: str
    code: str
    name: str
    description: str = ""


@dataclass
class PrerequisiteRelation:
    id: str
    competency_id: str
    prerequisite_id: str
    created_at: str


@dataclass
class RuleVersion:
    id: str
    version: str
    description: str
    created_at: str
    active: bool = True


@dataclass
class LearningSession:
    id: str
    learner_id: str
    started_at: str
    status: SessionStatus
    ended_at: str | None = None


@dataclass
class Activity:
    id: str
    session_id: str
    competency_targets: list[str]
    activity_type: str
    prompt: str
    support_level: HelpLevel
    created_at: str
    tutor_provider_event_id: str | None = None


@dataclass
class RawInteraction:
    id: str
    activity_id: str
    session_id: str
    idempotency_key: str
    learner_input: str
    tutor_output: str
    help_level: HelpLevel
    production_result: ProductionResult
    occurred_at: str
    evidence_cluster_id: str


@dataclass
class EvidenceEvent:
    id: str
    raw_interaction_id: str
    competency_id: str
    dimension: Dimension
    evidence_type: EvidenceType
    relation: EvidenceRelation
    help_level: HelpLevel
    production_result: ProductionResult
    evidence_cluster_id: str
    created_at: str


@dataclass
class EvidenceAssessment:
    """Interpretacao VERSIONADA de um EvidenceEvent (Secao 16).

    `classification` e o julgamento final do avaliador sobre o TIPO da
    evidencia (positive/negative/contradictory/inconclusive) apos escrutinio
    - pode diferir do `evidence_type` mecanico do EvidenceEvent. O estado da
    competencia (CompetencyDimensionState) NUNCA e decidido aqui: e sempre
    derivado por agregacao deterministica em `evidence.aggregation`.
    """

    id: str
    evidence_event_id: str
    rule_version_id: str
    classification: EvidenceType
    result: str
    confidence: float
    justification: str
    alternative_cause: str | None
    inconclusive: bool
    evaluator_provider_event_id: str | None
    created_at: str


@dataclass
class CompetencyState:
    """Projecao derivada e recalculavel (Principio 6, Secao 14).

    Cada recomputo insere uma NOVA linha (append-only). O estado "atual"
    e sempre a linha mais recente por (competency_id, dimension).
    """

    id: str
    competency_id: str
    dimension: Dimension
    state: CompetencyDimensionState
    possible_regression: bool
    has_unresolved_contradiction: bool
    last_evidence_assessment_id: str | None
    rule_version_id: str
    computed_at: str
    explanation: str = ""


@dataclass
class MemoryState:
    id: str
    competency_id: str
    fsrs_card_json: str
    due_at: str | None
    stability: float | None
    difficulty: float | None
    card_state: str
    last_review_at: str | None
    updated_at: str


@dataclass
class MemoryReviewLog:
    id: str
    competency_id: str
    memory_state_id: str
    review_datetime: str
    rating: int
    fsrs_review_log_json: str
    created_at: str


@dataclass
class DecisionEvent:
    id: str
    session_id: str | None
    learner_id: str
    competency_id: str | None
    routing: str
    decision_type: str | None
    rule_applied: str
    justification: str
    rule_version_id: str
    created_at: str


@dataclass
class ProviderEvent:
    id: str
    provider_name: str
    model: str
    function: ProviderFunction
    config_version: str
    occurred_at: str
    success: bool
    latency_ms: float | None = None
    tokens_used: int | None = None
    cost: float | None = None
    error_message: str | None = None


@dataclass
class BackupEvent:
    id: str
    backup_path: str
    started_at: str
    completed_at: str | None
    success: bool
    size_bytes: int | None
    retention_note: str
    error_message: str | None = None
