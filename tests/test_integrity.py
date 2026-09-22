from __future__ import annotations

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import PrerequisiteRelation, RuleVersion
from central_universal.domain.ids import new_id
from central_universal.integrity.checks import run_all
from central_universal.persistence.repositories import Repositories


def test_clean_database_has_no_findings(repos: Repositories, make_competency):
    make_competency()
    report = run_all(repos)
    assert report.ok
    assert report.findings == ()


def test_prerequisite_cycle_is_detected(repos: Repositories, make_competency):
    a = make_competency("a")
    b = make_competency("b")
    now = utc_now_iso()
    repos.prerequisites.insert(PrerequisiteRelation(id=new_id(), competency_id=a, prerequisite_id=b, created_at=now))
    repos.prerequisites.insert(PrerequisiteRelation(id=new_id(), competency_id=b, prerequisite_id=a, created_at=now))

    report = run_all(repos)
    assert not report.ok
    assert any(f.check == "prerequisite_cycles" for f in report.findings)


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
