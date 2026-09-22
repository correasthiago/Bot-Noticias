from __future__ import annotations

from central_universal.decision.service import DecisionService
from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import Competency, Learner, LearningDomain, RuleVersion
from central_universal.domain.ids import new_id
from central_universal.domain.enums import RoutingDecision
from central_universal.persistence.repositories import Repositories


def test_decision_service_records_events_with_justification(repos: Repositories):
    learner = Learner(id=new_id(), display_name="Aluno", created_at=utc_now_iso())
    repos.learners.insert(learner)
    domain = LearningDomain(id=new_id(), code="english", name="Ingles")
    repos.domains.insert(domain)
    competency = Competency(id=new_id(), domain_id=domain.id, code="be", name="BE")
    repos.competencies.insert(competency)
    rule_version = RuleVersion(id=new_id(), version="v0.1.0", description="V0", created_at=utc_now_iso())
    repos.rule_versions.insert(rule_version)

    service = DecisionService(repos)
    routing, action = service.decide(
        competency_id=competency.id, learner_id=learner.id, rule_version=rule_version
    )

    assert routing.routing == RoutingDecision.STUDY  # sem nenhuma evidencia ainda
    assert action is not None

    recorded = repos.decision_events.list_for_competency(competency.id)
    assert len(recorded) == 2  # um routing + uma acao
    for event in recorded:
        assert event.justification.strip() != ""
        assert event.rule_version_id == rule_version.id
