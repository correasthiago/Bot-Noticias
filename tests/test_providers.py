from __future__ import annotations

from central_universal.domain.enums import HelpLevel, ProductionResult, ProviderFunction
from central_universal.evaluator.contract import EvaluatorInput
from central_universal.evaluator.service import EvaluatorService
from central_universal.providers.base import Provider, ProviderCallResult
from central_universal.providers.mock import MockProvider
from central_universal.persistence.repositories import Repositories
from central_universal.tutor.contract import TutorInput
from central_universal.tutor.service import TutorService


def test_mock_tutor_produces_output_and_provider_event(repos: Repositories):
    service = TutorService(repos, MockProvider())
    tutor_input = TutorInput(
        objective="Praticar Simple Present na 3a pessoa",
        target_competency_ids=("c1",),
        context="Aluno iniciante",
        allowed_support_level=HelpLevel.A1,
        prompt_seed="Complete: She ___ (go) to school.",
    )
    output, event = service.run(tutor_input)
    assert output is not None
    assert output.utterance
    assert event.success is True
    assert event.function == ProviderFunction.TUTOR
    assert repos.provider_events.list_recent(1)[0].id == event.id


def test_mock_evaluator_produces_valid_findings(repos: Repositories, make_competency):
    competency_id = make_competency()
    service = EvaluatorService(repos, MockProvider())
    evaluator_input = EvaluatorInput(
        raw_interaction_id="ri1",
        learner_input="She go to school",
        tutor_output="...",
        candidate_competency_ids=(competency_id,),
        help_level=HelpLevel.A0,
        production_result=ProductionResult.INCORRECT,
        context="drill",
        rule_version="v0.1.0",
    )
    output, event = service.run(evaluator_input, {competency_id})
    assert output is not None
    assert len(output.findings) == 1
    assert output.findings[0].classification.value == "negative"
    assert event.success is True


class _BrokenProvider(Provider):
    provider_name = "broken"
    model = "broken-1"
    config_version = "1"

    def generate_tutor_turn(self, tutor_input):
        raise RuntimeError("network down")

    def generate_evaluation(self, evaluator_input):
        raise RuntimeError("network down")


class _MalformedProvider(Provider):
    provider_name = "malformed"
    model = "malformed-1"
    config_version = "1"

    def generate_tutor_turn(self, tutor_input):
        return ProviderCallResult(success=True, payload={"utterance": "oi"}, latency_ms=1.0)

    def generate_evaluation(self, evaluator_input):
        return ProviderCallResult(
            success=True, payload={"findings": [{"competency_id": "x"}]}, latency_ms=1.0
        )


def test_tutor_failure_leaves_session_recoverable(repos: Repositories):
    service = TutorService(repos, _BrokenProvider())
    tutor_input = TutorInput(
        objective="x", target_competency_ids=(), context="", allowed_support_level=HelpLevel.A0
    )
    output, event = service.run(tutor_input)
    assert output is None
    assert event.success is False
    assert event.error_message is not None


def test_evaluator_invalid_payload_never_produces_output(repos: Repositories, make_competency):
    competency_id = make_competency()
    service = EvaluatorService(repos, _MalformedProvider())
    evaluator_input = EvaluatorInput(
        raw_interaction_id="ri1",
        learner_input="...",
        tutor_output="...",
        candidate_competency_ids=(competency_id,),
        help_level=HelpLevel.A0,
        production_result=ProductionResult.INCORRECT,
        context="drill",
        rule_version="v0.1.0",
    )
    output, event = service.run(evaluator_input, {competency_id})
    assert output is None
    assert event.success is False
    assert "invalido" in event.error_message
