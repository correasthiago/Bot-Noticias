"""Liga o contrato do Avaliador a um Provider concreto.

Toda saida do provider passa por `validate_evaluator_payload` antes de
poder virar EvaluatorOutput. Se a validacao falhar - ou se o provider
falhar/excecionar - o metodo devolve `None` e grava um ProviderEvent com
success=False: RawInteraction permanece intacta e a avaliacao fica
pendente (Secao 16 e 25, T7).
"""

from __future__ import annotations

import time

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import ProviderEvent
from central_universal.domain.enums import ProviderFunction
from central_universal.domain.ids import new_id
from central_universal.evaluator.contract import (
    EvaluatorInput,
    EvaluatorOutput,
    InvalidEvaluatorOutput,
    validate_evaluator_payload,
)
from central_universal.providers.base import Provider, ProviderCallResult
from central_universal.persistence.repositories import Repositories


class EvaluatorService:
    def __init__(self, repos: Repositories, provider: Provider) -> None:
        self.repos = repos
        self.provider = provider

    def run(
        self, evaluator_input: EvaluatorInput, valid_competency_ids: set[str]
    ) -> tuple[EvaluatorOutput | None, ProviderEvent]:
        start = time.perf_counter()
        try:
            result = self.provider.generate_evaluation(evaluator_input)
        except Exception as exc:  # noqa: BLE001 - fronteira externa deliberada
            result = ProviderCallResult(
                success=False,
                payload=None,
                latency_ms=(time.perf_counter() - start) * 1000,
                error_message=str(exc),
            )

        output: EvaluatorOutput | None = None
        error_message = result.error_message

        if result.success and result.payload is not None:
            try:
                output = validate_evaluator_payload(result.payload, valid_competency_ids)
            except InvalidEvaluatorOutput as exc:
                error_message = f"payload invalido do avaliador: {exc}"

        event = ProviderEvent(
            id=new_id(),
            provider_name=self.provider.provider_name,
            model=self.provider.model,
            function=ProviderFunction.EVALUATOR,
            config_version=self.provider.config_version,
            occurred_at=utc_now_iso(),
            success=output is not None,
            latency_ms=result.latency_ms,
            tokens_used=result.tokens_used,
            cost=result.cost,
            error_message=error_message,
        )
        self.repos.provider_events.insert(event)

        return output, event
