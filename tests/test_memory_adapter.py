from __future__ import annotations

from datetime import datetime, timedelta, timezone

import fsrs
import pytest

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import (
    Activity,
    EvidenceAssessment,
    EvidenceEvent,
    Learner,
    LearningSession,
    RawInteraction,
)
from central_universal.domain.enums import (
    Dimension,
    EvaluationStatus,
    EvidenceRelation,
    EvidenceType,
    HelpLevel,
    ProductionResult,
    SessionStatus,
)
from central_universal.domain.ids import new_id
from central_universal.memory.fsrs_adapter import (
    MemoryAdapter,
    MemoryReviewError,
    evaluate_recall_eligibility,
    rating_from_production_result,
)
from central_universal.persistence.repositories import Repositories


def _planned_activity(session_id: str, planned: bool = True) -> Activity:
    return Activity(
        id=new_id(), session_id=session_id, competency_targets=[], activity_type="recall_drill",
        prompt="Recall this", support_level=HelpLevel.A0, created_at=utc_now_iso(),
        is_planned_recall=planned,
    )


def _valid_assessment(**overrides) -> EvidenceAssessment:
    base = dict(
        id=new_id(), evidence_event_id=new_id(), rule_version_id=new_id(),
        classification=EvidenceType.POSITIVE, result="correct", confidence=0.9,
        justification="ok", alternative_cause=None, inconclusive=False,
        evaluator_provider_event_id=None, created_at=utc_now_iso(),
    )
    base.update(overrides)
    return EvidenceAssessment(**base)


def _real_observation_refs(repos: Repositories, competency_id: str):
    """Cria Learner/Session/Activity/RawInteraction/EvidenceEvent/
    EvidenceAssessment de verdade, para satisfazer as foreign keys reais
    de memory_observation (raw_interaction_id, evidence_assessment_id)."""

    learner = Learner(id=new_id(), display_name="Aluno", created_at=utc_now_iso())
    repos.learners.insert(learner)
    session = LearningSession(id=new_id(), learner_id=learner.id, started_at=utc_now_iso(), status=SessionStatus.ACTIVE)
    repos.sessions.insert(session)
    activity = _planned_activity(session.id)
    repos.activities.insert(activity)
    interaction = RawInteraction(
        id=new_id(), activity_id=activity.id, session_id=session.id, idempotency_key=new_id(),
        learner_input="she goes", tutor_output="", help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT, occurred_at=utc_now_iso(),
        evidence_cluster_id=_ensure_cluster(repos, session.id, activity),
        evaluation_status=EvaluationStatus.PENDING,
    )
    repos.raw_interactions.insert(interaction)
    event = EvidenceEvent(
        id=new_id(), raw_interaction_id=interaction.id, competency_id=competency_id,
        dimension=Dimension.ACCURACY,
        relation=EvidenceRelation.TARGET, help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT, evidence_cluster_id=interaction.evidence_cluster_id,
        created_at=utc_now_iso(),
    )
    repos.evidence_events.insert(event)

    from central_universal.domain.entities import RuleVersion

    rule_version = RuleVersion(
        id=new_id(), version=f"v-test-{new_id()[:8]}", description="test",
        created_at=utc_now_iso(), config_json="{}", algorithm_version="v2",
    )
    repos.rule_versions.insert(rule_version)
    assessment = _valid_assessment(evidence_event_id=event.id, rule_version_id=rule_version.id)
    repos.evidence_assessments.insert(assessment)
    return activity, interaction, assessment


def _ensure_cluster(repos: Repositories, session_id: str, activity: Activity) -> str:
    from central_universal.evidence.clustering import resolve_cluster

    return resolve_cluster(repos, session_id=session_id, activity=activity).id


def test_get_or_create_state_is_idempotent(repos: Repositories, make_competency):
    adapter = MemoryAdapter(repos)
    competency_id = make_competency()
    first = adapter.get_or_create_state(competency_id)
    second = adapter.get_or_create_state(competency_id)
    assert first.id == second.id
    assert first.fsrs_card_json == second.fsrs_card_json


# --- elegibilidade (Secao 2 do pacote de correcao v0.2) --------------

def test_not_planned_activity_is_never_eligible():
    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1", planned=False),
        evidence_relation=EvidenceRelation.TARGET,
        assessment=_valid_assessment(),
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
        memory_state=None,
    )
    assert result.eligible is False
    assert "planejada" in result.reason


def test_mere_presence_is_never_eligible():
    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.MERE_PRESENCE,
        assessment=_valid_assessment(),
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
        memory_state=None,
    )
    assert result.eligible is False


def test_missing_assessment_is_never_eligible():
    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.TARGET,
        assessment=None,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
        memory_state=None,
    )
    assert result.eligible is False


def test_inconclusive_assessment_is_never_eligible():
    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.TARGET,
        assessment=_valid_assessment(classification=EvidenceType.INCONCLUSIVE, inconclusive=True),
        production_result=ProductionResult.INCONCLUSIVE,
        memory_state=None,
    )
    assert result.eligible is False


def test_pending_alternative_cause_is_never_eligible():
    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.TARGET,
        assessment=_valid_assessment(alternative_cause="possivel cola"),
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
        memory_state=None,
    )
    assert result.eligible is False


def test_first_observation_with_no_prior_state_is_eligible():
    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.TARGET,
        assessment=_valid_assessment(),
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
        memory_state=None,
    )
    assert result.eligible is True
    assert result.rating == int(fsrs.Rating.Easy)


# --- observe_and_review: escrita atomica ------------------------------

def test_observe_and_review_creates_state_log_and_observation(repos: Repositories, make_competency):
    adapter = MemoryAdapter(repos)
    competency_id = make_competency()
    activity, interaction, assessment = _real_observation_refs(repos, competency_id)
    eligibility = evaluate_recall_eligibility(
        activity=activity, evidence_relation=EvidenceRelation.TARGET,
        assessment=assessment, production_result=ProductionResult.SPONTANEOUS_CORRECT,
        memory_state=None,
    )
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    result = adapter.observe_and_review(
        competency_id=competency_id, raw_interaction_id=interaction.id, evidence_assessment_id=assessment.id,
        eligibility=eligibility, review_datetime=now,
    )

    assert not isinstance(result, MemoryReviewError)
    assert result.due_at is not None
    logs = repos.memory_review_logs.list_for_competency(competency_id)
    assert len(logs) == 1
    observations = repos.memory_observations.list_for_competency(competency_id)
    assert len(observations) == 1
    assert observations[0].rating == eligibility.rating


def test_ineligible_observation_never_calls_fsrs(repos: Repositories, make_competency, monkeypatch):
    adapter = MemoryAdapter(repos)
    competency_id = make_competency()
    activity, interaction, assessment = _real_observation_refs(repos, competency_id)

    def boom(*args, **kwargs):
        raise AssertionError("FSRS nao deveria ser chamado para observacao inelegivel")

    monkeypatch.setattr(adapter.scheduler, "review_card", boom)

    ineligible = evaluate_recall_eligibility(
        activity=_planned_activity("s1", planned=False), evidence_relation=EvidenceRelation.TARGET,
        assessment=assessment, production_result=ProductionResult.SPONTANEOUS_CORRECT,
        memory_state=None,
    )
    result = adapter.observe_and_review(
        competency_id=competency_id, raw_interaction_id=interaction.id, evidence_assessment_id=assessment.id,
        eligibility=ineligible,
    )
    assert isinstance(result, MemoryReviewError)
    assert repos.memory_states.get(competency_id) is None
    assert repos.memory_observations.list_for_competency(competency_id) == []


@pytest.mark.parametrize("failing_repo_attr,failing_method", [
    ("memory_states", "upsert"),
    ("memory_review_logs", "insert"),
    ("memory_observations", "insert"),
])
def test_failure_at_each_write_leaves_prior_state_byte_identical(
    repos: Repositories, make_competency, monkeypatch, failing_repo_attr, failing_method
):
    adapter = MemoryAdapter(repos)
    competency_id = make_competency()
    activity, interaction, assessment = _real_observation_refs(repos, competency_id)

    # primeira revisao bem sucedida, estabelece um estado "antes"
    eligibility = evaluate_recall_eligibility(
        activity=activity, evidence_relation=EvidenceRelation.TARGET,
        assessment=assessment, production_result=ProductionResult.SPONTANEOUS_CORRECT,
        memory_state=None,
    )
    adapter.observe_and_review(
        competency_id=competency_id, raw_interaction_id=interaction.id, evidence_assessment_id=assessment.id,
        eligibility=eligibility, review_datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    before_state = repos.memory_states.get(competency_id)
    before_logs = len(repos.memory_review_logs.list_for_competency(competency_id))
    before_observations = len(repos.memory_observations.list_for_competency(competency_id))

    # segunda interacao real, para a segunda tentativa de observacao
    _, interaction2, assessment2 = _real_observation_refs(repos, competency_id)

    target = getattr(repos, failing_repo_attr)
    call_count = {"n": 0}

    def boom(*args, **kwargs):
        call_count["n"] += 1
        raise RuntimeError(f"falha simulada em {failing_repo_attr}.{failing_method}")

    monkeypatch.setattr(target, failing_method, boom)

    second_eligibility = evaluate_recall_eligibility(
        activity=activity, evidence_relation=EvidenceRelation.TARGET,
        assessment=assessment2, production_result=ProductionResult.SPONTANEOUS_CORRECT,
        memory_state=before_state, now=datetime(2026, 1, 5, tzinfo=timezone.utc),
    )
    outcome = adapter.observe_and_review(
        competency_id=competency_id, raw_interaction_id=interaction2.id, evidence_assessment_id=assessment2.id,
        eligibility=second_eligibility, review_datetime=datetime(2026, 1, 5, tzinfo=timezone.utc),
    )

    assert isinstance(outcome, MemoryReviewError)
    assert call_count["n"] == 1

    after_state = repos.memory_states.get(competency_id)
    assert after_state.fsrs_card_json == before_state.fsrs_card_json  # byte a byte identico
    assert after_state.due_at == before_state.due_at
    assert after_state.updated_at == before_state.updated_at
    assert len(repos.memory_review_logs.list_for_competency(competency_id)) == before_logs
    assert len(repos.memory_observations.list_for_competency(competency_id)) == before_observations


def test_is_recall_due_reflects_scheduler(repos: Repositories, make_competency):
    adapter = MemoryAdapter(repos)
    competency_id = make_competency()
    activity, interaction, assessment = _real_observation_refs(repos, competency_id)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    eligibility = evaluate_recall_eligibility(
        activity=activity, evidence_relation=EvidenceRelation.TARGET,
        assessment=assessment, production_result=ProductionResult.SPONTANEOUS_CORRECT,
        memory_state=None,
    )
    adapter.observe_and_review(
        competency_id=competency_id, raw_interaction_id=interaction.id, evidence_assessment_id=assessment.id,
        eligibility=eligibility, review_datetime=now,
    )

    assert adapter.is_recall_due(competency_id, now=now) is False
    far_future = now + timedelta(days=3650)
    assert adapter.is_recall_due(competency_id, now=far_future) is True


def test_rating_from_production_result_maps_known_values():
    assert rating_from_production_result(ProductionResult.SPONTANEOUS_CORRECT) == int(fsrs.Rating.Easy)
    assert rating_from_production_result(ProductionResult.INCORRECT) == int(fsrs.Rating.Again)
    assert rating_from_production_result(ProductionResult.INCONCLUSIVE) is None
