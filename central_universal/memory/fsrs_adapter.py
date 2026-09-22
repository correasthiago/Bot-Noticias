"""Adapter isolando Py-FSRS do resto do sistema (Secao 13).

Nenhum outro modulo deve importar o pacote `fsrs` diretamente - so este
arquivo conhece `fsrs.Card`, `fsrs.Scheduler`, `fsrs.Rating`, `fsrs.State`
e `fsrs.ReviewLog`. Isso significa que, se a biblioteca for trocada ou
atualizada de forma incompativel no futuro, apenas este modulo muda.

Persistimos o Card e o ReviewLog serializados (JSON) inteiros, nao so os
campos que usamos hoje - assim o scheduler pode ser reproduzido/recalculado
integralmente a partir do banco (Secao 13: "persistir dados suficientes
para reproduzir/recalcular o scheduler").

Falha do FSRS NUNCA deve apagar evidencia: este adapter nunca toca em
raw_interaction/evidence_event/evidence_assessment, e se `review()` falhar
o MemoryState anterior permanece intacto (Secao 25, T14).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import fsrs

from central_universal.domain.clock import to_utc_iso, utc_now
from central_universal.domain.entities import MemoryReviewLog, MemoryState
from central_universal.domain.enums import MemoryCardState, ProductionResult
from central_universal.domain.ids import new_id
from central_universal.persistence.repositories import Repositories

_STATE_MAP = {
    fsrs.State.Learning: MemoryCardState.LEARNING,
    fsrs.State.Review: MemoryCardState.REVIEW,
    fsrs.State.Relearning: MemoryCardState.RELEARNING,
}

# Mapeamento provisorio e documentado (DECISIONS.md) de resultado de
# producao para a nota de 1-4 que o FSRS espera. Nao existe amostra ainda
# para calibrar isto com dados reais - e a interpretacao pedagogica mais
# direta do resultado observado.
_RATING_MAP = {
    ProductionResult.SPONTANEOUS_CORRECT: fsrs.Rating.Easy,
    ProductionResult.SPONTANEOUS_SELF_CORRECTION: fsrs.Rating.Good,
    ProductionResult.CORRECT_AFTER_HINT: fsrs.Rating.Hard,
    ProductionResult.CORRECT_AFTER_EXTERNAL_CORRECTION: fsrs.Rating.Again,
    ProductionResult.INCORRECT: fsrs.Rating.Again,
    # INCONCLUSIVE nao aparece aqui de proposito: incerteza e um estado
    # legitimo (Principio 14) e nao deve forcar uma nota de memoria.
}


def rating_from_production_result(result: ProductionResult) -> int | None:
    rating = _RATING_MAP.get(result)
    return int(rating) if rating is not None else None


@dataclass
class MemoryReviewError:
    message: str


class MemoryAdapter:
    def __init__(self, repos: Repositories, scheduler: fsrs.Scheduler | None = None) -> None:
        self.repos = repos
        self.scheduler = scheduler or fsrs.Scheduler()

    def get_or_create_state(self, competency_id: str) -> MemoryState:
        existing = self.repos.memory_states.get(competency_id)
        if existing is not None:
            return existing

        card = fsrs.Card()
        state = self._state_from_card(competency_id, card, existing_id=new_id())
        self.repos.memory_states.upsert(state)
        return state

    def review(
        self,
        competency_id: str,
        rating: int,
        review_datetime: datetime | None = None,
        review_duration: int | None = None,
    ) -> MemoryState | MemoryReviewError:
        try:
            current = self.get_or_create_state(competency_id)
            card = fsrs.Card.from_json(current.fsrs_card_json)
            fsrs_rating = fsrs.Rating(rating)
            when = review_datetime or utc_now()
            new_card, review_log = self.scheduler.review_card(
                card, fsrs_rating, review_datetime=when, review_duration=review_duration
            )
        except Exception as exc:  # noqa: BLE001 - fronteira externa deliberada
            return MemoryReviewError(message=str(exc))

        new_state = self._state_from_card(competency_id, new_card, existing_id=current.id)
        self.repos.memory_states.upsert(new_state)

        log = MemoryReviewLog(
            id=new_id(),
            competency_id=competency_id,
            memory_state_id=new_state.id,
            review_datetime=to_utc_iso(review_log.review_datetime),
            rating=int(review_log.rating),
            fsrs_review_log_json=review_log.to_json(),
            created_at=to_utc_iso(utc_now()),
        )
        self.repos.memory_review_logs.insert(log)
        return new_state

    def is_recall_due(self, competency_id: str, now: datetime | None = None) -> bool:
        state = self.repos.memory_states.get(competency_id)
        if state is None or state.due_at is None:
            return False
        from central_universal.domain.clock import parse_iso

        reference = now or utc_now()
        return parse_iso(state.due_at) <= reference

    def retrievability(self, competency_id: str, now: datetime | None = None) -> float | None:
        state = self.repos.memory_states.get(competency_id)
        if state is None:
            return None
        card = fsrs.Card.from_json(state.fsrs_card_json)
        return self.scheduler.get_card_retrievability(card, current_datetime=now or utc_now())

    def _state_from_card(self, competency_id: str, card: "fsrs.Card", existing_id: str) -> MemoryState:
        return MemoryState(
            id=existing_id,
            competency_id=competency_id,
            fsrs_card_json=card.to_json(),
            due_at=to_utc_iso(card.due),
            stability=card.stability,
            difficulty=card.difficulty,
            card_state=_STATE_MAP[card.state].value,
            last_review_at=to_utc_iso(card.last_review) if card.last_review else None,
            updated_at=to_utc_iso(utc_now()),
        )
