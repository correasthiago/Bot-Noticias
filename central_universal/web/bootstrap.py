"""Inicializacao do banco para o servidor web local: migrations, learner
padrao (Secao 1: "o primeiro usuario e uma unica pessoa"), RuleVersion
ativa e seed do grafo de ingles."""

from __future__ import annotations

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import Learner
from central_universal.domain.ids import new_id
from central_universal.evidence.rule_versions import V0_2_1_VERSION, build_v0_2_1_rule_version
from central_universal.persistence.db import connect
from central_universal.persistence.migrations import run_migrations
from central_universal.persistence.repositories import Repositories
from seed.english_graph import seed as seed_english_graph

DEFAULT_RULE_VERSION = V0_2_1_VERSION
DEFAULT_LEARNER_NAME = "Aprendiz"


def ensure_bootstrapped(db_path) -> None:
    conn = connect(db_path)
    try:
        run_migrations(conn)
        repos = Repositories(conn)

        rule_version = repos.rule_versions.get_by_version(DEFAULT_RULE_VERSION)
        if rule_version is None:
            rule_version = build_v0_2_1_rule_version()
            repos.rule_versions.insert(rule_version)
        if repos.active_rule_version.get() is None or repos.active_rule_version.get().version != DEFAULT_RULE_VERSION:
            repos.active_rule_version.set(rule_version.id)

        if not repos.learners.list_all():
            repos.learners.insert(
                Learner(id=new_id(), display_name=DEFAULT_LEARNER_NAME, created_at=utc_now_iso())
            )

        seed_english_graph(repos)
    finally:
        conn.close()
