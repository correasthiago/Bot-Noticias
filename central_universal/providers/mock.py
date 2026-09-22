"""MockProvider: fornecedor 100% local e deterministico (Secao 22).

Requisito de aceite explicito: a aplicacao inteira precisa rodar
end-to-end sem API paga e sem internet usando este provider. Ele nao
importa nenhuma biblioteca de rede; toda a "inteligencia" aqui e um
heuristico simples e auditavel sobre o `production_result` e o
`help_level` ja informados pela interacao.

Isto NAO pretende ser um bom avaliador pedagogico - pretende provar que o
motor inteiro (evidence -> decision -> memory -> orchestration) funciona
independente de qualquer fornecedor de IA real (Principio 16).
"""

from __future__ import annotations

import time

from central_universal.domain.enums import EvidenceType, ProductionResult
from central_universal.evaluator.contract import EvaluatorInput
from central_universal.providers.base import Provider, ProviderCallResult
from central_universal.tutor.contract import TutorInput

_CLASSIFICATION_BY_RESULT = {
    ProductionResult.SPONTANEOUS_CORRECT: EvidenceType.POSITIVE,
    ProductionResult.SPONTANEOUS_SELF_CORRECTION: EvidenceType.POSITIVE,
    ProductionResult.CORRECT_AFTER_HINT: EvidenceType.POSITIVE,
    ProductionResult.CORRECT_AFTER_EXTERNAL_CORRECTION: EvidenceType.NEGATIVE,
    ProductionResult.INCORRECT: EvidenceType.NEGATIVE,
    ProductionResult.INCONCLUSIVE: EvidenceType.INCONCLUSIVE,
}

_CONFIDENCE_BY_RESULT = {
    ProductionResult.SPONTANEOUS_CORRECT: 0.9,
    ProductionResult.SPONTANEOUS_SELF_CORRECTION: 0.7,
    ProductionResult.CORRECT_AFTER_HINT: 0.5,
    ProductionResult.CORRECT_AFTER_EXTERNAL_CORRECTION: 0.6,
    ProductionResult.INCORRECT: 0.8,
    ProductionResult.INCONCLUSIVE: 0.3,
}


class MockProvider(Provider):
    provider_name = "mock"
    model = "mock-deterministic-v1"
    config_version = "1"

    def generate_tutor_turn(self, tutor_input: TutorInput) -> ProviderCallResult:
        start = time.perf_counter()
        utterance = (
            f"[MockTutor] Objetivo: {tutor_input.objective}. "
            f"Suporte maximo permitido: {tutor_input.allowed_support_level.value}. "
            f"Contexto: {tutor_input.context}"
        )
        activity_prompt = tutor_input.prompt_seed or tutor_input.objective
        latency_ms = (time.perf_counter() - start) * 1000
        return ProviderCallResult(
            success=True,
            payload={"utterance": utterance, "activity_prompt": activity_prompt},
            latency_ms=latency_ms,
        )

    def generate_evaluation(self, evaluator_input: EvaluatorInput) -> ProviderCallResult:
        start = time.perf_counter()
        classification = _CLASSIFICATION_BY_RESULT[evaluator_input.production_result]
        confidence = _CONFIDENCE_BY_RESULT[evaluator_input.production_result]
        relation = "target"

        findings = [
            {
                "competency_id": competency_id,
                "dimension": "accuracy",
                "classification": classification.value,
                "result": evaluator_input.production_result.value,
                "confidence": confidence,
                "justification": (
                    f"MockProvider: producao classificada como "
                    f"'{evaluator_input.production_result.value}' com nivel de ajuda "
                    f"'{evaluator_input.help_level.value}'."
                ),
                "relation": relation,
                "inconclusive": classification == EvidenceType.INCONCLUSIVE,
            }
            for competency_id in evaluator_input.candidate_competency_ids
        ]
        latency_ms = (time.perf_counter() - start) * 1000
        return ProviderCallResult(
            success=True, payload={"findings": findings}, latency_ms=latency_ms
        )
