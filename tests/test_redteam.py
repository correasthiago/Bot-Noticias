"""Red Team final (Fase H, Secao 29): T1-T15, um teste por requisito.

Cada teste e nomeado e documentado com o numero exato da especificacao,
para que a rastreabilidade do relatorio final seja trivial de auditar.
Alguns destes cenarios ja sao cobertos indiretamente por testes de unidade
em outros arquivos (aggregation, evidence, memory, providers) - aqui eles
sao reexercitados de forma explicita e, sempre que possivel, atraves do
caminho de producao real (EvidenceService / SessionOrchestrator), nao só
da funcao pura isolada.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import fsrs
import pytest

from central_universal.decision.engine import route_competency
from central_universal.decision.service import DecisionService
from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import Activity, Learner, LearningSession, RuleVersion
from central_universal.domain.enums import (
    CompetencyDimensionState,
    Dimension,
    HelpLevel,
    ProductionResult,
    RoutingDecision,
    SessionStatus,
)
from central_universal.domain.ids import new_id
from central_universal.evaluator.contract import EvaluatorInput, validate_evaluator_payload
from central_universal.evaluator.service import EvaluatorService
from central_universal.evidence.service import EvidenceService, recompute_all_from_log
from central_universal.memory.fsrs_adapter import MemoryAdapter, MemoryReviewError
from central_universal.orchestration.session_service import SessionOrchestrator
from central_universal.persistence.repositories import Repositories
from central_universal.providers.base import Provider, ProviderCallResult
from central_universal.providers.mock import MockProvider


def _rule_version(repos: Repositories, version: str = "v0.1.0") -> RuleVersion:
    existing = repos.rule_versions.get_by_version(version)
    if existing:
        return existing
    rv = RuleVersion(id=new_id(), version=version, description="teste", created_at=utc_now_iso())
    repos.rule_versions.insert(rv)
    return rv


def _learner(repos: Repositories) -> Learner:
    learner = Learner(id=new_id(), display_name="Aluno Red Team", created_at=utc_now_iso())
    repos.learners.insert(learner)
    return learner


def _session_and_activity(repos: Repositories, learner_id: str, competency_id: str) -> tuple[LearningSession, Activity]:
    session = LearningSession(id=new_id(), learner_id=learner_id, started_at=utc_now_iso(), status=SessionStatus.ACTIVE)
    repos.sessions.insert(session)
    activity = Activity(
        id=new_id(), session_id=session.id, competency_targets=[competency_id],
        activity_type="drill", prompt="pratique", support_level=HelpLevel.A0, created_at=utc_now_iso(),
    )
    repos.activities.insert(activity)
    return session, activity


def _payload(competency_id: str, dimension: str, relation: str, classification: str) -> dict:
    return {
        "findings": [
            {
                "competency_id": competency_id,
                "dimension": dimension,
                "classification": classification,
                "result": "resultado de teste",
                "confidence": 0.75,
                "justification": "Justificativa de teste do Red Team.",
                "relation": relation,
            }
        ]
    }


# ---------------------------------------------------------------------
# T1 - 10 acertos iguais na mesma sessao nao consolidam retencao.
# ---------------------------------------------------------------------
def test_T1_ten_same_day_correct_answers_do_not_consolidate_retention(repos: Repositories, make_competency):
    competency_id = make_competency()
    learner = _learner(repos)
    rule_version = _rule_version(repos)
    service = EvidenceService(repos)
    session, activity = _session_and_activity(repos, learner.id, competency_id)

    for i in range(10):
        interaction, _ = service.record_interaction(
            activity_id=activity.id, session_id=session.id, idempotency_key=new_id(),
            learner_input=f"resposta {i}", tutor_output="", help_level=HelpLevel.A0,
            production_result=ProductionResult.SPONTANEOUS_CORRECT,
            evidence_cluster_id=new_id(),  # atividades/clusters distintos, MESMO dia
        )
        payload = _payload(competency_id, "retention", "target", "positive")
        output = validate_evaluator_payload(payload, {competency_id})
        service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)

    state = repos.competency_states.current(competency_id, Dimension.RETENTION)
    assert state is not None
    assert state.state != CompetencyDimensionState.CONSOLIDATED
    assert state.state != CompetencyDimensionState.DEMONSTRATED


# ---------------------------------------------------------------------
# T2 - resposta A3 nao demonstra retrieval independente.
# ---------------------------------------------------------------------
def test_T2_a3_response_never_demonstrates_independent_retrieval(repos: Repositories, make_competency):
    competency_id = make_competency()
    learner = _learner(repos)
    rule_version = _rule_version(repos)
    service = EvidenceService(repos)
    session, activity = _session_and_activity(repos, learner.id, competency_id)

    for i in range(3):
        interaction, _ = service.record_interaction(
            activity_id=activity.id, session_id=session.id, idempotency_key=new_id(),
            learner_input="resposta dada quase pronta", tutor_output="", help_level=HelpLevel.A3,
            production_result=ProductionResult.CORRECT_AFTER_EXTERNAL_CORRECTION,
            evidence_cluster_id=new_id(),
        )
        payload = _payload(competency_id, "retrieval", "target", "positive")
        output = validate_evaluator_payload(payload, {competency_id})
        service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)

    state = repos.competency_states.current(competency_id, Dimension.RETRIEVAL)
    assert state is not None
    assert state.state not in (CompetencyDimensionState.DEMONSTRATED, CompetencyDimensionState.CONSOLIDATED)


# ---------------------------------------------------------------------
# T3 - erro incidental isolado nao derruba competencia consolidada.
# ---------------------------------------------------------------------
def test_T3_isolated_incidental_error_does_not_topple_consolidated_competency(repos: Repositories, make_competency):
    competency_id = make_competency()
    learner = _learner(repos)
    rule_version = _rule_version(repos)
    service = EvidenceService(repos)
    session, activity = _session_and_activity(repos, learner.id, competency_id)

    for i in range(3):
        interaction, _ = service.record_interaction(
            activity_id=activity.id, session_id=session.id, idempotency_key=new_id(),
            learner_input="correto", tutor_output="", help_level=HelpLevel.A0,
            production_result=ProductionResult.SPONTANEOUS_CORRECT, evidence_cluster_id=new_id(),
        )
        payload = _payload(competency_id, "accuracy", "target", "positive")
        output = validate_evaluator_payload(payload, {competency_id})
        service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)

    before = repos.competency_states.current(competency_id, Dimension.ACCURACY)
    assert before.state == CompetencyDimensionState.CONSOLIDATED

    interaction, _ = service.record_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=new_id(),
        learner_input="um erro isolado", tutor_output="", help_level=HelpLevel.A0,
        production_result=ProductionResult.INCORRECT, evidence_cluster_id=new_id(),
    )
    payload = _payload(competency_id, "accuracy", "target", "negative")
    output = validate_evaluator_payload(payload, {competency_id})
    service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)

    after = repos.competency_states.current(competency_id, Dimension.ACCURACY)
    assert after.state == CompetencyDimensionState.CONSOLIDATED  # dominio anterior nao foi apagado
    assert after.possible_regression is True  # mas o sinal foi registrado


# ---------------------------------------------------------------------
# T4 - evidencia incidental qualificada pode criar hipotese/evidencia secundaria.
# ---------------------------------------------------------------------
def test_T4_qualified_incidental_evidence_creates_secondary_hypothesis(repos: Repositories, make_competency):
    competency_id = make_competency()
    learner = _learner(repos)
    rule_version = _rule_version(repos)
    service = EvidenceService(repos)
    session, activity = _session_and_activity(repos, learner.id, competency_id)

    interaction, _ = service.record_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=new_id(),
        learner_input="usou a estrutura de passagem, sem ser o foco", tutor_output="",
        help_level=HelpLevel.A0, production_result=ProductionResult.SPONTANEOUS_CORRECT,
        evidence_cluster_id=new_id(),
    )
    payload = _payload(competency_id, "transfer", "qualified_incidental", "positive")
    output = validate_evaluator_payload(payload, {competency_id})
    states = service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)

    assert len(states) == 1
    assert states[0].state == CompetencyDimensionState.ACQUIRING  # hipotese, nao promocao forte


# ---------------------------------------------------------------------
# T5 - mere_presence nao altera estado.
# ---------------------------------------------------------------------
def test_T5_mere_presence_never_alters_state(repos: Repositories, make_competency):
    competency_id = make_competency()
    learner = _learner(repos)
    rule_version = _rule_version(repos)
    service = EvidenceService(repos)
    session, activity = _session_and_activity(repos, learner.id, competency_id)

    interaction, _ = service.record_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=new_id(),
        learner_input="mencao de passagem", tutor_output="", help_level=HelpLevel.A0,
        production_result=ProductionResult.INCONCLUSIVE, evidence_cluster_id=new_id(),
    )
    payload = _payload(competency_id, "comprehension", "mere_presence", "inconclusive")
    output = validate_evaluator_payload(payload, {competency_id})
    states = service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)

    assert states == []
    assert repos.competency_states.current(competency_id, Dimension.COMPREHENSION) is None


# ---------------------------------------------------------------------
# T6 - resposta contraditoria cria incerteza/validacao.
# ---------------------------------------------------------------------
def test_T6_contradictory_response_creates_uncertainty_routed_to_validate(repos: Repositories, make_competency):
    competency_id = make_competency()
    learner = _learner(repos)
    rule_version = _rule_version(repos)
    service = EvidenceService(repos)
    session, activity = _session_and_activity(repos, learner.id, competency_id)

    interaction, _ = service.record_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=new_id(),
        learner_input="ora acerta ora erra a mesma regra", tutor_output="", help_level=HelpLevel.A0,
        production_result=ProductionResult.INCONCLUSIVE, evidence_cluster_id=new_id(),
    )
    payload = _payload(competency_id, "comprehension", "target", "contradictory")
    output = validate_evaluator_payload(payload, {competency_id})
    service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)

    state = repos.competency_states.current(competency_id, Dimension.COMPREHENSION)
    assert state.state == CompetencyDimensionState.INSUFFICIENT_EVIDENCE
    assert state.has_unresolved_contradiction is True

    decision_service = DecisionService(repos)
    routing, action = decision_service.decide(
        competency_id=competency_id, learner_id=learner.id, rule_version=rule_version
    )
    assert routing.routing == RoutingDecision.VALIDATE
    assert routing.rule_applied == "contradictory_evidence"


# ---------------------------------------------------------------------
# T7 - avaliacao invalida do LLM nao altera estado.
# ---------------------------------------------------------------------
class _MalformedProvider(Provider):
    provider_name = "malformed"
    model = "x"
    config_version = "1"

    def generate_tutor_turn(self, tutor_input):
        return ProviderCallResult(success=True, payload={"utterance": "oi", "activity_prompt": "oi"}, latency_ms=1)

    def generate_evaluation(self, evaluator_input):
        # dimensao inexistente -> falha de validacao estrutural
        return ProviderCallResult(
            success=True,
            payload={"findings": [{"competency_id": evaluator_input.candidate_competency_ids[0], "dimension": "nao-existe"}]},
            latency_ms=1,
        )


def test_T7_invalid_evaluator_response_never_alters_state(repos: Repositories, make_competency):
    competency_id = make_competency()
    evaluator_service = EvaluatorService(repos, _MalformedProvider())
    evaluator_input = EvaluatorInput(
        raw_interaction_id="ri", learner_input="x", tutor_output="y",
        candidate_competency_ids=(competency_id,), help_level=HelpLevel.A0,
        production_result=ProductionResult.INCORRECT, context="", rule_version="v0.1.0",
    )
    output, event = evaluator_service.run(evaluator_input, {competency_id})

    assert output is None
    assert event.success is False
    assert repos.competency_states.current(competency_id, Dimension.ACCURACY) is None
    assert repos.evidence_events.list_all() == []


# ---------------------------------------------------------------------
# T8 - double-submit nao duplica evidencia.
# ---------------------------------------------------------------------
def test_T8_double_submit_never_duplicates_evidence(repos: Repositories, make_competency):
    competency_id = make_competency()
    learner = _learner(repos)
    rule_version = _rule_version(repos)
    orchestrator = SessionOrchestrator(repos, MockProvider(), rule_version)
    session = orchestrator.start_session(learner.id)
    activity, _ = orchestrator.start_activity(session_id=session.id, competency_id=competency_id, action=None)

    key = new_id()
    for _ in range(2):
        orchestrator.submit_interaction(
            activity_id=activity.id, session_id=session.id, idempotency_key=key,
            learner_input="mesma resposta", help_level=HelpLevel.A0,
            production_result=ProductionResult.SPONTANEOUS_CORRECT,
        )

    assert len(repos.raw_interactions.list_all()) == 1
    assert len(repos.evidence_events.list_all()) == 1


# ---------------------------------------------------------------------
# T9 - falha no meio da transacao nao cria estado parcial.
# ---------------------------------------------------------------------
def test_T9_mid_transaction_failure_creates_no_partial_state(repos: Repositories, make_competency, monkeypatch):
    competency_a = make_competency("a")
    competency_b = make_competency("b")
    learner = _learner(repos)
    rule_version = _rule_version(repos)
    service = EvidenceService(repos)
    session, activity = _session_and_activity(repos, learner.id, competency_a)

    interaction, _ = service.record_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=new_id(),
        learner_input="x", tutor_output="", help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT, evidence_cluster_id=new_id(),
    )
    payload = {
        "findings": [
            {"competency_id": competency_a, "dimension": "accuracy", "classification": "positive",
             "result": "ok", "confidence": 0.9, "justification": "ok", "relation": "target"},
            {"competency_id": competency_b, "dimension": "accuracy", "classification": "positive",
             "result": "ok", "confidence": 0.9, "justification": "ok", "relation": "target"},
        ]
    }
    output = validate_evaluator_payload(payload, {competency_a, competency_b})

    original_insert = repos.evidence_assessments.insert
    call_count = {"n": 0}

    def boom(assessment):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("falha simulada no meio do lote")
        return original_insert(assessment)

    monkeypatch.setattr(repos.evidence_assessments, "insert", boom)

    from central_universal.persistence.db import transaction

    with pytest.raises(RuntimeError):
        with transaction(repos.conn):
            service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)

    # nada da SEGUNDA leva (nem a primeira) deve ter sobrevivido: tudo ou nada
    assert repos.evidence_events.list_all() == []
    assert repos.evidence_assessments.list_all() == []
    assert repos.competency_states.current(competency_a, Dimension.ACCURACY) is None
    assert repos.competency_states.current(competency_b, Dimension.ACCURACY) is None


# ---------------------------------------------------------------------
# T10 - reconstrucao dos estados a partir dos eventos reproduz estado atual.
# ---------------------------------------------------------------------
def test_T10_full_reconstruction_reproduces_current_state(repos: Repositories, make_competency):
    competency_id = make_competency()
    learner = _learner(repos)
    rule_version = _rule_version(repos)
    orchestrator = SessionOrchestrator(repos, MockProvider(), rule_version)
    session = orchestrator.start_session(learner.id)

    for result in (ProductionResult.SPONTANEOUS_CORRECT, ProductionResult.SPONTANEOUS_CORRECT, ProductionResult.INCORRECT):
        activity, _ = orchestrator.start_activity(session_id=session.id, competency_id=competency_id, action=None)
        orchestrator.submit_interaction(
            activity_id=activity.id, session_id=session.id, idempotency_key=new_id(),
            learner_input="x", help_level=HelpLevel.A0, production_result=result,
        )

    before = dict(repos.competency_states.current_all_dimensions(competency_id))
    recompute_all_from_log(repos, rule_version)
    after = dict(repos.competency_states.current_all_dimensions(competency_id))

    assert before.keys() == after.keys()
    for dimension in before:
        assert before[dimension].state == after[dimension].state
        assert before[dimension].possible_regression == after[dimension].possible_regression


# ---------------------------------------------------------------------
# T11 - mudanca de RuleVersion nao apaga avaliacao historica.
# ---------------------------------------------------------------------
def test_T11_rule_version_change_preserves_historical_assessments(repos: Repositories, make_competency):
    competency_id = make_competency()
    learner = _learner(repos)
    rule_v1 = _rule_version(repos, "v0.1.0")
    service = EvidenceService(repos)
    session, activity = _session_and_activity(repos, learner.id, competency_id)

    interaction, _ = service.record_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=new_id(),
        learner_input="x", tutor_output="", help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT, evidence_cluster_id=new_id(),
    )
    payload = _payload(competency_id, "accuracy", "target", "positive")
    output = validate_evaluator_payload(payload, {competency_id})
    service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_v1)

    assessments_before = repos.evidence_assessments.list_all()
    assert len(assessments_before) == 1
    assert assessments_before[0].rule_version_id == rule_v1.id

    rule_v2 = _rule_version(repos, "v0.2.0-teste")
    recompute_all_from_log(repos, rule_v2)

    assessments_after = repos.evidence_assessments.list_all()
    assert assessments_after == assessments_before  # nenhuma avaliacao historica foi tocada

    current_state = repos.competency_states.current(competency_id, Dimension.ACCURACY)
    assert current_state.rule_version_id == rule_v2.id  # a PROJECAO usa a nova versao
    assert current_state.last_evidence_assessment_id == assessments_before[0].id  # mas aponta pra evidencia antiga


# ---------------------------------------------------------------------
# T12 - multiplas sessoes no mesmo dia nao simulam retencao de varios dias.
# ---------------------------------------------------------------------
def test_T12_multiple_sessions_same_day_do_not_simulate_multi_day_retention(repos: Repositories, make_competency):
    competency_id = make_competency()
    learner = _learner(repos)
    rule_version = _rule_version(repos)
    service = EvidenceService(repos)

    for _ in range(4):
        session, activity = _session_and_activity(repos, learner.id, competency_id)
        interaction, _ = service.record_interaction(
            activity_id=activity.id, session_id=session.id, idempotency_key=new_id(),
            learner_input="x", tutor_output="", help_level=HelpLevel.A0,
            production_result=ProductionResult.SPONTANEOUS_CORRECT, evidence_cluster_id=new_id(),
        )
        payload = _payload(competency_id, "retention", "target", "positive")
        output = validate_evaluator_payload(payload, {competency_id})
        service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)

    state = repos.competency_states.current(competency_id, Dimension.RETENTION)
    assert state.state not in (CompetencyDimensionState.DEMONSTRATED, CompetencyDimensionState.CONSOLIDATED)


# ---------------------------------------------------------------------
# T13 - prerequisite cycle e bloqueado/detectado.
# ---------------------------------------------------------------------
def test_T13_prerequisite_cycle_is_detected(repos: Repositories, make_competency):
    from central_universal.domain.entities import PrerequisiteRelation
    from central_universal.integrity.checks import run_all

    a = make_competency("cycle-a")
    b = make_competency("cycle-b")
    now = utc_now_iso()
    repos.prerequisites.insert(PrerequisiteRelation(id=new_id(), competency_id=a, prerequisite_id=b, created_at=now))
    repos.prerequisites.insert(PrerequisiteRelation(id=new_id(), competency_id=b, prerequisite_id=a, created_at=now))

    report = run_all(repos)
    assert not report.ok
    assert any(f.check == "prerequisite_cycles" for f in report.findings)


# ---------------------------------------------------------------------
# T14 - falha do FSRS nao destroi evidencia.
# ---------------------------------------------------------------------
def test_T14_fsrs_failure_does_not_destroy_evidence(repos: Repositories, make_competency, monkeypatch):
    competency_id = make_competency()
    learner = _learner(repos)
    rule_version = _rule_version(repos)
    orchestrator = SessionOrchestrator(repos, MockProvider(), rule_version)

    def boom(*args, **kwargs):
        raise RuntimeError("fsrs indisponivel (simulado)")

    monkeypatch.setattr(orchestrator.memory_adapter.scheduler, "review_card", boom)

    session = orchestrator.start_session(learner.id)
    activity, _ = orchestrator.start_activity(session_id=session.id, competency_id=competency_id, action=None)
    outcome = orchestrator.submit_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=new_id(),
        learner_input="x", help_level=HelpLevel.A0, production_result=ProductionResult.SPONTANEOUS_CORRECT,
    )

    assert isinstance(outcome.memory_result, MemoryReviewError)
    assert len(repos.raw_interactions.list_all()) == 1
    assert len(repos.evidence_events.list_all()) >= 1
    assert len(outcome.competency_states) >= 1


# ---------------------------------------------------------------------
# T15 - sistema completo funciona offline com MockProvider.
# ---------------------------------------------------------------------
def test_T15_full_system_works_offline_with_mock_provider(repos: Repositories, make_competency):
    """MockProvider nao importa nenhuma biblioteca de rede (ver
    central_universal/providers/mock.py) - este teste roda o ciclo
    completo sem qualquer acesso externo, provando o requisito de aceite
    da Secao 22."""

    competency_id = make_competency()
    learner = _learner(repos)
    rule_version = _rule_version(repos)
    orchestrator = SessionOrchestrator(repos, MockProvider(), rule_version)

    session = orchestrator.start_session(learner.id)
    focus = orchestrator.choose_focus_competency(learner.id)
    assert focus is not None
    focus_id, _routing, action = focus
    activity, tutor_output = orchestrator.start_activity(session_id=session.id, competency_id=focus_id, action=action)
    assert tutor_output is not None

    outcome = orchestrator.submit_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=new_id(),
        learner_input="She goes to school", help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
    )
    orchestrator.end_session(session.id)

    assert outcome.evaluator_output is not None
    assert repos.sessions.get(session.id).status.value == "ended"
