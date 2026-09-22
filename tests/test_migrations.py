"""Ponto 4 do pacote de correcao v0.2.1: migration 0002 atomica, sem
perda silenciosa de avaliacoes v0.1, com backup automatico previo e
diagnostico auditavel."""

from __future__ import annotations

import sqlite3

import pytest

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.ids import new_id
from central_universal.persistence.db import connect
from central_universal.persistence.migrations import (
    MIGRATIONS_DIR,
    MigrationError,
    _apply_migration_atomically,
    _ensure_migrations_table,
    run_migrations,
)


def _apply_v01_schema_only(conn: sqlite3.Connection) -> None:
    """Aplica SOMENTE o schema v0.1 (migration 0001), sem a 0002 - para
    simular um banco de producao pre-correcao que ainda vai ser
    atualizado."""

    _ensure_migrations_table(conn)
    sql = (MIGRATIONS_DIR / "0001_init.sql").read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.execute(
        "INSERT INTO schema_migrations (filename, applied_at, notes) VALUES (?, ?, '');",
        ("0001_init.sql", utc_now_iso()),
    )


def _seed_v01_data(conn: sqlite3.Connection) -> dict:
    """Povoa um banco v0.1 com avaliacoes que o esquema ANTIGO permitia
    mas o novo CHECK de consistencia classification<->inconclusive (Secao
    6 da migration 0002) rejeitaria - exatamente o caso que a correcao do
    ponto 4 precisa preservar em vez de descartar silenciosamente."""

    now = utc_now_iso()
    learner_id, domain_id, competency_id = new_id(), new_id(), new_id()
    session_id, activity_id, rule_version_id = new_id(), new_id(), new_id()

    conn.execute("INSERT INTO learner (id, display_name, created_at) VALUES (?, ?, ?);", (learner_id, "Aluno v0.1", now))
    conn.execute("INSERT INTO learning_domain (id, code, name) VALUES (?, 'english', 'Ingles');", (domain_id,))
    conn.execute(
        "INSERT INTO competency (id, domain_id, code, name, description) VALUES (?, ?, 'simple_present', 'Simple Present', '');",
        (competency_id, domain_id),
    )
    conn.execute(
        "INSERT INTO rule_version (id, version, description, created_at, active) VALUES (?, 'v0.1.0', 'legado', ?, 1);",
        (rule_version_id, now),
    )
    conn.execute(
        "INSERT INTO learning_session (id, learner_id, started_at, ended_at, status) VALUES (?, ?, ?, NULL, 'active');",
        (session_id, learner_id, now),
    )
    conn.execute(
        "INSERT INTO activity (id, session_id, competency_targets, activity_type, prompt, support_level, created_at) "
        "VALUES (?, ?, ?, 'drill', 'prompt', 'A0', ?);",
        (activity_id, session_id, f'["{competency_id}"]', now),
    )

    evidence_cluster_id = new_id()  # v0.1: string livre, sem tabela/FK propria
    # (classification, inconclusive) - as linhas 1 e 2 sao INCONSISTENTES
    # sob o novo CHECK (permitido pelo esquema antigo, que nao o tinha).
    combos = [("positive", 0), ("positive", 1), ("inconclusive", 0), ("negative", 0)]
    for i, (classification, inconclusive) in enumerate(combos):
        raw_id = new_id()
        conn.execute(
            "INSERT INTO raw_interaction (id, activity_id, session_id, idempotency_key, learner_input, "
            "tutor_output, help_level, production_result, occurred_at, evidence_cluster_id) "
            "VALUES (?, ?, ?, ?, ?, '', 'A0', 'spontaneous_correct', ?, ?);",
            (raw_id, activity_id, session_id, new_id(), f"input {i}", now, evidence_cluster_id),
        )
        event_id = new_id()
        conn.execute(
            "INSERT INTO evidence_event (id, raw_interaction_id, competency_id, dimension, evidence_type, "
            "relation, help_level, production_result, evidence_cluster_id, created_at) "
            "VALUES (?, ?, ?, 'accuracy', 'positive', 'target', 'A0', 'spontaneous_correct', ?, ?);",
            (event_id, raw_id, competency_id, evidence_cluster_id, now),
        )
        conn.execute(
            "INSERT INTO evidence_assessment (id, evidence_event_id, rule_version_id, classification, result, "
            "confidence, justification, alternative_cause, inconclusive, created_at) "
            "VALUES (?, ?, ?, ?, 'resultado', 0.8, 'justificativa', NULL, ?, ?);",
            (new_id(), event_id, rule_version_id, classification, inconclusive, now),
        )

    return {"competency_id": competency_id, "rule_version_id": rule_version_id}


def test_migration_0002_preserves_all_v01_evaluations_including_incompatible_ones(tmp_path):
    """'Preserve todas as avaliacoes v0.1' - testa a atualizacao de um
    banco v0.1 populado, inclusive com avaliacoes permitidas pelo esquema
    antigo e incompativeis com o novo CHECK."""

    db_path = tmp_path / "legacy_v01.db"
    seed_conn = connect(db_path)
    _apply_v01_schema_only(seed_conn)
    _seed_v01_data(seed_conn)
    before_count = seed_conn.execute("SELECT COUNT(*) AS n FROM evidence_assessment;").fetchone()["n"]
    assert before_count == 4
    seed_conn.close()

    # "reinicio"/upgrade: conexao nova, migrations pendentes aplicadas.
    upgraded = connect(db_path)
    applied = run_migrations(upgraded, backup_dir=tmp_path / "backups")
    assert applied == ["0002_v0_2_corrections.sql", "0003_monotonic_projection_sequence.sql"]

    after_count = upgraded.execute("SELECT COUNT(*) AS n FROM evidence_assessment;").fetchone()["n"]
    assert after_count == before_count  # nenhuma avaliacao foi descartada

    rows = upgraded.execute("SELECT classification, inconclusive FROM evidence_assessment;").fetchall()
    assert len(rows) == 4
    for row in rows:
        # toda linha agora respeita o novo CHECK - normalizada a partir de
        # `classification`, a fonte de verdade, nunca apagada.
        if row["classification"] == "inconclusive":
            assert row["inconclusive"] == 1
        else:
            assert row["inconclusive"] == 0

    note = upgraded.execute(
        "SELECT notes FROM schema_migrations WHERE filename = '0002_v0_2_corrections.sql';"
    ).fetchone()["notes"]
    assert "2 avaliacao" in note  # diagnostico auditavel: exatamente 2 linhas precisaram ser normalizadas

    upgraded.close()


def test_migration_0002_takes_automatic_backup_before_upgrading_existing_database(tmp_path):
    db_path = tmp_path / "legacy_v01.db"
    seed_conn = connect(db_path)
    _apply_v01_schema_only(seed_conn)
    _seed_v01_data(seed_conn)
    seed_conn.close()

    backup_dir = tmp_path / "backups"
    upgraded = connect(db_path)
    run_migrations(upgraded, backup_dir=backup_dir)

    backups_in_db = upgraded.execute("SELECT COUNT(*) AS n FROM backup_event WHERE success = 1;").fetchone()["n"]
    assert backups_in_db >= 1
    assert any(backup_dir.glob("central-*.db"))  # arquivo de backup existe de verdade no disco
    upgraded.close()


def test_migration_pending_on_fresh_empty_database_does_not_trigger_backup(tmp_path):
    """Um banco NOVO (sem nenhuma migration aplicada ainda) nao e um
    'upgrade' - nao ha nada a preservar, entao nenhum backup e disparado."""

    db_path = tmp_path / "fresh.db"
    backup_dir = tmp_path / "backups"
    conn = connect(db_path)

    applied = run_migrations(conn, backup_dir=backup_dir)
    assert applied == ["0001_init.sql", "0002_v0_2_corrections.sql", "0003_monotonic_projection_sequence.sql"]
    assert not backup_dir.exists() or not any(backup_dir.glob("central-*.db"))
    conn.close()


def test_migration_atomic_rollback_on_mid_script_failure(conn: sqlite3.Connection) -> None:
    """'Torne a migracao 0002 atomica' - uma falha no meio de uma
    migration (aqui, sintetica) desfaz TUDO o que ela ja tinha feito,
    nunca deixa o banco parcialmente migrado."""

    broken_sql = """
    BEGIN IMMEDIATE;
    CREATE TABLE probe_atomic_rollback (id TEXT PRIMARY KEY);
    INSERT INTO probe_atomic_rollback (id) VALUES ('x');
    INSERT INTO esta_tabela_nao_existe (id) VALUES ('y');
    COMMIT;
    """
    with pytest.raises(MigrationError):
        _apply_migration_atomically(conn, "9999_fake_broken.sql", broken_sql)

    # nada da migration quebrada sobreviveu - nem a tabela criada antes do
    # ponto de falha, nem o registro de bookkeeping.
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='probe_atomic_rollback';"
    ).fetchall()
    assert tables == []
    registered = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE filename = '9999_fake_broken.sql';"
    ).fetchall()
    assert registered == []

    # PRAGMA foreign_keys foi restaurado mesmo apos a falha.
    fk_state = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
    assert fk_state == 1

    # a conexao continua utilizavel normalmente depois do rollback.
    conn.execute("SELECT 1;")


def test_migration_bookkeeping_failure_leaves_whole_schema_at_previous_version(conn: sqlite3.Connection) -> None:
    """Ponto 4 da terceira auditoria pos-entrega: 'inclua o registro em
    schema_migrations na mesma transacao da migracao 0002. Teste falha ao
    gravar esse registro e confirme que o esquema inteiro permanece na
    versao anterior.'

    Pre-semeia uma linha com o MESMO filename que a migration sintetica
    vai tentar gravar - o INSERT de bookkeeping (agora dentro do proprio
    script, injetado logo antes do COMMIT final) viola a PRIMARY KEY de
    `schema_migrations.filename` exatamente na ULTIMA instrucao do
    script, depois que toda a DDL/DML ja rodou - exercitando
    especificamente uma falha NA GRAVACAO DO REGISTRO, nao no resto da
    migration."""

    conn.execute(
        "INSERT INTO schema_migrations (filename, applied_at, notes) VALUES (?, ?, '');",
        ("9999_fake_bookkeeping_failure.sql", utc_now_iso()),
    )

    synthetic_sql = """
    BEGIN IMMEDIATE;
    CREATE TABLE probe_bookkeeping_failure (id TEXT PRIMARY KEY);
    INSERT INTO probe_bookkeeping_failure (id) VALUES ('x');
    COMMIT;
    """

    with pytest.raises(MigrationError):
        _apply_migration_atomically(conn, "9999_fake_bookkeeping_failure.sql", synthetic_sql)

    # o esquema inteiro voltou para a versao anterior: nem a tabela nem a
    # linha que a migration sintetica criou sobreviveram...
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='probe_bookkeeping_failure';"
    ).fetchall()
    assert tables == []

    # ...e o registro de bookkeeping continua sendo exatamente o UNICO
    # pre-semeado - nunca duplicado, nunca "quase" atualizado.
    rows = conn.execute(
        "SELECT applied_at FROM schema_migrations WHERE filename = '9999_fake_bookkeeping_failure.sql';"
    ).fetchall()
    assert len(rows) == 1

    # a conexao continua utilizavel normalmente depois do rollback.
    conn.execute("SELECT 1;")
