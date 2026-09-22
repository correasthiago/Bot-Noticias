"""Liga o contrato do Tutor a um Provider concreto, registrando ProviderEvent.

Se o provider falhar (excecao ou success=False), a sessao permanece
recuperavel (Secao 25): nada alem do ProviderEvent e escrito, e o
chamador recebe `None` para decidir o proximo passo.
"""

from __future__ import annotations

import time

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import ProviderEvent
from central_universal.domain.enums import ProviderFunction
from central_universal.domain.ids import new_id
from central_universal.providers.base import Provider, ProviderCallResult
from central_universal.persistence.repositories import Repositories
from central_universal.tutor.contract import TutorInput, TutorOutput


class TutorService:
    def __init__(self, repos: Repositories, provider: Provider) -> None:
        self.repos = repos
        self.provider = provider

    def run(self, tutor_input: TutorInput) -> tuple[TutorOutput | None, ProviderEvent]:
        start = time.perf_counter()
        try:
            result = self.provider.generate_tutor_turn(tutor_input)
        except Exception as exc:  # noqa: BLE001 - fronteira externa deliberada
            result = ProviderCallResult(
                success=False,
                payload=None,
                latency_ms=(time.perf_counter() - start) * 1000,
                error_message=str(exc),
            )

        event = ProviderEvent(
            id=new_id(),
            provider_name=self.provider.provider_name,
            model=self.provider.model,
            function=ProviderFunction.TUTOR,
            config_version=self.provider.config_version,
            occurred_at=utc_now_iso(),
            success=result.success and result.payload is not None,
            latency_ms=result.latency_ms,
            tokens_used=result.tokens_used,
            cost=result.cost,
            error_message=result.error_message,
        )
        self.repos.provider_events.insert(event)

        if not result.success or result.payload is None:
            return None, event

        output = TutorOutput(
            utterance=str(result.payload.get("utterance", "")),
            activity_prompt=str(result.payload.get("activity_prompt", "")),
            provider_event_id=event.id,
        )
        return output, event
