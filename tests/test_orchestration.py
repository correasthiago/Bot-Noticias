from __future__ import annotations

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import Learner
from central_universal.domain.enums import HelpLevel, ProductionResult, RoutingDecision
from central_universal.domain.ids import new_id
from central_universal.memory.fsrs_adapter import MemoryReviewError
from central_universal.persistence.repositories import Repositories
from central_universal.providers.mock import MockProvider


def test_full_cycle_offline_with_mock_provider(repos: Repositories, make_competency, make_rule_version, orchestrator_factory):
    """Cobre o ciclo completo da Secao 1, de ponta a ponta, sem rede
    (T15): diagnostico -> atividade -> interacao -> evidencia -> avaliacao
    -> estado -> memoria -> decisao -> persistencia."""

    competency_id = make_competency("simple_present")
    learner = Learner(id=new_id(), display_name="Aluno", created_at=utc_now_iso())
    repos.learners.insert(learner)
    rule_version = make_rule_version()

    orchestrator = orchestrator_factory(MockProvider(), rule_version)

    session = orchestrator.start_session(learner.id)
    assert session.status.value == "active"

    focus = orchestrator.choose_focus_competency(learner.id)
    assert focus is not None
    focus_competency_id, routing, action, recall_due = focus
    assert focus_competency_id == competency_id
    assert routing.routing == RoutingDecision.STUDY  # sem evidencia ainda
    assert recall_due is False  # sem memory_state ainda, nada esta "due"

    activity, tutor_output = orchestrator.start_activity(
        session_id=session.id, competency_id=focus_competency_id, action=action
    )
    assert tutor_output is not None
    assert activity.id in {a.id for a in repos.activities.list_for_session(session.id)}
    # Primeira acao da ladder e sempre sobre compreensao (Secao 1 do
    # pacote de correcao v0.2.1) - ainda nao e uma recuperacao planejada.
    assert activity.is_planned_recall is False

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
    # Esta atividade NAO foi planejada como recuperacao (STUDY, nao
    # retencao pendente) - por isso o FSRS nunca e chamado (Secao 1/2 do
    # pacote de correcao v0.2).
    assert outcome.memory_eligibility is not None
    assert outcome.memory_eligibility.eligible is False
    assert outcome.memory_result is None
    assert outcome.next_routing is not None

    orchestrator.end_session(session.id)
    ended = repos.sessions.get(session.id)
    assert ended.status.value == "ended"

    # tudo persistiu no event log
    assert len(repos.raw_interactions.list_all()) == 1
    assert len(repos.evidence_events.list_all()) >= 1
    assert len(repos.provider_events.list_recent(10)) >= 2  # tutor + evaluator
    assert len(repos.decision_events.list_recent(10)) >= 2  # routing inicial + apos interacao


def test_first_fsrs_card_is_born_through_normal_application_flow(
    repos: Repositories, make_competency, make_rule_version, orchestrator_factory, drive_to_schedule_recall
):
    """Secao 1 do pacote de correcao v0.2.1: o primeiro card FSRS precisa
    nascer usando SOMENTE a mesma sequencia de chamadas que a interface
    web usa (escolher foco -> comecar atividade -> responder), partindo
    de um banco novo, sem `is_planned_recall=True` definido diretamente
    no teste - a atividade so vira uma recuperacao planejada quando o
    proprio Decisor escolhe SCHEDULE_RECALL."""

    competency_id = make_competency("simple_present")
    learner = Learner(id=new_id(), display_name="Aluno", created_at=utc_now_iso())
    repos.learners.insert(learner)
    rule_version = make_rule_version()
    orchestrator = orchestrator_factory(MockProvider(), rule_version)

    assert repos.memory_states.get(competency_id) is None  # banco novo: nenhum card ainda

    session, activity, tutor_output, now = drive_to_schedule_recall(orchestrator, learner.id, competency_id)
    assert activity.is_planned_recall is True

    outcome = orchestrator.submit_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=new_id(),
        learner_input="She has gone to school before.", help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
        tutor_output_text=tutor_output.utterance if tutor_output else "",
        now=now,
    )

    assert outcome.memory_eligibility is not None
    assert outcome.memory_eligibility.eligible is True
    assert outcome.memory_result is not None
    card = repos.memory_states.get(competency_id)
    assert card is not None  # o PRIMEIRO card nasceu pelo fluxo normal, sem atalho de teste
    assert len(repos.memory_observations.list_for_competency(competency_id)) == 1


def test_memory_review_retries_after_fsrs_failure_without_duplicating(
    repos: Repositories, make_competency, make_rule_version, orchestrator_factory, drive_to_schedule_recall
):
    """Achado da oitava auditoria pos-entrega: se a AVALIACAO for gravada
    com sucesso mas a revisao de memoria (FSRS) falhar DEPOIS, reenviar a
    MESMA resposta (mesma idempotency_key) batia no atalho de
    'evaluation_status ja COMPLETED, nada a reprocessar' e saia sem
    sequer tentar a memoria de novo - a revisao ficava perdida para
    sempre. Este teste simula exatamente isso: forca `observe_and_review`
    a falhar na PRIMEIRA submissao (evaluation_status vira COMPLETED, mas
    nenhum MemoryObservation e gravado), reenvia a MESMA resposta com o
    FSRS funcionando de novo, e confirma que a revisao acontece desta
    vez - sem NUNCA duplicar se enviada uma TERCEIRA vez."""

    competency_id = make_competency("simple_present")
    learner = Learner(id=new_id(), display_name="Aluno", created_at=utc_now_iso())
    repos.learners.insert(learner)
    rule_version = make_rule_version()
    orchestrator = orchestrator_factory(MockProvider(), rule_version)

    session, activity, tutor_output, now = drive_to_schedule_recall(orchestrator, learner.id, competency_id)
    assert activity.is_planned_recall is True

    # `observe_and_review` NUNCA levanta de verdade (captura tudo
    # internamente e devolve um `MemoryReviewError`) - a falha realista e
    # exatamente essa: um retorno de erro, nao uma excecao escapando.
    real_observe_and_review = orchestrator.memory_adapter.observe_and_review

    def failing_observe_and_review(*args, **kwargs):
        return MemoryReviewError(message="falha simulada no FSRS")

    orchestrator.memory_adapter.observe_and_review = failing_observe_and_review

    key = new_id()
    first_outcome = orchestrator.submit_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=key,
        learner_input="She has gone to school before.", help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
        tutor_output_text=tutor_output.utterance if tutor_output else "",
        now=now,
    )
    orchestrator.memory_adapter.observe_and_review = real_observe_and_review

    assert isinstance(first_outcome.memory_result, MemoryReviewError)

    # a AVALIACAO foi gravada com sucesso apesar da falha subsequente do
    # FSRS - a interacao esta COMPLETED, mas nenhuma revisao de memoria
    # foi gravada ainda.
    raw_interaction = repos.raw_interactions.get_by_idempotency_key(key)
    assert raw_interaction is not None
    assert raw_interaction.evaluation_status.value == "completed"
    assert repos.memory_observations.get_by_raw_interaction(raw_interaction.id) is None
    assert repos.memory_states.get(competency_id) is None  # nenhum card ainda

    # reenvia a MESMA resposta (mesma idempotency_key) - desta vez o FSRS
    # funciona: a revisao precisa acontecer agora, nao ficar perdida.
    retry_outcome = orchestrator.submit_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=key,
        learner_input="She has gone to school before.", help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
        tutor_output_text=tutor_output.utterance if tutor_output else "",
        now=now,
    )
    assert retry_outcome.created_now is False  # a interacao em si nao foi duplicada
    assert retry_outcome.memory_result is not None
    assert repos.memory_observations.get_by_raw_interaction(raw_interaction.id) is not None
    assert repos.memory_states.get(competency_id) is not None
    assert len(repos.memory_observations.list_for_competency(competency_id)) == 1

    # uma TERCEIRA submissao (mesma idempotency_key de novo) NUNCA duplica
    # a revisao ja gravada com sucesso.
    third_outcome = orchestrator.submit_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=key,
        learner_input="She has gone to school before.", help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
        tutor_output_text=tutor_output.utterance if tutor_output else "",
        now=now,
    )
    assert third_outcome.memory_result is None  # ja revisado - nao tenta de novo
    assert len(repos.memory_observations.list_for_competency(competency_id)) == 1  # nao duplicou


def test_double_submit_through_orchestrator_is_idempotent(repos: Repositories, make_competency, make_rule_version, orchestrator_factory):
    competency_id = make_competency()
    learner = Learner(id=new_id(), display_name="Aluno", created_at=utc_now_iso())
    repos.learners.insert(learner)
    rule_version = make_rule_version()
    orchestrator = orchestrator_factory(MockProvider(), rule_version)

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
