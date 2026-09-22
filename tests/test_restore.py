from __future__ import annotations

import sqlite3
from pathlib import Path

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import Learner
from central_universal.domain.ids import new_id
from central_universal.persistence.backup import create_backup, maybe_run_automatic_backup
from central_universal.persistence.db import connect
from central_universal.persistence.migrations import run_migrations
from central_universal.persistence.repositories import Repositories
from central_universal.persistence.restore import restore_from_backup, validate_snapshot


def test_restore_substitutes_and_recovers_the_database(tmp_path: Path):
    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    learner = Learner(id=new_id(), display_name="Antes do backup", created_at=utc_now_iso())
    repos.learners.insert(learner)

    backup_event = create_backup(conn, repos, backup_dir=tmp_path / "backups")
    assert backup_event.success is True

    # Corrompe/perde o banco "ativo": simula desastre real.
    conn.close()
    db_path.write_bytes(b"isto nao e mais um banco sqlite valido")

    result = restore_from_backup(db_path, backup_event.backup_path)
    assert result.success is True
    assert result.integrity_ok is True

    restored_conn = connect(db_path)
    restored_repos = Repositories(restored_conn)
    restored = restored_repos.learners.get(learner.id)
    assert restored is not None
    assert restored.display_name == "Antes do backup"
    restored_conn.close()


def test_restore_rejects_invalid_snapshot_without_touching_active_db(tmp_path: Path):
    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    learner = Learner(id=new_id(), display_name="Original", created_at=utc_now_iso())
    repos.learners.insert(learner)
    conn.close()

    fake_backup = tmp_path / "not-a-real-backup.db"
    fake_backup.write_bytes(b"lixo, nao e sqlite")

    valid, _ = validate_snapshot(fake_backup)
    assert valid is False

    result = restore_from_backup(db_path, fake_backup)
    assert result.success is False

    # o banco original continua intacto
    still_there = connect(db_path)
    assert Repositories(still_there).learners.get(learner.id) is not None
    still_there.close()


def test_restore_reverts_when_integrity_check_fails_after_swap(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    original_learner = Learner(id=new_id(), display_name="Fica", created_at=utc_now_iso())
    repos.learners.insert(original_learner)

    # um snapshot valido, mas de um banco DIFERENTE (para simular sucesso
    # na copia porem falha na checagem pos-restauracao)
    other_db = tmp_path / "other.db"
    other_conn = connect(other_db)
    run_migrations(other_conn)
    other_conn.close()

    conn.close()

    import central_universal.persistence.restore as restore_module

    def fake_run_all(_repos):
        class _FakeReport:
            ok = False
            findings = [type("F", (), {"severity": "error", "message": "forcado no teste"})()]

        return _FakeReport()

    monkeypatch.setattr(restore_module, "run_all", fake_run_all)

    result = restore_from_backup(db_path, other_db)
    assert result.success is False
    assert "revertida" in result.message

    reverted = connect(db_path)
    assert Repositories(reverted).learners.get(original_learner.id) is not None
    reverted.close()


def test_restore_flattens_active_wal_before_swapping(tmp_path: Path):
    """Ponto 5 do pacote de correcao v0.2.1: restaurar por cima de um
    banco com WAL genuinamente ativo (dados commitados so no `-wal`,
    ainda nao levados ao arquivo principal) precisa funcionar - e nao
    pode deixar nenhum sidecar `-wal`/`-shm` orfao grudado no banco
    recem-restaurado."""

    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    # Impede o auto-checkpoint para garantir que o WAL fique realmente
    # ativo (nao seja achatado sozinho pelo SQLite antes do teste rodar).
    conn.execute("PRAGMA wal_autocheckpoint = 0;")
    learner = Learner(id=new_id(), display_name="Commitado so no WAL", created_at=utc_now_iso())
    repos.learners.insert(learner)

    backup_event = create_backup(conn, repos, backup_dir=tmp_path / "backups")
    assert backup_event.success is True

    wal_path = db_path.with_name(db_path.name + "-wal")
    assert wal_path.exists() and wal_path.stat().st_size > 0  # WAL genuinamente ativo

    conn.close()

    result = restore_from_backup(db_path, backup_event.backup_path)
    assert result.success is True

    assert not wal_path.exists()  # nenhum sidecar orfao sobrou grudado no banco restaurado

    restored = connect(db_path)
    assert Repositories(restored).learners.get(learner.id) is not None
    restored.close()


def test_restore_aborts_cleanly_with_concurrent_connection_open(tmp_path: Path):
    """Ponto 5 do pacote de correcao v0.2.1: 'impeca novas escritas' -
    uma conexao concorrente ainda aberta no banco ativo tem que bloquear
    a restauracao (sem tocar em nenhum arquivo), nao arriscar uma
    corrida com a troca atomica do arquivo."""

    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    learner = Learner(id=new_id(), display_name="Original", created_at=utc_now_iso())
    repos.learners.insert(learner)
    backup_event = create_backup(conn, repos, backup_dir=tmp_path / "backups")
    assert backup_event.success is True

    # `conn` continua deliberadamente aberta - simula uma sessao/processo
    # concorrente que ainda nao fechou sua conexao com o banco ativo.
    result = restore_from_backup(db_path, backup_event.backup_path)
    assert result.success is False
    assert "conexao" in result.message

    # nada foi tocado: a mesma conexao concorrente ainda enxerga o original
    assert repos.learners.get(learner.id) is not None
    conn.close()

    # com a conexao concorrente fechada, a restauracao agora funciona
    result2 = restore_from_backup(db_path, backup_event.backup_path)
    assert result2.success is True


def test_restore_blocks_a_connection_opened_after_the_check_and_before_the_swap(tmp_path: Path, monkeypatch):
    """Ponto 3 da terceira auditoria pos-entrega: 'proteja toda a operacao
    de restore contra novas conexoes e escritas, desde a verificacao de
    exclusividade ate o fim da troca ou reversao'. Uma conexao aberta
    DEPOIS da checagem de exclusividade, mas ANTES do `os.replace` (no
    meio da copia do backup para staging), precisa ser bloqueada se
    tentar escrever - a guarda continua com o lock exclusivo ate a troca
    terminar, nao so ate o instante da checagem."""

    import central_universal.persistence.restore as restore_module

    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    learner = Learner(id=new_id(), display_name="Original", created_at=utc_now_iso())
    repos.learners.insert(learner)
    backup_event = create_backup(conn, repos, backup_dir=tmp_path / "backups")
    assert backup_event.success is True
    conn.close()  # nenhuma conexao "oficial" aberta - simula o app real (cada request abre/fecha a sua)

    staging_path = db_path.with_name(db_path.name + ".restoring")
    attempt: dict[str, object] = {}
    real_copy2 = restore_module.shutil.copy2

    def spy_copy2(src, dst, *args, **kwargs):
        # so intercepta a copia do BACKUP para staging - acontece DEPOIS
        # da checagem de exclusividade (ja feita antes desta chamada) e
        # ANTES do os.replace (que so vem depois que esta copia retorna).
        if str(dst) == str(staging_path) and not attempt:
            attempt["ran"] = True
            late_conn = sqlite3.connect(str(db_path), timeout=0.2)
            try:
                late_conn.execute("BEGIN IMMEDIATE;")
                late_conn.execute(
                    "INSERT INTO learner (id, display_name, created_at) VALUES (?, ?, ?);",
                    (new_id(), "Escritor tardio", utc_now_iso()),
                )
                late_conn.execute("COMMIT;")
                attempt["blocked"] = False
            except sqlite3.OperationalError as exc:
                attempt["blocked"] = True
                attempt["error"] = str(exc)
            finally:
                late_conn.close()
        return real_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(restore_module.shutil, "copy2", spy_copy2)

    result = restore_from_backup(db_path, backup_event.backup_path)

    assert attempt.get("ran") is True  # a janela realmente foi exercitada
    assert attempt.get("blocked") is True  # a escrita tardia foi recusada pela guarda
    assert "locked" in str(attempt.get("error", "")).lower()
    assert result.success is True  # a restauracao em si prossegue normalmente

    restored = connect(db_path)
    restored_repos = Repositories(restored)
    assert restored_repos.learners.get(learner.id) is not None
    assert all(l.display_name != "Escritor tardio" for l in restored_repos.learners.list_all())
    restored.close()


def test_restore_reverts_cleanly_when_swap_target_had_active_wal(tmp_path: Path, monkeypatch):
    """Combina reversao (integrity_check falha apos a troca) com um WAL
    ativo no banco original - a reversao precisa devolver exatamente o
    estado anterior, sem sidecars orfaos da tentativa que falhou."""

    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    conn.execute("PRAGMA wal_autocheckpoint = 0;")
    original_learner = Learner(id=new_id(), display_name="Fica mesmo com WAL ativo", created_at=utc_now_iso())
    repos.learners.insert(original_learner)

    wal_path = db_path.with_name(db_path.name + "-wal")
    assert wal_path.exists() and wal_path.stat().st_size > 0

    other_db = tmp_path / "other.db"
    other_conn = connect(other_db)
    run_migrations(other_conn)
    other_conn.close()

    conn.close()

    import central_universal.persistence.restore as restore_module

    def fake_run_all(_repos):
        class _FakeReport:
            ok = False
            findings = [type("F", (), {"severity": "error", "message": "forcado no teste"})()]

        return _FakeReport()

    monkeypatch.setattr(restore_module, "run_all", fake_run_all)

    result = restore_from_backup(db_path, other_db)
    assert result.success is False
    assert "revertida" in result.message

    assert not wal_path.exists()  # nenhum sidecar orfao da tentativa que falhou

    reverted = connect(db_path)
    assert Repositories(reverted).learners.get(original_learner.id) is not None
    reverted.close()


def test_maybe_run_automatic_backup_respects_min_interval(tmp_path: Path):
    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    backup_dir = tmp_path / "backups"

    first = maybe_run_automatic_backup(conn, repos, backup_dir=backup_dir, min_interval_hours=1.0)
    assert first is not None

    second = maybe_run_automatic_backup(conn, repos, backup_dir=backup_dir, min_interval_hours=1.0)
    assert second is None  # ainda dentro do intervalo minimo

    third = maybe_run_automatic_backup(conn, repos, backup_dir=backup_dir, min_interval_hours=0.0)
    assert third is not None  # intervalo zerado, roda de novo
    conn.close()
