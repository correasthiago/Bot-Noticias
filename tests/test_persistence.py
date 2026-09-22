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


def test_raw_interaction_is_immutable(conn: sqlite3.Connection, repos: Repositories) -> None:
    from central_universal.domain.entities import Activity, RawInteraction
    from central_universal.domain.enums import HelpLevel, ProductionResult

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
    interaction = RawInteraction(
        id=new_id(),
        activity_id=activity.id,
        session_id=session.id,
        idempotency_key=new_id(),
        learner_input="I go to school",
        tutor_output="",
        help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
        occurred_at=utc_now_iso(),
        evidence_cluster_id=new_id(),
    )
    repos.raw_interactions.insert(interaction)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE raw_interaction SET learner_input = 'tampered' WHERE id = ?;",
            (interaction.id,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM raw_interaction WHERE id = ?;", (interaction.id,))
