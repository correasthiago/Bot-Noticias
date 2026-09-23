from __future__ import annotations

import importlib


def _fresh_client(tmp_path, monkeypatch):
    db_path = tmp_path / "web_test.db"
    monkeypatch.setenv("CENTRAL_DB_PATH", str(db_path))

    import central_universal.web.deps as deps_module

    importlib.reload(deps_module)
    import central_universal.web.app as app_module

    importlib.reload(app_module)

    from fastapi.testclient import TestClient

    return TestClient(app_module.app)


def test_index_page_renders(tmp_path, monkeypatch):
    with _fresh_client(tmp_path, monkeypatch) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Aprendiz" in response.text
        assert "STUDY" in response.text


def test_full_session_flow_over_http(tmp_path, monkeypatch):
    with _fresh_client(tmp_path, monkeypatch) as client:
        start = client.post("/session/start", follow_redirects=False)
        assert start.status_code == 303
        session_url = start.headers["location"]

        page = client.get(session_url)
        assert page.status_code == 200
        assert "Nenhuma atividade ainda" in page.text

        next_resp = client.post(f"{session_url}/next", follow_redirects=False)
        assert next_resp.status_code == 303

        page_with_activity = client.get(session_url)
        assert page_with_activity.status_code == 200
        assert "Sua resposta" in page_with_activity.text

        import re

        activity_id_match = re.search(r'name="activity_id" value="([^"]+)"', page_with_activity.text)
        assert activity_id_match is not None
        activity_id = activity_id_match.group(1)

        answer = client.post(
            f"{session_url}/answer",
            data={
                "activity_id": activity_id,
                "learner_input": "She goes to school",
                "help_level": "A0",
                "production_result": "spontaneous_correct",
                "idempotency_key": activity_id,
            },
            follow_redirects=False,
        )
        assert answer.status_code == 303

        feedback_page = client.get(session_url)
        assert "Sua resposta" in feedback_page.text
        assert "She goes to school" in feedback_page.text

        end_resp = client.post(f"{session_url}/end", follow_redirects=False)
        assert end_resp.status_code == 303


def test_map_and_audit_pages_render(tmp_path, monkeypatch):
    with _fresh_client(tmp_path, monkeypatch) as client:
        map_page = client.get("/map")
        assert map_page.status_code == 200
        assert "be" in map_page.text.lower()

        audit_page = client.get("/audit")
        assert audit_page.status_code == 200
        assert "Integridade" in audit_page.text

        backup_resp = client.post("/audit/backup", follow_redirects=False)
        assert backup_resp.status_code == 303


def test_restore_failure_is_shown_to_the_user(tmp_path, monkeypatch):
    """Ponto 5 do pacote de correcao v0.2.1: 'mostre falha ao usuario
    quando o restore falhar' - a pagina de auditoria precisa exibir o
    motivo, nunca so redirecionar silenciosamente."""

    with _fresh_client(tmp_path, monkeypatch) as client:
        client.get("/audit")  # garante que o app ja fez bootstrap do banco

        restore_resp = client.post(
            "/audit/restore",
            data={"backup_path": str(tmp_path / "nao-existe.db")},
            follow_redirects=False,
        )
        assert restore_resp.status_code == 303
        assert "restore_status=error" in restore_resp.headers["location"]

        audit_page = client.get(restore_resp.headers["location"])
        assert audit_page.status_code == 200
        assert "Restauracao" in audit_page.text
        assert 'class="error"' in audit_page.text


def test_memory_review_recovery_is_visible_and_retryable_over_http(tmp_path, monkeypatch):
    """Achado P2 da nona auditoria pos-entrega: o servico ja sabia
    retentar a revisao de memoria numa submissao repetida (Principio 35),
    mas a rota web descartava `MemoryReviewError` sem mostrar nada ao
    usuario, e o formulario de resposta sumia assim que a interacao
    existia - nao havia caminho VISIVEL nem TESTADO para recuperar a
    revisao. Constroi uma atividade de recuperacao planejada diretamente
    (sem percorrer a ladder inteira, ja coberta em outros testes), forca
    o FSRS a falhar na primeira resposta via HTTP, confirma que a pagina
    da sessao mostra o aviso de falha E o botao de retentativa, clica
    nele (reenviando a MESMA resposta) e confirma que a revisao e
    recuperada - usando o horario da tentativa ORIGINAL (nunca o do
    clique de retentativa) e sem duplicar."""

    with _fresh_client(tmp_path, monkeypatch) as client:
        client.get("/")  # garante que o app ja fez bootstrap do banco

        from datetime import datetime, timedelta, timezone

        import central_universal.memory.fsrs_adapter as fsrs_adapter_module
        import central_universal.web.deps as deps_module
        from central_universal.domain.clock import parse_iso, utc_now_iso
        from central_universal.domain.entities import Activity, LearningSession
        from central_universal.domain.enums import DecisionType, HelpLevel, ProductionResult, SessionStatus
        from central_universal.domain.ids import new_id
        from central_universal.memory.fsrs_adapter import MemoryReviewError
        from central_universal.orchestration.session_service import SessionOrchestrator
        from central_universal.persistence.repositories import Repositories
        from central_universal.providers.mock import MockProvider

        conn = deps_module.connect(deps_module.DB_PATH)
        repos = Repositories(conn)
        learner = repos.learners.list_all()[0]
        competency_id = repos.competencies.list_all()[0].id
        rule_version = repos.active_rule_version.get()

        session = LearningSession(
            id=new_id(), learner_id=learner.id, started_at=utc_now_iso(), status=SessionStatus.ACTIVE
        )
        repos.sessions.insert(session)

        # evidencia PRIMING, 2h no passado - sem ela, a checagem de
        # intervalo desde a primeira evidencia (Secao 2 da terceira
        # auditoria pos-entrega) rejeitaria a recuperacao abaixo por
        # intervalo zero (a propria tentativa seria a unica evidencia).
        priming_orchestrator = SessionOrchestrator(repos, MockProvider(), rule_version, backup_dir=tmp_path / "backups")
        priming_activity = Activity(
            id=new_id(),
            session_id=session.id,
            competency_targets=[competency_id],
            activity_type=DecisionType.MINIMAL_EXPLANATION.value,
            prompt="Atividade de priming",
            support_level=HelpLevel.A0,
            created_at=utc_now_iso(),
            tutor_provider_event_id=None,
            is_planned_recall=False,
        )
        repos.activities.insert(priming_activity)
        priming_orchestrator.submit_interaction(
            activity_id=priming_activity.id, session_id=session.id, idempotency_key=new_id(),
            learner_input="resposta de priming", help_level=HelpLevel.A0,
            production_result=ProductionResult.SPONTANEOUS_CORRECT, tutor_output_text="",
            now=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        activity = Activity(
            id=new_id(),
            session_id=session.id,
            competency_targets=[competency_id],
            activity_type=DecisionType.SCHEDULE_RECALL.value,
            prompt="Revisao de teste",
            support_level=HelpLevel.A0,
            created_at=utc_now_iso(),
            tutor_provider_event_id=None,
            is_planned_recall=True,
        )
        repos.activities.insert(activity)
        conn.close()

        # forca a PRIMEIRA chamada a observe_and_review a falhar (o jeito
        # REAL que ela falha - devolve MemoryReviewError, nunca levanta) -
        # chamadas seguintes funcionam normalmente.
        call_count = {"n": 0}
        real_observe_and_review = fsrs_adapter_module.MemoryAdapter.observe_and_review

        def flaky_observe_and_review(self, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return MemoryReviewError(message="falha simulada no FSRS (P2)")
            return real_observe_and_review(self, *args, **kwargs)

        monkeypatch.setattr(fsrs_adapter_module.MemoryAdapter, "observe_and_review", flaky_observe_and_review)

        answer_data = {
            "activity_id": activity.id,
            "learner_input": "She has gone to school before.",
            "help_level": "A0",
            "production_result": "spontaneous_correct",
            "idempotency_key": activity.id,
        }

        first_resp = client.post(
            f"/session/{session.id}/answer", data=answer_data, follow_redirects=False
        )
        assert first_resp.status_code == 303
        assert "memory_status=error" in first_resp.headers["location"]

        failure_page = client.get(first_resp.headers["location"])
        assert failure_page.status_code == 200
        assert "Revisao de memoria" in failure_page.text
        assert 'class="error"' in failure_page.text
        # o formulario de resposta sumiu (a interacao ja existe), mas o
        # caminho de recuperacao PRECISA estar visivel.
        assert "Tentar revisao de memoria novamente" in failure_page.text

        retry_resp = client.post(
            f"/session/{session.id}/answer", data=answer_data, follow_redirects=False
        )
        assert retry_resp.status_code == 303
        assert "memory_status=ok" in retry_resp.headers["location"]

        recovered_page = client.get(retry_resp.headers["location"])
        assert recovered_page.status_code == 200
        assert "recuperada com sucesso" in recovered_page.text
        # ja foi recuperada - o botao de retentativa nao aparece mais.
        assert "Tentar revisao de memoria novamente" not in recovered_page.text

        conn2 = deps_module.connect(deps_module.DB_PATH)
        repos2 = Repositories(conn2)
        raw_interaction = repos2.raw_interactions.get_by_idempotency_key(activity.id)
        assert raw_interaction is not None
        observation = repos2.memory_observations.get_by_raw_interaction(raw_interaction.id)
        assert observation is not None
        assert len(repos2.memory_observations.list_for_competency(competency_id)) == 1  # nao duplicou

        memory_state = repos2.memory_states.get(competency_id)
        assert memory_state is not None
        # a revisao recuperada usou o horario da tentativa ORIGINAL, nunca
        # o horario do clique de retentativa (que aconteceu depois).
        assert parse_iso(memory_state.last_review_at) == parse_iso(raw_interaction.occurred_at)
        conn2.close()


def test_ineligible_memory_review_shows_reason_never_a_retry_button(tmp_path, monkeypatch):
    """Achado remanescente da decima auditoria pos-entrega: `session_view`
    tratava TODA recuperacao planejada sem `MemoryObservation` como
    'pendente', inclusive tentativas NAO ELEGIVEIS (intervalo
    insuficiente, avaliacao inconclusiva, etc.) - o botao de retentativa
    reaparecia identico apos cada clique, sem explicar por que nada
    mudava (retentar nunca muda o resultado: a elegibilidade e calculada
    com o horario FIXO da tentativa original). Este teste submete uma
    recuperacao planejada SEM nenhuma evidencia anterior da competencia -
    intervalo desde a 'aprendizagem' e zero, abaixo do minimo exigido -
    e confirma que a pagina explica o motivo (nao esconde) e NUNCA mostra
    um botao de retentativa para essa tentativa."""

    with _fresh_client(tmp_path, monkeypatch) as client:
        client.get("/")

        import central_universal.web.deps as deps_module
        from central_universal.domain.clock import utc_now_iso
        from central_universal.domain.entities import Activity, LearningSession
        from central_universal.domain.enums import DecisionType, HelpLevel, SessionStatus
        from central_universal.domain.ids import new_id
        from central_universal.persistence.repositories import Repositories

        conn = deps_module.connect(deps_module.DB_PATH)
        repos = Repositories(conn)
        learner = repos.learners.list_all()[0]
        competency_id = repos.competencies.list_all()[0].id

        session = LearningSession(
            id=new_id(), learner_id=learner.id, started_at=utc_now_iso(), status=SessionStatus.ACTIVE
        )
        repos.sessions.insert(session)
        # SEM nenhuma interacao/evidencia previa para esta competencia -
        # esta sera a PRIMEIRA evidencia, entao o intervalo desde a
        # "aprendizagem" e zero (abaixo do minimo de 3600s por padrao).
        activity = Activity(
            id=new_id(),
            session_id=session.id,
            competency_targets=[competency_id],
            activity_type=DecisionType.SCHEDULE_RECALL.value,
            prompt="Revisao sem intervalo suficiente",
            support_level=HelpLevel.A0,
            created_at=utc_now_iso(),
            tutor_provider_event_id=None,
            is_planned_recall=True,
        )
        repos.activities.insert(activity)
        conn.close()

        answer_data = {
            "activity_id": activity.id,
            "learner_input": "She has gone to school before.",
            "help_level": "A0",
            "production_result": "spontaneous_correct",
            "idempotency_key": activity.id,
        }
        answer_resp = client.post(f"/session/{session.id}/answer", data=answer_data, follow_redirects=False)
        assert answer_resp.status_code == 303
        # nao elegivel - nenhuma revisao aconteceu, entao nenhum flash de
        # sucesso/erro de FSRS e disparado por esta submissao.
        assert "memory_status" not in answer_resp.headers["location"]

        page = client.get(answer_resp.headers["location"])
        assert page.status_code == 200
        assert "Revisoes de memoria nao recuperaveis" in page.text
        assert "intervalo" in page.text.lower()
        assert "Tentar revisao de memoria novamente" not in page.text

        # reenviar de novo (mesma idempotency_key) nao muda nada - a
        # elegibilidade e permanente para esta interacao especifica.
        again_resp = client.post(f"/session/{session.id}/answer", data=answer_data, follow_redirects=False)
        again_page = client.get(again_resp.headers["location"])
        assert "Revisoes de memoria nao recuperaveis" in again_page.text
        assert "Tentar revisao de memoria novamente" not in again_page.text


def test_pending_memory_review_stays_reachable_after_advancing_to_next_activity(tmp_path, monkeypatch):
    """Segundo achado remanescente da decima auditoria pos-entrega: a
    pagina da sessao so mostrava a atividade mais recente - ao avancar
    para uma nova atividade, uma revisao de memoria ainda RECUPERAVEL
    (elegivel, so o FSRS falhou) de uma atividade anterior deixava de ter
    qualquer caminho de acesso. Este teste forca a falha do FSRS numa
    recuperacao planejada, avanca para outra atividade via
    `/session/{id}/next`, e confirma que a revisao anterior CONTINUA
    visivel e retentavel na pagina da sessao - e que retentar a partir
    dali funciona e nao deixa duplicata."""

    with _fresh_client(tmp_path, monkeypatch) as client:
        client.get("/")

        from datetime import datetime, timedelta, timezone

        import central_universal.memory.fsrs_adapter as fsrs_adapter_module
        import central_universal.web.deps as deps_module
        from central_universal.domain.clock import utc_now_iso
        from central_universal.domain.entities import Activity, LearningSession
        from central_universal.domain.enums import DecisionType, HelpLevel, ProductionResult, SessionStatus
        from central_universal.domain.ids import new_id
        from central_universal.memory.fsrs_adapter import MemoryReviewError
        from central_universal.orchestration.session_service import SessionOrchestrator
        from central_universal.persistence.repositories import Repositories
        from central_universal.providers.mock import MockProvider

        conn = deps_module.connect(deps_module.DB_PATH)
        repos = Repositories(conn)
        learner = repos.learners.list_all()[0]
        competency_id = repos.competencies.list_all()[0].id
        rule_version = repos.active_rule_version.get()

        session = LearningSession(
            id=new_id(), learner_id=learner.id, started_at=utc_now_iso(), status=SessionStatus.ACTIVE
        )
        repos.sessions.insert(session)

        priming_orchestrator = SessionOrchestrator(repos, MockProvider(), rule_version, backup_dir=tmp_path / "backups")
        priming_activity = Activity(
            id=new_id(),
            session_id=session.id,
            competency_targets=[competency_id],
            activity_type=DecisionType.MINIMAL_EXPLANATION.value,
            prompt="Atividade de priming",
            support_level=HelpLevel.A0,
            created_at=utc_now_iso(),
            tutor_provider_event_id=None,
            is_planned_recall=False,
        )
        repos.activities.insert(priming_activity)
        priming_orchestrator.submit_interaction(
            activity_id=priming_activity.id, session_id=session.id, idempotency_key=new_id(),
            learner_input="resposta de priming", help_level=HelpLevel.A0,
            production_result=ProductionResult.SPONTANEOUS_CORRECT, tutor_output_text="",
            now=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        recall_activity = Activity(
            id=new_id(),
            session_id=session.id,
            competency_targets=[competency_id],
            activity_type=DecisionType.SCHEDULE_RECALL.value,
            prompt="Revisao recuperavel",
            support_level=HelpLevel.A0,
            created_at=utc_now_iso(),
            tutor_provider_event_id=None,
            is_planned_recall=True,
        )
        repos.activities.insert(recall_activity)
        conn.close()

        real_observe_and_review = fsrs_adapter_module.MemoryAdapter.observe_and_review

        def failing_observe_and_review(self, *args, **kwargs):
            return MemoryReviewError(message="falha simulada no FSRS")

        monkeypatch.setattr(fsrs_adapter_module.MemoryAdapter, "observe_and_review", failing_observe_and_review)

        answer_data = {
            "activity_id": recall_activity.id,
            "learner_input": "She has gone to school before.",
            "help_level": "A0",
            "production_result": "spontaneous_correct",
            "idempotency_key": recall_activity.id,
        }
        first_resp = client.post(f"/session/{session.id}/answer", data=answer_data, follow_redirects=False)
        assert first_resp.status_code == 303
        assert "memory_status=error" in first_resp.headers["location"]

        # confirma que a revisao esta recuperavel ENQUANTO ainda e a
        # atividade corrente.
        before_advancing = client.get(f"/session/{session.id}")
        assert "Tentar revisao de memoria novamente" in before_advancing.text

        # avanca para uma NOVA atividade - `recall_activity` deixa de ser
        # a atividade corrente da sessao.
        next_resp = client.post(f"/session/{session.id}/next", follow_redirects=False)
        assert next_resp.status_code == 303

        after_advancing = client.get(f"/session/{session.id}")
        assert after_advancing.status_code == 200
        # a revisao pendente da atividade ANTERIOR continua acessivel.
        assert "Tentar revisao de memoria novamente" in after_advancing.text
        assert 'value="{}"'.format(recall_activity.id) in after_advancing.text

        # o FSRS volta a funcionar - retentar a partir da lista de
        # pendencias (mesmos dados escondidos no formulario) recupera a
        # revisao.
        monkeypatch.setattr(fsrs_adapter_module.MemoryAdapter, "observe_and_review", real_observe_and_review)
        retry_resp = client.post(f"/session/{session.id}/answer", data=answer_data, follow_redirects=False)
        assert retry_resp.status_code == 303
        assert "memory_status=ok" in retry_resp.headers["location"]

        recovered_page = client.get(f"/session/{session.id}")
        # a revisao ja foi recuperada - some da lista de pendencias.
        assert "Tentar revisao de memoria novamente" not in recovered_page.text

        conn2 = deps_module.connect(deps_module.DB_PATH)
        repos2 = Repositories(conn2)
        assert len(repos2.memory_observations.list_for_competency(competency_id)) == 1  # nao duplicou
        conn2.close()


def test_superseded_memory_review_becomes_unrecoverable_not_silently_lost(tmp_path, monkeypatch):
    """Achado remanescente da decima primeira auditoria pos-entrega: uma
    revisao A falha e fica pendente/recuperavel; o usuario avanca; uma
    revisao B POSTERIOR da MESMA competencia e registrada com sucesso,
    avancando o card FSRS. Recalcular a elegibilidade de A nesse ponto
    usa o `memory_state.last_review_at` ATUAL (de B, mais recente) - o
    intervalo de A em relacao a essa revisao fica NEGATIVO (A aconteceu
    ANTES de B), e a checagem generica de 'intervalo minimo' excluiria A
    silenciosamente da lista de pendencias, sem nenhum registro explicito
    do motivo. Este teste confirma que A NUNCA some sem explicacao: ela
    migra para a lista de revisoes NAO RECUPERAVEIS, com um motivo
    auditavel que deixa claro que foi SUPERADA por uma revisao mais
    recente - nunca aplicada fora de ordem ao card, e nunca com um botao
    de retentativa que nao levaria a lugar nenhum."""

    with _fresh_client(tmp_path, monkeypatch) as client:
        client.get("/")

        from datetime import datetime, timedelta, timezone

        import central_universal.memory.fsrs_adapter as fsrs_adapter_module
        import central_universal.web.deps as deps_module
        from central_universal.domain.clock import utc_now_iso
        from central_universal.domain.entities import Activity, LearningSession
        from central_universal.domain.enums import DecisionType, HelpLevel, ProductionResult, SessionStatus
        from central_universal.domain.ids import new_id
        from central_universal.memory.fsrs_adapter import MemoryReviewError
        from central_universal.orchestration.session_service import SessionOrchestrator
        from central_universal.persistence.repositories import Repositories
        from central_universal.providers.mock import MockProvider

        conn = deps_module.connect(deps_module.DB_PATH)
        repos = Repositories(conn)
        learner = repos.learners.list_all()[0]
        competency_id = repos.competencies.list_all()[0].id
        rule_version = repos.active_rule_version.get()

        session = LearningSession(
            id=new_id(), learner_id=learner.id, started_at=utc_now_iso(), status=SessionStatus.ACTIVE
        )
        repos.sessions.insert(session)

        priming_orchestrator = SessionOrchestrator(repos, MockProvider(), rule_version, backup_dir=tmp_path / "backups")
        priming_activity = Activity(
            id=new_id(),
            session_id=session.id,
            competency_targets=[competency_id],
            activity_type=DecisionType.MINIMAL_EXPLANATION.value,
            prompt="Atividade de priming",
            support_level=HelpLevel.A0,
            created_at=utc_now_iso(),
            tutor_provider_event_id=None,
            is_planned_recall=False,
        )
        repos.activities.insert(priming_activity)
        priming_orchestrator.submit_interaction(
            activity_id=priming_activity.id, session_id=session.id, idempotency_key=new_id(),
            learner_input="resposta de priming", help_level=HelpLevel.A0,
            production_result=ProductionResult.SPONTANEOUS_CORRECT, tutor_output_text="",
            now=datetime.now(timezone.utc) - timedelta(hours=2),
        )

        activity_a = Activity(
            id=new_id(),
            session_id=session.id,
            competency_targets=[competency_id],
            activity_type=DecisionType.SCHEDULE_RECALL.value,
            prompt="Revisao A (vai falhar)",
            support_level=HelpLevel.A0,
            created_at=utc_now_iso(),
            tutor_provider_event_id=None,
            is_planned_recall=True,
        )
        repos.activities.insert(activity_a)
        activity_b = Activity(
            id=new_id(),
            session_id=session.id,
            competency_targets=[competency_id],
            activity_type=DecisionType.SCHEDULE_RECALL.value,
            prompt="Revisao B (vai funcionar, depois de A)",
            support_level=HelpLevel.A0,
            created_at=utc_now_iso(),
            tutor_provider_event_id=None,
            is_planned_recall=True,
        )
        repos.activities.insert(activity_b)
        conn.close()

        # A falha (FSRS forcado a devolver MemoryReviewError).
        real_observe_and_review = fsrs_adapter_module.MemoryAdapter.observe_and_review

        def failing_observe_and_review(self, *args, **kwargs):
            return MemoryReviewError(message="falha simulada no FSRS para A")

        monkeypatch.setattr(fsrs_adapter_module.MemoryAdapter, "observe_and_review", failing_observe_and_review)

        answer_a = {
            "activity_id": activity_a.id,
            "learner_input": "She has gone to school before.",
            "help_level": "A0",
            "production_result": "spontaneous_correct",
            "idempotency_key": activity_a.id,
        }
        resp_a = client.post(f"/session/{session.id}/answer", data=answer_a, follow_redirects=False)
        assert resp_a.status_code == 303
        assert "memory_status=error" in resp_a.headers["location"]

        # confirma que A esta RECUPERAVEL antes de B ser registrada.
        before_b = client.get(f"/session/{session.id}")
        assert "Tentar revisao de memoria novamente" in before_b.text

        # o FSRS volta a funcionar - B e registrada com sucesso, MAIS
        # RECENTE que A (submetida depois, em ordem real de wall-clock).
        monkeypatch.setattr(fsrs_adapter_module.MemoryAdapter, "observe_and_review", real_observe_and_review)
        answer_b = {
            "activity_id": activity_b.id,
            "learner_input": "She had already left when I arrived.",
            "help_level": "A0",
            "production_result": "spontaneous_correct",
            "idempotency_key": activity_b.id,
        }
        resp_b = client.post(f"/session/{session.id}/answer", data=answer_b, follow_redirects=False)
        assert resp_b.status_code == 303

        # o destino de A: nunca mais some silenciosamente - migra para a
        # lista de NAO RECUPERAVEIS, com motivo auditavel explicando que
        # foi superada por uma revisao mais recente. NUNCA um botao de
        # retentativa (aplicar A fora de ordem corromperia o card).
        after_b = client.get(f"/session/{session.id}")
        assert after_b.status_code == 200
        assert "Tentar revisao de memoria novamente" not in after_b.text
        assert "Revisoes de memoria nao recuperaveis" in after_b.text
        assert "Revisao A (vai falhar)" in after_b.text
        assert "anterior a ultima revisao" in after_b.text

        # A NUNCA foi aplicada ao card - so a revisao de B existe.
        conn2 = deps_module.connect(deps_module.DB_PATH)
        repos2 = Repositories(conn2)
        assert len(repos2.memory_observations.list_for_competency(competency_id)) == 1
        observation = repos2.memory_observations.list_for_competency(competency_id)[0]
        raw_interaction_b = repos2.raw_interactions.get_by_idempotency_key(activity_b.id)
        assert observation.raw_interaction_id == raw_interaction_b.id
        conn2.close()


def test_restore_rejects_new_requests_while_in_progress(tmp_path, monkeypatch):
    """Quarta auditoria pos-entrega, Secao 3: 'coordene a pausa de
    requisicoes no nivel da aplicacao'. Uma requisicao HTTP que chega
    ENQUANTO a restauracao esta em andamento precisa ser recusada (503) -
    a protecao portatil (funciona em Windows) que substitui manter uma
    conexao SQLite presa durante a troca de arquivo (que quebrava com
    `PermissionError: [WinError 5]` no Windows)."""

    with _fresh_client(tmp_path, monkeypatch) as client:
        client.get("/audit")  # garante que o app ja fez bootstrap do banco

        import central_universal.persistence.backup as backup_module
        import central_universal.persistence.restore as restore_module
        import central_universal.web.deps as deps_module
        from central_universal.persistence.repositories import Repositories

        conn = deps_module.connect(deps_module.DB_PATH)
        repos = Repositories(conn)
        backup_event = backup_module.create_backup(conn, repos, backup_dir=tmp_path / "backups")
        assert backup_event.success is True
        conn.close()

        observed: dict[str, object] = {}
        real_copy2 = restore_module.shutil.copy2
        staging_path = deps_module.DB_PATH.with_name(deps_module.DB_PATH.name + ".restoring")

        def spy_copy2(src, dst, *args, **kwargs):
            if str(dst) == str(staging_path) and "checked" not in observed:
                observed["checked"] = True
                mid_flight = client.get("/audit")
                observed["mid_flight_status"] = mid_flight.status_code
            return real_copy2(src, dst, *args, **kwargs)

        monkeypatch.setattr(restore_module.shutil, "copy2", spy_copy2)

        restore_resp = client.post(
            "/audit/restore",
            data={"backup_path": backup_event.backup_path},
            follow_redirects=False,
        )
        assert restore_resp.status_code == 303

        assert observed.get("checked") is True
        assert observed.get("mid_flight_status") == 503

        # depois da restauracao, o portao foi desligado e requisicoes voltam ao normal
        after = client.get("/audit")
        assert after.status_code == 200
