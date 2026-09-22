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
    EvaluationStatus,
    EvidenceRelation,
    EvidenceType,
    HelpLevel,
    ProductionResult,
    ProjectionGenerationStatus,
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
    """Uma versao IMUTAVEL das regras pedagogicas (Principio 18).

    `config_json` e a UNICA fonte de thresholds/politicas provisorias
    (Secao 8 do pacote de correcao v0.2): nenhum threshold de agregacao
    vive mais hardcoded em `evidence/aggregation.py`. `algorithm_version`
    identifica qual forma de codigo sabe interpretar `config_json` - uma
    nova RuleVersion nunca pode "reetiquetar" projecoes de uma versao de
    algoritmo diferente sem recomputa-las de verdade.
    """

    id: str
    version: str
    description: str
    created_at: str
    config_json: str
    algorithm_version: str
    active: bool = True  # anotacao historica; a versao ATIVA de verdade e active_rule_version


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
    is_planned_recall: bool = False


@dataclass
class EvidenceCluster:
    """Contexto de tentativa computado no SERVIDOR (Secao 6 do pacote de
    correcao v0.2): nunca aceitamos um id arbitrario do chamador como
    prova de independencia. Duas interacoes na MESMA sessao com o mesmo
    tipo de atividade e o mesmo prompt normalizado caem no mesmo cluster,
    nao importa quantos `activity_id`/`raw_interaction_id` distintos
    existam por baixo.
    """

    id: str
    session_id: str
    activity_type: str
    context_signature: str
    origin: str
    first_seen_at: str
    last_seen_at: str


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
    evaluation_status: EvaluationStatus = EvaluationStatus.PENDING


@dataclass
class EvidenceEvent:
    """Fatos OBSERVADOS e condicoes da tentativa (Secao 4 do pacote de
    correcao v0.2). Nunca carrega classificacao/julgamento - isso e
    exclusivo de EvidenceAssessment."""

    id: str
    raw_interaction_id: str
    competency_id: str
    dimension: Dimension
    relation: EvidenceRelation
    help_level: HelpLevel
    production_result: ProductionResult
    evidence_cluster_id: str
    created_at: str


@dataclass
class EvidenceAssessment:
    """Interpretacao VERSIONADA de um EvidenceEvent (Secao 16).

    Classificacao, confianca, causa alternativa, conclusividade e
    RuleVersion pertencem EXCLUSIVAMENTE aqui (Secao 4 do pacote de
    correcao v0.2). O estado da competencia (CompetencyDimensionState)
    nunca e decidido aqui: e sempre derivado por agregacao deterministica
    em `evidence.aggregation`.
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
class ProjectionGeneration:
    """Uma geracao da projecao CompetencyState (Secao 10 do pacote de
    correcao v0.2). Reconstrucao total deixa de apagar `competency_state`:
    ela constroi uma geracao nova, valida, e so entao a ativa - gerações
    antigas permanecem no banco, nunca sao apagadas."""

    id: str
    rule_version_id: str
    created_at: str
    status: ProjectionGenerationStatus
    activated_at: str | None = None
    note: str = ""


@dataclass
class CompetencyState:
    """Projecao derivada e recalculavel (Principio 6, Secao 14).

    Cada linha pertence a uma `ProjectionGeneration`. Dentro da geracao
    ATIVA, o estado "atual" e a linha mais recente por
    (competency_id, dimension). Linhas de geracoes antigas nunca sao
    apagadas nem alteradas.
    """

    id: str
    generation_id: str
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
class MemoryObservation:
    """Evento EXPLICITO de observacao de memoria (Secao 1 do pacote de
    correcao v0.2). E o UNICO gatilho legitimo para chamar
    `MemoryAdapter.review`: so existe quando uma tentativa de recuperacao
    foi planejada, ocorreu apos um intervalo relevante, e recebeu uma
    avaliacao valida e conclusiva."""

    id: str
    competency_id: str
    raw_interaction_id: str
    evidence_assessment_id: str
    planned_recall: bool
    interval_days: float | None
    rating: int
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
