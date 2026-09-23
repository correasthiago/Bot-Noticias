from __future__ import annotations

import sqlite3

import pytest

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import Learner, LearningSession
from central_universal.domain.enums import SessionStatus
from central_universal.domain.ids import new_id
from central_universal.persistence.migrations import available_migrations, run_migrations
from central_universal.persistence.repositories import Repositories


def test_foreign_keys_enforced(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO learning_session (id, learner_id, started_at, ended_at, status) "
            "VALUES ('s1', 'nonexistent-learner', ?, NULL, 'active');",
            (utc_now_iso(),),
        )


def test_migrations_idempotent(conn: sqlite3.Connection) -> None:
    assert run_migrations(conn) == []  # ja aplicadas pela fixture
    assert len(available_migrations()) >= 1


def test_competency_state_current_and_history_use_monotonic_sequence_frozen_clock(
    repos: Repositories, make_competency, make_rule_version
) -> None:
    """Terceira auditoria pos-entrega, ponto 1: reproduz a falha
    intermitente de `test_P3_two_negatives_from_same_cluster_never_corroborate_regression`
    com o RELOGIO CONGELADO - todas as linhas gravadas com o MESMO
    `computed_at`, exatamente a condicao (resolucao de relogio do SO mais
    grosseira que o tempo entre duas gravacoes) que produzia uma escolha
    de "estado atual" nao-deterministica (desempate por `id`, um
    `uuid4().hex` aleatorio). `current()`/`history()` agora decidem
    exclusivamente por `sequence_number` (inteiro monotonico explicito) -
    o resultado abaixo precisa ser o MESMO em toda execucao, nunca
    depender de qual UUID "ganhou" o sorteio."""

    from central_universal.domain.entities import CompetencyState
    from central_universal.domain.enums import CompetencyDimensionState, Dimension
    from central_universal.evidence.service import ensure_active_generation

    competency_id = make_competency()
    rule_version = make_rule_version()
    generation_id = ensure_active_generation(repos, rule_version)

    frozen_computed_at = "2026-01-01T00:00:00.000000+00:00"
    states_in_order = [
        CompetencyDimensionState.NOT_ASSESSED,
        CompetencyDimensionState.INSUFFICIENT_EVIDENCE,
        CompetencyDimensionState.ACQUIRING,
        CompetencyDimensionState.DEMONSTRATED,
        CompetencyDimensionState.CONSOLIDATED,
    ] * 6  # 30 gravacoes seguidas, todas com o MESMO computed_at

    inserted_ids: list[str] = []
    for state_value in states_in_order:
        row = CompetencyState(
            id=new_id(),
            generation_id=generation_id,
            competency_id=competency_id,
            dimension=Dimension.ACCURACY,
            state=state_value,
            possible_regression=False,
            has_unresolved_contradiction=False,
            last_evidence_assessment_id=None,
            rule_version_id=rule_version.id,
            computed_at=frozen_computed_at,  # relogio congelado: NUNCA muda entre gravacoes
        )
        repos.competency_states.insert(row)
        inserted_ids.append(row.id)

    current = repos.competency_states.current(competency_id, Dimension.ACCURACY)
    assert current is not None
    assert current.id == inserted_ids[-1]  # a ULTIMA linha gravada, nunca uma escolhida por sorteio de id
    assert current.state == states_in_order[-1]

    history = repos.competency_states.history(competency_id, Dimension.ACCURACY)
    assert [row.id for row in history] == inserted_ids  # ordem de insercao real, preservada

    sequence_numbers = [row.sequence_number for row in history]
    assert sequence_numbers == sorted(sequence_numbers)
    assert len(set(sequence_numbers)) == len(sequence_numbers)  # estritamente unicos, nunca repetidos


def test_learner_roundtrip(repos: Repositories) -> None:
    learner = Learner(id=new_id(), display_name="Aluno Um", created_at=utc_now_iso())
    repos.learners.insert(learner)
    fetched = repos.learners.get(learner.id)
    assert fetched == learner


def test_session_roundtrip(repos: Repositories) -> None:
    learner = Learner(id=new_id(), display_name="Aluno", created_at=utc_now_iso())
    repos.learners.insert(learner)
    session = LearningSession(
        id=new_id(),
        learner_id=learner.id,
        started_at=utc_now_iso(),
        status=SessionStatus.ACTIVE,
    )
    repos.sessions.insert(session)
    fetched = repos.sessions.get(session.id)
    assert fetched is not None
    assert fetched.status == SessionStatus.ACTIVE

    repos.sessions.end_session(session.id, utc_now_iso())
    ended = repos.sessions.get(session.id)
    assert ended is not None
    assert ended.status == SessionStatus.ENDED
    assert ended.ended_at is not None


def _bootstrap_interaction(repos: Repositories):
    from central_universal.domain.entities import Activity
    from central_universal.domain.enums import HelpLevel, ProductionResult
    from central_universal.evidence.service import EvidenceService

    learner = Learner(id=new_id(), display_name="Aluno", created_at=utc_now_iso())
    repos.learners.insert(learner)
    session = LearningSession(
        id=new_id(), learner_id=learner.id, started_at=utc_now_iso(), status=SessionStatus.ACTIVE
    )
    repos.sessions.insert(session)
    activity = Activity(
        id=new_id(),
        session_id=session.id,
        competency_targets=[],
        activity_type="drill",
        prompt="...",
        support_level=HelpLevel.A0,
        created_at=utc_now_iso(),
    )
    repos.activities.insert(activity)
    service = EvidenceService(repos)
    interaction, _ = service.record_interaction(
        activity=activity, session_id=session.id, idempotency_key=new_id(),
        learner_input="I go to school", tutor_output="", help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
    )
    return interaction


def test_raw_interaction_is_immutable(conn: sqlite3.Connection, repos: Repositories) -> None:
    interaction = _bootstrap_interaction(repos)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE raw_interaction SET learner_input = 'tampered' WHERE id = ?;",
            (interaction.id,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM raw_interaction WHERE id = ?;", (interaction.id,))
    # nem mesmo tentar "reabrir" uma avaliacao ja concluida e permitido
    conn.execute("UPDATE raw_interaction SET evaluation_status = 'completed' WHERE id = ?;", (interaction.id,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE raw_interaction SET evaluation_status = 'pending' WHERE id = ?;", (interaction.id,))


def test_raw_interaction_allows_only_pending_to_completed_transition(conn: sqlite3.Connection, repos: Repositories) -> None:
    interaction = _bootstrap_interaction(repos)
    assert interaction.evaluation_status.value == "pending"

    conn.execute("UPDATE raw_interaction SET evaluation_status = 'completed' WHERE id = ?;", (interaction.id,))
    row = conn.execute("SELECT evaluation_status FROM raw_interaction WHERE id = ?;", (interaction.id,)).fetchone()
    assert row["evaluation_status"] == "completed"

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE raw_interaction SET evaluation_status = 'completed' WHERE id = ?;", (interaction.id,))
