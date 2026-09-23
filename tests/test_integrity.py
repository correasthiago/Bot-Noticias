from __future__ import annotations

import pytest

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import PrerequisiteRelation, RuleVersion
from central_universal.domain.ids import new_id
from central_universal.integrity.checks import run_all
from central_universal.persistence.repositories import PrerequisiteCycleError, Repositories


def test_clean_database_has_no_findings(repos: Repositories, make_competency):
    make_competency()
    report = run_all(repos)
    assert report.ok
    assert report.findings == ()


def test_prerequisite_cycle_is_rejected_at_write_time(repos: Repositories, make_competency):
    """Secao 12 do pacote de correcao v0.2: o ciclo e bloqueado ANTES de
    persistir, nao so detectado depois por integrity_check."""

    a = make_competency("a")
    b = make_competency("b")
    now = utc_now_iso()
    repos.prerequisites.insert(PrerequisiteRelation(id=new_id(), competency_id=a, prerequisite_id=b, created_at=now))

    with pytest.raises(PrerequisiteCycleError):
        repos.prerequisites.insert(
            PrerequisiteRelation(id=new_id(), competency_id=b, prerequisite_id=a, created_at=now)
        )

    # o ciclo nunca chegou a existir no banco
    report = run_all(repos)
    assert report.ok


def test_integrity_check_still_catches_a_cycle_that_bypassed_the_repository(repos: Repositories, make_competency):
    """Defesa em profundidade: mesmo que algo escreva SQL bruto
    contornando PrerequisiteRepository.insert (ex.: uma migration futura
    malfeita), o integrity_check ainda pega o ciclo."""

    a = make_competency("a")
    b = make_competency("b")
    now = utc_now_iso()
    repos.conn.execute(
        "INSERT INTO prerequisite_relation (id, competency_id, prerequisite_id, created_at) VALUES (?, ?, ?, ?);",
        (new_id(), a, b, now),
    )
    repos.conn.execute(
        "INSERT INTO prerequisite_relation (id, competency_id, prerequisite_id, created_at) VALUES (?, ?, ?, ?);",
        (new_id(), b, a, now),
    )

    report = run_all(repos)
    assert not report.ok
    assert any(f.check == "prerequisite_cycles" for f in report.findings)


def test_generation_consistency_catches_multiple_active_generations(repos: Repositories, make_rule_version):
    from central_universal.domain.entities import ProjectionGeneration
    from central_universal.domain.enums import ProjectionGenerationStatus

    rule_version = make_rule_version()
    g1 = ProjectionGeneration(id=new_id(), rule_version_id=rule_version.id, created_at=utc_now_iso(), status=ProjectionGenerationStatus.ACTIVE)
    g2 = ProjectionGeneration(id=new_id(), rule_version_id=rule_version.id, created_at=utc_now_iso(), status=ProjectionGenerationStatus.ACTIVE)
    repos.projection_generations.insert(g1)
    repos.projection_generations.insert(g2)

    report = run_all(repos)
    assert not report.ok
    assert any(f.check == "generation_consistency" for f in report.findings)


def test_memory_consistency_catches_mismatched_last_review_at(repos: Repositories, make_competency):
    from central_universal.domain.entities import MemoryState

    competency_id = make_competency()
    repos.memory_states.upsert(
        MemoryState(
            id=new_id(), competency_id=competency_id, fsrs_card_json="{}", due_at=None,
            stability=None, difficulty=None, card_state="learning",
            last_review_at=utc_now_iso(), updated_at=utc_now_iso(),
        )
    )
    # last_review_at preenchido mas nenhum memory_review_log existe
    report = run_all(repos)
    assert not report.ok
    assert any(f.check == "memory_card_review_log_consistency" for f in report.findings)


def test_dangling_activity_reference_is_detected(repos: Repositories, make_competency):
    from central_universal.domain.entities import Activity, Learner, LearningSession
    from central_universal.domain.enums import HelpLevel, SessionStatus

    learner = Learner(id=new_id(), display_name="Aluno", created_at=utc_now_iso())
    repos.learners.insert(learner)
    session = LearningSession(id=new_id(), learner_id=learner.id, started_at=utc_now_iso(), status=SessionStatus.ACTIVE)
    repos.sessions.insert(session)
    activity = Activity(
        id=new_id(), session_id=session.id, competency_targets=["does-not-exist"],
        activity_type="drill", prompt="...", support_level=HelpLevel.A0, created_at=utc_now_iso(),
    )
    repos.activities.insert(activity)

    report = run_all(repos)
    assert not report.ok
    assert any(f.check == "dangling_references" for f in report.findings)
