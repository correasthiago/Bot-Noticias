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
