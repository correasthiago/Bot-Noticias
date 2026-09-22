from __future__ import annotations

from central_universal.integrity.checks import run_all
from central_universal.persistence.repositories import Repositories
from seed.english_graph import COMPETENCIES, seed


def test_seed_is_idempotent(repos: Repositories):
    assert seed(repos) is True
    assert seed(repos) is False
    assert len(repos.competencies.list_all()) == len(COMPETENCIES)


def test_seed_has_no_prerequisite_cycles(repos: Repositories):
    seed(repos)
    report = run_all(repos)
    assert report.ok, report.findings


def test_seed_prerequisites_reference_real_competencies(repos: Repositories):
    seed(repos)
    codes = {c.code for c in repos.competencies.list_all()}
    for rel in repos.prerequisites.list_all():
        competency = repos.competencies.get(rel.competency_id)
        prerequisite = repos.competencies.get(rel.prerequisite_id)
        assert competency is not None and competency.code in codes
        assert prerequisite is not None and prerequisite.code in codes
