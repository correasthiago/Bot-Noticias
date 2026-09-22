"""Repositorios: unica camada que conhece SQL.

Cada metodo mapeia 1:1 entre uma dataclass de `domain.entities` e uma
linha de tabela. Nenhuma regra pedagogica vive aqui - apenas persistencia.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import Any, Iterable

from central_universal.domain.entities import (
    Activity,
    BackupEvent,
    Competency,
    CompetencyState,
    DecisionEvent,
    EvidenceAssessment,
    EvidenceEvent,
    Learner,
    LearningDomain,
    LearningSession,
    MemoryReviewLog,
    MemoryState,
    PrerequisiteRelation,
    ProviderEvent,
    RawInteraction,
    RuleVersion,
)
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


class Repositories:
    """Fachada com um repositorio por entidade, todos sobre a mesma conexao."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.learners = LearnerRepository(conn)
        self.domains = DomainRepository(conn)
        self.competencies = CompetencyRepository(conn)
        self.prerequisites = PrerequisiteRepository(conn)
        self.rule_versions = RuleVersionRepository(conn)
        self.sessions = SessionRepository(conn)
        self.activities = ActivityRepository(conn)
        self.raw_interactions = RawInteractionRepository(conn)
        self.evidence_events = EvidenceEventRepository(conn)
        self.evidence_assessments = EvidenceAssessmentRepository(conn)
        self.competency_states = CompetencyStateRepository(conn)
        self.memory_states = MemoryStateRepository(conn)
        self.memory_review_logs = MemoryReviewLogRepository(conn)
        self.decision_events = DecisionEventRepository(conn)
        self.provider_events = ProviderEventRepository(conn)
        self.backup_events = BackupEventRepository(conn)


def _bool(value: int) -> bool:
    return bool(value)


class LearnerRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, learner: Learner) -> None:
        self.conn.execute(
            "INSERT INTO learner (id, display_name, created_at) VALUES (?, ?, ?);",
            (learner.id, learner.display_name, learner.created_at),
        )

    def get(self, learner_id: str) -> Learner | None:
        row = self.conn.execute(
            "SELECT * FROM learner WHERE id = ?;", (learner_id,)
        ).fetchone()
        return Learner(**dict(row)) if row else None

    def list_all(self) -> list[Learner]:
        rows = self.conn.execute("SELECT * FROM learner ORDER BY created_at;").fetchall()
        return [Learner(**dict(r)) for r in rows]


class DomainRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, domain: LearningDomain) -> None:
        self.conn.execute(
            "INSERT INTO learning_domain (id, code, name) VALUES (?, ?, ?);",
            (domain.id, domain.code, domain.name),
        )

    def get_by_code(self, code: str) -> LearningDomain | None:
        row = self.conn.execute(
            "SELECT * FROM learning_domain WHERE code = ?;", (code,)
        ).fetchone()
        return LearningDomain(**dict(row)) if row else None

    def list_all(self) -> list[LearningDomain]:
        rows = self.conn.execute("SELECT * FROM learning_domain;").fetchall()
        return [LearningDomain(**dict(r)) for r in rows]


class CompetencyRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, competency: Competency) -> None:
        self.conn.execute(
            "INSERT INTO competency (id, domain_id, code, name, description) "
            "VALUES (?, ?, ?, ?, ?);",
            (
                competency.id,
                competency.domain_id,
                competency.code,
                competency.name,
                competency.description,
            ),
        )

    def get(self, competency_id: str) -> Competency | None:
        row = self.conn.execute(
            "SELECT * FROM competency WHERE id = ?;", (competency_id,)
        ).fetchone()
        return Competency(**dict(row)) if row else None

    def get_by_code(self, code: str) -> Competency | None:
        row = self.conn.execute(
            "SELECT * FROM competency WHERE code = ?;", (code,)
        ).fetchone()
        return Competency(**dict(row)) if row else None

    def list_all(self) -> list[Competency]:
        rows = self.conn.execute("SELECT * FROM competency ORDER BY code;").fetchall()
        return [Competency(**dict(r)) for r in rows]


class PrerequisiteRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, rel: PrerequisiteRelation) -> None:
        self.conn.execute(
            "INSERT INTO prerequisite_relation "
            "(id, competency_id, prerequisite_id, created_at) VALUES (?, ?, ?, ?);",
            (rel.id, rel.competency_id, rel.prerequisite_id, rel.created_at),
        )

    def list_all(self) -> list[PrerequisiteRelation]:
        rows = self.conn.execute("SELECT * FROM prerequisite_relation;").fetchall()
        return [PrerequisiteRelation(**dict(r)) for r in rows]

    def prerequisites_of(self, competency_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT prerequisite_id FROM prerequisite_relation WHERE competency_id = ?;",
            (competency_id,),
        ).fetchall()
        return [r["prerequisite_id"] for r in rows]


class RuleVersionRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, rv: RuleVersion) -> None:
        self.conn.execute(
            "INSERT INTO rule_version (id, version, description, created_at, active) "
            "VALUES (?, ?, ?, ?, ?);",
            (rv.id, rv.version, rv.description, rv.created_at, int(rv.active)),
        )

    def get_by_version(self, version: str) -> RuleVersion | None:
        row = self.conn.execute(
            "SELECT * FROM rule_version WHERE version = ?;", (version,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["active"] = _bool(d["active"])
        return RuleVersion(**d)

    def get(self, rule_version_id: str) -> RuleVersion | None:
        row = self.conn.execute(
            "SELECT * FROM rule_version WHERE id = ?;", (rule_version_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["active"] = _bool(d["active"])
        return RuleVersion(**d)

    def get_active(self) -> RuleVersion | None:
        row = self.conn.execute(
            "SELECT * FROM rule_version WHERE active = 1 ORDER BY created_at DESC LIMIT 1;"
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["active"] = _bool(d["active"])
        return RuleVersion(**d)

    def list_all(self) -> list[RuleVersion]:
        rows = self.conn.execute("SELECT * FROM rule_version ORDER BY created_at;").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["active"] = _bool(d["active"])
            out.append(RuleVersion(**d))
        return out


class SessionRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, session: LearningSession) -> None:
        self.conn.execute(
            "INSERT INTO learning_session (id, learner_id, started_at, ended_at, status) "
            "VALUES (?, ?, ?, ?, ?);",
            (
                session.id,
                session.learner_id,
                session.started_at,
                session.ended_at,
                session.status.value,
            ),
        )

    def end_session(self, session_id: str, ended_at: str) -> None:
        self.conn.execute(
            "UPDATE learning_session SET ended_at = ?, status = 'ended' WHERE id = ?;",
            (ended_at, session_id),
        )

    def get(self, session_id: str) -> LearningSession | None:
        row = self.conn.execute(
            "SELECT * FROM learning_session WHERE id = ?;", (session_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["status"] = SessionStatus(d["status"])
        return LearningSession(**d)

    def list_for_learner(self, learner_id: str) -> list[LearningSession]:
        rows = self.conn.execute(
            "SELECT * FROM learning_session WHERE learner_id = ? ORDER BY started_at DESC;",
            (learner_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["status"] = SessionStatus(d["status"])
            out.append(LearningSession(**d))
        return out


class ActivityRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, activity: Activity) -> None:
        self.conn.execute(
            "INSERT INTO activity "
            "(id, session_id, competency_targets, activity_type, prompt, support_level, "
            "created_at, tutor_provider_event_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
            (
                activity.id,
                activity.session_id,
                json.dumps(activity.competency_targets),
                activity.activity_type,
                activity.prompt,
                activity.support_level.value,
                activity.created_at,
                activity.tutor_provider_event_id,
            ),
        )

    def get(self, activity_id: str) -> Activity | None:
        row = self.conn.execute(
            "SELECT * FROM activity WHERE id = ?;", (activity_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["competency_targets"] = json.loads(d["competency_targets"])
        d["support_level"] = HelpLevel(d["support_level"])
        return Activity(**d)

    def list_for_session(self, session_id: str) -> list[Activity]:
        rows = self.conn.execute(
            "SELECT * FROM activity WHERE session_id = ? ORDER BY created_at;",
            (session_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["competency_targets"] = json.loads(d["competency_targets"])
            d["support_level"] = HelpLevel(d["support_level"])
            out.append(Activity(**d))
        return out


class RawInteractionRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, interaction: RawInteraction) -> None:
        self.conn.execute(
            "INSERT INTO raw_interaction "
            "(id, activity_id, session_id, idempotency_key, learner_input, tutor_output, "
            "help_level, production_result, occurred_at, evidence_cluster_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            (
                interaction.id,
                interaction.activity_id,
                interaction.session_id,
                interaction.idempotency_key,
                interaction.learner_input,
                interaction.tutor_output,
                interaction.help_level.value,
                interaction.production_result.value,
                interaction.occurred_at,
                interaction.evidence_cluster_id,
            ),
        )

    def get_by_idempotency_key(self, key: str) -> RawInteraction | None:
        row = self.conn.execute(
            "SELECT * FROM raw_interaction WHERE idempotency_key = ?;", (key,)
        ).fetchone()
        return self._map(row) if row else None

    def get(self, interaction_id: str) -> RawInteraction | None:
        row = self.conn.execute(
            "SELECT * FROM raw_interaction WHERE id = ?;", (interaction_id,)
        ).fetchone()
        return self._map(row) if row else None

    def list_all(self) -> list[RawInteraction]:
        rows = self.conn.execute(
            "SELECT * FROM raw_interaction ORDER BY occurred_at;"
        ).fetchall()
        return [self._map(r) for r in rows]

    def list_by_cluster(self, cluster_id: str) -> list[RawInteraction]:
        rows = self.conn.execute(
            "SELECT * FROM raw_interaction WHERE evidence_cluster_id = ? ORDER BY occurred_at;",
            (cluster_id,),
        ).fetchall()
        return [self._map(r) for r in rows]

    @staticmethod
    def _map(row: sqlite3.Row) -> RawInteraction:
        d = dict(row)
        d["help_level"] = HelpLevel(d["help_level"])
        d["production_result"] = ProductionResult(d["production_result"])
        return RawInteraction(**d)


class EvidenceEventRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, event: EvidenceEvent) -> None:
        self.conn.execute(
            "INSERT INTO evidence_event "
            "(id, raw_interaction_id, competency_id, dimension, evidence_type, relation, "
            "help_level, production_result, evidence_cluster_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            (
                event.id,
                event.raw_interaction_id,
                event.competency_id,
                event.dimension.value,
                event.evidence_type.value,
                event.relation.value,
                event.help_level.value,
                event.production_result.value,
                event.evidence_cluster_id,
                event.created_at,
            ),
        )

    def get(self, event_id: str) -> EvidenceEvent | None:
        row = self.conn.execute(
            "SELECT * FROM evidence_event WHERE id = ?;", (event_id,)
        ).fetchone()
        return self._map(row) if row else None

    def list_for_competency(self, competency_id: str, dimension: Dimension | None = None) -> list[EvidenceEvent]:
        if dimension is None:
            rows = self.conn.execute(
                "SELECT * FROM evidence_event WHERE competency_id = ? ORDER BY created_at;",
                (competency_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM evidence_event WHERE competency_id = ? AND dimension = ? "
                "ORDER BY created_at;",
                (competency_id, dimension.value),
            ).fetchall()
        return [self._map(r) for r in rows]

    def list_all(self) -> list[EvidenceEvent]:
        rows = self.conn.execute("SELECT * FROM evidence_event ORDER BY created_at;").fetchall()
        return [self._map(r) for r in rows]

    def list_by_raw_interaction(self, raw_interaction_id: str) -> list[EvidenceEvent]:
        rows = self.conn.execute(
            "SELECT * FROM evidence_event WHERE raw_interaction_id = ?;",
            (raw_interaction_id,),
        ).fetchall()
        return [self._map(r) for r in rows]

    @staticmethod
    def _map(row: sqlite3.Row) -> EvidenceEvent:
        d = dict(row)
        d["dimension"] = Dimension(d["dimension"])
        d["evidence_type"] = EvidenceType(d["evidence_type"])
        d["relation"] = EvidenceRelation(d["relation"])
        d["help_level"] = HelpLevel(d["help_level"])
        d["production_result"] = ProductionResult(d["production_result"])
        return EvidenceEvent(**d)


class EvidenceAssessmentRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, assessment: EvidenceAssessment) -> None:
        self.conn.execute(
            "INSERT INTO evidence_assessment "
            "(id, evidence_event_id, rule_version_id, classification, result, confidence, "
            "justification, alternative_cause, inconclusive, evaluator_provider_event_id, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            (
                assessment.id,
                assessment.evidence_event_id,
                assessment.rule_version_id,
                assessment.classification.value,
                assessment.result,
                assessment.confidence,
                assessment.justification,
                assessment.alternative_cause,
                int(assessment.inconclusive),
                assessment.evaluator_provider_event_id,
                assessment.created_at,
            ),
        )

    def get_for_evidence_event(self, evidence_event_id: str) -> list[EvidenceAssessment]:
        rows = self.conn.execute(
            "SELECT * FROM evidence_assessment WHERE evidence_event_id = ? ORDER BY created_at;",
            (evidence_event_id,),
        ).fetchall()
        return [self._map(r) for r in rows]

    def list_all(self) -> list[EvidenceAssessment]:
        rows = self.conn.execute(
            "SELECT * FROM evidence_assessment ORDER BY created_at;"
        ).fetchall()
        return [self._map(r) for r in rows]

    @staticmethod
    def _map(row: sqlite3.Row) -> EvidenceAssessment:
        d = dict(row)
        d["classification"] = EvidenceType(d["classification"])
        d["inconclusive"] = _bool(d["inconclusive"])
        return EvidenceAssessment(**d)


class CompetencyStateRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, state: CompetencyState) -> None:
        self.conn.execute(
            "INSERT INTO competency_state "
            "(id, competency_id, dimension, state, possible_regression, "
            "has_unresolved_contradiction, last_evidence_assessment_id, rule_version_id, "
            "computed_at, explanation) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            (
                state.id,
                state.competency_id,
                state.dimension.value,
                state.state.value,
                int(state.possible_regression),
                int(state.has_unresolved_contradiction),
                state.last_evidence_assessment_id,
                state.rule_version_id,
                state.computed_at,
                state.explanation,
            ),
        )

    def current(self, competency_id: str, dimension: Dimension) -> CompetencyState | None:
        row = self.conn.execute(
            "SELECT * FROM competency_state WHERE competency_id = ? AND dimension = ? "
            "ORDER BY computed_at DESC, id DESC LIMIT 1;",
            (competency_id, dimension.value),
        ).fetchone()
        return self._map(row) if row else None

    def current_all_dimensions(self, competency_id: str) -> dict[Dimension, CompetencyState]:
        out: dict[Dimension, CompetencyState] = {}
        for dim in Dimension:
            state = self.current(competency_id, dim)
            if state:
                out[dim] = state
        return out

    def history(self, competency_id: str, dimension: Dimension) -> list[CompetencyState]:
        rows = self.conn.execute(
            "SELECT * FROM competency_state WHERE competency_id = ? AND dimension = ? "
            "ORDER BY computed_at;",
            (competency_id, dimension.value),
        ).fetchall()
        return [self._map(r) for r in rows]

    def delete_all(self) -> None:
        """Usado apenas pelo recompute total (Secao 14): apaga a projecao
        inteira para reconstrui-la a partir do event log."""
        self.conn.execute("DELETE FROM competency_state;")

    @staticmethod
    def _map(row: sqlite3.Row) -> CompetencyState:
        d = dict(row)
        d["dimension"] = Dimension(d["dimension"])
        d["state"] = CompetencyDimensionState(d["state"])
        d["possible_regression"] = _bool(d["possible_regression"])
        d["has_unresolved_contradiction"] = _bool(d["has_unresolved_contradiction"])
        return CompetencyState(**d)


class MemoryStateRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(self, state: MemoryState) -> None:
        existing = self.get(state.competency_id)
        if existing is None:
            self.conn.execute(
                "INSERT INTO memory_state "
                "(id, competency_id, fsrs_card_json, due_at, stability, difficulty, "
                "card_state, last_review_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
                (
                    state.id,
                    state.competency_id,
                    state.fsrs_card_json,
                    state.due_at,
                    state.stability,
                    state.difficulty,
                    state.card_state,
                    state.last_review_at,
                    state.updated_at,
                ),
            )
        else:
            self.conn.execute(
                "UPDATE memory_state SET fsrs_card_json = ?, due_at = ?, stability = ?, "
                "difficulty = ?, card_state = ?, last_review_at = ?, updated_at = ? "
                "WHERE competency_id = ?;",
                (
                    state.fsrs_card_json,
                    state.due_at,
                    state.stability,
                    state.difficulty,
                    state.card_state,
                    state.last_review_at,
                    state.updated_at,
                    state.competency_id,
                ),
            )

    def get(self, competency_id: str) -> MemoryState | None:
        row = self.conn.execute(
            "SELECT * FROM memory_state WHERE competency_id = ?;", (competency_id,)
        ).fetchone()
        return MemoryState(**dict(row)) if row else None

    def list_all(self) -> list[MemoryState]:
        rows = self.conn.execute("SELECT * FROM memory_state;").fetchall()
        return [MemoryState(**dict(r)) for r in rows]

    def due_before(self, iso_datetime: str) -> list[MemoryState]:
        rows = self.conn.execute(
            "SELECT * FROM memory_state WHERE due_at IS NOT NULL AND due_at <= ? "
            "ORDER BY due_at;",
            (iso_datetime,),
        ).fetchall()
        return [MemoryState(**dict(r)) for r in rows]

    def delete_all(self) -> None:
        self.conn.execute("DELETE FROM memory_state;")


class MemoryReviewLogRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, log: MemoryReviewLog) -> None:
        self.conn.execute(
            "INSERT INTO memory_review_log "
            "(id, competency_id, memory_state_id, review_datetime, rating, "
            "fsrs_review_log_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?);",
            (
                log.id,
                log.competency_id,
                log.memory_state_id,
                log.review_datetime,
                log.rating,
                log.fsrs_review_log_json,
                log.created_at,
            ),
        )

    def list_for_competency(self, competency_id: str) -> list[MemoryReviewLog]:
        rows = self.conn.execute(
            "SELECT * FROM memory_review_log WHERE competency_id = ? ORDER BY review_datetime;",
            (competency_id,),
        ).fetchall()
        return [MemoryReviewLog(**dict(r)) for r in rows]

    def delete_all(self) -> None:
        self.conn.execute("DELETE FROM memory_review_log;")


class DecisionEventRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, event: DecisionEvent) -> None:
        self.conn.execute(
            "INSERT INTO decision_event "
            "(id, session_id, learner_id, competency_id, routing, decision_type, "
            "rule_applied, justification, rule_version_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            (
                event.id,
                event.session_id,
                event.learner_id,
                event.competency_id,
                event.routing,
                event.decision_type,
                event.rule_applied,
                event.justification,
                event.rule_version_id,
                event.created_at,
            ),
        )

    def list_recent(self, limit: int = 50) -> list[DecisionEvent]:
        rows = self.conn.execute(
            "SELECT * FROM decision_event ORDER BY created_at DESC LIMIT ?;", (limit,)
        ).fetchall()
        return [DecisionEvent(**dict(r)) for r in rows]

    def list_for_competency(self, competency_id: str) -> list[DecisionEvent]:
        rows = self.conn.execute(
            "SELECT * FROM decision_event WHERE competency_id = ? ORDER BY created_at;",
            (competency_id,),
        ).fetchall()
        return [DecisionEvent(**dict(r)) for r in rows]


class ProviderEventRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, event: ProviderEvent) -> None:
        self.conn.execute(
            "INSERT INTO provider_event "
            "(id, provider_name, model, function, config_version, occurred_at, success, "
            "latency_ms, tokens_used, cost, error_message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            (
                event.id,
                event.provider_name,
                event.model,
                event.function.value,
                event.config_version,
                event.occurred_at,
                int(event.success),
                event.latency_ms,
                event.tokens_used,
                event.cost,
                event.error_message,
            ),
        )

    def list_recent(self, limit: int = 50) -> list[ProviderEvent]:
        rows = self.conn.execute(
            "SELECT * FROM provider_event ORDER BY occurred_at DESC LIMIT ?;", (limit,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["function"] = ProviderFunction(d["function"])
            d["success"] = _bool(d["success"])
            out.append(ProviderEvent(**d))
        return out


class BackupEventRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, event: BackupEvent) -> None:
        self.conn.execute(
            "INSERT INTO backup_event "
            "(id, backup_path, started_at, completed_at, success, size_bytes, "
            "retention_note, error_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
            (
                event.id,
                event.backup_path,
                event.started_at,
                event.completed_at,
                int(event.success),
                event.size_bytes,
                event.retention_note,
                event.error_message,
            ),
        )

    def list_recent(self, limit: int = 50) -> list[BackupEvent]:
        rows = self.conn.execute(
            "SELECT * FROM backup_event ORDER BY started_at DESC LIMIT ?;", (limit,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["success"] = _bool(d["success"])
            out.append(BackupEvent(**d))
        return out
