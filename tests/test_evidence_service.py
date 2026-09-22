from __future__ import annotations

import pytest

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import Activity, Learner, LearningSession, RuleVersion
from central_universal.domain.enums import (
    CompetencyDimensionState,
    Dimension,
    HelpLevel,
    ProductionResult,
    SessionStatus,
)
from central_universal.domain.ids import new_id
from central_universal.evaluator.contract import (
    InvalidEvaluatorOutput,
    validate_evaluator_payload,
)
from central_universal.evidence.service import EvidenceService, recompute_all_from_log
from central_universal.persistence.repositories import Repositories


def _bootstrap(repos: Repositories):
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
        prompt="Use simple present",
        support_level=HelpLevel.A0,
        created_at=utc_now_iso(),
    )
    repos.activities.insert(activity)
    from central_universal.domain.entities import Competency, LearningDomain

    domain = LearningDomain(id=new_id(), code="english", name="Ingles")
    repos.domains.insert(domain)
    competency = Competency(id=new_id(), domain_id=domain.id, code="simple_present", name="Simple Present")
    repos.competencies.insert(competency)
    rule_version = RuleVersion(
        id=new_id(), version="v0.1.0", description="V0", created_at=utc_now_iso(),
        config_json="{}", algorithm_version="v2",
    )
    repos.rule_versions.insert(rule_version)
    return session, activity, competency, rule_version


def _valid_payload(competency_id: str, dimension: str = "accuracy", relation: str = "target",
                    classification: str = "positive") -> dict:
    return {
        "findings": [
            {
                "competency_id": competency_id,
                "dimension": dimension,
                "classification": classification,
                "result": "correct usage of 3rd person -s",
                "confidence": 0.8,
                "justification": "Learner produced 'she goes' spontaneously.",
                "relation": relation,
            }
        ]
    }


def _new_activity(repos: Repositories, session_id: str, prompt: str = "drill") -> Activity:
    activity = Activity(
        id=new_id(), session_id=session_id, competency_targets=[], activity_type="drill",
        prompt=prompt, support_level=HelpLevel.A0, created_at=utc_now_iso(),
    )
    repos.activities.insert(activity)
    return activity


def test_double_submit_does_not_duplicate_evidence(repos: Repositories):
    session, activity, competency, rule_version = _bootstrap(repos)
    service = EvidenceService(repos)
    key = new_id()

    interaction1, created1 = service.record_interaction(
        activity=activity, session_id=session.id, idempotency_key=key,
        learner_input="She go to school", tutor_output="", help_level=HelpLevel.A0,
        production_result=ProductionResult.INCORRECT,
    )
    interaction2, created2 = service.record_interaction(
        activity=activity, session_id=session.id, idempotency_key=key,
        learner_input="She go to school", tutor_output="", help_level=HelpLevel.A0,
        production_result=ProductionResult.INCORRECT,
    )
    assert created1 is True
    assert created2 is False
    assert interaction1.id == interaction2.id
    assert len(repos.raw_interactions.list_all()) == 1


def test_mere_presence_does_not_alter_state(repos: Repositories):
    session, activity, competency, rule_version = _bootstrap(repos)
    service = EvidenceService(repos)
    interaction, _ = service.record_interaction(
        activity=activity, session_id=session.id, idempotency_key=new_id(),
        learner_input="text mentioning BE in passing", tutor_output="", help_level=HelpLevel.A0,
        production_result=ProductionResult.INCONCLUSIVE,
    )
    payload = _valid_payload(competency.id, relation="mere_presence", classification="inconclusive")
    output = validate_evaluator_payload(payload, {competency.id})
    result = service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)

    assert result.already_evaluated is False
    assert result.competency_states == []  # nao dispara recomputo
    current = repos.competency_states.current(competency.id, Dimension.ACCURACY)
    assert current is None


def test_invalid_evaluator_payload_raises_and_alters_nothing(repos: Repositories):
    session, activity, competency, rule_version = _bootstrap(repos)
    bad_payload = {"findings": [{"competency_id": competency.id, "dimension": "not-a-real-dimension"}]}
    with pytest.raises(InvalidEvaluatorOutput):
        validate_evaluator_payload(bad_payload, {competency.id})
    assert repos.evidence_events.list_all() == []
    assert repos.evidence_assessments.list_all() == []


def test_full_reconstruction_matches_incremental_state(repos: Repositories):
    session, activity, competency, rule_version = _bootstrap(repos)
    service = EvidenceService(repos)

    for i in range(3):
        drill_activity = _new_activity(repos, session.id, prompt=f"drill #{i}")
        interaction, _ = service.record_interaction(
            activity=drill_activity, session_id=session.id, idempotency_key=new_id(),
            learner_input=f"She goes #{i}", tutor_output="", help_level=HelpLevel.A0,
            production_result=ProductionResult.SPONTANEOUS_CORRECT,
        )
        payload = _valid_payload(competency.id)
        output = validate_evaluator_payload(payload, {competency.id})
        service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)

    before = repos.competency_states.current(competency.id, Dimension.ACCURACY)
    assert before is not None
    assert before.state == CompetencyDimensionState.CONSOLIDATED

    recompute_all_from_log(repos, rule_version)

    after = repos.competency_states.current(competency.id, Dimension.ACCURACY)
    assert after is not None
    assert after.state == before.state
    assert after.possible_regression == before.possible_regression
    assert after.explanation == before.explanation
    assert after.generation_id != before.generation_id  # nova geracao, historico preservado

    # a geracao ANTERIOR continua no banco, intacta, so nao e mais a "atual"
    old_generation_rows = repos.competency_states.list_by_generation(before.generation_id)
    assert len(old_generation_rows) >= 1


def test_reprocessing_pending_interaction_is_idempotent(repos: Repositories):
    """Secao 5 do pacote de correcao v0.2: uma interacao ainda pending
    pode ser reprocessada sem duplicar evidencia se a avaliacao for
    tentada duas vezes seguidas (ex.: apos uma falha anterior)."""

    session, activity, competency, rule_version = _bootstrap(repos)
    service = EvidenceService(repos)
    interaction, _ = service.record_interaction(
        activity=activity, session_id=session.id, idempotency_key=new_id(),
        learner_input="She goes", tutor_output="", help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
    )
    payload = _valid_payload(competency.id)
    output = validate_evaluator_payload(payload, {competency.id})

    first = service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)
    assert first.already_evaluated is False
    assert len(first.assessments) == 1

    completed_interaction = repos.raw_interactions.get(interaction.id)
    assert completed_interaction.evaluation_status.value == "completed"

    second = service.record_evaluation(raw_interaction=completed_interaction, evaluator_output=output, rule_version=rule_version)
    assert second.already_evaluated is True
    assert second.assessments == []
    assert len(repos.evidence_events.list_all()) == 1
    assert len(repos.evidence_assessments.list_all()) == 1


def test_inconclusive_finding_never_updates_state(repos: Repositories):
    session, activity, competency, rule_version = _bootstrap(repos)
    service = EvidenceService(repos)
    interaction, _ = service.record_interaction(
        activity=activity, session_id=session.id, idempotency_key=new_id(),
        learner_input="ambiguous", tutor_output="", help_level=HelpLevel.A0,
        production_result=ProductionResult.INCONCLUSIVE,
    )
    payload = _valid_payload(competency.id, classification="inconclusive")
    payload["findings"][0]["inconclusive"] = True
    output = validate_evaluator_payload(payload, {competency.id})
    result = service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)

    assert len(result.assessments) == 1  # o assessment existe (auditavel)
    assert result.competency_states[0].state == CompetencyDimensionState.NOT_ASSESSED  # mas nao promoveu nada


def test_pending_alternative_cause_never_updates_state(repos: Repositories):
    session, activity, competency, rule_version = _bootstrap(repos)
    service = EvidenceService(repos)
    interaction, _ = service.record_interaction(
        activity=activity, session_id=session.id, idempotency_key=new_id(),
        learner_input="she goes", tutor_output="", help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
    )
    payload = _valid_payload(competency.id)
    payload["findings"][0]["alternative_cause"] = "pode ter sido colado do material de apoio"
    output = validate_evaluator_payload(payload, {competency.id})
    result = service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)

    assert result.competency_states[0].state == CompetencyDimensionState.NOT_ASSESSED
