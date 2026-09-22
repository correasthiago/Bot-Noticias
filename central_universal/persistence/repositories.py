"""Repositorios: unica camada que conhece SQL.

Cada metodo mapeia 1:1 entre uma dataclass de `domain.entities` e uma
linha de tabela. Nenhuma regra pedagogica vive aqui - apenas persistencia,
com uma excecao deliberada: a deteccao de ciclo de pre-requisitos em
`PrerequisiteRepository.insert` (Secao 12 do pacote de correcao v0.2),
porque bloquear a escrita ali e a UNICA forma de impedir o ciclo de
existir no banco - um `integrity_check` posterior so poderia detectar
depois do fato.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from central_universal.domain.entities import (
    Activity,
    BackupEvent,
    Competency,
    CompetencyState,
    DecisionEvent,
    EvidenceAssessment,
    EvidenceCluster,
    EvidenceEvent,
    Learner,
    LearningDomain,
    LearningSession,
    MemoryObservation,
    MemoryReviewLog,
    MemoryState,
    PrerequisiteRelation,
    ProjectionGeneration,
    ProviderEvent,
    RawInteraction,
    RuleVersion,
)
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


class PrerequisiteCycleError(ValueError):
    """Levantada quando uma PrerequisiteRelation criaria um self-loop ou um
    ciclo no grafo de pre-requisitos (Secao 12 do pacote de correcao v0.2:
    "Bloqueie self-loops e ciclos de pre-requisitos antes de persistir")."""


class Repositories:
    """Fachada com um repositorio por entidade, todos sobre a mesma conexao."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.learners = LearnerRepository(conn)
        self.domains = DomainRepository(conn)
        self.competencies = CompetencyRepository(conn)
        self.prerequisites = PrerequisiteRepository(conn)
        self.rule_versions = RuleVersionRepository(conn)
        self.active_rule_version = ActiveRuleVersionRepository(conn)
        self.sessions = SessionRepository(conn)
        self.activities = ActivityRepository(conn)
        self.evidence_clusters = EvidenceClusterRepository(conn)
        self.raw_interactions = RawInteractionRepository(conn)
        self.evidence_events = EvidenceEventRepository(conn)
        self.evidence_assessments = EvidenceAssessmentRepository(conn)
        self.projection_generations = ProjectionGenerationRepository(conn)
        self.active_projection_generation = ActiveProjectionGenerationRepository(conn)
        self.competency_states = CompetencyStateRepository(conn)
        self.memory_states = MemoryStateRepository(conn)
        self.memory_review_logs = MemoryReviewLogRepository(conn)
        self.memory_observations = MemoryObservationRepository(conn)
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
        if rel.competency_id == rel.prerequisite_id:
            raise PrerequisiteCycleError(
                f"self-loop rejeitado: competencia {rel.competency_id} nao pode "
                "ser pre-requisito de si mesma."
            )

        if self._creates_cycle(rel.competency_id, rel.prerequisite_id):
            raise PrerequisiteCycleError(
                f"ciclo rejeitado: {rel.competency_id} -> {rel.prerequisite_id} "
                "fecharia um ciclo no grafo de pre-requisitos."
            )

        self.conn.execute(
            "INSERT INTO prerequisite_relation "
            "(id, competency_id, prerequisite_id, created_at) VALUES (?, ?, ?, ?);",
            (rel.id, rel.competency_id, rel.prerequisite_id, rel.created_at),
        )

    def _creates_cycle(self, competency_id: str, prerequisite_id: str) -> bool:
        """A aresta candidata (competency_id -> prerequisite_id) fecha um
        ciclo se, partindo de `prerequisite_id`, for possivel alcancar
        `competency_id` seguindo arestas EXISTENTES."""

        edges: dict[str, list[str]] = {}
        for row in self.conn.execute(
            "SELECT competency_id, prerequisite_id FROM prerequisite_relation;"
        ).fetchall():
            edges.setdefault(row["competency_id"], []).append(row["prerequisite_id"])

        visited: set[str] = set()
        stack = [prerequisite_id]
        while stack:
            node = stack.pop()
            if node == competency_id:
                return True
            if node in visited:
                continue
            visited.add(node)
            stack.extend(edges.get(node, []))
        return False

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
    """`rule_version` e imutavel (trigger SQL). Qual versao esta ATIVA vive
    em `active_rule_version` (ver `ActiveRuleVersionRepository`), nunca
    numa coluna da propria linha - do contrario "ativar" uma versao
    exigiria dar UPDATE em outra, o que a trigger de imutabilidade proibe
    (Secao 9 do pacote de correcao v0.2)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, rv: RuleVersion) -> None:
        self.conn.execute(
            "INSERT INTO rule_version "
            "(id, version, description, created_at, config_json, algorithm_version) "
            "VALUES (?, ?, ?, ?, ?, ?);",
            (rv.id, rv.version, rv.description, rv.created_at, rv.config_json, rv.algorithm_version),
        )

    def get_by_version(self, version: str) -> RuleVersion | None:
        row = self.conn.execute(
            "SELECT * FROM rule_version WHERE version = ?;", (version,)
        ).fetchone()
        return RuleVersion(**dict(row)) if row else None

    def get(self, rule_version_id: str) -> RuleVersion | None:
        row = self.conn.execute(
            "SELECT * FROM rule_version WHERE id = ?;", (rule_version_id,)
        ).fetchone()
        return RuleVersion(**dict(row)) if row else None

    def list_all(self) -> list[RuleVersion]:
        rows = self.conn.execute("SELECT * FROM rule_version ORDER BY created_at;").fetchall()
        return [RuleVersion(**dict(r)) for r in rows]


class ActiveRuleVersionRepository:
    """Ponteiro MUTAVEL (singleton) para a RuleVersion ativa agora. Trocar
    de versao ativa nunca reescreve/reetiqueta a versao antiga (Principio
    17 e 18): so move o ponteiro."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get(self) -> RuleVersion | None:
        row = self.conn.execute(
            "SELECT rv.* FROM active_rule_version arv "
            "JOIN rule_version rv ON rv.id = arv.rule_version_id WHERE arv.id = 1;"
        ).fetchone()
        return RuleVersion(**dict(row)) if row else None

    def set(self, rule_version_id: str) -> None:
        self.conn.execute(
            "INSERT INTO active_rule_version (id, rule_version_id) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET rule_version_id = excluded.rule_version_id;",
            (rule_version_id,),
        )


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
            "created_at, tutor_provider_event_id, is_planned_recall) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
            (
                activity.id,
                activity.session_id,
                json.dumps(activity.competency_targets),
                activity.activity_type,
                activity.prompt,
                activity.support_level.value,
                activity.created_at,
                activity.tutor_provider_event_id,
                int(activity.is_planned_recall),
            ),
        )

    def get(self, activity_id: str) -> Activity | None:
        row = self.conn.execute(
            "SELECT * FROM activity WHERE id = ?;", (activity_id,)
        ).fetchone()
        return self._map(row) if row else None

    def list_for_session(self, session_id: str) -> list[Activity]:
        rows = self.conn.execute(
            "SELECT * FROM activity WHERE session_id = ? ORDER BY created_at;",
            (session_id,),
        ).fetchall()
        return [self._map(r) for r in rows]

    @staticmethod
    def _map(row: sqlite3.Row) -> Activity:
        d = dict(row)
        d["competency_targets"] = json.loads(d["competency_targets"])
        d["support_level"] = HelpLevel(d["support_level"])
        d["is_planned_recall"] = _bool(d["is_planned_recall"])
        return Activity(**d)


class EvidenceClusterRepository:
    """Clusters sao computados e mantidos pelo SERVIDOR (Secao 6 do pacote
    de correcao v0.2) - nada aqui aceita um id vindo do chamador como
    prova de independencia; `evidence.clustering.resolve_cluster` e o
    unico lugar que decide a que cluster uma tentativa pertence."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, cluster: EvidenceCluster) -> None:
        self.conn.execute(
            "INSERT INTO evidence_cluster "
            "(id, session_id, activity_type, context_signature, origin, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?);",
            (
                cluster.id,
                cluster.session_id,
                cluster.activity_type,
                cluster.context_signature,
                cluster.origin,
                cluster.first_seen_at,
                cluster.last_seen_at,
            ),
        )

    def find_by_signature(self, session_id: str, context_signature: str) -> EvidenceCluster | None:
        row = self.conn.execute(
            "SELECT * FROM evidence_cluster WHERE session_id = ? AND context_signature = ?;",
            (session_id, context_signature),
        ).fetchone()
        return EvidenceCluster(**dict(row)) if row else None

    def get(self, cluster_id: str) -> EvidenceCluster | None:
        row = self.conn.execute(
            "SELECT * FROM evidence_cluster WHERE id = ?;", (cluster_id,)
        ).fetchone()
        return EvidenceCluster(**dict(row)) if row else None

    def touch_last_seen(self, cluster_id: str, last_seen_at: str) -> None:
        self.conn.execute(
            "UPDATE evidence_cluster SET last_seen_at = ? WHERE id = ?;",
            (last_seen_at, cluster_id),
        )


class RawInteractionRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, interaction: RawInteraction) -> None:
        self.conn.execute(
            "INSERT INTO raw_interaction "
            "(id, activity_id, session_id, idempotency_key, learner_input, tutor_output, "
            "help_level, production_result, occurred_at, evidence_cluster_id, evaluation_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
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
                interaction.evaluation_status.value,
            ),
        )

    def try_claim_evaluation(self, interaction_id: str) -> bool:
        """Transicao atomica pending->completed (Secao 5 do pacote de
        correcao v0.2). Devolve True se ESTA chamada e que completou a
        avaliacao; False se outra chamada ja tinha completado antes -
        nesse caso o chamador NAO deve inserir evidencia nova (idempotencia
        real, nao apenas checagem antes de escrever)."""

        cursor = self.conn.execute(
            "UPDATE raw_interaction SET evaluation_status = 'completed' "
            "WHERE id = ? AND evaluation_status = 'pending';",
            (interaction_id,),
        )
        return cursor.rowcount == 1

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

    def list_by_activity(self, activity_id: str) -> list[RawInteraction]:
        rows = self.conn.execute(
            "SELECT * FROM raw_interaction WHERE activity_id = ? ORDER BY occurred_at;",
            (activity_id,),
        ).fetchall()
        return [self._map(r) for r in rows]

    @staticmethod
    def _map(row: sqlite3.Row) -> RawInteraction:
        d = dict(row)
        d["help_level"] = HelpLevel(d["help_level"])
        d["production_result"] = ProductionResult(d["production_result"])
        d["evaluation_status"] = EvaluationStatus(d["evaluation_status"])
        return RawInteraction(**d)


class EvidenceEventRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, event: EvidenceEvent) -> None:
        self.conn.execute(
            "INSERT INTO evidence_event "
            "(id, raw_interaction_id, competency_id, dimension, relation, "
            "help_level, production_result, evidence_cluster_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
            (
                event.id,
                event.raw_interaction_id,
                event.competency_id,
                event.dimension.value,
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

    def get(self, assessment_id: str) -> EvidenceAssessment | None:
        row = self.conn.execute(
            "SELECT * FROM evidence_assessment WHERE id = ?;", (assessment_id,)
        ).fetchone()
        return self._map(row) if row else None

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


class ProjectionGenerationRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, generation: ProjectionGeneration) -> None:
        self.conn.execute(
            "INSERT INTO projection_generation "
            "(id, rule_version_id, created_at, status, activated_at, note) "
            "VALUES (?, ?, ?, ?, ?, ?);",
            (
                generation.id,
                generation.rule_version_id,
                generation.created_at,
                generation.status.value,
                generation.activated_at,
                generation.note,
            ),
        )

    def get(self, generation_id: str) -> ProjectionGeneration | None:
        row = self.conn.execute(
            "SELECT * FROM projection_generation WHERE id = ?;", (generation_id,)
        ).fetchone()
        return self._map(row) if row else None

    def set_status(self, generation_id: str, status: ProjectionGenerationStatus, activated_at: str | None = None) -> None:
        self.conn.execute(
            "UPDATE projection_generation SET status = ?, activated_at = COALESCE(?, activated_at) WHERE id = ?;",
            (status.value, activated_at, generation_id),
        )

    def list_all(self) -> list[ProjectionGeneration]:
        rows = self.conn.execute(
            "SELECT * FROM projection_generation ORDER BY created_at;"
        ).fetchall()
        return [self._map(r) for r in rows]

    @staticmethod
    def _map(row: sqlite3.Row) -> ProjectionGeneration:
        d = dict(row)
        d["status"] = ProjectionGenerationStatus(d["status"])
        return ProjectionGeneration(**d)


class ActiveProjectionGenerationRepository:
    """Ponteiro MUTAVEL (singleton) para a geracao de CompetencyState
    ativa agora. Gerações antigas permanecem no banco, nunca sao
    apagadas (Secao 10 do pacote de correcao v0.2)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get_id(self) -> str | None:
        row = self.conn.execute("SELECT generation_id FROM active_projection_generation WHERE id = 1;").fetchone()
        return row["generation_id"] if row else None

    def set(self, generation_id: str) -> None:
        self.conn.execute(
            "INSERT INTO active_projection_generation (id, generation_id) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET generation_id = excluded.generation_id;",
            (generation_id,),
        )


class CompetencyStateRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, state: CompetencyState) -> None:
        self.conn.execute(
            "INSERT INTO competency_state "
            "(id, generation_id, competency_id, dimension, state, possible_regression, "
            "has_unresolved_contradiction, last_evidence_assessment_id, rule_version_id, "
            "computed_at, explanation) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            (
                state.id,
                state.generation_id,
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

    def current(self, competency_id: str, dimension: Dimension, generation_id: str | None = None) -> CompetencyState | None:
        """Estado "atual": a linha mais recente por (competency, dimension)
        DENTRO de uma geracao especifica. Sem `generation_id` explicito,
        usa a geracao ATIVA agora - nunca mistura linhas de geracoes
        diferentes."""

        gen_id = generation_id if generation_id is not None else self._active_generation_id()
        if gen_id is None:
            return None
        row = self.conn.execute(
            "SELECT * FROM competency_state WHERE generation_id = ? AND competency_id = ? "
            "AND dimension = ? ORDER BY computed_at DESC, id DESC LIMIT 1;",
            (gen_id, competency_id, dimension.value),
        ).fetchone()
        return self._map(row) if row else None

    def current_all_dimensions(self, competency_id: str, generation_id: str | None = None) -> dict[Dimension, CompetencyState]:
        out: dict[Dimension, CompetencyState] = {}
        for dim in Dimension:
            state = self.current(competency_id, dim, generation_id=generation_id)
            if state:
                out[dim] = state
        return out

    def history(self, competency_id: str, dimension: Dimension) -> list[CompetencyState]:
        """Historico COMPLETO, atravessando todas as geracoes (para
        auditoria) - ordenado por tempo de computo."""

        rows = self.conn.execute(
            "SELECT * FROM competency_state WHERE competency_id = ? AND dimension = ? "
            "ORDER BY computed_at;",
            (competency_id, dimension.value),
        ).fetchall()
        return [self._map(r) for r in rows]

    def list_by_generation(self, generation_id: str) -> list[CompetencyState]:
        rows = self.conn.execute(
            "SELECT * FROM competency_state WHERE generation_id = ? ORDER BY computed_at;",
            (generation_id,),
        ).fetchall()
        return [self._map(r) for r in rows]

    def _active_generation_id(self) -> str | None:
        row = self.conn.execute("SELECT generation_id FROM active_projection_generation WHERE id = 1;").fetchone()
        return row["generation_id"] if row else None

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

    def latest_for_competency(self, competency_id: str) -> MemoryReviewLog | None:
        row = self.conn.execute(
            "SELECT * FROM memory_review_log WHERE competency_id = ? "
            "ORDER BY review_datetime DESC LIMIT 1;",
            (competency_id,),
        ).fetchone()
        return MemoryReviewLog(**dict(row)) if row else None


class MemoryObservationRepository:
    """Secao 1 do pacote de correcao v0.2: o UNICO evento que autoriza uma
    chamada a `MemoryAdapter.review`."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, observation: MemoryObservation) -> None:
        self.conn.execute(
            "INSERT INTO memory_observation "
            "(id, competency_id, raw_interaction_id, evidence_assessment_id, planned_recall, "
            "interval_days, rating, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
            (
                observation.id,
                observation.competency_id,
                observation.raw_interaction_id,
                observation.evidence_assessment_id,
                int(observation.planned_recall),
                observation.interval_days,
                observation.rating,
                observation.created_at,
            ),
        )

    def get_by_raw_interaction(self, raw_interaction_id: str) -> MemoryObservation | None:
        row = self.conn.execute(
            "SELECT * FROM memory_observation WHERE raw_interaction_id = ?;",
            (raw_interaction_id,),
        ).fetchone()
        return self._map(row) if row else None

    def list_for_competency(self, competency_id: str) -> list[MemoryObservation]:
        rows = self.conn.execute(
            "SELECT * FROM memory_observation WHERE competency_id = ? ORDER BY created_at;",
            (competency_id,),
        ).fetchall()
        return [self._map(r) for r in rows]

    @staticmethod
    def _map(row: sqlite3.Row) -> MemoryObservation:
        d = dict(row)
        d["planned_recall"] = _bool(d["planned_recall"])
        return MemoryObservation(**d)


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
