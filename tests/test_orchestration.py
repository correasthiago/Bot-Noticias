from __future__ import annotations

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import Learner, RuleVersion
from central_universal.domain.enums import HelpLevel, ProductionResult, RoutingDecision
from central_universal.domain.ids import new_id
from central_universal.orchestration.session_service import SessionOrchestrator
from central_universal.persistence.repositories import Repositories
from central_universal.providers.mock import MockProvider


def _bootstrap_rule_version(repos: Repositories) -> RuleVersion:
    rv = RuleVersion(id=new_id(), version="v0.1.0", description="V0", created_at=utc_now_iso())
    repos.rule_versions.insert(rv)
    return rv


def test_full_cycle_offline_with_mock_provider(repos: Repositories, make_competency):
    """Cobre o ciclo completo da Secao 1, de ponta a ponta, sem rede
    (T15): diagnostico -> atividade -> interacao -> evidencia -> avaliacao
    -> estado -> memoria -> decisao -> persistencia."""

    competency_id = make_competency("simple_present")
    learner = Learner(id=new_id(), display_name="Aluno", created_at=utc_now_iso())
    repos.learners.insert(learner)
    rule_version = _bootstrap_rule_version(repos)

    orchestrator = SessionOrchestrator(repos, MockProvider(), rule_version)

    session = orchestrator.start_session(learner.id)
    assert session.status.value == "active"

    focus = orchestrator.choose_focus_competency(learner.id)
    assert focus is not None
    focus_competency_id, routing, action = focus
    assert focus_competency_id == competency_id
    assert routing.routing == RoutingDecision.STUDY  # sem evidencia ainda

    activity, tutor_output = orchestrator.start_activity(
        session_id=session.id, competency_id=focus_competency_id, action=action
    )
    assert tutor_output is not None
    assert activity.id in {a.id for a in repos.activities.list_for_session(session.id)}

    outcome = orchestrator.submit_interaction(
        activity_id=activity.id,
        session_id=session.id,
        idempotency_key=new_id(),
        learner_input="She goes to school",
        help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
        tutor_output_text=tutor_output.utterance,
    )

    assert outcome.created_now is True
    assert outcome.evaluator_output is not None
    assert len(outcome.competency_states) >= 1
    assert outcome.memory_result is not None
    assert outcome.next_routing is not None

    orchestrator.end_session(session.id)
    ended = repos.sessions.get(session.id)
    assert ended.status.value == "ended"

    # tudo persistiu no event log
    assert len(repos.raw_interactions.list_all()) == 1
    assert len(repos.evidence_events.list_all()) >= 1
    assert len(repos.provider_events.list_recent(10)) >= 2  # tutor + evaluator
    assert len(repos.decision_events.list_recent(10)) >= 2  # routing inicial + apos interacao


def test_double_submit_through_orchestrator_is_idempotent(repos: Repositories, make_competency):
    competency_id = make_competency()
    learner = Learner(id=new_id(), display_name="Aluno", created_at=utc_now_iso())
    repos.learners.insert(learner)
    rule_version = _bootstrap_rule_version(repos)
    orchestrator = SessionOrchestrator(repos, MockProvider(), rule_version)

    session = orchestrator.start_session(learner.id)
    activity, _ = orchestrator.start_activity(session_id=session.id, competency_id=competency_id, action=None)

    key = new_id()
    first = orchestrator.submit_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=key,
        learner_input="I is happy", help_level=HelpLevel.A0,
        production_result=ProductionResult.INCORRECT,
    )
    second = orchestrator.submit_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=key,
        learner_input="I is happy", help_level=HelpLevel.A0,
        production_result=ProductionResult.INCORRECT,
    )

    assert first.created_now is True
    assert second.created_now is False
    assert len(repos.raw_interactions.list_all()) == 1
    assert len(repos.evidence_events.list_all()) == 1  # nao duplicou
