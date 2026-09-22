"""Red Team pos-correcao v0.2: testes de PRINCIPIOS, nao mais numerados
T1-T15 (esses numeros descreviam cenarios do algoritmo v1, hardcoded e
com corroboracao de regressao - superado). Cada teste aqui cobre,
literalmente, um dos itens exigidos no pacote de correcao:

P1  - avaliacao invalida nao altera nenhuma projecao nem FSRS.
P2  - queda depois da interacao bruta permite reavaliacao idempotente.
P3  - duas negativas no mesmo cluster nao corroboram regressao.
P4  - IDs de cluster diferentes nao provam independencia por si.
P5  - reconstrucao preserva historico e e atomica.
P6  - duas RuleVersions produzem resultados diferentes quando suas regras diferem.
P7  - falha ao inserir MemoryReviewLog nao altera MemoryState.
P8  - ciclo de pre-requisito e rejeitado na escrita.
P9  - restore real substitui e recupera o banco.
P10 - reinicio completo do processo preserva estado.
P11 - execucao offline falha no teste se qualquer acesso de rede for tentado.

Os principios do algoritmo v1 que continuam validos (retencao longitudinal,
independencia A0/A1, mere_presence, contradicao -> incerteza, erro isolado
nao apaga dominio, double-submit idempotente) permanecem cobertos em
`test_evidence_aggregation.py`, `test_evidence_service.py`,
`test_memory_adapter.py` e `test_orchestration.py` - nao duplicados aqui.
"""

from __future__ import annotations

import socket

import pytest

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import (
    Activity,
    Learner,
    LearningSession,
    PrerequisiteRelation,
)
from central_universal.domain.enums import (
    CompetencyDimensionState,
    DecisionType,
    Dimension,
    HelpLevel,
    ProductionResult,
    SessionStatus,
)
from central_universal.domain.ids import new_id
from central_universal.evaluator.contract import validate_evaluator_payload
from central_universal.evidence.aggregation import AggregationConfig
from central_universal.evidence.service import EvidenceService, recompute_all_from_log
from central_universal.memory.fsrs_adapter import MemoryReviewError
from central_universal.orchestration.session_service import SessionOrchestrator
from central_universal.persistence.backup import create_backup
from central_universal.persistence.db import connect
from central_universal.persistence.migrations import run_migrations
from central_universal.persistence.repositories import PrerequisiteCycleError, Repositories
from central_universal.persistence.restore import restore_from_backup
from central_universal.providers.base import Provider, ProviderCallResult
from central_universal.providers.mock import MockProvider


def _learner(repos: Repositories) -> Learner:
    learner = Learner(id=new_id(), display_name="Aluno Red Team", created_at=utc_now_iso())
    repos.learners.insert(learner)
    return learner


def _session(repos: Repositories, learner_id: str) -> LearningSession:
    session = LearningSession(id=new_id(), learner_id=learner_id, started_at=utc_now_iso(), status=SessionStatus.ACTIVE)
    repos.sessions.insert(session)
    return session


def _new_activity(
    repos: Repositories,
    session_id: str,
    competency_targets: list[str] | None = None,
    prompt: str = "drill",
    activity_type: str = "drill",
) -> Activity:
    activity = Activity(
        id=new_id(), session_id=session_id, competency_targets=competency_targets or [], activity_type=activity_type,
        prompt=prompt, support_level=HelpLevel.A0, created_at=utc_now_iso(),
    )
    repos.activities.insert(activity)
    return activity


def _planned_recall_activity(repos: Repositories, session_id: str, competency_id: str) -> Activity:
    """Constroi uma Activity ja marcada como recuperacao planejada, sem
    passar por `start_activity`/o Decisor - usado apenas para exercitar
    isoladamente o adapter de memoria (P7), ja que `is_planned_recall`
    deixou de ser um parametro aceito pelo orquestrador (Secao 1 do
    pacote de correcao v0.2.1: so o proprio Decisor, ao escolher
    SCHEDULE_RECALL, pode produzir uma atividade assim)."""

    activity = Activity(
        id=new_id(), session_id=session_id, competency_targets=[competency_id],
        activity_type=DecisionType.SCHEDULE_RECALL.value, prompt="Recall this",
        support_level=HelpLevel.A0, created_at=utc_now_iso(), is_planned_recall=True,
    )
    repos.activities.insert(activity)
    return activity


def _payload(competency_id: str, dimension: str, relation: str, classification: str) -> dict:
    return {
        "findings": [
            {
                "competency_id": competency_id,
                "dimension": dimension,
                "classification": classification,
                "result": "resultado de teste",
                "confidence": 0.9,
                "justification": "Justificativa de teste do Red Team.",
                "relation": relation,
            }
        ]
    }


# ---------------------------------------------------------------------
# P1 - avaliacao invalida nao altera nenhuma projecao nem FSRS.
# ---------------------------------------------------------------------
class _MalformedProvider(Provider):
    provider_name = "malformed"
    model = "x"
    config_version = "1"

    def generate_tutor_turn(self, tutor_input):
        return ProviderCallResult(success=True, payload={"utterance": "oi", "activity_prompt": "oi"}, latency_ms=1)

    def generate_evaluation(self, evaluator_input):
        return ProviderCallResult(
            success=True,
            payload={"findings": [{"competency_id": evaluator_input.candidate_competency_ids[0], "dimension": "nao-existe"}]},
            latency_ms=1,
        )


def test_P1_invalid_evaluation_never_alters_projection_or_memory(repos, make_competency, make_rule_version, orchestrator_factory):
    competency_id = make_competency()
    learner = _learner(repos)
    rule_version = make_rule_version()
    orchestrator = orchestrator_factory(_MalformedProvider(), rule_version)

    session = orchestrator.start_session(learner.id)
    activity, _ = orchestrator.start_activity(
        session_id=session.id, competency_id=competency_id, action=None
    )
    outcome = orchestrator.submit_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=new_id(),
        learner_input="x", help_level=HelpLevel.A0, production_result=ProductionResult.SPONTANEOUS_CORRECT,
    )

    assert outcome.evaluator_output is None
    assert outcome.competency_states == []
    assert outcome.memory_result is None
    assert repos.competency_states.current(competency_id, Dimension.ACCURACY) is None
    assert repos.memory_states.get(competency_id) is None
    assert repos.evidence_events.list_all() == []
    assert repos.evidence_assessments.list_all() == []


# ---------------------------------------------------------------------
# P2 - queda depois da interacao bruta permite reavaliacao idempotente.
# ---------------------------------------------------------------------
class _EvaluatorAlwaysFailsProvider(Provider):
    provider_name = "eval-broken"
    model = "x"
    config_version = "1"

    def generate_tutor_turn(self, tutor_input):
        return ProviderCallResult(success=True, payload={"utterance": "oi", "activity_prompt": "oi"}, latency_ms=1)

    def generate_evaluation(self, evaluator_input):
        raise RuntimeError("avaliador indisponivel (simulado)")


def test_P2_reprocessing_after_evaluator_failure_is_idempotent(repos, make_competency, make_rule_version, orchestrator_factory):
    competency_id = make_competency()
    learner = _learner(repos)
    rule_version = make_rule_version()

    broken_orchestrator = orchestrator_factory(_EvaluatorAlwaysFailsProvider(), rule_version)
    session = broken_orchestrator.start_session(learner.id)
    activity, _ = broken_orchestrator.start_activity(session_id=session.id, competency_id=competency_id, action=None)

    key = new_id()
    first_attempt = broken_orchestrator.submit_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=key,
        learner_input="She goes to school", help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
    )
    assert first_attempt.evaluator_output is None
    interaction = repos.raw_interactions.get_by_idempotency_key(key)
    assert interaction.evaluation_status.value == "pending"  # a QUEDA nao trava a interacao
    assert repos.evidence_events.list_all() == []

    # "conserta" o avaliador e reprocessa a MESMA interacao (mesma idempotency_key)
    working_orchestrator = orchestrator_factory(MockProvider(), rule_version)
    second_attempt = working_orchestrator.submit_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=key,
        learner_input="She goes to school", help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
    )
    assert second_attempt.evaluator_output is not None
    assert len(repos.raw_interactions.list_all()) == 1  # mesma interacao, nunca duplicada
    assert len(repos.evidence_events.list_all()) == 1  # avaliada exatamente uma vez
    completed = repos.raw_interactions.get(interaction.id)
    assert completed.evaluation_status.value == "completed"

    # reenviar de novo agora (ja completed) e um no-op idempotente
    third_attempt = working_orchestrator.submit_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=key,
        learner_input="She goes to school", help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
    )
    assert third_attempt.evaluator_output is None
    assert len(repos.evidence_events.list_all()) == 1


# ---------------------------------------------------------------------
# P3 - duas negativas no mesmo cluster nao corroboram regressao.
# ---------------------------------------------------------------------
def test_P3_two_negatives_from_same_cluster_never_corroborate_regression(repos, make_competency, make_rule_version):
    competency_id = make_competency()
    learner = _learner(repos)
    rule_version = make_rule_version()
    service = EvidenceService(repos)

    # Tres clusters genuinamente independentes (Secao 6 da correcao
    # v0.2.1: dentro da MESMA sessao, mesmo tipo+competencia colapsam num
    # cluster so - independencia real exige sessoes distintas).
    for i in range(3):
        session = _session(repos, learner.id)
        activity = _new_activity(repos, session.id, competency_targets=[competency_id], prompt=f"positive-drill-{i}")
        interaction, _ = service.record_interaction(
            activity=activity, session_id=session.id, idempotency_key=new_id(),
            learner_input="ok", tutor_output="", help_level=HelpLevel.A0,
            production_result=ProductionResult.SPONTANEOUS_CORRECT,
        )
        output = validate_evaluator_payload(_payload(competency_id, "accuracy", "target", "positive"), {competency_id})
        service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)

    before = repos.competency_states.current(competency_id, Dimension.ACCURACY)
    assert before.state == CompetencyDimensionState.CONSOLIDATED

    # As duas negativas reusam a MESMA atividade (mesma sessao, mesmo
    # tipo+competencia) - deliberadamente o MESMO cluster, para provar
    # que repeti-las nele nao corrobora regressao.
    negative_session = _session(repos, learner.id)
    negative_activity = _new_activity(repos, negative_session.id, competency_targets=[competency_id], prompt="negative-drill")
    for _ in range(2):
        interaction, _ = service.record_interaction(
            activity=negative_activity, session_id=negative_session.id, idempotency_key=new_id(),
            learner_input="erro", tutor_output="", help_level=HelpLevel.A0,
            production_result=ProductionResult.INCORRECT,
        )
        output = validate_evaluator_payload(_payload(competency_id, "accuracy", "target", "negative"), {competency_id})
        service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)

    after = repos.competency_states.current(competency_id, Dimension.ACCURACY)
    assert after.state == CompetencyDimensionState.CONSOLIDATED  # nunca rebaixado
    assert after.possible_regression is True  # mas o sinal esta visivel


# ---------------------------------------------------------------------
# P4 - IDs de cluster diferentes nao provam independencia por si.
# ---------------------------------------------------------------------
def test_P4_different_activity_ids_same_context_collapse_into_one_cluster(repos, make_competency, make_rule_version):
    competency_id = make_competency()
    learner = _learner(repos)
    rule_version = make_rule_version()
    service = EvidenceService(repos)
    session = _session(repos, learner.id)

    for i in range(3):
        # cada iteracao cria uma ATIVIDADE NOVA (id diferente), mas o MESMO
        # prompt/tipo na mesma sessao - o cluster no servidor deve ser o
        # MESMO, mesmo que o activity_id (o "ID" ingenuo do v1) mude.
        activity = _new_activity(
            repos, session.id, competency_targets=[competency_id], prompt="She ___ (go) to school.", activity_type="drill"
        )
        interaction, _ = service.record_interaction(
            activity=activity, session_id=session.id, idempotency_key=new_id(),
            learner_input=f"tentativa {i}", tutor_output="", help_level=HelpLevel.A0,
            production_result=ProductionResult.SPONTANEOUS_CORRECT,
        )
        output = validate_evaluator_payload(_payload(competency_id, "accuracy", "target", "positive"), {competency_id})
        service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)

    events = repos.evidence_events.list_for_competency(competency_id, Dimension.ACCURACY)
    cluster_ids = {e.evidence_cluster_id for e in events}
    assert len(cluster_ids) == 1  # tres activity_id distintos, UM cluster

    state = repos.competency_states.current(competency_id, Dimension.ACCURACY)
    assert state.state == CompetencyDimensionState.ACQUIRING  # nunca passa de 1 cluster


# ---------------------------------------------------------------------
# P5 - reconstrucao preserva historico e e atomica.
# ---------------------------------------------------------------------
def test_P5_reconstruction_preserves_history_and_is_atomic_on_failure(repos, make_competency, make_rule_version, monkeypatch):
    competency_id = make_competency()
    learner = _learner(repos)
    rule_version = make_rule_version()
    service = EvidenceService(repos)
    session = _session(repos, learner.id)
    activity = _new_activity(repos, session.id)

    interaction, _ = service.record_interaction(
        activity=activity, session_id=session.id, idempotency_key=new_id(),
        learner_input="ok", tutor_output="", help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
    )
    output = validate_evaluator_payload(_payload(competency_id, "accuracy", "target", "positive"), {competency_id})
    service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=rule_version)

    before = repos.competency_states.current(competency_id, Dimension.ACCURACY)
    before_generation_id = before.generation_id

    # reconstrucao bem sucedida: nova geracao, geracao antiga preservada
    recompute_all_from_log(repos, rule_version)
    after = repos.competency_states.current(competency_id, Dimension.ACCURACY)
    assert after.generation_id != before_generation_id
    assert after.state == before.state
    old_generation_rows = repos.competency_states.list_by_generation(before_generation_id)
    assert len(old_generation_rows) >= 1  # nada foi apagado

    # reconstrucao com falha simulada no meio: nao pode deixar a geracao
    # ativa pela metade nem trocar o ponteiro.
    active_before_failure = repos.active_projection_generation.get_id()

    def boom(*args, **kwargs):
        raise RuntimeError("falha simulada no meio da reconstrucao")

    monkeypatch.setattr(repos.competency_states, "insert", boom)

    with pytest.raises(RuntimeError):
        recompute_all_from_log(repos, rule_version)

    assert repos.active_projection_generation.get_id() == active_before_failure


# ---------------------------------------------------------------------
# P6 - duas RuleVersions produzem resultados diferentes quando suas regras diferem.
# ---------------------------------------------------------------------
def test_P6_two_rule_versions_with_different_config_diverge(repos, make_competency, make_rule_version):
    competency_id = make_competency()
    learner = _learner(repos)
    service = EvidenceService(repos)

    lenient = make_rule_version(config=AggregationConfig(demonstrated_min_clusters=2, consolidated_min_clusters=99))
    strict = make_rule_version(config=AggregationConfig(demonstrated_min_clusters=5, consolidated_min_clusters=99))

    for i in range(2):
        # sessoes distintas: independencia real (Secao 6 da correcao
        # v0.2.1), nao dois eventos colapsados no mesmo cluster.
        session = _session(repos, learner.id)
        activity = _new_activity(repos, session.id, competency_targets=[competency_id], prompt=f"drill {i}")
        interaction, _ = service.record_interaction(
            activity=activity, session_id=session.id, idempotency_key=new_id(),
            learner_input="ok", tutor_output="", help_level=HelpLevel.A0,
            production_result=ProductionResult.SPONTANEOUS_CORRECT,
        )
        output = validate_evaluator_payload(_payload(competency_id, "accuracy", "target", "positive"), {competency_id})
        # grava a EVIDENCIA uma vez so (o evento/avaliacao nao depende de RuleVersion);
        # so a PROJECAO e recalculada sob cada regra a seguir.
        service.record_evaluation(raw_interaction=interaction, evaluator_output=output, rule_version=lenient)

    state_lenient = service.recompute_state(competency_id, Dimension.ACCURACY, lenient)
    state_strict = service.recompute_state(competency_id, Dimension.ACCURACY, strict)

    assert state_lenient.state == CompetencyDimensionState.DEMONSTRATED
    assert state_strict.state == CompetencyDimensionState.ACQUIRING
    assert state_lenient.state != state_strict.state


# ---------------------------------------------------------------------
# P7 - falha ao inserir MemoryReviewLog nao altera MemoryState.
# ---------------------------------------------------------------------
def test_P7_memory_review_log_failure_never_alters_memory_state(repos, make_competency, make_rule_version, orchestrator_factory, monkeypatch):
    competency_id = make_competency()
    learner = _learner(repos)
    # `recall_min_interval_seconds=0`/`first_review_min_interval_since_learning_seconds=0`
    # isolam este teste do relogio real: a atividade1 e a PRIMEIRA
    # evidencia desta competencia (intervalo desde a aprendizagem ~0s) e a
    # atividade2 e a segunda tentativa de recuperacao (segundos depois da
    # primeira, em tempo de execucao do teste) - ambas precisam continuar
    # elegiveis para exercitar a falha de escrita; a politica de intervalo
    # em si e o que `test_memory_adapter.py` testa isoladamente.
    rule_version = make_rule_version(config=AggregationConfig(
        recall_min_interval_seconds=0.0, first_review_min_interval_since_learning_seconds=0.0,
    ))
    orchestrator = orchestrator_factory(MockProvider(), rule_version)
    session = orchestrator.start_session(learner.id)

    # `is_planned_recall` nao e mais um parametro do orquestrador (Secao 1
    # da correcao v0.2.1: so o proprio Decisor pode produzi-lo, escolhendo
    # SCHEDULE_RECALL) - a atividade e construida diretamente para isolar
    # este teste no comportamento do adapter de memoria.
    activity1 = _planned_recall_activity(repos, session.id, competency_id)
    outcome1 = orchestrator.submit_interaction(
        activity_id=activity1.id, session_id=session.id, idempotency_key=new_id(),
        learner_input="ok", help_level=HelpLevel.A0, production_result=ProductionResult.SPONTANEOUS_CORRECT,
    )
    assert outcome1.memory_eligibility is not None and outcome1.memory_eligibility.eligible is True
    assert outcome1.memory_result is not None
    before = repos.memory_states.get(competency_id)
    assert before is not None

    def boom(*args, **kwargs):
        raise RuntimeError("falha simulada ao gravar memory_review_log")

    monkeypatch.setattr(repos.memory_review_logs, "insert", boom)

    activity2 = _planned_recall_activity(repos, session.id, competency_id)
    outcome2 = orchestrator.submit_interaction(
        activity_id=activity2.id, session_id=session.id, idempotency_key=new_id(),
        learner_input="ok de novo", help_level=HelpLevel.A0, production_result=ProductionResult.SPONTANEOUS_CORRECT,
    )

    assert outcome2.memory_eligibility is not None and outcome2.memory_eligibility.eligible is True
    assert isinstance(outcome2.memory_result, MemoryReviewError)
    after = repos.memory_states.get(competency_id)
    assert after.fsrs_card_json == before.fsrs_card_json
    assert after.updated_at == before.updated_at


# ---------------------------------------------------------------------
# P8 - ciclo de pre-requisito e rejeitado na escrita.
# ---------------------------------------------------------------------
def test_P8_prerequisite_cycle_is_rejected_at_write_time(repos, make_competency):
    a = make_competency("cycle-a")
    b = make_competency("cycle-b")
    now = utc_now_iso()
    repos.prerequisites.insert(PrerequisiteRelation(id=new_id(), competency_id=a, prerequisite_id=b, created_at=now))

    with pytest.raises(PrerequisiteCycleError):
        repos.prerequisites.insert(PrerequisiteRelation(id=new_id(), competency_id=b, prerequisite_id=a, created_at=now))


# ---------------------------------------------------------------------
# P9 - restore real substitui e recupera o banco.
# ---------------------------------------------------------------------
def test_P9_real_restore_substitutes_and_recovers_the_database(tmp_path):
    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    learner = Learner(id=new_id(), display_name="Preservado pelo backup", created_at=utc_now_iso())
    repos.learners.insert(learner)

    backup_event = create_backup(conn, repos, backup_dir=tmp_path / "backups")
    assert backup_event.success is True

    conn.close()
    db_path.write_bytes(b"banco corrompido/perdido (simulado)")

    result = restore_from_backup(db_path, backup_event.backup_path)
    assert result.success is True

    recovered_conn = connect(db_path)
    recovered = Repositories(recovered_conn).learners.get(learner.id)
    assert recovered is not None
    assert recovered.display_name == "Preservado pelo backup"
    recovered_conn.close()


# ---------------------------------------------------------------------
# P10 - reinicio completo do processo preserva estado.
# ---------------------------------------------------------------------
def test_P10_full_process_restart_preserves_state(tmp_path):
    db_path = tmp_path / "central.db"

    conn1 = connect(db_path)
    run_migrations(conn1)
    repos1 = Repositories(conn1)
    rule_version = repos1.rule_versions
    from central_universal.evidence.rule_versions import build_v0_2_0_rule_version

    rv = build_v0_2_0_rule_version()
    repos1.rule_versions.insert(rv)
    repos1.active_rule_version.set(rv.id)
    learner = Learner(id=new_id(), display_name="Sobrevive ao reinicio", created_at=utc_now_iso())
    repos1.learners.insert(learner)
    conn1.close()  # fim do "processo"

    # "reinicio": processo novo, conexao nova, do zero
    conn2 = connect(db_path)
    run_migrations(conn2)  # idempotente - nao recria nada
    repos2 = Repositories(conn2)

    restored_learner = repos2.learners.get(learner.id)
    assert restored_learner is not None
    assert restored_learner.display_name == "Sobrevive ao reinicio"

    active = repos2.active_rule_version.get()
    assert active is not None
    assert active.id == rv.id
    conn2.close()


# ---------------------------------------------------------------------
# P11 - execucao offline falha no teste se qualquer acesso de rede for tentado.
# ---------------------------------------------------------------------
def test_P11_offline_execution_fails_if_network_is_attempted(repos, make_competency, make_rule_version, orchestrator_factory, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError(
            "tentativa de acesso de rede detectada durante execucao offline - "
            "MockProvider nunca deveria abrir um socket (Secao 22/T15)"
        )

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)

    competency_id = make_competency("simple_present")
    learner = _learner(repos)
    rule_version = make_rule_version()
    orchestrator = orchestrator_factory(MockProvider(), rule_version)

    session = orchestrator.start_session(learner.id)
    focus = orchestrator.choose_focus_competency(learner.id)
    assert focus is not None
    focus_id, _routing, action, recall_due = focus
    activity, tutor_output = orchestrator.start_activity(
        session_id=session.id, competency_id=focus_id, action=action
    )
    assert tutor_output is not None

    outcome = orchestrator.submit_interaction(
        activity_id=activity.id, session_id=session.id, idempotency_key=new_id(),
        learner_input="She goes to school", help_level=HelpLevel.A0,
        production_result=ProductionResult.SPONTANEOUS_CORRECT,
    )
    orchestrator.end_session(session.id)

    assert outcome.evaluator_output is not None
    assert repos.sessions.get(session.id).status.value == "ended"
