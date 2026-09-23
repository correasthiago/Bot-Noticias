from __future__ import annotations

from datetime import datetime, timedelta, timezone

import fsrs
import pytest

from central_universal.domain.clock import to_utc_iso, utc_now_iso
from central_universal.domain.entities import (
    Activity,
    EvidenceAssessment,
    EvidenceEvent,
    Learner,
    LearningSession,
    MemoryState,
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
from central_universal.evidence.aggregation import AggregationConfig
from central_universal.memory.fsrs_adapter import (
    MemoryAdapter,
    MemoryReviewError,
    derive_recall_rating,
    evaluate_recall_eligibility,
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


def _real_observation_refs(repos: Repositories, competency_id: str, **assessment_overrides):
    """Cria Learner/Session/Activity/RawInteraction/EvidenceEvent/
    EvidenceAssessment de verdade, para satisfazer as foreign keys reais
    de memory_observation (raw_interaction_id, evidence_assessment_id).

    O evento criado e da dimensao RETENTION com relacao `target` - o
    unico par que `evaluate_recall_eligibility` aceita para revisao de
    memoria (Secao 2 do pacote de correcao v0.2.1)."""

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
        dimension=Dimension.RETENTION,
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
    assessment = _valid_assessment(evidence_event_id=event.id, rule_version_id=rule_version.id, **assessment_overrides)
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


# --- elegibilidade (Secao 2 do pacote de correcao v0.2/v0.2.1) --------

def test_not_planned_activity_is_never_eligible():
    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1", planned=False),
        evidence_relation=EvidenceRelation.TARGET,
        evidence_dimension=Dimension.RETENTION,
        assessment=_valid_assessment(),
        memory_state=None,
    )
    assert result.eligible is False
    assert "planejada" in result.reason


def test_mere_presence_is_never_eligible():
    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.MERE_PRESENCE,
        evidence_dimension=Dimension.RETENTION,
        assessment=_valid_assessment(),
        memory_state=None,
    )
    assert result.eligible is False


def test_incidental_evidence_is_never_eligible():
    """Ponto 2 da correcao v0.2.1: evidencia incidental (relacao
    `qualified_incidental`, nunca `target`) jamais pode gerar uma
    observacao de memoria, mesmo com avaliacao positiva e confiante."""

    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.QUALIFIED_INCIDENTAL,
        evidence_dimension=Dimension.RETENTION,
        assessment=_valid_assessment(confidence=0.95),
        memory_state=None,
    )
    assert result.eligible is False
    assert "incidental" in result.reason or "target" in result.reason


def test_wrong_dimension_is_never_eligible():
    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.TARGET,
        evidence_dimension=Dimension.ACCURACY,
        assessment=_valid_assessment(),
        memory_state=None,
    )
    assert result.eligible is False
    assert "retention" in result.reason


def test_missing_assessment_is_never_eligible():
    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.TARGET,
        evidence_dimension=Dimension.RETENTION,
        assessment=None,
        memory_state=None,
    )
    assert result.eligible is False


def test_inconclusive_assessment_is_never_eligible():
    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.TARGET,
        evidence_dimension=Dimension.RETENTION,
        assessment=_valid_assessment(classification=EvidenceType.INCONCLUSIVE, inconclusive=True),
        memory_state=None,
    )
    assert result.eligible is False


def test_pending_alternative_cause_is_never_eligible():
    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.TARGET,
        evidence_dimension=Dimension.RETENTION,
        assessment=_valid_assessment(alternative_cause="possivel cola"),
        memory_state=None,
    )
    assert result.eligible is False


def test_low_confidence_assessment_is_never_eligible():
    """Ponto 2 da correcao v0.2.1: confianca abaixo de
    `recall_min_confidence` bloqueia a observacao mesmo com classificacao
    POSITIVE e relacao target."""

    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.TARGET,
        evidence_dimension=Dimension.RETENTION,
        assessment=_valid_assessment(confidence=0.3),
        memory_state=None,
    )
    assert result.eligible is False
    assert "confianca" in result.reason


def test_missing_observable_signals_is_never_eligible():
    """Terceira auditoria pos-entrega, ponto 2: sem `help_level`/
    `production_result` (os sinais observaveis que decidem a nota), a
    observacao NUNCA e aceita - nunca cai de volta para confianca."""

    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.TARGET,
        evidence_dimension=Dimension.RETENTION,
        assessment=_valid_assessment(confidence=0.99),
        memory_state=None,
    )
    assert result.eligible is False
    assert "observaveis" in result.reason


def test_first_observation_with_no_prior_state_is_eligible():
    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.TARGET,
        evidence_dimension=Dimension.RETENTION,
        assessment=_valid_assessment(confidence=0.9),
        help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
        memory_state=None,
    )
    assert result.eligible is True
    assert result.rating == int(fsrs.Rating.Easy)


def test_negative_evaluation_is_eligible_with_again_rating():
    """Ponto 2 da correcao v0.2.1: uma avaliacao NEGATIVE, conclusiva e
    confiante, ainda produz uma observacao valida - so que com nota
    "Again", nunca derivada do ProductionResult bruto ou da confianca."""

    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.TARGET,
        evidence_dimension=Dimension.RETENTION,
        assessment=_valid_assessment(classification=EvidenceType.NEGATIVE, result="incorrect", confidence=0.9),
        help_level=HelpLevel.A0,
        production_result=ProductionResult.INCORRECT,
        memory_state=None,
    )
    assert result.eligible is True
    assert result.rating == int(fsrs.Rating.Again)


def test_derive_recall_rating_uses_observable_signals_not_confidence():
    """Terceira auditoria pos-entrega, ponto 2: confianca do avaliador
    NUNCA decide Easy/Good/Hard - so os sinais observaveis da propria
    tentativa (help_level, production_result). Uma avaliacao POSITIVE de
    confianca altissima que so saiu certa depois de uma pista explicita
    tem que virar Hard, nunca Easy."""

    high_confidence_but_hinted = _valid_assessment(confidence=0.99)
    assert derive_recall_rating(
        high_confidence_but_hinted, help_level=HelpLevel.A2, production_result=ProductionResult.SPONTANEOUS_CORRECT
    ) == int(fsrs.Rating.Hard)
    assert derive_recall_rating(
        high_confidence_but_hinted, help_level=HelpLevel.A0, production_result=ProductionResult.CORRECT_AFTER_HINT
    ) == int(fsrs.Rating.Hard)

    low_confidence_but_spontaneous = _valid_assessment(confidence=0.51)
    assert derive_recall_rating(
        low_confidence_but_spontaneous, help_level=HelpLevel.A0, production_result=ProductionResult.SPONTANEOUS_CORRECT
    ) == int(fsrs.Rating.Easy)

    assert derive_recall_rating(
        _valid_assessment(), help_level=HelpLevel.A0, production_result=ProductionResult.SPONTANEOUS_SELF_CORRECTION
    ) == int(fsrs.Rating.Good)
    assert derive_recall_rating(
        _valid_assessment(), help_level=HelpLevel.A1, production_result=ProductionResult.SPONTANEOUS_CORRECT
    ) == int(fsrs.Rating.Good)
    assert derive_recall_rating(
        _valid_assessment(), help_level=HelpLevel.A3, production_result=ProductionResult.SPONTANEOUS_CORRECT
    ) == int(fsrs.Rating.Hard)

    assert derive_recall_rating(
        _valid_assessment(classification=EvidenceType.NEGATIVE),
        help_level=HelpLevel.A0, production_result=ProductionResult.INCORRECT,
    ) == int(fsrs.Rating.Again)
    assert derive_recall_rating(
        _valid_assessment(classification=EvidenceType.INCONCLUSIVE),
        help_level=HelpLevel.A0, production_result=ProductionResult.INCONCLUSIVE,
    ) is None


def test_attempts_seconds_apart_are_not_independent_observations():
    """Ponto 2 da correcao v0.2.1: duas tentativas separadas por poucos
    segundos nao podem contar como duas revisoes espacadas - o intervalo
    minimo (`recall_min_interval_seconds`) e definido pela regra, nao
    hardcoded."""

    config = AggregationConfig(recall_min_interval_seconds=3600.0)
    last_review = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    memory_state = MemoryState(
        id=new_id(), competency_id="c1", fsrs_card_json="{}", due_at=None,
        stability=1.0, difficulty=5.0, card_state="review",
        last_review_at=to_utc_iso(last_review), updated_at=to_utc_iso(last_review),
    )
    seconds_later = last_review + timedelta(seconds=17)

    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.TARGET,
        evidence_dimension=Dimension.RETENTION,
        assessment=_valid_assessment(confidence=0.9),
        help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
        memory_state=memory_state,
        config=config,
        now=seconds_later,
    )
    assert result.eligible is False
    assert "intervalo" in result.reason


def test_attempts_far_apart_are_independent_observations():
    config = AggregationConfig(recall_min_interval_seconds=3600.0)
    last_review = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    memory_state = MemoryState(
        id=new_id(), competency_id="c1", fsrs_card_json="{}", due_at=None,
        stability=1.0, difficulty=5.0, card_state="review",
        last_review_at=to_utc_iso(last_review), updated_at=to_utc_iso(last_review),
    )
    days_later = last_review + timedelta(days=3)

    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.TARGET,
        evidence_dimension=Dimension.RETENTION,
        assessment=_valid_assessment(confidence=0.9),
        help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
        memory_state=memory_state,
        config=config,
        now=days_later,
    )
    assert result.eligible is True
    assert result.interval_days == pytest.approx(3.0)


def test_first_review_too_soon_after_learning_is_never_eligible():
    """Terceira auditoria pos-entrega, ponto 2, ultima frase: a PRIMEIRA
    revisao (sem memory_state ainda) tambem precisa verificar o intervalo
    desde a aprendizagem - nao pode ficar sem NENHUMA checagem so porque
    nao ha uma revisao anterior para comparar."""

    config = AggregationConfig(first_review_min_interval_since_learning_seconds=3600.0)
    first_evidence_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    minutes_later = first_evidence_at + timedelta(minutes=5)

    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.TARGET,
        evidence_dimension=Dimension.RETENTION,
        assessment=_valid_assessment(confidence=0.9),
        help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
        memory_state=None,
        first_evidence_at=to_utc_iso(first_evidence_at),
        config=config,
        now=minutes_later,
    )
    assert result.eligible is False
    assert "aprendizagem" in result.reason


def test_first_review_long_after_learning_is_eligible():
    config = AggregationConfig(first_review_min_interval_since_learning_seconds=3600.0)
    first_evidence_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    days_later = first_evidence_at + timedelta(days=2)

    result = evaluate_recall_eligibility(
        activity=_planned_activity("s1"),
        evidence_relation=EvidenceRelation.TARGET,
        evidence_dimension=Dimension.RETENTION,
        assessment=_valid_assessment(confidence=0.9),
        help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
        memory_state=None,
        first_evidence_at=to_utc_iso(first_evidence_at),
        config=config,
        now=days_later,
    )
    assert result.eligible is True
    assert result.interval_days == pytest.approx(2.0)


# --- observe_and_review: escrita atomica ------------------------------

def test_observe_and_review_creates_state_log_and_observation(repos: Repositories, make_competency):
    adapter = MemoryAdapter(repos)
    competency_id = make_competency()
    activity, interaction, assessment = _real_observation_refs(repos, competency_id)
    eligibility = evaluate_recall_eligibility(
        activity=activity, evidence_relation=EvidenceRelation.TARGET,
        evidence_dimension=Dimension.RETENTION,
        assessment=assessment,
        help_level=HelpLevel.A0, production_result=ProductionResult.SPONTANEOUS_CORRECT,
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
        evidence_dimension=Dimension.RETENTION,
        assessment=assessment,
        help_level=HelpLevel.A0, production_result=ProductionResult.SPONTANEOUS_CORRECT,
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
        evidence_dimension=Dimension.RETENTION,
        assessment=assessment,
        help_level=HelpLevel.A0, production_result=ProductionResult.SPONTANEOUS_CORRECT,
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
        evidence_dimension=Dimension.RETENTION,
        assessment=assessment2,
        help_level=HelpLevel.A0, production_result=ProductionResult.SPONTANEOUS_CORRECT,
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
        evidence_dimension=Dimension.RETENTION,
        assessment=assessment,
        help_level=HelpLevel.A0, production_result=ProductionResult.SPONTANEOUS_CORRECT,
        memory_state=None,
    )
    adapter.observe_and_review(
        competency_id=competency_id, raw_interaction_id=interaction.id, evidence_assessment_id=assessment.id,
        eligibility=eligibility, review_datetime=now,
    )

    assert adapter.is_recall_due(competency_id, now=now) is False
    far_future = now + timedelta(days=3650)
    assert adapter.is_recall_due(competency_id, now=far_future) is True
