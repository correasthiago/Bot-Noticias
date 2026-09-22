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
